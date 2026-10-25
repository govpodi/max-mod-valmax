//! Prepare only an explicitly supplied MXMOD configuration, with no profile
//! discovery, credentials, application launch or recovery-file mutation.
//! The launcher must retain its outer transfer guard through client startup;
//! this inner recursive acquisition protects this operation's own filesystem IO.
use crate::{
    launcher_config,
    settings_io::{GuardedStore, SafeStorePath, StoreError},
};
use std::{
    fmt,
    path::{Path, PathBuf},
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    ProfileGuardFailed,
    TransferBusy,
    RecoveryRequired,
}
impl Error {
    pub fn code(self) -> &'static str {
        match self {
            Self::ProfileGuardFailed => "profile_guard_failed",
            Self::TransferBusy => "transfer_busy",
            Self::RecoveryRequired => "recovery_required",
        }
    }
}
impl fmt::Display for Error {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.write_str(self.code())
    }
}
impl std::error::Error for Error {}
type Result<T> = std::result::Result<T, Error>;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConfigState {
    Absent,
    Existing,
    Created,
}
pub struct PreparedConfig {
    pub path: PathBuf,
    pub state: ConfigState,
}
const TRANSFER_MUTEX: &str = "Local\\MAXModSessionTransferV1";

/// Check-only never creates a directory/file and does not authorize a launch.
/// Existing bytes are validated, never normalized or replaced. A recovery
/// marker of any content/type prevents a mutating preparation.
pub fn prepare(local_appdata: &Path, check_only: bool) -> Result<PreparedConfig> {
    #[cfg(windows)]
    {
        let lease = win::Lease::acquire(TRANSFER_MUTEX)?;
        prepare_held(local_appdata, check_only, &lease)
    }
    #[cfg(not(windows))]
    {
        let _ = (local_appdata, check_only);
        Err(Error::ProfileGuardFailed)
    }
}

#[cfg(windows)]
fn prepare_held(local: &Path, check_only: bool, _lease: &win::Lease) -> Result<PreparedConfig> {
    let fresh = launcher_config::make_config().map_err(|_| Error::ProfileGuardFailed)?;
    let path = launcher_config::config_path(local).map_err(|_| Error::ProfileGuardFailed)?;
    let profile = path.parent().ok_or(Error::ProfileGuardFailed)?;
    let local = profile.parent().ok_or(Error::ProfileGuardFailed)?;
    // Begin on the marker path pins and validates the existing LocalAppData
    // directory itself; creating missing arbitrary ancestors is not allowed.
    let marker = SafeStorePath::new(local.join("MXMOD-MData-Recovery.json"))
        .map_err(|_| Error::ProfileGuardFailed)?
        .begin(false)
        .map_err(|_| Error::ProfileGuardFailed)?
        .ok_or(Error::ProfileGuardFailed)?;
    if !check_only {
        ensure_no_recovery(&marker)?;
    }
    let candidate = SafeStorePath::new(&path).map_err(|_| Error::ProfileGuardFailed)?;
    if let Some(guard) = candidate
        .begin(false)
        .map_err(|_| Error::ProfileGuardFailed)?
    {
        if existing(&guard)? {
            return Ok(PreparedConfig {
                path,
                state: ConfigState::Existing,
            });
        }
    }
    if check_only {
        return Ok(PreparedConfig {
            path,
            state: ConfigState::Absent,
        });
    }
    // Serialize before creating the one missing MXMOD directory.
    let mut bytes = serde_json::to_vec_pretty(&fresh).map_err(|_| Error::ProfileGuardFailed)?;
    bytes.push(b'\n');
    let guard = candidate
        .begin(true)
        .map_err(|_| Error::ProfileGuardFailed)?
        .ok_or(Error::ProfileGuardFailed)?;
    ensure_no_recovery(&marker)?;
    match guard.atomic_create(&bytes) {
        Ok(()) => Ok(PreparedConfig {
            path,
            state: ConfigState::Created,
        }),
        Err(StoreError::FileExists) => {
            // A non-cooperating creator won. Read and validate its file; never
            // overwrite it with our template or silently repair unsafe flags.
            if existing(&guard)? {
                Ok(PreparedConfig {
                    path,
                    state: ConfigState::Existing,
                })
            } else {
                Err(Error::ProfileGuardFailed)
            }
        }
        Err(_) => Err(Error::ProfileGuardFailed),
    }
}
fn existing(guard: &GuardedStore) -> Result<bool> {
    match guard
        .read_bounded(64 * 1024)
        .map_err(|_| Error::ProfileGuardFailed)?
    {
        Some(bytes) => {
            launcher_config::validate_config(&bytes).map_err(|_| Error::ProfileGuardFailed)?;
            Ok(true)
        }
        None => Ok(false),
    }
}
fn ensure_no_recovery(marker: &GuardedStore) -> Result<()> {
    // Even a zero-byte or malformed existing marker must preserve recovery.
    match marker.read_bounded(0) {
        Ok(None) => Ok(()),
        _ => Err(Error::RecoveryRequired),
    }
}

#[cfg(windows)]
mod win {
    use super::*;
    use std::{ffi::c_void, marker::PhantomData, ptr, rc::Rc};
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateMutexW(security: *const c_void, owned: i32, name: *const u16) -> *mut c_void;
        fn WaitForSingleObject(handle: *mut c_void, milliseconds: u32) -> u32;
        fn ReleaseMutex(handle: *mut c_void) -> i32;
        fn CloseHandle(handle: *mut c_void) -> i32;
    }
    // Rc marker makes a lease neither Send nor Sync: Windows mutex ownership
    // belongs to an OS thread, including recursive acquisitions.
    pub(super) struct Lease {
        handle: *mut c_void,
        _thread: PhantomData<Rc<()>>,
    }
    impl Lease {
        pub(super) fn acquire(name: &str) -> Result<Self> {
            let wide: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
            let handle = unsafe { CreateMutexW(ptr::null(), 0, wide.as_ptr()) };
            if handle.is_null() {
                return Err(Error::ProfileGuardFailed);
            }
            match unsafe { WaitForSingleObject(handle, 0) } {
                // An abandoned mutex never bypasses ensure_no_recovery.
                0 | 0x80 => Ok(Self {
                    handle,
                    _thread: PhantomData,
                }),
                value => {
                    unsafe { CloseHandle(handle) };
                    Err(if value == 0x102 {
                        Error::TransferBusy
                    } else {
                        Error::ProfileGuardFailed
                    })
                }
            }
        }
        #[cfg(test)]
        pub(super) fn abandon_on_test_thread(self) -> usize {
            let handle = self.handle as usize;
            std::mem::forget(self);
            handle
        }
    }
    #[cfg(test)]
    pub(super) fn close_abandoned_test_handle(handle: usize) {
        assert_ne!(unsafe { CloseHandle(handle as *mut c_void) }, 0);
    }
    impl Drop for Lease {
        fn drop(&mut self) {
            unsafe {
                ReleaseMutex(self.handle);
                CloseHandle(self.handle);
            }
        }
    }
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use serde_json::json;
    use std::{fs, sync::mpsc};
    struct Fixture {
        path: PathBuf,
        mutex: String,
    }
    impl Fixture {
        fn new() -> Self {
            let mut nonce = [0u8; 16];
            getrandom::getrandom(&mut nonce).unwrap();
            let id: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
            let path = PathBuf::from("C:/ValMAXBuild/fixtures/launcher-profile").join(&id);
            fs::create_dir_all(&path).unwrap();
            Self {
                path,
                mutex: format!("Local\\ValMAXNativeTransferTestV1-{id}"),
            }
        }
        fn prepare(&self, check_only: bool) -> Result<PreparedConfig> {
            let lease = win::Lease::acquire(&self.mutex)?;
            prepare_held(&self.path, check_only, &lease)
        }
        fn config(&self) -> PathBuf {
            self.path.join("MXMOD/config.json")
        }
        fn marker(&self) -> PathBuf {
            self.path.join("MXMOD-MData-Recovery.json")
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let base = fs::canonicalize("C:/ValMAXBuild/fixtures/launcher-profile").unwrap();
            let path = fs::canonicalize(&self.path).unwrap();
            assert!(path.starts_with(&base) && path != base);
            fs::remove_dir_all(path).unwrap();
        }
    }
    #[test]
    fn missing_check_is_read_only_and_first_write_creates_full_config() {
        let f = Fixture::new();
        assert_eq!(f.prepare(true).unwrap().state, ConfigState::Absent);
        assert_eq!(fs::read_dir(&f.path).unwrap().count(), 0);
        let result = f.prepare(false).unwrap();
        assert_eq!(result.state, ConfigState::Created);
        assert_eq!(result.path, f.config());
        let before = fs::read(f.config()).unwrap();
        assert_eq!(
            launcher_config::validate_config(&before).unwrap(),
            launcher_config::make_config().unwrap()
        );
        assert_eq!(f.prepare(false).unwrap().state, ConfigState::Existing);
        assert_eq!(fs::read(f.config()).unwrap(), before);
        assert_eq!(
            fs::read_dir(f.config().parent().unwrap()).unwrap().count(),
            1
        );
    }
    #[test]
    fn existing_unknown_fields_and_original_whitespace_are_preserved() {
        let f = Fixture::new();
        fs::create_dir(f.path.join("MXMOD")).unwrap();
        let mut config = launcher_config::make_config().unwrap();
        config["synthetic_extra"] = json!({"unicode":"тест"});
        let mut bytes = vec![b' ', b'\n'];
        bytes.extend(serde_json::to_vec_pretty(&config).unwrap());
        bytes.extend(b" \n");
        fs::write(f.config(), &bytes).unwrap();
        for mode in [true, false] {
            assert_eq!(f.prepare(mode).unwrap().state, ConfigState::Existing);
            assert_eq!(fs::read(f.config()).unwrap(), bytes);
        }
    }
    #[test]
    fn malformed_or_unsafe_existing_config_is_never_repaired() {
        let f = Fixture::new();
        fs::create_dir(f.path.join("MXMOD")).unwrap();
        let mut wrong = launcher_config::make_config().unwrap();
        wrong["app_folder_name"] = json!("ONEME");
        for bytes in [
            b"{bad".to_vec(),
            serde_json::to_vec(&wrong).unwrap(),
            vec![b'x'; 65537],
        ] {
            fs::write(f.config(), &bytes).unwrap();
            assert_eq!(f.prepare(false).err(), Some(Error::ProfileGuardFailed));
            assert_eq!(fs::read(f.config()).unwrap(), bytes);
        }
    }
    #[test]
    fn any_recovery_marker_prevents_mutation_including_abandoned_lock() {
        let f = Fixture::new();
        for bytes in [b"".as_slice(), b"{malformed".as_slice()] {
            fs::write(f.marker(), bytes).unwrap();
            assert_eq!(f.prepare(false).err(), Some(Error::RecoveryRequired));
            assert!(!f.config().exists());
            assert_eq!(fs::read(f.marker()).unwrap(), bytes);
            assert_eq!(f.prepare(true).unwrap().state, ConfigState::Absent);
        }
        let name = f.mutex.clone();
        let abandoned = std::thread::spawn(move || {
            win::Lease::acquire(&name).unwrap().abandon_on_test_thread()
        })
        .join()
        .unwrap();
        let result = f.prepare(false).err();
        win::close_abandoned_test_handle(abandoned);
        assert_eq!(result, Some(Error::RecoveryRequired));
        assert!(!f.config().exists());
    }
    #[test]
    fn unsafe_or_busy_recovery_marker_is_preserved_without_config_creation() {
        use std::os::windows::fs::OpenOptionsExt;
        let f = Fixture::new();
        fs::create_dir(f.marker()).unwrap();
        assert_eq!(f.prepare(false).err(), Some(Error::RecoveryRequired));
        assert!(!f.path.join("MXMOD").exists());
        assert!(f.marker().is_dir());
        fs::remove_dir(f.marker()).unwrap();
        let other = f.path.join("synthetic-marker.json");
        fs::write(&other, b"synthetic recovery marker").unwrap();
        fs::hard_link(&other, f.marker()).unwrap();
        assert_eq!(f.prepare(false).err(), Some(Error::RecoveryRequired));
        assert!(!f.path.join("MXMOD").exists());
        assert_eq!(fs::read(&other).unwrap(), b"synthetic recovery marker");
        fs::remove_file(f.marker()).unwrap();
        let held = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .share_mode(0)
            .open(f.marker())
            .unwrap();
        assert_eq!(f.prepare(false).err(), Some(Error::RecoveryRequired));
        assert!(!f.path.join("MXMOD").exists());
        drop(held);
        assert_eq!(fs::read(f.marker()).unwrap(), b"");
    }
    #[test]
    fn hardlink_and_directory_configs_and_missing_local_root_rejected() {
        let f = Fixture::new();
        fs::create_dir(f.path.join("MXMOD")).unwrap();
        let foreign = f.path.join("synthetic-original.json");
        let bytes = serde_json::to_vec(&launcher_config::make_config().unwrap()).unwrap();
        fs::write(&foreign, &bytes).unwrap();
        fs::hard_link(&foreign, f.config()).unwrap();
        assert_eq!(f.prepare(false).err(), Some(Error::ProfileGuardFailed));
        assert_eq!(fs::read(&foreign).unwrap(), bytes);
        fs::remove_file(f.config()).unwrap();
        fs::create_dir(f.config()).unwrap();
        assert_eq!(f.prepare(false).err(), Some(Error::ProfileGuardFailed));
        let lease = win::Lease::acquire(&f.mutex).unwrap();
        let missing = f.path.join("missing");
        assert_eq!(
            prepare_held(&missing, false, &lease).err(),
            Some(Error::ProfileGuardFailed)
        );
        assert!(!missing.exists());
    }
    #[test]
    fn recursive_same_thread_and_cross_thread_contention_are_bounded() {
        let f = Fixture::new();
        let outer = win::Lease::acquire(&f.mutex).unwrap();
        let inner = win::Lease::acquire(&f.mutex).unwrap();
        drop(inner);
        let name = f.mutex.clone();
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || tx.send(win::Lease::acquire(&name).err()).unwrap())
            .join()
            .unwrap();
        assert_eq!(rx.recv().unwrap(), Some(Error::TransferBusy));
        drop(outer);
        assert!(win::Lease::acquire(&f.mutex).is_ok());
    }
}
