//! Private file staging for an explicitly requested full-profile recovery.
//!
//! This never opens MXMOD, WinCred, a recovery marker or a network connection.
//! Only a fully authenticated profile-v1 stream can produce StagedProfile.
//! Sealing verifies the owned ledger and directory inventory at that instant.
//! A future publisher must repeat validation immediately before publication;
//! enumeration is not an atomic freeze of another process's directory changes.
//! Unpublished files remain owned by the staging ledger until discard/drop or
//! the explicit consuming recovery handoff. Handoff disarms cleanup before
//! closing descendant handles; RootSwap then retains both trees on every Drop.
use crate::{
    mdata_archive, mdata_profile, session_archive::OwnedValue, settings_io::SafeStorePath,
};
use base64::{Engine, engine::general_purpose::STANDARD};
#[cfg(all(test, windows))]
use std::path::PathBuf;
use std::{
    cell::{Cell, RefCell},
    collections::{BTreeMap, BTreeSet},
    fmt,
    fs::File,
    io::{self, Read, Write},
    path::Path,
    rc::Rc,
};
use zeroize::Zeroizing;

pub const RESERVE_BYTES: u64 = 64 * 1024 * 1024;
const FILE_OVERHEAD: u64 = 4096;
const MAX_DIRECTORIES: usize = 32_768;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StageError {
    InvalidRequest,
    InvalidPayload,
    TooLarge,
    UnsafePath,
    ProfileChanged,
    FileExists,
    IoError,
    InsufficientSpace,
    CleanupFailed,
    RollbackFailed,
    Archive(mdata_archive::MDataError),
}
impl StageError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidPayload => "invalid_payload",
            Self::TooLarge => "too_large",
            Self::UnsafePath => "unsafe_path",
            Self::ProfileChanged => "profile_changed",
            Self::FileExists => "file_exists",
            Self::IoError => "io_error",
            Self::InsufficientSpace => "insufficient_space",
            Self::CleanupFailed => "cleanup_failed",
            Self::RollbackFailed => "rollback_failed",
            Self::Archive(error) => error.code(),
        }
    }
}
impl fmt::Display for StageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for StageError {}
pub type Result<T> = std::result::Result<T, StageError>;
impl From<io::Error> for StageError {
    fn from(error: io::Error) -> Self {
        if error.kind() == io::ErrorKind::StorageFull
            || matches!(error.raw_os_error(), Some(112 | 39))
        {
            Self::InsufficientSpace
        } else {
            Self::IoError
        }
    }
}
fn archive_error(error: StageError) -> mdata_archive::MDataError {
    use mdata_archive::MDataError as M;
    match error {
        StageError::TooLarge => M::TooLarge,
        StageError::UnsafePath => M::UnsafePath,
        StageError::InvalidPayload | StageError::FileExists => M::InvalidPayload,
        StageError::InsufficientSpace => M::InsufficientSpace,
        StageError::Archive(error) => error,
        _ => M::IoError,
    }
}

#[cfg(windows)]
#[path = "mdata_profile_stage/win.rs"]
mod win;

#[cfg(windows)]
#[path = "mdata_profile_stage/recovery_swap.rs"]
mod recovery_swap;
#[cfg(windows)]
pub use recovery_swap::{RootSwap, RootSwapState};

#[cfg(windows)]
struct DirectorySlot {
    name: String,
    directory: win::Directory,
    // Indices into the stable ownership ledger, sorted by Windows ordinal name.
    children: Vec<usize>,
    files: Vec<usize>,
}
#[cfg(windows)]
struct FileSlot {
    name: String,
    file: win::OwnedFile,
    size: u64,
}

#[cfg(windows)]
struct State {
    directories: Vec<DirectorySlot>,
    files: Vec<FileSlot>,
    names: BTreeSet<String>,
    // Authenticated archive totals only; derived config may change actual ledger.
    expected: Option<(u64, usize)>,
    declared: u64,
    open_sinks: Cell<usize>,
    failed: Cell<bool>,
    sealed: bool,
    recovery_prepared: bool,
    closed: bool,
    cleanup_failed: bool,
    #[cfg(test)]
    name_comparisons: Cell<usize>,
    // These pins cover only the explicitly supplied parent, not active MXMOD.
    _parent: win::Directory,
    _ancestors: crate::settings_io::GuardedStore,
}

#[cfg(windows)]
impl State {
    fn find_file(&self, parent: usize, name: &str) -> Result<(Option<usize>, usize)> {
        let files = &self.directories[parent].files;
        let (mut low, mut high) = (0, files.len());
        while low < high {
            let middle = low + (high - low) / 2;
            let index = files[middle];
            match win::compare_names(&self.files[index].name, name)? {
                std::cmp::Ordering::Less => low = middle + 1,
                std::cmp::Ordering::Greater => high = middle,
                std::cmp::Ordering::Equal => return Ok((Some(index), middle)),
            }
        }
        Ok((None, low))
    }
    fn find_child(&self, parent: usize, name: &str) -> Result<(Option<usize>, usize)> {
        let children = &self.directories[parent].children;
        let (mut low, mut high) = (0, children.len());
        while low < high {
            let middle = low + (high - low) / 2;
            let index = children[middle];
            #[cfg(test)]
            self.name_comparisons.set(self.name_comparisons.get() + 1);
            match win::compare_names(&self.directories[index].name, name)? {
                std::cmp::Ordering::Less => low = middle + 1,
                std::cmp::Ordering::Greater => high = middle,
                std::cmp::Ordering::Equal => return Ok((Some(index), middle)),
            }
        }
        Ok((None, low))
    }
    fn verify_contents(&self, flush: bool) -> Result<()> {
        for (parent, slot) in self.directories.iter().enumerate() {
            slot.directory.verify()?;
            let inventory = slot.directory.inventory()?;
            if inventory.len() != slot.children.len() + slot.files.len() {
                return Err(StageError::ProfileChanged);
            }
            for entry in inventory {
                if entry.directory {
                    let index = self
                        .find_child(parent, &entry.name)?
                        .0
                        .ok_or(StageError::ProfileChanged)?;
                    if self.directories[index].directory.identity() != entry.identity {
                        return Err(StageError::ProfileChanged);
                    }
                } else {
                    let index = self
                        .find_file(parent, &entry.name)?
                        .0
                        .ok_or(StageError::ProfileChanged)?;
                    if self.files[index].file.identity() != entry.identity {
                        return Err(StageError::ProfileChanged);
                    }
                }
            }
        }
        for file in &self.files {
            file.file.verify_size(file.size)?;
            if flush {
                file.file.flush()?;
            }
        }
        Ok(())
    }
    fn cleanup(&mut self) -> Result<()> {
        if self.closed {
            return if self.cleanup_failed {
                Err(StageError::CleanupFailed)
            } else {
                Ok(())
            };
        }
        if self.open_sinks.get() != 0 {
            return Err(StageError::InvalidRequest);
        }
        let mut failed = false;
        // Pop and close each handle before deleting its parent directory.
        while let Some(item) = self.files.pop() {
            if item.file.delete_owned().is_err() {
                failed = true;
            }
        }
        while let Some(item) = self.directories.pop() {
            if item.directory.delete_owned().is_err() {
                failed = true;
            }
        }
        self.closed = true;
        self.cleanup_failed = failed;
        if failed {
            Err(StageError::CleanupFailed)
        } else {
            Ok(())
        }
    }
}
#[cfg(windows)]
impl Drop for State {
    fn drop(&mut self) {
        // Only unpublished objects created by this state are eligible. Explicit
        // discard reports failures; drop is a final best-effort safeguard.
        let _ = self.cleanup();
    }
}

#[cfg(windows)]
struct Sink {
    file: Option<File>,
    remaining: u64,
    state: Rc<RefCell<State>>,
}
#[cfg(windows)]
impl Write for Sink {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if bytes.len() as u64 > self.remaining || self.state.borrow().failed.get() {
            self.state.borrow().failed.set(true);
            return Err(io::Error::from(io::ErrorKind::InvalidInput));
        }
        let result = self
            .file
            .as_mut()
            .ok_or(io::ErrorKind::BrokenPipe)?
            .write(bytes);
        match result {
            Ok(count) => {
                self.remaining -= count as u64;
                Ok(count)
            }
            Err(error) => {
                self.state.borrow().failed.set(true);
                Err(error)
            }
        }
    }
    fn flush(&mut self) -> io::Result<()> {
        let result = self.file.as_mut().ok_or(io::ErrorKind::BrokenPipe)?.flush();
        if result.is_err() {
            self.state.borrow().failed.set(true);
        }
        result
    }
}
#[cfg(windows)]
impl Drop for Sink {
    fn drop(&mut self) {
        // A writer may outlive its stage wrapper. Keep the ledger alive until
        // after that writer is closed, then allow final owned-object cleanup.
        drop(self.file.take());
        let state = self.state.borrow();
        if self.remaining != 0 {
            state.failed.set(true);
        }
        state.open_sinks.set(state.open_sinks.get() - 1);
    }
}

/// No Debug/Serialize: a stage can contain account metadata and credentials in
/// an authenticated manifest. Only count-based outcomes should reach a UI/log.
pub struct StagedProfile {
    manifest: OwnedValue,
    stage: Stage,
}
impl StagedProfile {
    pub fn file_count(&self) -> usize {
        self.manifest.0["files"].as_array().map_or(0, Vec::len)
    }
    pub fn discard(&mut self) -> Result<()> {
        self.stage.discard()
    }
    /// Consume a fully authenticated private stage and replace only its root
    /// configuration with the current pinned defaults. No active profile IO.
    pub fn prepare_recovery(self) -> Result<PreparedRecovery> {
        if let Err(error) = self.stage.prepare_config() {
            self.stage.discard()?;
            return Err(error);
        }
        Ok(PreparedRecovery { inner: self })
    }
}

/// A prepared tree can be discarded or explicitly handed to a root transaction.
/// Credential operations remain separate and require the transfer coordinator.
pub struct PreparedRecovery {
    inner: StagedProfile,
}
impl PreparedRecovery {
    /// Obtain bounded, zeroizing values only from this authenticated manifest.
    /// This does not inspect or change the Windows credential store.
    pub fn credentials(&self) -> Result<crate::mdata_credential_transaction::RecoveryKeys> {
        self.revalidate()?;
        crate::mdata_credential_transaction::RecoveryKeys::new(credential_values(
            &self.inner.manifest.0,
        )?)
        .map_err(|_| StageError::InvalidPayload)
    }
    pub fn file_count(&self) -> usize {
        #[cfg(windows)]
        {
            self.inner.stage.state.borrow().files.len()
        }
        #[cfg(not(windows))]
        {
            0
        }
    }
    pub fn revalidate(&self) -> Result<()> {
        self.inner.stage.revalidate_prepared()
    }
    pub fn discard(&mut self) -> Result<()> {
        self.inner.stage.discard()
    }
    /// Once handoff starts, any failure preserves the tree for recovery. The
    /// caller must already hold the transfer mutex and verify closed clients.
    /// Closing descendants is required for Windows directory rename; inventory
    /// validation is point-in-time, not protection against arbitrary writers.
    #[cfg(windows)]
    pub fn into_root_swap(self) -> Result<RootSwap> {
        self.revalidate()?;
        if Rc::strong_count(&self.inner.stage.state) != 1 {
            return Err(StageError::InvalidRequest);
        }
        let state = Rc::clone(&self.inner.stage.state);
        {
            let mut inner = state.borrow_mut();
            if inner.open_sinks.get() != 0 || inner.directories.is_empty() {
                return Err(StageError::InvalidRequest);
            }
            // Set this BEFORE releasing any pins or calling fallible code.
            // State::Drop must never delete an eventually published tree.
            inner.closed = true;
            inner.files.clear();
            inner.directories.truncate(1);
        }
        RootSwap::new(state)
    }
}

struct Stage {
    #[cfg(windows)]
    state: Rc<RefCell<State>>,
}
impl Stage {
    fn create(parent: &Path) -> Result<Self> {
        if parent.components().any(|part| {
            part.as_os_str().eq_ignore_ascii_case("ONEME")
                || part.as_os_str().eq_ignore_ascii_case("MXMOD")
        }) {
            return Err(StageError::UnsafePath);
        }
        let safe = SafeStorePath::new(parent.join(".mdata-staging-parent"))
            .map_err(|_| StageError::UnsafePath)?;
        let pinned = safe
            .begin(false)
            .map_err(|_| StageError::UnsafePath)?
            .ok_or(StageError::UnsafePath)?;
        #[cfg(windows)]
        {
            let parent =
                win::Directory::open_parent(safe.path().parent().ok_or(StageError::UnsafePath)?)?;
            let name = format!(
                ".MXMOD-MData-stage-{}",
                crate::startup_status::new_operation_id().map_err(|_| StageError::IoError)?
            );
            let directory = parent.create_child(&name)?;
            Ok(Self {
                state: Rc::new(RefCell::new(State {
                    directories: vec![DirectorySlot {
                        name,
                        directory,
                        children: Vec::new(),
                        files: Vec::new(),
                    }],
                    files: Vec::new(),
                    names: BTreeSet::new(),
                    expected: None,
                    declared: 0,
                    open_sinks: Cell::new(0),
                    failed: Cell::new(false),
                    sealed: false,
                    recovery_prepared: false,
                    closed: false,
                    cleanup_failed: false,
                    #[cfg(test)]
                    name_comparisons: Cell::new(0),
                    _parent: parent,
                    _ancestors: pinned,
                })),
            })
        }
        #[cfg(not(windows))]
        {
            let _ = pinned;
            Err(StageError::IoError)
        }
    }
    fn prepare(&self, total: u64, count: usize) -> Result<()> {
        if total > mdata_archive::MAX_FILE_BYTES || count > mdata_archive::MAX_FILES {
            return Err(StageError::TooLarge);
        }
        #[cfg(windows)]
        {
            let mut state = self.state.borrow_mut();
            if state.closed || state.sealed || state.expected.is_some() {
                return Err(StageError::InvalidRequest);
            }
            let required = total
                .checked_add(count as u64 * FILE_OVERHEAD)
                .and_then(|n| n.checked_add(RESERVE_BYTES))
                .ok_or(StageError::TooLarge)?;
            if state.directories[0].directory.available_bytes()? < required {
                return Err(StageError::InsufficientSpace);
            }
            state.expected = Some((total, count));
            Ok(())
        }
        #[cfg(not(windows))]
        {
            Err(StageError::IoError)
        }
    }
    fn sink(&self, relative: &str, size: u64) -> Result<Box<dyn Write>> {
        if !mdata_profile::safe_relative(relative) {
            return Err(StageError::UnsafePath);
        }
        #[cfg(windows)]
        {
            let mut state = self.state.borrow_mut();
            let (expected_bytes, expected_count) =
                state.expected.ok_or(StageError::InvalidRequest)?;
            if state.closed || state.sealed || state.failed.get() || state.open_sinks.get() != 0 {
                return Err(StageError::InvalidRequest);
            }
            if state.files.len() >= expected_count
                || size > expected_bytes.saturating_sub(state.declared)
            {
                return Err(StageError::InvalidPayload);
            }
            if !state
                .names
                .insert(mdata_archive::path_comparison_key(relative))
            {
                return Err(StageError::InvalidPayload);
            }
            let parts: Vec<_> = relative.split('/').collect();
            let mut parent = 0;
            for component in &parts[..parts.len() - 1] {
                let (existing, position) = state.find_child(parent, component)?;
                parent = if let Some(index) = existing {
                    state.directories[index].directory.verify()?;
                    index
                } else {
                    if state.directories.len() >= MAX_DIRECTORIES {
                        return Err(StageError::TooLarge);
                    }
                    let directory = state.directories[parent]
                        .directory
                        .create_child(component)?;
                    let index = state.directories.len();
                    state.directories.push(DirectorySlot {
                        name: (*component).to_owned(),
                        directory,
                        children: Vec::new(),
                        files: Vec::new(),
                    });
                    // Ownership is recorded before indexing it. Inserting an
                    // index never transfers cleanup authority to a pathname.
                    state.directories[parent].children.insert(position, index);
                    index
                };
            }
            let name = *parts.last().ok_or(StageError::InvalidPayload)?;
            let (existing, position) = state.find_file(parent, name)?;
            if existing.is_some() {
                return Err(StageError::InvalidPayload);
            }
            let file = state.directories[parent].directory.create_file(name)?;
            // Keep ownership even if duplicating the writer handle fails.
            let index = state.files.len();
            state.files.push(FileSlot {
                name: name.to_owned(),
                file,
                size,
            });
            state.directories[parent].files.insert(position, index);
            let writer = state
                .files
                .last()
                .ok_or(StageError::IoError)?
                .file
                .writer_clone()?;
            state.declared += size;
            state.open_sinks.set(state.open_sinks.get() + 1);
            Ok(Box::new(Sink {
                file: Some(writer),
                remaining: size,
                state: Rc::clone(&self.state),
            }))
        }
        #[cfg(not(windows))]
        {
            let _ = size;
            Err(StageError::IoError)
        }
    }
    fn seal(&self) -> Result<()> {
        #[cfg(windows)]
        {
            let mut state = self.state.borrow_mut();
            let expected = state.expected.ok_or(StageError::InvalidRequest)?;
            if state.closed
                || state.sealed
                || state.failed.get()
                || state.open_sinks.get() != 0
                || expected != (state.declared, state.files.len())
            {
                return Err(StageError::InvalidPayload);
            }
            state.verify_contents(true)?;
            state.sealed = true;
            Ok(())
        }
        #[cfg(not(windows))]
        {
            Err(StageError::IoError)
        }
    }
    fn prepare_config(&self) -> Result<()> {
        #[cfg(windows)]
        {
            self.prepare_config_with(|file, bytes| file.replace_contents(bytes))
        }
        #[cfg(not(windows))]
        {
            Err(StageError::IoError)
        }
    }
    #[cfg(windows)]
    fn prepare_config_with(
        &self,
        replace: impl FnOnce(&win::OwnedFile, &[u8]) -> Result<()>,
    ) -> Result<()> {
        let value =
            crate::launcher_config::make_config().map_err(|_| StageError::InvalidPayload)?;
        let bytes = serde_json::to_vec_pretty(&value).map_err(|_| StageError::InvalidPayload)?;
        if bytes.len() > crate::launcher_config::MAX_CONFIG_BYTES {
            return Err(StageError::TooLarge);
        }
        {
            let mut state = self.state.borrow_mut();
            if state.closed
                || !state.sealed
                || state.recovery_prepared
                || state.failed.get()
                || state.open_sinks.get() != 0
            {
                return Err(StageError::InvalidRequest);
            }
            state.verify_contents(false)?;
            if state.find_child(0, "config.json")?.0.is_some() {
                return Err(StageError::InvalidPayload);
            }
            let (existing, position) = state.find_file(0, "config.json")?;
            let old_size = existing.map_or(0, |index| state.files[index].size);
            let total = state
                .declared
                .checked_sub(old_size)
                .and_then(|size| size.checked_add(bytes.len() as u64))
                .ok_or(StageError::TooLarge)?;
            // The authenticated archive limit remains unchanged. A derived
            // config may add one bounded file on top of a maximum-size archive.
            if total
                > mdata_archive::MAX_FILE_BYTES + crate::launcher_config::MAX_CONFIG_BYTES as u64
                || (existing.is_none() && state.files.len() > mdata_archive::MAX_FILES)
            {
                return Err(StageError::TooLarge);
            }
            let additional = (bytes.len() as u64).saturating_sub(old_size);
            if state.directories[0].directory.available_bytes()?
                < RESERVE_BYTES + FILE_OVERHEAD + additional
            {
                return Err(StageError::InsufficientSpace);
            }
            // No subsequent failure can restore readiness after a partial
            // mutation, even if the caller retains this private stage.
            state.failed.set(true);
            let index = if let Some(index) = existing {
                index
            } else {
                let file = state.directories[0].directory.create_file("config.json")?;
                let index = state.files.len();
                state.files.push(FileSlot {
                    name: "config.json".to_owned(),
                    file,
                    size: 0,
                });
                state.directories[0].files.insert(position, index);
                index
            };
            replace(&state.files[index].file, &bytes)?;
            state.files[index].size = bytes.len() as u64;
            state.declared = total;
            state.verify_contents(true)?;
            state.failed.set(false);
            state.recovery_prepared = true;
            Ok(())
        }
    }
    fn revalidate_prepared(&self) -> Result<()> {
        #[cfg(windows)]
        {
            let state = self.state.borrow();
            if state.closed
                || !state.sealed
                || !state.recovery_prepared
                || state.failed.get()
                || state.open_sinks.get() != 0
            {
                return Err(StageError::InvalidRequest);
            }
            state.verify_contents(false)
        }
        #[cfg(not(windows))]
        {
            Err(StageError::IoError)
        }
    }
    fn discard(&self) -> Result<()> {
        #[cfg(windows)]
        {
            self.state.borrow_mut().cleanup()
        }
        #[cfg(not(windows))]
        {
            Err(StageError::IoError)
        }
    }
    #[cfg(all(test, windows))]
    fn path(&self) -> PathBuf {
        self.state.borrow().directories[0]
            .directory
            .path()
            .to_owned()
    }
}

/// The caller supplies an already guarded reader and an explicit existing
/// parent. No archive path/profile discovery occurs. On any authentication or
/// storage-validation failure all owned staging objects are discarded first.
pub fn decode<R: Read + ?Sized>(
    source: &mut R,
    password: &str,
    parent: &Path,
) -> Result<StagedProfile> {
    let stage = Stage::create(parent)?;
    let result = (|| {
        let credentials_policy = mdata_profile::credential_policy();
        let policy = mdata_archive::MDataPolicy {
            credential_policy: &credentials_policy,
            file_path_validator: &mdata_profile::included,
            credential_name_validator: Some(&mdata_profile::allowed_name),
        };
        let mut factory = |name: &str, size: u64| stage.sink(name, size).map_err(archive_error);
        let mut preflight =
            |total: u64, count: usize| stage.prepare(total, count).map_err(archive_error);
        let manifest = OwnedValue(
            mdata_archive::decrypt_stream(
                source,
                password,
                &mut factory,
                &policy,
                Some(&mut preflight),
                Some(mdata_archive::ArchiveKind::Profile),
            )
            .map_err(StageError::Archive)?,
        );
        let credentials = credential_values(&manifest.0)?;
        mdata_profile::validate_storage(&manifest.0, &credentials).map_err(
            |error| match error {
                mdata_profile::ProfileError::TooLarge => StageError::TooLarge,
                mdata_profile::ProfileError::InvalidPayload => StageError::InvalidPayload,
            },
        )?;
        stage.seal()?;
        Ok(manifest)
    })();
    match result {
        Ok(manifest) => Ok(StagedProfile { manifest, stage }),
        Err(error) => {
            stage.discard()?;
            Err(error)
        }
    }
}

fn credential_values(manifest: &serde_json::Value) -> Result<BTreeMap<String, Zeroizing<Vec<u8>>>> {
    let mut credentials = BTreeMap::new();
    for item in manifest["credentials"]
        .as_array()
        .ok_or(StageError::InvalidPayload)?
    {
        let name = item["name"].as_str().ok_or(StageError::InvalidPayload)?;
        let blob = Zeroizing::new(
            STANDARD
                .decode(item["blob"].as_str().ok_or(StageError::InvalidPayload)?)
                .map_err(|_| StageError::InvalidPayload)?,
        );
        if credentials.insert(name.to_owned(), blob).is_some() {
            return Err(StageError::InvalidPayload);
        }
    }
    Ok(credentials)
}

#[cfg(all(test, windows))]
#[path = "mdata_profile_stage/tests.rs"]
mod tests;
