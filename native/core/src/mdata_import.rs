//! Host-only compact login import. Never a Runtime/QML route or restart API.
//! The native transfer owner supplies a bounded closed-client check on this
//! worker thread. A UI boolean is not an implementation of that contract.
//!
//! The shared transfer lease covers waiting, authenticated input, profile
//! preparation and the complete token/file commit or rollback. Cancellation is
//! checked between stages, never halfway through the token transaction. Windows
//! I/O and scrypt are not interruptible here and have no hard latency promise.
//! A newly prepared valid config may remain after a later error. No failure
//! result claims that the profile is unchanged, and no client is auto-started.
use crate::{
    launcher_config, launcher_profile,
    login_credentials::LoginCredentials,
    mdata_archive, mdata_import_archive,
    mdata_login_import::{self, ImportResult},
    mdata_login_storage::LoginStorage,
    mdata_recovery_state,
    selected_path::selected_file_url,
    session_archive,
    settings_io::{GuardedStore, SafeStorePath, StoreError},
    transfer_lock::{LockError, TransferLease},
};
use std::{fmt, path::Path};

pub const RESERVE_BYTES: u64 = 256 * 1024 * 1024;
const FILE_OVERHEAD: u64 = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HostError {
    ClientStillRunning,
    ProcessScanFailed,
    Cancelled,
}
impl HostError {
    pub fn code(self) -> &'static str {
        match self {
            Self::ClientStillRunning => "client_still_running",
            Self::ProcessScanFailed => "process_scan_failed",
            Self::Cancelled => "cancelled",
        }
    }
}
/// Trusted same-thread native callbacks. Implementations must not reenter this
/// service, destroy their own context or infer closure from the UI's state.
/// wait_closed owns its bounded wait policy; check_closed performs a fresh scan.
pub trait ImportHost {
    fn wait_closed(&mut self) -> std::result::Result<(), HostError>;
    fn check_closed(&mut self) -> std::result::Result<(), HostError>;
    fn cancelled(&mut self) -> bool;
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct ImportError(&'static str);
impl ImportError {
    pub fn code(self) -> &'static str {
        self.0
    }
}
impl fmt::Display for ImportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.0)
    }
}
impl std::error::Error for ImportError {}
pub type Result<T> = std::result::Result<T, ImportError>;

fn store(error: StoreError) -> ImportError {
    ImportError(match error {
        StoreError::UnsafePath => "unsafe_path",
        StoreError::StoreBusy => "busy",
        _ => "io_error",
    })
}
fn cancel(host: &mut dyn ImportHost) -> Result<()> {
    if host.cancelled() {
        Err(ImportError("cancelled"))
    } else {
        Ok(())
    }
}
fn closed(host: &mut dyn ImportHost) -> Result<()> {
    host.check_closed().map_err(|e| ImportError(e.code()))?;
    cancel(host)
}

// The production call always supplies TransferLease::acquire(). The private
// generic seam lets tests prove admission/guard lifetime with an owned fake,
// without sharing the user's named transfer mutex with parallel test cases.
fn with_lease<L>(
    lease: std::result::Result<L, LockError>,
    work: impl FnOnce() -> Result<ImportResult>,
) -> Result<ImportResult> {
    let _lease = lease.map_err(|e| ImportError(e.code()))?;
    work()
}

/// Import only an explicitly confirmed compact v2 archive. The selected file
/// must be outside the target MXMOD profile. Local must already exist; MXMOD
/// may be absent. No environment/profile discovery or real credential adapter
/// is constructed by this function. Legacy full-profile restore is unsupported.
pub fn execute(
    explicit_local: &Path,
    file: &str,
    password: &str,
    fingerprint: &str,
    credentials: &dyn LoginCredentials,
    host: &mut dyn ImportHost,
) -> Result<ImportResult> {
    with_lease(TransferLease::acquire(), || {
        execute_held(
            explicit_local,
            file,
            password,
            fingerprint,
            credentials,
            host,
            &DiskCapacity,
            |local| {
                launcher_profile::prepare(local, false)
                    .map(|_| ())
                    .map_err(|e| ImportError(e.code()))
            },
        )
    })
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
                return Err(ImportError("io_error"));
            }
            Ok(available)
        }
        #[cfg(not(windows))]
        {
            let _ = path;
            Err(ImportError("io_error"))
        }
    }
}

fn scalar_request(password: &str, fingerprint: &str) -> Result<()> {
    session_archive::password_bytes(password).map_err(|e| ImportError(e.code()))?;
    if fingerprint.len() != 64
        || !fingerprint
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(ImportError("invalid_request"));
    }
    Ok(())
}

fn local_guard(local: &Path) -> Result<(std::path::PathBuf, GuardedStore)> {
    let config = launcher_config::config_path(local).map_err(|_| ImportError("unsafe_path"))?;
    let profile = config.parent().ok_or(ImportError("unsafe_path"))?;
    let local = profile.parent().ok_or(ImportError("unsafe_path"))?;
    // Pins every existing ancestor and Local itself without reading/creating
    // the marker. A missing Local is never created by profile preparation.
    let pinned = SafeStorePath::new(local.join(mdata_recovery_state::FILENAME))
        .map_err(store)?
        .begin(false)
        .map_err(store)?
        .ok_or(ImportError("invalid_profile"))?;
    Ok((profile.to_owned(), pinned))
}
fn selected_source(profile: &Path, file: &str) -> Result<()> {
    let path = selected_file_url(file).map_err(|_| ImportError("unsafe_path"))?;
    if path
        .components()
        .any(|part| part.as_os_str().eq_ignore_ascii_case("ONEME"))
    {
        return Err(ImportError("unsafe_path"));
    }
    if !path
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("mdata"))
    {
        return Err(ImportError("invalid_archive"));
    }
    let path = SafeStorePath::new(path).map_err(store)?;
    let base = mdata_archive::path_comparison_key(&profile.to_string_lossy());
    let selected = mdata_archive::path_comparison_key(&path.path().to_string_lossy());
    if selected == base || selected.starts_with(&(base + "\\")) {
        return Err(ImportError("unsafe_path"));
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn execute_held(
    explicit_local: &Path,
    file: &str,
    password: &str,
    fingerprint: &str,
    credentials: &dyn LoginCredentials,
    host: &mut dyn ImportHost,
    capacity: &dyn Capacity,
    prepare: impl FnOnce(&Path) -> Result<()>,
) -> Result<ImportResult> {
    scalar_request(password, fingerprint)?;
    let (profile, _local_pin) = local_guard(explicit_local)?;
    selected_source(&profile, file)?;
    // If MXMOD exists, keep its identity stable through preparation and apply.
    // If absent, Local remains pinned and launcher_profile creates only MXMOD.
    let _profile_pin = SafeStorePath::new(profile.join("config.json"))
        .map_err(store)?
        .begin(false)
        .map_err(store)?;
    cancel(host)?;
    host.wait_closed().map_err(|e| ImportError(e.code()))?;
    cancel(host)?;
    mdata_recovery_state::ensure_clear(&profile).map_err(|e| ImportError(e.code()))?;
    let decoded = mdata_import_archive::load(file, password, fingerprint)
        .map_err(|e| ImportError(e.code()))?;
    cancel(host)?;
    // This is headroom, not a reservation against unrelated concurrent writers.
    // Include bounded auth payloads, file overhead and a fresh config's cap.
    let required = decoded
        .auth
        .values()
        .try_fold(
            RESERVE_BYTES + launcher_config::MAX_CONFIG_BYTES as u64,
            |total, bytes| total.checked_add(bytes.len() as u64 + FILE_OVERHEAD),
        )
        .ok_or(ImportError("too_large"))?;
    let local = profile.parent().ok_or(ImportError("unsafe_path"))?;
    if capacity.available(local)? < required {
        return Err(ImportError("insufficient_space"));
    }
    closed(host)?;
    prepare(local)?;
    let mut storage = LoginStorage::open(&profile).map_err(|e| ImportError(e.code()))?;
    mdata_recovery_state::ensure_clear(&profile).map_err(|e| ImportError(e.code()))?;
    closed(host)?;
    // From this point, cancellation must not interrupt commit/rollback or turn
    // a successfully committed import into an ambiguous cancellation response.
    mdata_login_import::apply(
        decoded.manifest.value(),
        &decoded.tokens,
        &decoded.auth,
        credentials,
        &mut storage,
    )
    .map_err(|e| ImportError(e.code()))
}

#[cfg(all(test, windows))]
#[path = "mdata_import_tests.rs"]
mod tests;
