//! Compact MData export from an explicit MXMOD root to a selected new file.
//! No close/restart, history/cache traversal, HTTP or credential inventory.
//! Work is synchronous and belongs on a native worker. Windows filesystem I/O
//! and scrypt have no hard latency guarantee; cancellation is checked between
//! stages. Once atomic publication starts, a late cancellation may lose to it.
use crate::{
    export_storage::Cancellation,
    login_credentials::LoginCredentials,
    mdata_archive::{self as archive, ArchiveKind, MDataError, MDataPolicy},
    mdata_login,
    mdata_login_capture::{self as capture, Snapshot},
    mdata_preview::MAX_ARCHIVE_BYTES,
    mdata_recovery_state,
    selected_path::selected_file_url,
    session_archive,
    settings_io::{GuardedStore, SafeStorePath, StoreError},
    transfer_lock::TransferLease,
};
use serde::Serialize;
use serde_json::Value;
use std::{
    fmt,
    io::{self, Read, Write},
    path::{Path, PathBuf},
    sync::Mutex,
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

pub const RESERVE_BYTES: u64 = 256 * 1024 * 1024;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ExportError(&'static str);
impl ExportError {
    pub fn code(self) -> &'static str {
        self.0
    }
}
impl fmt::Display for ExportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for ExportError {}
type Result<T> = std::result::Result<T, ExportError>;
fn store(error: StoreError) -> ExportError {
    ExportError(match error {
        StoreError::UnsafePath => "unsafe_path",
        StoreError::StoreBusy => "busy",
        StoreError::FileExists => "file_exists",
        _ => "io_error",
    })
}
fn check(cancel: &Cancellation) -> Result<()> {
    cancel.check().map_err(|_| ExportError("cancelled"))
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportResult {
    pub schema: u8,
    pub state: &'static str,
    pub action: &'static str,
    pub accepted: bool,
    pub result: &'static str,
    pub profiles: usize,
    pub archive_bytes: u64,
}

#[derive(Default)]
struct Slot {
    closed: bool,
    active: Option<Cancellation>,
}
/// A runtime owns one slot. No credentials are read at construction or status.
#[derive(Default)]
pub struct MDataExportService {
    slot: Mutex<Slot>,
}
struct Active<'a> {
    service: &'a MDataExportService,
    cancel: Cancellation,
}
impl Drop for Active<'_> {
    fn drop(&mut self) {
        self.service
            .slot
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .active = None;
    }
}
impl MDataExportService {
    fn admit(&self) -> Result<Active<'_>> {
        let mut slot = self.slot.lock().map_err(|_| ExportError("io_error"))?;
        if slot.closed {
            return Err(ExportError("cancelled"));
        }
        if slot.active.is_some() {
            return Err(ExportError("busy"));
        }
        let cancel = Cancellation::default();
        slot.active = Some(cancel.clone());
        Ok(Active {
            service: self,
            cancel,
        })
    }
    pub fn execute(
        &self,
        profile: &Path,
        file: &str,
        password: &str,
        selection: Option<&Value>,
        credentials: &dyn LoginCredentials,
    ) -> Result<ExportResult> {
        let active = self.admit()?;
        let _transfer = TransferLease::acquire().map_err(|e| ExportError(e.code()))?;
        export(
            profile,
            file,
            password,
            selection,
            credentials,
            &active.cancel,
            &DiskCapacity,
        )
    }
    /// Host shutdown signal only; no I/O. Admission stays closed permanently.
    pub fn cancel_pending(&self) {
        let active = {
            let mut slot = self.slot.lock().unwrap_or_else(|e| e.into_inner());
            slot.closed = true;
            slot.active.clone()
        };
        if let Some(cancel) = active {
            cancel.cancel();
        }
    }
}

trait Capacity {
    fn available(&self, pinned_directory: &Path) -> Result<u64>;
}
struct DiskCapacity;
impl Capacity for DiskCapacity {
    fn available(&self, path: &Path) -> Result<u64> {
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            #[link(name = "kernel32")]
            unsafe extern "system" {
                fn GetDiskFreeSpaceExW(
                    path: *const u16,
                    available: *mut u64,
                    total: *mut u64,
                    free: *mut u64,
                ) -> i32;
            }
            let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
            let mut available = 0;
            if unsafe {
                GetDiskFreeSpaceExW(
                    wide.as_ptr(),
                    &mut available,
                    std::ptr::null_mut(),
                    std::ptr::null_mut(),
                )
            } == 0
            {
                return Err(ExportError("io_error"));
            }
            Ok(available)
        }
        #[cfg(not(windows))]
        {
            let _ = path;
            Err(ExportError("io_error"))
        }
    }
}
fn space(capacity: &dyn Capacity, profile: &Path, destination: &Path, bytes: u64) -> Result<()> {
    let required = RESERVE_BYTES
        .checked_add(bytes)
        .ok_or(ExportError("too_large"))?;
    if capacity.available(profile)? < RESERVE_BYTES || capacity.available(destination)? < required {
        return Err(ExportError("insufficient_space"));
    }
    Ok(())
}
fn new_output(output: &GuardedStore) -> Result<()> {
    match output.read_bounded(0) {
        Ok(None) => Ok(()),
        Ok(Some(_)) | Err(StoreError::InvalidStore) => Err(ExportError("file_exists")),
        Err(error) => Err(store(error)),
    }
}
fn profile_path(profile: &Path) -> Result<PathBuf> {
    let path = SafeStorePath::new(profile.join("config.json")).map_err(store)?;
    let profile = path.path().parent().ok_or(ExportError("unsafe_path"))?;
    if profile.file_name().and_then(|v| v.to_str()) != Some("MXMOD")
        || profile
            .components()
            .any(|v| v.as_os_str().eq_ignore_ascii_case("ONEME"))
    {
        return Err(ExportError("unsafe_path"));
    }
    Ok(profile.to_owned())
}
fn destination_path(profile: &Path, file: &str) -> Result<PathBuf> {
    let path = selected_file_url(file).map_err(|_| ExportError("unsafe_path"))?;
    if !path
        .extension()
        .is_some_and(|v| v.eq_ignore_ascii_case("mdata"))
        || path
            .components()
            .any(|v| v.as_os_str().eq_ignore_ascii_case("ONEME"))
    {
        return Err(ExportError("unsafe_path"));
    }
    // Unicode/case aliases cannot redirect a guarded path, but conservatively
    // reject case-insensitive lexical descendants before any credential read.
    let base = archive::path_comparison_key(&profile.to_string_lossy());
    let target = archive::path_comparison_key(&path.to_string_lossy());
    if target == base || target.starts_with(&(base + "\\")) {
        return Err(ExportError("unsafe_path"));
    }
    Ok(path)
}
struct SecretReader {
    bytes: Zeroizing<Vec<u8>>,
    at: usize,
}
impl Read for SecretReader {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        let length = out.len().min(self.bytes.len() - self.at);
        out[..length].copy_from_slice(&self.bytes[self.at..self.at + length]);
        self.at += length;
        Ok(length)
    }
}
struct Ciphertext<'a> {
    bytes: Zeroizing<Vec<u8>>,
    cancel: &'a Cancellation,
}
impl Write for Ciphertext<'_> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        // write_all retries Interrupted, so cancellation must be terminal.
        if self.cancel.check().is_err() {
            return Err(io::ErrorKind::Other.into());
        }
        if data.len() > MAX_ARCHIVE_BYTES.saturating_sub(self.bytes.len()) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        self.bytes.extend_from_slice(data);
        Ok(data.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
fn same(first: &Snapshot, second: &Snapshot) -> bool {
    if first.tokens.len() != second.tokens.len() || first.auth.len() != second.auth.len() {
        return false;
    }
    let mut equal = true;
    for (key, value) in &first.tokens {
        equal &= second
            .tokens
            .get(key)
            .is_some_and(|other| bool::from(value.as_slice().ct_eq(other.as_slice())));
    }
    for (key, value) in &first.auth {
        equal &= second
            .auth
            .get(key)
            .is_some_and(|other| bool::from(value.as_slice().ct_eq(other.as_slice())));
    }
    equal
}
fn attempt(
    profile: &Path,
    output: &GuardedStore,
    password: &str,
    selection: Option<&Value>,
    credentials: &dyn LoginCredentials,
    cancel: &Cancellation,
    capacity: &dyn Capacity,
) -> Result<ExportResult> {
    check(cancel)?;
    let snapshot =
        capture::capture(profile, credentials, selection).map_err(|e| ExportError(e.code()))?;
    let policy = session_archive::ArchivePolicy::default();
    let auth_name = |path: &str| mdata_login::auth_id(path).is_some();
    let token_name = |name: &str| mdata_login::token_id(name).is_some();
    let policy = MDataPolicy {
        credential_policy: &policy,
        file_path_validator: &auth_name,
        credential_name_validator: Some(&token_name),
    };
    let mut output_bytes = Ciphertext {
        bytes: Zeroizing::new(Vec::new()),
        cancel,
    };
    let mut source = |path: &str| -> std::result::Result<Box<dyn Read>, MDataError> {
        let bytes = snapshot
            .auth
            .get(path)
            .ok_or(MDataError::InvalidPayload)?
            .clone();
        Ok(Box::new(SecretReader { bytes, at: 0 }))
    };
    let encoded = archive::encrypt_stream(
        snapshot.manifest.value(),
        password,
        &mut source,
        &mut output_bytes,
        &policy,
        ArchiveKind::Login,
    );
    check(cancel)?;
    let summary = encoded.map_err(|e| ExportError(e.code()))?;
    let again =
        capture::capture(profile, credentials, selection).map_err(|e| ExportError(e.code()))?;
    if !same(&snapshot, &again) {
        return Err(ExportError("profile_changed"));
    }
    for (name, value) in &snapshot.tokens {
        let observed = credentials.read(name).map_err(|e| ExportError(e.code()))?;
        if !observed.is_some_and(|other| bool::from(value.as_slice().ct_eq(other.as_slice()))) {
            return Err(ExportError("profile_changed"));
        }
    }
    let parent = output.path().parent().ok_or(ExportError("unsafe_path"))?;
    space(capacity, profile, parent, summary.archive_bytes)?;
    mdata_recovery_state::ensure_clear(profile).map_err(|_| ExportError("recovery_required"))?;
    check(cancel)?;
    new_output(output)?;
    // No plaintext is written at any point; the owned temporary contains only
    // the authenticated ciphertext and the final rename refuses replacement.
    output.atomic_create(&output_bytes.bytes).map_err(|error| {
        if error == StoreError::IoError
            && space(capacity, profile, parent, summary.archive_bytes)
                == Err(ExportError("insufficient_space"))
        {
            ExportError("insufficient_space")
        } else {
            store(error)
        }
    })?;
    Ok(ExportResult {
        schema: 1,
        state: "done",
        action: "export",
        accepted: true,
        result: "exported",
        profiles: snapshot.tokens.len(),
        archive_bytes: summary.archive_bytes,
    })
}
fn export(
    profile: &Path,
    file: &str,
    password: &str,
    selection: Option<&Value>,
    credentials: &dyn LoginCredentials,
    cancel: &Cancellation,
    capacity: &dyn Capacity,
) -> Result<ExportResult> {
    session_archive::password_bytes(password).map_err(|_| ExportError("invalid_password"))?;
    if let Some(selection) = selection {
        mdata_login::selected_account_ids(selection).map_err(|e| ExportError(e.code()))?;
    }
    let profile = profile_path(profile)?;
    let destination = destination_path(&profile, file)?;
    check(cancel)?;
    let config = SafeStorePath::new(profile.join("config.json")).map_err(store)?;
    let _profile_pin = config
        .begin(false)
        .map_err(store)?
        .ok_or(ExportError("invalid_profile"))?;
    mdata_recovery_state::ensure_clear(&profile).map_err(|_| ExportError("recovery_required"))?;
    let safe = SafeStorePath::new(&destination).map_err(store)?;
    let output = safe
        .begin(false)
        .map_err(store)?
        .ok_or(ExportError("unsafe_path"))?;
    let _writer = output.writer_lock().map_err(store)?;
    new_output(&output)?;
    // Capacity is checked before listing/capture can read any credentials.
    space(
        capacity,
        &profile,
        destination.parent().unwrap(),
        MAX_ARCHIVE_BYTES as u64,
    )?;
    for retry in 0..3 {
        match attempt(
            &profile,
            &output,
            password,
            selection,
            credentials,
            cancel,
            capacity,
        ) {
            Err(error)
                if retry < 2
                    && matches!(error.0, "profile_changed" | "source_changed" | "busy") =>
            {
                check(cancel)?;
                std::thread::sleep(std::time::Duration::from_millis(50));
            }
            result => return result,
        }
    }
    unreachable!()
}

#[cfg(all(test, windows))]
#[path = "mdata_export_tests.rs"]
mod tests;
