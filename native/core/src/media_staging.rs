//! Selected public Commons media, staged for a manual MAX draft. No network,
//! profile discovery, message sending, QML path input or exported C ABI.
//!
//! The host supplies an existing, dedicated local directory. Paths are resolved
//! once through pinned, non-reparse ancestors; subsequent operations are handle
//! relative. A cross-process lock serializes quota checks and writes. Files
//! remain readable but immutable while PreparedMedia is held. The host rechecks
//! account/page generation, then retains BEFORE native presenter dispatch. This
//! is TTL ownership transfer, not an attachment ACK; no message is sent here.
use std::{
    fs::File,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
};

pub const MAX_FILE_BYTES: u64 = 25 * 1024 * 1024;
pub const MAX_CACHE_BYTES: u64 = 128 * 1024 * 1024;
pub const TTL_SECONDS: u64 = 24 * 60 * 60;
const MAX_ENTRIES: usize = 4096;
const CHUNK: usize = 64 * 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error {
    InvalidInput,
    UnsupportedMedia,
    FileTooLarge,
    CacheFull,
    Busy,
    UnsafePath,
    Unavailable,
    NoSpace,
    Cancelled,
    CleanupFailed,
    UnsupportedPlatform,
}
impl Error {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidInput => "invalid_input",
            Self::UnsupportedMedia => "unsupported_media",
            Self::FileTooLarge => "file_too_large",
            Self::CacheFull => "cache_full",
            Self::Busy => "download_busy",
            Self::UnsafePath => "unsafe_path",
            Self::Unavailable => "storage_unavailable",
            Self::NoSpace => "no_space",
            Self::Cancelled => "cancelled",
            Self::CleanupFailed => "cleanup_failed",
            Self::UnsupportedPlatform => "unsupported_platform",
        }
    }
}
pub type Result<T> = std::result::Result<T, Error>;

#[derive(Clone, Default)]
pub struct Cancellation(Arc<AtomicBool>);
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

/// MIME and length originate in the validated native Commons result, not QML.
pub struct MediaSpec<'a> {
    pub mime: &'a str,
    pub expected_bytes: u64,
}

fn extension(bytes: &[u8], spec: &MediaSpec<'_>) -> Result<&'static str> {
    if spec.expected_bytes > MAX_FILE_BYTES || bytes.len() as u64 > MAX_FILE_BYTES {
        return Err(Error::FileTooLarge);
    }
    if bytes.is_empty() || spec.expected_bytes != bytes.len() as u64 {
        return Err(Error::InvalidInput);
    }
    let (ext, valid) = match spec.mime {
        "image/jpeg" => ("jpg", bytes.starts_with(&[0xff, 0xd8, 0xff])),
        "image/png" => ("png", bytes.starts_with(b"\x89PNG\r\n\x1a\n")),
        "image/gif" => (
            "gif",
            bytes.starts_with(b"GIF87a") || bytes.starts_with(b"GIF89a"),
        ),
        "image/webp" => (
            "webp",
            bytes.len() >= 12 && &bytes[..4] == b"RIFF" && &bytes[8..12] == b"WEBP",
        ),
        "video/webm" => (
            "webm",
            bytes.starts_with(&[0x1a, 0x45, 0xdf, 0xa3])
                && bytes[..bytes.len().min(4096)]
                    .windows(4)
                    .any(|b| b == b"webm"),
        ),
        "video/mp4" => (
            "mp4",
            bytes.len() >= 12
                && &bytes[4..8] == b"ftyp"
                && matches!(
                    &bytes[8..12],
                    b"isom" | b"iso2" | b"mp41" | b"mp42" | b"avc1" | b"M4V "
                ),
        ),
        _ => return Err(Error::UnsupportedMedia),
    };
    if valid {
        Ok(ext)
    } else {
        Err(Error::UnsupportedMedia)
    }
}
fn owned_name(name: &str) -> bool {
    name.strip_prefix("valmedia-")
        .and_then(|v| v.split_once('.'))
        .is_some_and(|(id, ext)| {
            id.len() == 32
                && id
                    .bytes()
                    .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                && matches!(
                    ext,
                    "jpg" | "png" | "gif" | "webp" | "webm" | "mp4" | "part"
                )
        })
}
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
        && !matches!(
            stem.as_str(),
            "COM¹" | "COM²" | "COM³" | "LPT¹" | "LPT²" | "LPT³"
        )
}
fn absolute_path(path: &Path) -> Result<PathBuf> {
    let text = path.to_str().ok_or(Error::UnsafePath)?;
    let b = text.as_bytes();
    if b.len() < 4
        || b.len() > 32000
        || !b[0].is_ascii_alphabetic()
        || b[1] != b':'
        || !matches!(b[2], b'/' | b'\\')
        || text[3..].split(['/', '\\']).any(|c| !component(c))
    {
        return Err(Error::UnsafePath);
    }
    Ok(PathBuf::from(text.replace('/', "\\")))
}
fn file_url(path: &Path) -> String {
    let text = path
        .to_str()
        .expect("validated UTF-8 path")
        .replace('\\', "/");
    let mut out = String::from("file:///");
    for (i, b) in text.bytes().enumerate() {
        if b.is_ascii_alphanumeric() || b"-._~/".contains(&b) || (i == 1 && b == b':') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct Identity {
    volume: u64,
    id: [u8; 16],
    size: u64,
    created: u64,
    modified: u64,
}
impl Identity {
    fn same_object(self, other: Self) -> bool {
        self.volume == other.volume && self.id == other.id && self.created == other.created
    }
}
struct Root {
    path: PathBuf,
    parents: Vec<File>,
}
impl Root {
    fn directory(&self) -> &File {
        self.parents.last().expect("pinned root")
    }
}

pub struct StagingRoot {
    root: Arc<Root>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CacheUsage {
    pub bytes: u64,
    pub files: usize,
    pub removed: usize,
}

/// An immutable read lease. Its URL is for trusted native draft attachment only.
/// Dropping an unaccepted selection attempts exact-identity cleanup. Failure to
/// obtain exclusive deletion leaves an orphan charged to the next quota scan.
pub struct PreparedMedia {
    root: Arc<Root>,
    name: String,
    path: PathBuf,
    lease: Option<File>,
    identity: Identity,
    cleanup: bool,
}
impl PreparedMedia {
    pub fn path(&self) -> &Path {
        &self.path
    }
    pub fn file_url(&self) -> String {
        file_url(&self.path)
    }
    pub fn byte_length(&self) -> u64 {
        self.identity.size
    }
    pub fn cancel(mut self) -> Result<()> {
        self.remove()
    }
    /// Retain after a generation check, BEFORE presenter handoff; not an ACK.
    /// No message is sent. A suppressed handoff leaves a quota-charged TTL file.
    /// Later 24-hour cleanup never forcibly deletes a file with an open reader.
    pub fn retain_for_draft(mut self) {
        self.cleanup = false;
        self.lease.take();
    }
    fn remove(&mut self) -> Result<()> {
        if !self.cleanup {
            return Ok(());
        }
        self.cleanup = false;
        self.lease.take();
        #[cfg(windows)]
        {
            win::remove_exact(self.root.directory(), &self.name, self.identity)
                .map_err(|_| Error::CleanupFailed)
        }
        #[cfg(not(windows))]
        {
            Err(Error::UnsupportedPlatform)
        }
    }
}
impl Drop for PreparedMedia {
    fn drop(&mut self) {
        let _ = self.remove();
    }
}

impl StagingRoot {
    /// Does not create or discover any directory. Caller prepares a dedicated
    /// root and controls its lifetime; parent handles remain held by every lease.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = absolute_path(path.as_ref())?;
        #[cfg(windows)]
        {
            let parents = win::pin_root(&path)?;
            Ok(Self {
                root: Arc::new(Root { path, parents }),
            })
        }
        #[cfg(not(windows))]
        {
            let _ = path;
            Err(Error::UnsupportedPlatform)
        }
    }
    pub fn stage(
        &mut self,
        bytes: &[u8],
        spec: MediaSpec<'_>,
        now_unix_seconds: u64,
        cancel: &Cancellation,
    ) -> Result<PreparedMedia> {
        self.stage_impl(bytes, spec, now_unix_seconds, cancel, |_, _| Ok(()))
    }
    fn stage_impl(
        &mut self,
        bytes: &[u8],
        spec: MediaSpec<'_>,
        now: u64,
        cancel: &Cancellation,
        mut checkpoint: impl FnMut(Checkpoint, &Cancellation) -> Result<()>,
    ) -> Result<PreparedMedia> {
        cancel.check()?;
        let ext = extension(bytes, &spec)?;
        #[cfg(not(windows))]
        {
            let _ = (ext, now, checkpoint);
            Err(Error::UnsupportedPlatform)
        }
        #[cfg(windows)]
        {
            let _lock = win::lock(self.root.directory())?;
            let (usage, _quota_pins, retained_entries) = self.scan(now, cancel)?;
            if retained_entries >= MAX_ENTRIES
                || usage
                    .bytes
                    .checked_add(bytes.len() as u64)
                    .is_none_or(|n| n > MAX_CACHE_BYTES)
            {
                return Err(Error::CacheFull);
            }
            cancel.check()?;
            let mut random = [0u8; 16];
            getrandom::getrandom(&mut random).map_err(|_| Error::Unavailable)?;
            let name = format!(
                "valmedia-{}.{}",
                random
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>(),
                ext
            );
            let mut writer = win::create(self.root.directory(), &name)?;
            // Keep the delete-capable exclusive handle through every failure.
            let write_result = (|| {
                win::identity(&writer)?;
                checkpoint(Checkpoint::Created, cancel)?;
                for chunk in bytes.chunks(CHUNK) {
                    cancel.check()?;
                    writer.write_all(chunk).map_err(win::io_error)?;
                    checkpoint(Checkpoint::Written, cancel)?;
                }
                cancel.check()?;
                checkpoint(Checkpoint::BeforeFlush, cancel)?;
                writer.sync_all().map_err(win::io_error)?;
                checkpoint(Checkpoint::Flushed, cancel)?;
                cancel.check()?;
                let id = win::identity(&writer)?;
                if id.size != bytes.len() as u64 {
                    return Err(Error::Unavailable);
                }
                Ok(id)
            })();
            let written = match write_result {
                Ok(id) => id,
                Err(error) => {
                    if win::delete(&writer).is_err() {
                        return Err(Error::CleanupFailed);
                    }
                    return Err(error);
                }
            };
            drop(writer); // MAX readers need a read-only lease, not DELETE access.
            if let Err(error) =
                checkpoint(Checkpoint::AfterClose, cancel).and_then(|_| cancel.check())
            {
                win::remove_object(self.root.directory(), &name, written)
                    .map_err(|_| Error::CleanupFailed)?;
                return Err(error);
            }
            let mut lease = match win::read(self.root.directory(), &name) {
                Ok(file) => file,
                Err(error) => {
                    win::remove_object(self.root.directory(), &name, written)
                        .map_err(|_| Error::CleanupFailed)?;
                    return Err(error);
                }
            };
            let identity = match win::identity(&lease) {
                Ok(id) if id.same_object(written) && id.size == written.size => id,
                _ => {
                    drop(lease);
                    let _ = win::remove_object(self.root.directory(), &name, written);
                    return Err(Error::UnsafePath);
                }
            };
            // Closing the write handle commits its Windows modification time.
            // ID+length alone cannot detect a same-file rewrite in that gap.
            // Compare all bytes while this read lease forbids WRITE/DELETE opens.
            let verified = (|| {
                let mut buffer = [0u8; CHUNK];
                for expected in bytes.chunks(CHUNK) {
                    cancel.check()?;
                    lease
                        .read_exact(&mut buffer[..expected.len()])
                        .map_err(win::io_error)?;
                    if buffer[..expected.len()] != *expected {
                        return Err(Error::UnsafePath);
                    }
                    checkpoint(Checkpoint::VerifiedChunk, cancel)?;
                }
                cancel.check()?;
                if lease.read(&mut buffer[..1]).map_err(win::io_error)? != 0
                    || win::identity(&lease)? != identity
                {
                    return Err(Error::UnsafePath);
                }
                Ok(())
            })();
            if let Err(error) = verified {
                drop(lease);
                win::remove_exact(self.root.directory(), &name, identity)
                    .map_err(|_| Error::CleanupFailed)?;
                return Err(error);
            }
            let mut prepared = PreparedMedia {
                path: self.root.path.join(&name),
                root: self.root.clone(),
                name,
                lease: Some(lease),
                identity,
                cleanup: true,
            };
            if let Err(error) = cancel.check() {
                prepared.remove()?;
                return Err(error);
            }
            Ok(prepared)
        }
    }
    /// Explicit maintenance; never recursively traverses or removes unknown files.
    pub fn cleanup_expired(
        &mut self,
        now_unix_seconds: u64,
        cancel: &Cancellation,
    ) -> Result<CacheUsage> {
        cancel.check()?;
        #[cfg(windows)]
        {
            let _lock = win::lock(self.root.directory())?;
            Ok(self.scan(now_unix_seconds, cancel)?.0)
        }
        #[cfg(not(windows))]
        {
            let _ = now_unix_seconds;
            Err(Error::UnsupportedPlatform)
        }
    }
    #[cfg(windows)]
    fn scan(&self, now: u64, cancel: &Cancellation) -> Result<(CacheUsage, Vec<File>, usize)> {
        let names = win::names(self.root.directory())?;
        let total_entries = names.len();
        let mut usage = CacheUsage {
            bytes: 0,
            files: 0,
            removed: 0,
        };
        let mut pins = Vec::new();
        for name in names {
            cancel.check()?;
            if !owned_name(&name) {
                continue;
            }
            let file = win::read(self.root.directory(), &name)?;
            let id = win::identity(&file)?;
            // Windows timestamps before 1970 are expired; future timestamps are
            // retained. Saturation avoids wrapped attacker-controlled clocks.
            let modified = (id.modified / 10_000_000).saturating_sub(11_644_473_600);
            if now.saturating_sub(modified) > TTL_SECONDS {
                drop(file);
                match win::remove_exact(self.root.directory(), &name, id) {
                    Ok(()) => {
                        usage.removed += 1;
                        continue;
                    }
                    Err(Error::Busy) => {}
                    Err(error) => return Err(error),
                }
                let file = win::read(self.root.directory(), &name)?;
                if win::identity(&file)? != id {
                    return Err(Error::UnsafePath);
                }
                pins.push(file);
            } else {
                pins.push(file);
            }
            usage.files += 1;
            usage.bytes = usage.bytes.checked_add(id.size).ok_or(Error::CacheFull)?;
        }
        Ok((usage, pins, total_entries - usage.removed))
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Checkpoint {
    Created,
    Written,
    BeforeFlush,
    Flushed,
    AfterClose,
    VerifiedChunk,
}

#[cfg(windows)]
mod win;

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let mut id = [0u8; 16];
            getrandom::getrandom(&mut id).unwrap();
            let path = Path::new("C:/ValMAXBuild/fixtures/media-staging-unit")
                .join(id.iter().map(|b| format!("{b:02x}")).collect::<String>());
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let base = fs::canonicalize("C:/ValMAXBuild/fixtures/media-staging-unit").unwrap();
            let path = fs::canonicalize(&self.0).unwrap();
            assert!(path.starts_with(&base) && path != base);
            fs::remove_dir_all(path).unwrap();
        }
    }
    fn now() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
    }
    fn data() -> Vec<u8> {
        let mut value = vec![0u8; CHUNK * 2 + 17];
        value[..8].copy_from_slice(b"\x89PNG\r\n\x1a\n");
        value
    }
    #[test]
    fn media_staging_write_failure_and_cancel_checkpoints_remove_partial_files() {
        for point in [
            Checkpoint::Created,
            Checkpoint::Written,
            Checkpoint::BeforeFlush,
            Checkpoint::Flushed,
            Checkpoint::AfterClose,
            Checkpoint::VerifiedChunk,
        ] {
            for cancel_at in [false, true] {
                let fixture = Fixture::new();
                let mut root = StagingRoot::open(&fixture.0).unwrap();
                let bytes = data();
                let result = root.stage_impl(
                    &bytes,
                    MediaSpec {
                        mime: "image/png",
                        expected_bytes: bytes.len() as u64,
                    },
                    now(),
                    &Cancellation::default(),
                    |at, cancel| {
                        if at == point {
                            if cancel_at {
                                cancel.cancel();
                            } else {
                                return Err(Error::NoSpace);
                            }
                        }
                        Ok(())
                    },
                );
                assert!(
                    matches!(result,Err(e) if e==if cancel_at {Error::Cancelled} else {Error::NoSpace})
                );
                assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 1); // lock only
            }
        }
    }
    #[test]
    fn media_staging_readback_rejects_same_file_same_length_write_gap_mutation() {
        let fixture = Fixture::new();
        let mut root = StagingRoot::open(&fixture.0).unwrap();
        let bytes = data();
        let result = root.stage_impl(
            &bytes,
            MediaSpec {
                mime: "image/png",
                expected_bytes: bytes.len() as u64,
            },
            now(),
            &Cancellation::default(),
            |at, _| {
                if at == Checkpoint::AfterClose {
                    let path = fs::read_dir(&fixture.0)
                        .unwrap()
                        .map(|e| e.unwrap().path())
                        .find(|p| owned_name(p.file_name().unwrap().to_str().unwrap()))
                        .unwrap();
                    let mut changed = bytes.clone();
                    changed[CHUNK + 3] = 1;
                    fs::write(path, changed).unwrap();
                }
                Ok(())
            },
        );
        assert!(matches!(result, Err(Error::UnsafePath)));
        assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 1);
    }
    #[test]
    fn media_staging_cross_root_zero_wait_lock_and_exclusive_create() {
        let fixture = Fixture::new();
        let root = StagingRoot::open(&fixture.0).unwrap();
        let mut other = StagingRoot::open(&fixture.0).unwrap();
        let held = win::lock(root.root.directory()).unwrap();
        assert_eq!(
            other
                .cleanup_expired(now(), &Cancellation::default())
                .unwrap_err(),
            Error::Busy
        );
        drop(held);
        let name = "valmedia-00000000000000000000000000000001.png";
        let file = win::create(root.root.directory(), name).unwrap();
        drop(file);
        assert!(win::create(root.root.directory(), name).is_err());
        assert_eq!(fs::metadata(fixture.0.join(name)).unwrap().len(), 0);
    }
    #[test]
    fn media_staging_cleanup_refuses_replacement_and_changed_metadata() {
        let fixture = Fixture::new();
        let root = StagingRoot::open(&fixture.0).unwrap();
        let name = "valmedia-00000000000000000000000000000001.png";
        let path = fixture.0.join(name);
        fs::write(&path, b"FIRST").unwrap();
        let file = win::read(root.root.directory(), name).unwrap();
        let expected = win::identity(&file).unwrap();
        drop(file);
        fs::remove_file(&path).unwrap();
        fs::write(&path, b"OTHER").unwrap();
        assert_eq!(
            win::remove_exact(root.root.directory(), name, expected),
            Err(Error::UnsafePath)
        );
        assert_eq!(fs::read(&path).unwrap(), b"OTHER");
        let file = win::read(root.root.directory(), name).unwrap();
        let expected = win::identity(&file).unwrap();
        drop(file);
        fs::write(&path, b"CHANGED").unwrap();
        assert_eq!(
            win::remove_exact(root.root.directory(), name, expected),
            Err(Error::UnsafePath)
        );
        assert_eq!(fs::read(&path).unwrap(), b"CHANGED");
    }
    #[test]
    fn media_staging_owned_filename_policy_is_exact() {
        for ext in ["jpg", "png", "gif", "webp", "webm", "mp4", "part"] {
            assert!(owned_name(&format!("valmedia-{:032x}.{ext}", 42)));
        }
        for name in [
            "valmedia-1.png",
            "valmedia-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF.png",
            "valmedia-00000000000000000000000000000000.exe",
            "valmedia-00000000000000000000000000000000.png.old",
        ] {
            assert!(!owned_name(name));
        }
    }
    #[test]
    fn media_staging_python_signature_fixtures() {
        let fixtures: serde_json::Value =
            serde_json::from_str(include_str!("../tests/media_staging_fixtures.json")).unwrap();
        let constants = &fixtures["constants"];
        assert_eq!(
            constants["MAX_FILE_BYTES"].as_u64().unwrap(),
            MAX_FILE_BYTES
        );
        assert_eq!(
            constants["MAX_CACHE_BYTES"].as_u64().unwrap(),
            MAX_CACHE_BYTES
        );
        assert_eq!(
            constants["STAGING_TTL_SECONDS"].as_u64().unwrap(),
            TTL_SECONDS
        );
        let cases = fixtures["cases"].as_array().unwrap();
        assert!(cases.len() >= 200);
        for (index, case) in cases.iter().enumerate() {
            let data = case["hex"].as_str().unwrap();
            let bytes: Vec<u8> = (0..data.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&data[i..i + 2], 16).unwrap())
                .collect();
            let got = extension(
                &bytes,
                &MediaSpec {
                    mime: case["mime"].as_str().unwrap(),
                    expected_bytes: bytes.len() as u64,
                },
            )
            .is_ok();
            assert_eq!(
                got,
                case["valid"].as_bool().unwrap(),
                "synthetic signature case {index}"
            );
        }
    }
    #[test]
    fn media_staging_cross_process_lock_child() {
        let Ok(path) = std::env::var("VALMAX_SYNTHETIC_STAGING_LOCK_TEST") else {
            return;
        };
        let path = fs::canonicalize(path).unwrap();
        let base = fs::canonicalize("C:/ValMAXBuild/fixtures/media-staging-unit").unwrap();
        assert!(path.starts_with(&base) && path != base);
        let path = path.to_str().unwrap().strip_prefix("\\\\?\\").unwrap();
        let mut root = StagingRoot::open(path).unwrap();
        assert_eq!(
            root.cleanup_expired(now(), &Cancellation::default())
                .unwrap_err(),
            Error::Busy
        );
    }
    #[test]
    fn media_staging_cross_process_lock_is_zero_wait() {
        use std::{
            os::windows::process::CommandExt,
            process::{Command, Stdio},
            time::{Duration, Instant},
        };
        let fixture = Fixture::new();
        let root = StagingRoot::open(&fixture.0).unwrap();
        let held = win::lock(root.root.directory()).unwrap();
        let mut child = Command::new(std::env::current_exe().unwrap())
            .args([
                "--exact",
                "media_staging::tests::media_staging_cross_process_lock_child",
                "--test-threads=1",
            ])
            .env("VALMAX_SYNTHETIC_STAGING_LOCK_TEST", &fixture.0)
            .creation_flags(0x08000000)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(status) = child.try_wait().unwrap() {
                assert!(status.success());
                break;
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                panic!("synthetic lock child timed out");
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        drop(held);
    }
}
