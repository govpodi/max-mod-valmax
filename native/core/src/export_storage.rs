//! Windows staging for an explicitly selected chat export. No profile discovery,
//! network, credentials or arbitrary directory traversal. Every child operation
//! is relative to pinned directory handles; cleanup removes only owned handles.
//! Publication renames the original staging directory without replacing a target.
use crate::export_render::{self, Error, RenderSink, Result, VerifiedAsset};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

const RESERVE_BYTES: u64 = 64 * 1024 * 1024;
const MEDIA_WRITER_SPACE_CHECK_BYTES: u64 = 4 * 1024 * 1024;
const CHUNK_BYTES: usize = 48 * 1024;
const CHUNK_ASSET_LIMIT: u64 = 64 * 1024 * 1024;

fn component(value: &str) -> bool {
    let stem = value.split('.').next().unwrap_or("").to_ascii_uppercase();
    !value.is_empty()
        && !matches!(value, "." | "..")
        && !value.ends_with(['.', ' '])
        && !value
            .chars()
            .any(|c| c < ' ' || c == '\u{7f}' || "<>:\"/\\|?*".contains(c))
        && !matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        && !(stem.len() == 4
            && (stem.starts_with("COM") || stem.starts_with("LPT"))
            && (b'1'..=b'9').contains(&stem.as_bytes()[3]))
}

/// Local absolute Windows path; UNC, device namespaces, ADS and aliases are not
/// accepted. Existing ancestors are subsequently verified by handle.
pub fn absolute_path(value: &Path) -> Result<PathBuf> {
    let text = value.to_str().ok_or(Error::InvalidPath)?;
    let bytes = text.as_bytes();
    if bytes.len() < 4
        || bytes.len() > 32000
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1] != b':'
        || !matches!(bytes[2], b'/' | b'\\')
        || text[3..].split(['/', '\\']).any(|v| !component(v))
    {
        return Err(Error::InvalidPath);
    }
    Ok(PathBuf::from(text.replace('/', "\\")))
}

fn text_name(name: &str) -> bool {
    matches!(
        name,
        "index.html" | "manifest.json" | "assets.jsonl" | "omissions.jsonl" | "local-deleted.html"
    ) || name
        .strip_prefix("messages-")
        .and_then(|v| v.strip_suffix(".html"))
        .is_some_and(|v| {
            !v.starts_with('0') && v.parse::<u64>().is_ok_and(|n| (2..=1001).contains(&n))
        })
}

#[derive(Clone)]
pub struct Cancellation(Arc<AtomicBool>);
impl Default for Cancellation {
    fn default() -> Self {
        Self(Arc::new(AtomicBool::new(false)))
    }
}
impl Cancellation {
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Release);
    }
    pub fn check(&self) -> Result<()> {
        if self.0.load(Ordering::Acquire) {
            Err(Error::Cancelled)
        } else {
            Ok(())
        }
    }
}

struct OwnedFile {
    file: File,
    size: u64,
    pending: Vec<u8>,
}
#[derive(Clone, Copy, PartialEq, Eq)]
struct Identity {
    volume: u64,
    index: [u8; 16],
    size: u64,
    modified: u64,
}

/// A single destination handed to the already selected chat's native downloader.
/// It is deliberately outside the sealed export workspace. The downloader may
/// create/replace its exact child, but pinned ancestors cannot be renamed. No
/// caller pathname or network URL is accepted. Only an explicit terminal native
/// callback permits import/cleanup; dropping an unfinished lease preserves it,
/// because an asynchronous SDK task can still open the path after cancellation.
pub(crate) struct ExternalMediaLease {
    path: PathBuf,
    parents: Vec<File>,
    directory: Option<File>,
    name: String,
    finished: Option<File>,
}
impl ExternalMediaLease {
    pub(crate) fn new(destination: &Path, extension: &str) -> Result<Self> {
        #[cfg(not(windows))]
        {
            let _ = (destination, extension);
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            if !export_render::safe_source(&format!("media/asset.{extension}")) {
                return Err(Error::InvalidPath);
            }
            let destination = absolute_path(destination)?;
            let parents = win::parents(&destination)?;
            let parent = parents.last().ok_or(Error::UnsafePath)?;
            let mut nonce = [0u8; 16];
            getrandom::getrandom(&mut nonce).map_err(|_| Error::IoError)?;
            let suffix: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
            let path = destination.with_file_name(format!(".valmax-download-{suffix}"));
            let directory = win::create(parent, path.file_name().unwrap(), true)?;
            let name = format!("attachment.{extension}");
            Ok(Self {
                path: path.join(&name),
                parents,
                directory: Some(directory),
                name,
                finished: None,
            })
        }
    }
    pub(crate) fn file_url(&self) -> String {
        let mut url = String::from("file:///");
        for b in self.path.to_string_lossy().replace('\\', "/").bytes() {
            if b.is_ascii_alphanumeric() || b"/-_.~:".contains(&b) {
                url.push(b as char);
            } else {
                url.push_str(&format!("%{b:02X}"));
            }
        }
        url
    }
    pub(crate) fn begin_writer(&self, cancel: Cancellation) -> Result<ExternalMediaWriter> {
        cancel.check()?;
        #[cfg(not(windows))]
        {
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            let file = win::create(
                self.directory.as_ref().ok_or(Error::UnsafePath)?,
                std::ffi::OsStr::new(&self.name),
                false,
            )?;
            Ok(ExternalMediaWriter {
                file: Some(file),
                cancel,
                written: 0,
                path: self.path.clone(),
                space_budget: 0,
            })
        }
    }
    #[cfg(windows)]
    fn open_finished(&mut self) -> Result<&mut File> {
        if self.finished.is_none() {
            let directory = self.directory.as_ref().ok_or(Error::InvalidPath)?;
            // Hold this exact handle across copy, verification and deletion.
            // No replacing/open writer can land between import and cleanup.
            let file = win::open(
                directory,
                std::ffi::OsStr::new(&self.name),
                win::READ | win::DELETE,
                0,
                false,
                Some(false),
            )?;
            win::verify(&file, false)?;
            self.finished = Some(file);
        }
        self.finished.as_mut().ok_or(Error::SourceUnavailable)
    }
    /// Terminal callback only. Unknown children make directory deletion fail;
    /// there is no recursive walk, glob cleanup or deletion by external path.
    pub(crate) fn settle(&mut self) -> Result<()> {
        #[cfg(not(windows))]
        {
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            if self.directory.is_none() {
                return Ok(());
            }
            match self.open_finished() {
                Ok(file) => win::delete(file)?,
                Err(Error::SourceUnavailable) => {}
                Err(error) => return Err(error),
            }
            self.finished.take();
            win::delete(self.directory.as_ref().unwrap())?;
            self.directory.take();
            self.parents.clear();
            Ok(())
        }
    }
}

/// Host-only bounded opaque stream. No path or URL is accepted by the writer.
pub(crate) struct ExternalMediaWriter {
    file: Option<File>,
    cancel: Cancellation,
    written: u64,
    path: PathBuf,
    space_budget: u64,
}
impl ExternalMediaWriter {
    pub(crate) fn write(&mut self, bytes: &[u8]) -> Result<()> {
        self.cancel.check()?;
        if bytes.is_empty() || bytes.len() > 1024 * 1024 {
            return Err(Error::LimitExceeded);
        }
        let total = self
            .written
            .checked_add(bytes.len() as u64)
            .ok_or(Error::AssetTooLarge)?;
        if total > export_render::MAX_ASSET_BYTES {
            return Err(Error::AssetTooLarge);
        }
        #[cfg(windows)]
        if self.space_budget < bytes.len() as u64 {
            // The temporary download is outside the sealed archive. Keep a
            // reserve on its own volume during long unknown-length streams.
            win::space(&self.path, MEDIA_WRITER_SPACE_CHECK_BYTES)?;
            self.space_budget = MEDIA_WRITER_SPACE_CHECK_BYTES;
        }
        self.file
            .as_mut()
            .ok_or(Error::SourceUnavailable)?
            .write_all(bytes)
            .map_err(|error| {
                #[cfg(windows)]
                {
                    win::io_error(error)
                }
                #[cfg(not(windows))]
                {
                    let _ = error;
                    Error::IoError
                }
            })?;
        self.written = total;
        self.space_budget = self.space_budget.saturating_sub(bytes.len() as u64);
        Ok(())
    }
    /// Reuse this exact pinned download file for a lower VIDEO_PLAY variant.
    /// No pathname lookup or new lease occurs between attempts.
    pub(crate) fn rewind_truncate(&mut self) -> Result<()> {
        self.cancel.check()?;
        let file = self.file.as_mut().ok_or(Error::SourceUnavailable)?;
        file.set_len(0).map_err(|error| {
            #[cfg(windows)]
            { win::io_error(error) }
            #[cfg(not(windows))]
            { let _ = error; Error::IoError }
        })?;
        file.seek(SeekFrom::Start(0)).map_err(|error| {
            #[cfg(windows)]
            { win::io_error(error) }
            #[cfg(not(windows))]
            { let _ = error; Error::IoError }
        })?;
        self.written = 0;
        self.space_budget = 0;
        Ok(())
    }
    pub(crate) fn close(&mut self) -> Result<()> {
        if let Some(file) = self.file.take() {
            file.sync_all().map_err(|error| {
                #[cfg(windows)]
                {
                    win::io_error(error)
                }
                #[cfg(not(windows))]
                {
                    let _ = error;
                    Error::IoError
                }
            })?;
        }
        Ok(())
    }
}

#[cfg(all(test, windows))]
mod download_lease_tests {
    use super::*;
    use std::fs;
    struct Fixture {
        root: PathBuf,
        lease: Option<ExternalMediaLease>,
    }
    impl Fixture {
        fn new() -> Self {
            let base = Path::new("E:/ValMAXBuild/fixtures/export-download-lease");
            fs::create_dir_all(base).unwrap();
            let mut nonce = [0u8; 16];
            getrandom::getrandom(&mut nonce).unwrap();
            let suffix: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
            let root = base.join(suffix);
            fs::create_dir(&root).unwrap();
            let lease = ExternalMediaLease::new(&root.join("archive"), "png").unwrap();
            Self {
                root,
                lease: Some(lease),
            }
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            self.lease.take();
            assert_eq!(
                self.root.parent(),
                Some(Path::new("E:/ValMAXBuild/fixtures/export-download-lease"))
            );
            assert_eq!(self.root.file_name().unwrap().to_str().unwrap().len(), 32);
            assert!(
                !fs::symlink_metadata(&self.root)
                    .unwrap()
                    .file_type()
                    .is_symlink()
            );
            fs::remove_dir_all(&self.root).unwrap();
        }
    }
    #[test]
    fn verified_handle_stays_pinned_until_cleanup() {
        let mut f = Fixture::new();
        let lease = f.lease.as_mut().unwrap();
        let path = lease.path.clone();
        fs::write(&path, b"synthetic-original").unwrap();
        lease.open_finished().unwrap();
        let other = f.root.join("replacement.bin");
        fs::write(&other, b"synthetic-replacement").unwrap();
        assert!(fs::remove_file(&path).is_err());
        assert!(fs::rename(&other, &path).is_err());
        lease.settle().unwrap();
        assert_eq!(fs::read(&other).unwrap(), b"synthetic-replacement");
        assert!(!path.parent().unwrap().exists());
    }
    #[test]
    fn unexpected_children_are_preserved_without_recursive_cleanup() {
        let mut f = Fixture::new();
        let lease = f.lease.as_mut().unwrap();
        let unknown = lease.path.with_file_name("unrelated.bin");
        fs::write(&unknown, b"synthetic-unknown").unwrap();
        assert!(lease.settle().is_err());
        assert_eq!(fs::read(&unknown).unwrap(), b"synthetic-unknown");
        fs::remove_file(&unknown).unwrap();
        lease.settle().unwrap();
    }
    #[test]
    fn pinned_writer_rewinds_partial_high_variant_before_lower_variant() {
        let mut f = Fixture::new();
        let lease = f.lease.as_mut().unwrap();
        let mut writer = lease.begin_writer(Cancellation::default()).unwrap();
        writer.write(b"partial-high-variant").unwrap();
        writer.rewind_truncate().unwrap();
        writer.write(b"complete-lower-variant").unwrap();
        writer.close().unwrap();
        assert_eq!(fs::read(&lease.path).unwrap(), b"complete-lower-variant");
        lease.settle().unwrap();
    }
}

/// Private SQLite spool alongside the selected export destination. This owns
/// only index.sqlite and its randomly named directory. The index pin permits
/// SQLite reads/writes but denies deletion/replacement. The SQLite connection
/// must be closed before cleanup; no recursive or pathname-based removal is
/// used, and unexpected files make cleanup fail visibly.
pub(crate) struct OwnedExportSpool {
    path: PathBuf,
    parents: Vec<File>,
    directory: Option<File>,
    pin: Option<File>,
    identity: Option<Identity>,
    cleanup_failed: bool,
}

impl OwnedExportSpool {
    pub(crate) fn new(destination: &Path, cancel: &Cancellation) -> Result<Self> {
        #[cfg(not(windows))]
        {
            let _ = (destination, cancel);
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            cancel.check()?;
            let destination = absolute_path(destination)?;
            let parents = win::parents(&destination)?;
            let parent = parents.last().ok_or(Error::UnsafePath)?;
            let mut nonce = [0u8; 16];
            getrandom::getrandom(&mut nonce).map_err(|_| Error::IoError)?;
            let suffix: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
            let directory_path = destination.with_file_name(format!(".valmax-index-{suffix}"));
            let directory = win::create(parent, directory_path.file_name().unwrap(), true)?;
            let mut result = Self {
                path: directory_path.join("index.sqlite"),
                parents,
                directory: Some(directory),
                pin: None,
                identity: None,
                cleanup_failed: false,
            };
            let outcome = (|| {
                let directory = result.directory.as_ref().unwrap();
                result.pin = Some(win::create(
                    directory,
                    std::ffi::OsStr::new("index.sqlite"),
                    false,
                )?);
                let identity = win::identity(result.pin.as_ref().unwrap())?;
                result.identity = Some(identity);
                // Drop the exclusive create handle before SQLite-compatible
                // READ sharing. A replacement in this gap is rejected by ID.
                result.pin.take();
                result.pin = Some(win::pin_spool(directory, identity)?);
                cancel.check()
            })();
            if let Err(error) = outcome {
                return Err(if result.cleanup().is_err() {
                    Error::CleanupFailed
                } else {
                    error
                });
            }
            Ok(result)
        }
    }

    pub(crate) fn path(&self) -> &Path {
        &self.path
    }

    /// Verify SQLite's actual opened file, rather than merely trusting the
    /// named open. Size and mtime legitimately change as SQLite writes.
    #[cfg(windows)]
    pub(crate) fn verify_database(&self, file: &File) -> Result<()> {
        let expected = self.identity.ok_or(Error::UnsafePath)?;
        let actual = win::identity(file)?;
        if actual.volume != expected.volume || actual.index != expected.index {
            return Err(Error::SourceChanged);
        }
        Ok(())
    }

    pub(crate) fn cleanup(&mut self) -> Result<()> {
        if self.cleanup_failed {
            return Err(Error::CleanupFailed);
        }
        let Some(directory) = self.directory.take() else {
            return Ok(());
        };
        let mut failed = false;
        #[cfg(windows)]
        {
            if let Some(expected) = self.identity.take() {
                self.pin.take();
                match win::reopen(
                    &directory,
                    std::ffi::OsStr::new("index.sqlite"),
                    expected,
                    true,
                ) {
                    Ok(file) => {
                        if win::delete(&file).is_err() {
                            failed = true;
                        }
                    }
                    Err(Error::SourceUnavailable) => {}
                    Err(_) => failed = true,
                }
            } else if let Some(file) = self.pin.take() {
                // Only constructor failure before recording the create ID.
                if win::delete(&file).is_err() {
                    failed = true;
                }
            }
            // Kernel deletion only succeeds for an empty directory. Unknown
            // entries are preserved; no glob, recursive walk or cleanup retry.
            if win::delete(&directory).is_err() {
                failed = true;
            }
        }
        self.pin.take();
        self.parents.clear();
        self.cleanup_failed = failed;
        if failed {
            Err(Error::CleanupFailed)
        } else {
            Ok(())
        }
    }
}

impl Drop for OwnedExportSpool {
    fn drop(&mut self) {
        let _ = self.cleanup();
    }
}

/// Render errors poison the workspace. `render` is the only way to mark it ready;
/// direct low-level writes alone can never publish an incomplete HTML export.
pub struct ExportWorkspace {
    destination: PathBuf,
    staging: PathBuf,
    parents: Vec<File>,
    stage: Option<File>,
    media: Option<File>,
    media_identity: Option<Identity>,
    files: BTreeMap<String, OwnedFile>,
    closed: BTreeMap<String, Identity>,
    reserved: BTreeSet<String>,
    cancel: Cancellation,
    poisoned: bool,
    rendered: bool,
    published: bool,
    space_budget: u64,
    cleanup_failed: bool,
}

impl ExportWorkspace {
    pub fn new(destination: impl AsRef<Path>, cancel: Cancellation) -> Result<Self> {
        #[cfg(not(windows))]
        {
            let _ = (destination, cancel);
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            cancel.check()?;
            let destination = absolute_path(destination.as_ref())?;
            let parents = win::parents(&destination)?;
            let parent = parents.last().ok_or(Error::UnsafePath)?;
            if win::exists(parent, destination.file_name().ok_or(Error::UnsafePath)?)? {
                return Err(Error::Exists);
            }
            let mut nonce = [0u8; 16];
            getrandom::getrandom(&mut nonce).map_err(|_| Error::IoError)?;
            let suffix: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
            let staging = destination.with_file_name(format!(".valmax-export-{suffix}.tmp"));
            let stage = win::create(parent, staging.file_name().unwrap(), true)?;
            let mut result = Self {
                destination,
                staging,
                parents,
                stage: Some(stage),
                media: None,
                media_identity: None,
                files: BTreeMap::new(),
                closed: BTreeMap::new(),
                reserved: BTreeSet::new(),
                cancel,
                poisoned: false,
                rendered: false,
                published: false,
                space_budget: 0,
                cleanup_failed: false,
            };
            if let Err(error) = win::verify(result.stage.as_ref().unwrap(), true) {
                let _ = result.abort();
                return Err(error);
            }
            Ok(result)
        }
    }

    /// Host-only path for diagnostics/tests. Never send this path to an external
    /// downloader; media currently enters via owned bounded chunks.
    pub fn staging_path(&self) -> &Path {
        &self.staging
    }
    pub fn cancellation(&self) -> Cancellation {
        self.cancel.clone()
    }

    fn active(&self) -> Result<()> {
        self.cancel.check()?;
        if self.poisoned || self.rendered || self.published || self.stage.is_none() {
            return Err(Error::UnsafePath);
        }
        Ok(())
    }
    fn record<T>(&mut self, result: Result<T>) -> Result<T> {
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }
    #[cfg(windows)]
    fn text_space(&mut self, bytes: u64) -> Result<()> {
        if bytes > self.space_budget {
            win::space(&self.destination, bytes)?;
            self.space_budget = 1024 * 1024;
        }
        self.space_budget = self.space_budget.saturating_sub(bytes);
        Ok(())
    }
    #[cfg(windows)]
    fn flush(owned: &mut OwnedFile) -> Result<()> {
        if !owned.pending.is_empty() {
            owned
                .file
                .write_all(&owned.pending)
                .map_err(win::io_error)?;
            owned.pending.clear();
        }
        owned.file.sync_all().map_err(win::io_error)?;
        if win::verify(&owned.file, false)? != owned.size {
            return Err(Error::SourceChanged);
        }
        Ok(())
    }
    #[cfg(windows)]
    fn seal(&mut self, name: &str) -> Result<()> {
        let owned = self.files.get_mut(name).ok_or(Error::SourceUnavailable)?;
        Self::flush(owned)?;
        let identity = win::identity(&owned.file)?;
        self.closed.insert(name.into(), identity);
        self.files.remove(name);
        Ok(())
    }
    #[cfg(windows)]
    fn reopen(&self, name: &str, identity: Identity, delete: bool) -> Result<File> {
        let (parent, base) = if let Some(base) = name.strip_prefix("media/") {
            (self.media.as_ref().ok_or(Error::UnsafePath)?, base)
        } else {
            (self.stage.as_ref().ok_or(Error::UnsafePath)?, name)
        };
        win::reopen(parent, std::ffi::OsStr::new(base), identity, delete)
    }
    #[cfg(windows)]
    fn media_directory(&mut self) -> Result<&File> {
        if self.media.is_none() {
            let file = win::create(
                self.stage.as_ref().ok_or(Error::UnsafePath)?,
                std::ffi::OsStr::new("media"),
                true,
            )?;
            self.media = Some(file);
            self.media_identity = Some(win::object_identity(self.media.as_ref().unwrap(), true)?);
        }
        self.media.as_ref().ok_or(Error::UnsafePath)
    }

    pub fn reserve_asset(&mut self, name: &str) -> Result<()> {
        let outcome = (|| {
            self.active()?;
            if !export_render::safe_source(name) {
                return Err(Error::InvalidPath);
            }
            if !self.reserved.insert(name.to_owned()) {
                return Err(Error::Exists);
            }
            Ok(())
        })();
        self.record(outcome)
    }

    /// A completed SDK download enters the sealed workspace by a constant-memory
    /// copy, never by granting the SDK a workspace path. The lease remains owned
    /// by the job until explicit settlement, including failed/cancelled imports.
    pub(crate) fn import_download(
        &mut self,
        name: &str,
        lease: &mut ExternalMediaLease,
    ) -> Result<()> {
        self.active()?;
        if !self.reserved.contains(name)
            || !export_render::safe_source(name)
            || self.files.contains_key(name)
            || self.closed.contains_key(name)
        {
            return Err(Error::UnsafePath);
        }
        #[cfg(not(windows))]
        {
            let _ = lease;
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            let source = lease.open_finished()?;
            let identity = win::identity(source)?;
            if identity.size == 0 {
                return Err(Error::SourceUnavailable);
            }
            if identity.size > export_render::MAX_ASSET_BYTES {
                return Err(Error::AssetTooLarge);
            }
            win::space(&self.destination, identity.size)?;
            let file = win::create(
                self.media_directory()?,
                std::ffi::OsStr::new(&name[6..]),
                false,
            )?;
            self.files.insert(
                name.into(),
                OwnedFile {
                    file,
                    size: 0,
                    pending: Vec::new(),
                },
            );
            let mut block = vec![0u8; 1024 * 1024];
            let mut total = 0u64;
            loop {
                self.cancel.check()?;
                let count = source.read(&mut block).map_err(win::io_error)?;
                if count == 0 {
                    break;
                }
                total = total
                    .checked_add(count as u64)
                    .ok_or(Error::AssetTooLarge)?;
                if total > identity.size {
                    return Err(Error::SourceChanged);
                }
                let owned = self.files.get_mut(name).ok_or(Error::SourceUnavailable)?;
                owned
                    .file
                    .write_all(&block[..count])
                    .map_err(win::io_error)?;
                owned.size = total;
            }
            if total != identity.size || win::identity(source)? != identity {
                return Err(Error::SourceChanged);
            }
            self.seal(name)
        }
    }

    /// Strictly acknowledged append. The coordinator owns retry deduplication.
    /// No replacing/truncating existing files, arbitrary URLs or caller paths.
    pub fn write_asset_chunk(
        &mut self,
        name: &str,
        offset: u64,
        data: &[u8],
        maximum: u64,
    ) -> Result<u64> {
        let outcome = (|| {
            self.active()?;
            if !self.reserved.contains(name) || !export_render::safe_source(name) {
                return Err(Error::UnsafePath);
            }
            if data.is_empty()
                || data.len() > CHUNK_BYTES
                || maximum == 0
                || maximum > CHUNK_ASSET_LIMIT
                || offset
                    .checked_add(data.len() as u64)
                    .is_none_or(|n| n > maximum)
            {
                return Err(Error::AssetTooLarge);
            }
            #[cfg(windows)]
            {
                win::space(&self.destination, data.len() as u64)?;
                if offset == 0 {
                    if self.files.contains_key(name) || self.closed.contains_key(name) {
                        return Err(Error::Exists);
                    }
                    let file = win::create(
                        self.media_directory()?,
                        std::ffi::OsStr::new(&name[6..]),
                        false,
                    )?;
                    self.files.insert(
                        name.to_owned(),
                        OwnedFile {
                            file,
                            size: 0,
                            pending: Vec::new(),
                        },
                    );
                } else if !self.files.contains_key(name) {
                    let identity = *self.closed.get(name).ok_or(Error::SourceUnavailable)?;
                    let file = win::reopen_write(
                        self.media_directory()?,
                        std::ffi::OsStr::new(&name[6..]),
                        identity,
                    )?;
                    self.closed.remove(name);
                    self.files.insert(
                        name.into(),
                        OwnedFile {
                            file,
                            size: identity.size,
                            pending: Vec::new(),
                        },
                    );
                }
                let owned = self.files.get_mut(name).ok_or(Error::SourceUnavailable)?;
                let actual = win::verify(&owned.file, false)?;
                if actual != offset || actual != owned.size {
                    return Err(Error::SourceChanged);
                }
                owned
                    .file
                    .seek(SeekFrom::Start(offset))
                    .map_err(|_| Error::IoError)?;
                self.cancel.check()?;
                owned.file.write_all(data).map_err(win::io_error)?;
                owned.size = offset + data.len() as u64;
                if win::verify(&owned.file, false)? != owned.size {
                    return Err(Error::SourceChanged);
                }
                let size = owned.size;
                // No open descriptor is retained for the downloader between chunks.
                self.seal(name)?;
                Ok(size)
            }
            #[cfg(not(windows))]
            {
                Err(Error::UnsupportedPlatform)
            }
        })();
        self.record(outcome)
    }

    fn verify(&mut self, name: &str, maximum: u64) -> Result<VerifiedAsset> {
        self.active()?;
        if !export_render::safe_source(name) {
            return Err(Error::InvalidPath);
        }
        if maximum == 0 || maximum > export_render::MAX_ASSET_BYTES {
            return Err(Error::AssetTooLarge);
        }
        #[cfg(windows)]
        {
            let identity = *self.closed.get(name).ok_or(Error::SourceUnavailable)?;
            let mut file = self.reopen(name, identity, false)?;
            let before = win::verify(&file, false)?;
            if before != identity.size {
                return Err(Error::SourceChanged);
            }
            if before > maximum {
                return Err(Error::AssetTooLarge);
            }
            let mut hash = Sha256::new();
            let mut head = Vec::new();
            let mut total = 0;
            let mut block = vec![0u8; 1024 * 1024];
            loop {
                self.cancel.check()?;
                let n = file.read(&mut block).map_err(win::io_error)?;
                if n == 0 {
                    break;
                }
                total += n as u64;
                if total > maximum {
                    return Err(Error::AssetTooLarge);
                }
                if head.len() < 32 {
                    head.extend_from_slice(&block[..n.min(32 - head.len())]);
                }
                hash.update(&block[..n]);
            }
            if total != before || win::identity(&file)? != identity {
                return Err(Error::SourceChanged);
            }
            Ok(VerifiedAsset {
                bytes: total,
                sha256: format!("{:x}", hash.finalize()),
                head,
            })
        }
        #[cfg(not(windows))]
        {
            Err(Error::UnsupportedPlatform)
        }
    }

    pub fn render<I: IntoIterator<Item = Result<serde_json::Value>>>(
        &mut self,
        chat: &serde_json::Value,
        messages: I,
        completion: &serde_json::Value,
        options: export_render::RenderOptions,
        progress: Option<&mut dyn FnMut(export_render::Counts)>,
    ) -> Result<serde_json::Value> {
        self.active()?;
        let result =
            export_render::render_into(self, chat, messages, completion, options, progress);
        match result {
            Ok(value) => {
                self.rendered = true;
                Ok(value)
            }
            Err(error) => {
                self.poisoned = true;
                Err(error)
            }
        }
    }

    /// No destination can appear until the full renderer succeeds and every owned
    /// file is flushed. Windows requires closing descendants before renaming a
    /// directory; the original stage and all ancestors stay pinned throughout.
    /// This does not promise immutability against the account that owns the export.
    pub fn publish(&mut self) -> Result<PathBuf> {
        let result = (|| {
            self.cancel.check()?;
            if self.poisoned || !self.rendered || self.published {
                return Err(Error::UnsafePath);
            }
            #[cfg(windows)]
            {
                win::verify(self.stage.as_ref().ok_or(Error::UnsafePath)?, true)?;
                let names: Vec<_> = self.files.keys().cloned().collect();
                for name in names {
                    self.seal(&name)?;
                }
                for (name, identity) in &self.closed {
                    drop(self.reopen(name, *identity, false)?);
                }
                self.check_inventory()?;
                self.cancel.check()?;
                // Root handle owns the rename, no pathname reopen and no replace.
                self.media.take();
                win::rename(
                    self.stage.as_ref().unwrap(),
                    self.destination.file_name().ok_or(Error::UnsafePath)?,
                )?;
                self.published = true;
                self.closed.clear();
                self.stage.take();
                self.parents.clear();
                Ok(self.destination.clone())
            }
            #[cfg(not(windows))]
            {
                Err(Error::UnsupportedPlatform)
            }
        })();
        self.record(result)
    }

    #[cfg(windows)]
    fn check_inventory(&self) -> Result<()> {
        let expected: BTreeSet<_> = self
            .files
            .keys()
            .chain(self.closed.keys())
            .cloned()
            .collect();
        let mut actual = BTreeSet::new();
        for item in std::fs::read_dir(&self.staging).map_err(win::io_error)? {
            let item = item.map_err(win::io_error)?;
            let name = item
                .file_name()
                .into_string()
                .map_err(|_| Error::UnsafePath)?;
            if name == "media" && self.media.is_some() {
                for child in std::fs::read_dir(item.path()).map_err(win::io_error)? {
                    let child = child.map_err(win::io_error)?;
                    let name = child
                        .file_name()
                        .into_string()
                        .map_err(|_| Error::UnsafePath)?;
                    actual.insert(format!("media/{name}"));
                }
            } else {
                actual.insert(name);
            }
        }
        if actual != expected {
            return Err(Error::UnsafePath);
        }
        Ok(())
    }

    /// Does not traverse or recursively delete unknown entries. An injected file
    /// leaves a finite cleanup_failed result instead of deleting somebody's data.
    pub fn abort(&mut self) -> Result<()> {
        if self.cleanup_failed {
            return Err(Error::CleanupFailed);
        }
        if self.published || self.stage.is_none() {
            return Ok(());
        }
        self.poisoned = true;
        let mut failed = false;
        #[cfg(windows)]
        {
            for (_, owned) in std::mem::take(&mut self.files) {
                if win::delete(&owned.file).is_err() {
                    failed = true;
                }
            }
            // After a failed rename, child handles have been closed. Reopen only
            // previously owned identities, relative to the still pinned stage.
            if self.media.is_none() && self.media_identity.is_some() {
                match win::reopen_dir(
                    self.stage.as_ref().unwrap(),
                    std::ffi::OsStr::new("media"),
                    self.media_identity.ok_or(Error::CleanupFailed)?,
                ) {
                    Ok(file) => self.media = Some(file),
                    Err(_) => failed = true,
                }
            }
            for (name, identity) in std::mem::take(&mut self.closed) {
                match self.reopen(&name, identity, true) {
                    Ok(file) => {
                        if win::delete(&file).is_err() {
                            failed = true;
                        }
                    }
                    Err(Error::SourceUnavailable) => {}
                    Err(_) => failed = true,
                }
            }
            if let Some(media) = self.media.take() {
                if win::delete(&media).is_err() {
                    failed = true;
                }
            }
            if let Some(stage) = self.stage.take() {
                if win::delete(&stage).is_err() {
                    failed = true;
                }
            }
        }
        self.parents.clear();
        self.cleanup_failed = failed;
        if failed {
            Err(Error::CleanupFailed)
        } else {
            Ok(())
        }
    }
}
impl Drop for ExportWorkspace {
    fn drop(&mut self) {
        let _ = self.abort();
    }
}

impl RenderSink for ExportWorkspace {
    fn begin_file(&mut self, name: &str) -> Result<()> {
        let outcome = (|| {
            self.active()?;
            if !text_name(name) {
                return Err(Error::InvalidPath);
            }
            if self.files.contains_key(name) || self.closed.contains_key(name) {
                return Err(Error::Exists);
            }
            #[cfg(windows)]
            {
                if name.ends_with(".html") {
                    let old: Vec<_> = self
                        .files
                        .keys()
                        .filter(|name| name.ends_with(".html"))
                        .cloned()
                        .collect();
                    for name in old {
                        self.seal(&name)?;
                    }
                }
                let file = win::create(
                    self.stage.as_ref().ok_or(Error::UnsafePath)?,
                    std::ffi::OsStr::new(name),
                    false,
                )?;
                self.files.insert(
                    name.into(),
                    OwnedFile {
                        file,
                        size: 0,
                        pending: Vec::new(),
                    },
                );
                Ok(())
            }
            #[cfg(not(windows))]
            {
                Err(Error::UnsupportedPlatform)
            }
        })();
        self.record(outcome)
    }
    fn write_text(&mut self, name: &str, text: &str) -> Result<()> {
        let outcome = (|| {
            self.active()?;
            if !text_name(name) {
                return Err(Error::InvalidPath);
            }
            #[cfg(windows)]
            {
                self.text_space(text.len() as u64)?;
                let owned = self.files.get_mut(name).ok_or(Error::SourceUnavailable)?;
                self.cancel.check()?;
                // Handles are exclusive while open. Batch small HTML fragments;
                // do not issue metadata/free-space/seek calls per message span.
                if owned.pending.len() + text.len() > 64 * 1024 {
                    owned
                        .file
                        .write_all(&owned.pending)
                        .map_err(win::io_error)?;
                    owned.pending.clear();
                    owned
                        .file
                        .write_all(text.as_bytes())
                        .map_err(win::io_error)?;
                } else {
                    owned.pending.extend_from_slice(text.as_bytes());
                }
                owned.size = owned
                    .size
                    .checked_add(text.len() as u64)
                    .ok_or(Error::LimitExceeded)?;
                Ok(())
            }
            #[cfg(not(windows))]
            {
                let _ = text;
                Err(Error::UnsupportedPlatform)
            }
        })();
        self.record(outcome)
    }
    fn verify_asset(&mut self, name: &str, maximum: u64) -> Result<VerifiedAsset> {
        // Missing/oversize media is an omission, not a corrupt workspace.
        let result = self.verify(name, maximum);
        if result.as_ref().is_err_and(|e| {
            !matches!(
                e,
                Error::SourceUnavailable | Error::AssetTooLarge | Error::SourceChanged
            )
        }) {
            self.poisoned = true;
        }
        result
    }
    fn discard_asset(&mut self, name: &str) -> Result<()> {
        let result = (|| {
            self.active()?;
            if !export_render::safe_source(name) {
                return Err(Error::InvalidPath);
            }
            if let Some(owned) = self.files.get(name) {
                #[cfg(windows)]
                {
                    win::delete(&owned.file)?;
                }
                self.files.remove(name);
                self.closed.remove(name);
            } else if let Some(identity) = self.closed.get(name).copied() {
                #[cfg(windows)]
                {
                    match self.reopen(name, identity, true) {
                        Ok(file) => win::delete(&file)?,
                        Err(Error::SourceUnavailable) => {}
                        Err(error) => return Err(error),
                    }
                }
                self.closed.remove(name);
            }
            self.reserved.remove(name);
            Ok(())
        })();
        self.record(result)
    }
    fn check_cancelled(&mut self) -> Result<()> {
        let result = self.active();
        self.record(result)
    }
}

#[cfg(windows)]
mod win {
    use super::*;
    use std::{
        ffi::{OsStr, c_void},
        mem,
        os::windows::{
            ffi::OsStrExt,
            io::{AsRawHandle, FromRawHandle},
        },
        ptr,
    };
    type Handle = *mut c_void;
    pub(super) const DELETE: u32 = 0x10000;
    pub(super) const READ: u32 = 0x80000000;
    const WRITE: u32 = 0x40000000;
    const OPEN_REPARSE: u32 = 0x200000;
    #[repr(C)]
    #[derive(Default)]
    struct Info {
        attributes: u32,
        creation: [u32; 2],
        access: [u32; 2],
        write: [u32; 2],
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct FileIdInfo {
        volume: u64,
        index: [u8; 16],
    }
    #[repr(C)]
    struct UnicodeString {
        length: u16,
        maximum: u16,
        buffer: *mut u16,
    }
    #[repr(C)]
    struct ObjectAttributes {
        length: u32,
        root: Handle,
        name: *mut UnicodeString,
        attributes: u32,
        security: *mut c_void,
        quality: *mut c_void,
    }
    #[repr(C)]
    struct IoStatus {
        status: usize,
        information: usize,
    }
    #[repr(C)]
    #[derive(Default)]
    struct DeviceInfo {
        device: u32,
        characteristics: u32,
    }
    #[repr(C)]
    struct Rename {
        replace: u32,
        root: Handle,
        length: u32,
        name: [u16; 1],
    }
    // BY_HANDLE_FILE_INFORMATION has the same ABI as settings_io's private type.
    const _: () = assert!(mem::size_of::<Info>() == 52);
    #[allow(clashing_extern_declarations)]
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateFileW(
            name: *const u16,
            access: u32,
            share: u32,
            security: *const c_void,
            creation: u32,
            flags: u32,
            template: Handle,
        ) -> Handle;
        fn GetLastError() -> u32;
        fn GetFileType(handle: Handle) -> u32;
        fn GetFileInformationByHandle(handle: Handle, info: *mut Info) -> i32;
        fn GetFileInformationByHandleEx(
            handle: Handle,
            class: u32,
            info: *mut c_void,
            length: u32,
        ) -> i32;
        fn GetFinalPathNameByHandleW(
            handle: Handle,
            buffer: *mut u16,
            length: u32,
            flags: u32,
        ) -> u32;
        fn SetFileInformationByHandle(
            handle: Handle,
            class: u32,
            buffer: *const c_void,
            length: u32,
        ) -> i32;
        fn GetDiskFreeSpaceExW(
            path: *const u16,
            available: *mut u64,
            total: *mut u64,
            free: *mut u64,
        ) -> i32;
    }
    #[link(name = "ntdll")]
    unsafe extern "system" {
        fn NtCreateFile(
            handle: *mut Handle,
            access: u32,
            attributes: *mut ObjectAttributes,
            status: *mut IoStatus,
            allocation: *const i64,
            attrs: u32,
            share: u32,
            disposition: u32,
            options: u32,
            ea: *const c_void,
            ea_length: u32,
        ) -> i32;
        fn NtSetInformationFile(
            handle: Handle,
            status: *mut IoStatus,
            information: *const c_void,
            length: u32,
            class: u32,
        ) -> i32;
        fn RtlNtStatusToDosError(status: i32) -> u32;
        fn NtQueryVolumeInformationFile(
            handle: Handle,
            status: *mut IoStatus,
            information: *mut c_void,
            length: u32,
            class: u32,
        ) -> i32;
    }
    fn wide(value: &OsStr) -> Vec<u16> {
        value.encode_wide().chain(Some(0)).collect()
    }
    fn code(code: u32) -> Error {
        match code {
            80 | 183 => Error::Exists,
            2 | 3 => Error::SourceUnavailable,
            112 | 39 => Error::NoSpace,
            267 | 4390 | 1920 | 1921 => Error::UnsafePath,
            _ => Error::IoError,
        }
    }
    pub(super) fn io_error(error: std::io::Error) -> Error {
        code(error.raw_os_error().unwrap_or(0) as u32)
    }
    fn last() -> Error {
        code(unsafe { GetLastError() })
    }
    fn final_path(file: &File, flags: u32) -> Result<String> {
        let mut buffer = vec![0u16; 32768];
        let length = unsafe {
            GetFinalPathNameByHandleW(
                file.as_raw_handle(),
                buffer.as_mut_ptr(),
                buffer.len() as u32,
                flags,
            )
        } as usize;
        if length == 0 || length >= buffer.len() {
            return Err(Error::UnsafePath);
        }
        String::from_utf16(&buffer[..length]).map_err(|_| Error::UnsafePath)
    }
    fn local_volume_root(file: &File, expected: &Path) -> Result<()> {
        // Query the device behind the opened handle, not the drive-letter mapping
        // before opening it. Removable local disks are allowed; remote devices,
        // unsupported queries and non-disk devices fail closed.
        let mut io = IoStatus {
            status: 0,
            information: 0,
        };
        let mut info = DeviceInfo::default();
        let status = unsafe {
            NtQueryVolumeInformationFile(
                file.as_raw_handle(),
                &mut io,
                &mut info as *mut _ as *mut c_void,
                mem::size_of::<DeviceInfo>() as u32,
                4,
            )
        };
        if status < 0 || !local_device(info.device, info.characteristics, io.information) {
            return Err(Error::UnsafePath);
        }
        let dos = final_path(file, 0)?;
        let expected = format!("\\\\?\\{}", expected.display());
        if !dos.eq_ignore_ascii_case(&expected) {
            return Err(Error::UnsafePath);
        }
        Ok(())
    }
    fn local_device(device: u32, characteristics: u32, returned: usize) -> bool {
        returned == mem::size_of::<DeviceInfo>() && device == 7 && characteristics & 0x10 == 0
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        #[test]
        fn local_device_predicate_rejects_remote_and_unknown() {
            assert!(local_device(7, 0x20020, 8));
            assert!(local_device(7, 0x1, 8));
            assert!(!local_device(7, 0x10, 8));
            assert!(!local_device(7, 0x20030, 8));
            assert!(!local_device(2, 0, 8));
            assert!(!local_device(7, 0, 0));
            assert!(!local_device(7, 0, 4));
        }
        #[test]
        fn file_identity_compares_all_128_bits_and_volume() {
            let first = Identity {
                volume: 0x123456789abcdef0,
                index: [1; 16],
                size: 4,
                modified: 5,
            };
            let mut second = first;
            second.index[15] = 2;
            assert!(first != second);
            second = first;
            second.volume ^= 1 << 40;
            assert!(first != second);
        }
    }
    pub(super) fn open(
        parent: &File,
        name: &OsStr,
        access: u32,
        share: u32,
        create: bool,
        directory: Option<bool>,
    ) -> Result<File> {
        let mut encoded: Vec<u16> = name.encode_wide().collect();
        if encoded.is_empty()
            || encoded.len() > 32766
            || encoded.iter().any(|&c| matches!(c, 0 | 47 | 92 | 58))
        {
            return Err(Error::InvalidPath);
        }
        let mut text = UnicodeString {
            length: (encoded.len() * 2) as u16,
            maximum: (encoded.len() * 2) as u16,
            buffer: encoded.as_mut_ptr(),
        };
        let mut attr = ObjectAttributes {
            length: mem::size_of::<ObjectAttributes>() as u32,
            root: parent.as_raw_handle(),
            name: &mut text,
            attributes: 0x40,
            security: ptr::null_mut(),
            quality: ptr::null_mut(),
        };
        let mut status = IoStatus {
            status: 0,
            information: 0,
        };
        let mut handle = ptr::null_mut();
        let result = unsafe {
            NtCreateFile(
                &mut handle,
                access | 0x100000,
                &mut attr,
                &mut status,
                ptr::null(),
                0x80,
                share,
                if create { 2 } else { 1 },
                OPEN_REPARSE
                    | 0x20
                    | match directory {
                        Some(true) => 1,
                        Some(false) => 0x40,
                        None => 0,
                    },
                ptr::null(),
                0,
            )
        };
        if result < 0 {
            return Err(code(unsafe { RtlNtStatusToDosError(result) }));
        }
        Ok(unsafe { File::from_raw_handle(handle) })
    }
    pub(super) fn verify(file: &File, directory: bool) -> Result<u64> {
        let mut info = Info::default();
        if unsafe { GetFileType(file.as_raw_handle()) } != 1
            || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.attributes & 0x400 != 0
            || (info.attributes & 0x10 != 0) != directory
            || !directory && info.links != 1
        {
            return Err(Error::UnsafePath);
        }
        Ok(((info.size_high as u64) << 32) | info.size_low as u64)
    }
    pub(super) fn identity(file: &File) -> Result<Identity> {
        object_identity(file, false)
    }
    pub(super) fn object_identity(file: &File, directory: bool) -> Result<Identity> {
        verify(file, directory)?;
        let mut info = Info::default();
        if unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0 {
            return Err(last());
        }
        // FILE_ID_INFO is 128-bit on both NTFS and ReFS. Do not assume the old
        // 64-bit BY_HANDLE_FILE_INFORMATION index is unique on every volume.
        let mut identity = FileIdInfo::default();
        if unsafe {
            GetFileInformationByHandleEx(
                file.as_raw_handle(),
                18,
                &mut identity as *mut _ as *mut c_void,
                mem::size_of::<FileIdInfo>() as u32,
            )
        } == 0
        {
            return Err(Error::UnsafePath);
        }
        Ok(Identity {
            volume: identity.volume,
            index: identity.index,
            size: ((info.size_high as u64) << 32) | info.size_low as u64,
            modified: ((info.write[1] as u64) << 32) | info.write[0] as u64,
        })
    }
    pub(super) fn reopen(
        parent: &File,
        name: &OsStr,
        expected: Identity,
        delete: bool,
    ) -> Result<File> {
        let file = open(
            parent,
            name,
            READ | if delete { DELETE } else { 0 },
            0,
            false,
            Some(false),
        )?;
        let actual = identity(&file)?;
        if if delete {
            actual.volume != expected.volume || actual.index != expected.index
        } else {
            actual != expected
        } {
            return Err(Error::SourceChanged);
        }
        Ok(file)
    }
    pub(super) fn reopen_write(parent: &File, name: &OsStr, expected: Identity) -> Result<File> {
        let file = open(parent, name, READ | WRITE | DELETE, 0, false, Some(false))?;
        if identity(&file)? != expected {
            return Err(Error::SourceChanged);
        }
        Ok(file)
    }
    pub(super) fn pin_spool(parent: &File, expected: Identity) -> Result<File> {
        // READ (not just metadata access) participates in Windows sharing;
        // SQLite can open READ|WRITE, while all delete-capable opens fail.
        let file = open(
            parent,
            OsStr::new("index.sqlite"),
            READ,
            3,
            false,
            Some(false),
        )?;
        let actual = identity(&file)?;
        if actual.volume != expected.volume || actual.index != expected.index {
            return Err(Error::SourceChanged);
        }
        Ok(file)
    }
    pub(super) fn reopen_dir(parent: &File, name: &OsStr, expected: Identity) -> Result<File> {
        let file = open(parent, name, DELETE | 0x81, 3, false, Some(true))?;
        let actual = object_identity(&file, true)?;
        if actual.volume != expected.volume || actual.index != expected.index {
            return Err(Error::UnsafePath);
        }
        Ok(file)
    }
    pub(super) fn parents(destination: &Path) -> Result<Vec<File>> {
        let mut held = Vec::new();
        for path in destination
            .ancestors()
            .skip(1)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            let file = if let Some(parent) = held.last() {
                open(
                    parent,
                    path.file_name().ok_or(Error::UnsafePath)?,
                    0x81,
                    3,
                    false,
                    Some(true),
                )?
            } else {
                let name = wide(path.as_os_str());
                let raw = unsafe {
                    CreateFileW(
                        name.as_ptr(),
                        0x81,
                        3,
                        ptr::null(),
                        3,
                        0x2000000 | OPEN_REPARSE,
                        ptr::null_mut(),
                    )
                };
                if raw as isize == -1 {
                    return Err(last());
                }
                let file = unsafe { File::from_raw_handle(raw) };
                local_volume_root(&file, path)?;
                file
            };
            verify(&file, true)?;
            held.push(file);
        }
        Ok(held)
    }
    pub(super) fn exists(parent: &File, name: &OsStr) -> Result<bool> {
        match open(parent, name, 0x80, 7, false, None) {
            Ok(_) => Ok(true),
            Err(Error::SourceUnavailable) => Ok(false),
            Err(error) => Err(error),
        }
    }
    pub(super) fn create(parent: &File, name: &OsStr, directory: bool) -> Result<File> {
        let file = open(
            parent,
            name,
            if directory {
                DELETE | 0x81
            } else {
                READ | WRITE | DELETE
            },
            if directory { 3 } else { 0 },
            true,
            Some(directory),
        )?;
        if let Err(error) = verify(&file, directory) {
            let _ = delete(&file);
            return Err(error);
        }
        Ok(file)
    }
    pub(super) fn delete(file: &File) -> Result<()> {
        let yes: u8 = 1;
        if unsafe {
            SetFileInformationByHandle(
                file.as_raw_handle(),
                4,
                &yes as *const _ as *const c_void,
                1,
            )
        } == 0
        {
            Err(last())
        } else {
            Ok(())
        }
    }
    pub(super) fn rename(stage: &File, name: &OsStr) -> Result<()> {
        let name = wide(name);
        let offset = mem::offset_of!(Rename, name);
        let size = (offset + name.len() * 2).max(mem::size_of::<Rename>());
        let mut memory = vec![0usize; size.div_ceil(mem::size_of::<usize>())];
        let raw = memory.as_mut_ptr() as *mut Rename;
        unsafe {
            (*raw).replace = 0;
            (*raw).root = ptr::null_mut();
            (*raw).length = ((name.len() - 1) * 2) as u32;
            ptr::copy_nonoverlapping(
                name.as_ptr(),
                (raw as *mut u8).add(offset) as *mut u16,
                name.len(),
            );
            let mut status = IoStatus {
                status: 0,
                information: 0,
            };
            let result = NtSetInformationFile(
                stage.as_raw_handle(),
                &mut status,
                raw as *const c_void,
                size as u32,
                10,
            );
            if result < 0 {
                return Err(code(RtlNtStatusToDosError(result)));
            }
        }
        Ok(())
    }
    pub(super) fn space(destination: &Path, bytes: u64) -> Result<()> {
        let path = wide(destination.parent().ok_or(Error::UnsafePath)?.as_os_str());
        let mut available = 0;
        if unsafe {
            GetDiskFreeSpaceExW(
                path.as_ptr(),
                &mut available,
                ptr::null_mut(),
                ptr::null_mut(),
            )
        } == 0
        {
            return Err(last());
        }
        if available < bytes.saturating_add(RESERVE_BYTES) {
            return Err(Error::NoSpace);
        }
        Ok(())
    }
}
