//! Service ordering with fake host/credentials and explicitly owned files only.
//! execute_held substitutes config preparation to avoid the shared production
//! mutex; launcher_profile's own file tests cover its actual preparation path.
use super::*;
use crate::{
    login_credentials::{CredentialError, Result as CredentialResult},
    mdata_archive::{ArchiveKind, MDataError, MDataPolicy},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    fs,
    io::{Cursor, Read},
    path::PathBuf,
    rc::Rc,
    sync::OnceLock,
};
use zeroize::Zeroizing;

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-import-service";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("mdata-import-service"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}
const PASSWORD: &str = "synthetic-service-import-password";
const TOKEN: &[u8] = b"synthetic service import token";
const AUTH: &[u8] = b"opaque synthetic auth\0\xff";
type Trace = Rc<RefCell<Vec<&'static str>>>;

struct Fixture {
    root: PathBuf,
    local: PathBuf,
    profile: PathBuf,
    source: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let name: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let root = fixture_base().join(name);
        let local = root.join("Local");
        let selected = root.join("selected");
        fs::create_dir_all(&local).unwrap();
        fs::create_dir(&selected).unwrap();
        let source = selected.join("chosen.mdata");
        fs::write(&source, archive()).unwrap();
        Self {
            profile: local.join("MXMOD"),
            root,
            local,
            source,
        }
    }
    fn url(&self) -> String {
        file_url(&self.source)
    }
    fn config(&self) -> PathBuf {
        self.profile.join("config.json")
    }
    fn marker(&self) -> PathBuf {
        self.local.join(mdata_recovery_state::FILENAME)
    }
    fn prepare(&self, local: &Path, trace: &Trace) -> Result<()> {
        assert_eq!(local, self.local);
        trace.borrow_mut().push("prepare");
        let path = SafeStorePath::new(self.config()).unwrap();
        let held = path.begin(true).unwrap().unwrap();
        if let Some(bytes) = held
            .read_bounded(launcher_config::MAX_CONFIG_BYTES)
            .unwrap()
        {
            launcher_config::validate_config(&bytes).unwrap();
        } else {
            let bytes = serde_json::to_vec(&launcher_config::make_config().unwrap()).unwrap();
            held.atomic_create(&bytes).unwrap();
        }
        Ok(())
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(fixture_base()).unwrap();
        let own = fs::canonicalize(&self.root).unwrap();
        let name = self.root.file_name().unwrap().to_str().unwrap();
        assert!(own.starts_with(&base) && own != base && name.len() == 32);
        assert!(name.bytes().all(|b| b.is_ascii_hexdigit()));
        fs::remove_dir_all(own).unwrap();
    }
}
fn file_url(path: &Path) -> String {
    let mut url = String::from("file:///");
    for byte in path.to_str().unwrap().replace('\\', "/").bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~/:".contains(&byte) {
            url.push(byte as char);
        } else {
            url.push_str(&format!("%{byte:02X}"));
        }
    }
    url
}
fn archive() -> &'static [u8] {
    static BYTES: OnceLock<Vec<u8>> = OnceLock::new();
    BYTES.get_or_init(|| {
        let manifest = json!({"schema":1,"sourceClient":session_archive::SOURCE_CLIENT,
            "sourceVersion":session_archive::SOURCE_VERSION,
            "credentials":[{"name":"access_token_42@MXMOD","blob":STANDARD.encode(TOKEN)},
                {"name":"access_token_43@MXMOD","blob":STANDARD.encode(TOKEN)}],
            "files":[{"path":"auth_42","size":AUTH.len()},{"path":"auth_43","size":AUTH.len()}]});
        let credential_policy = session_archive::ArchivePolicy::default();
        let file_policy = |path: &str| crate::mdata_login::auth_id(path).is_some();
        let token_policy = |name: &str| crate::mdata_login::token_id(name).is_some();
        let policy = MDataPolicy {
            credential_policy: &credential_policy,
            file_path_validator: &file_policy,
            credential_name_validator: Some(&token_policy),
        };
        let mut source = |_: &str| -> std::result::Result<Box<dyn Read>, MDataError> {
            Ok(Box::new(Cursor::new(AUTH)))
        };
        let mut bytes = Vec::new();
        mdata_archive::encrypt_stream(
            &manifest,
            PASSWORD,
            &mut source,
            &mut bytes,
            &policy,
            ArchiveKind::Login,
        )
        .unwrap();
        bytes
    })
}
fn fingerprint() -> String {
    format!("{:x}", Sha256::digest(archive()))
}

struct FakeHost {
    trace: Trace,
    cancelled: Rc<Cell<bool>>,
    polls: usize,
    cancel_at: Option<usize>,
    wait_error: Option<HostError>,
    check_error: Option<(usize, HostError)>,
    checks: usize,
}
impl FakeHost {
    fn new(trace: &Trace, cancelled: &Rc<Cell<bool>>) -> Self {
        Self {
            trace: trace.clone(),
            cancelled: cancelled.clone(),
            polls: 0,
            cancel_at: None,
            wait_error: None,
            check_error: None,
            checks: 0,
        }
    }
}
impl ImportHost for FakeHost {
    fn wait_closed(&mut self) -> std::result::Result<(), HostError> {
        self.trace.borrow_mut().push("wait");
        self.wait_error.map_or(Ok(()), Err)
    }
    fn check_closed(&mut self) -> std::result::Result<(), HostError> {
        self.checks += 1;
        self.trace.borrow_mut().push("closed");
        if let Some((at, error)) = self.check_error {
            if at == self.checks {
                return Err(error);
            }
        }
        Ok(())
    }
    fn cancelled(&mut self) -> bool {
        self.polls += 1;
        if self.cancel_at == Some(self.polls) {
            self.cancelled.set(true);
        }
        self.cancelled.get()
    }
}
struct FakeCredentials {
    trace: Trace,
    data: RefCell<BTreeMap<String, Vec<u8>>>,
    cancel_on_set: Option<Rc<Cell<bool>>>,
    fail_second: bool,
}
impl FakeCredentials {
    fn new(trace: &Trace) -> Self {
        Self {
            trace: trace.clone(),
            data: RefCell::new(BTreeMap::new()),
            cancel_on_set: None,
            fail_second: false,
        }
    }
}
impl LoginCredentials for FakeCredentials {
    fn owned_token_names(&self) -> CredentialResult<Vec<String>> {
        self.trace.borrow_mut().push("inventory");
        Ok(self.data.borrow().keys().cloned().collect())
    }
    fn read(&self, name: &str) -> CredentialResult<Option<Zeroizing<Vec<u8>>>> {
        assert!(crate::mdata_login::token_id(name).is_some());
        self.trace.borrow_mut().push("read");
        Ok(self
            .data
            .borrow()
            .get(name)
            .map(|v| Zeroizing::new(v.clone())))
    }
    fn set_owned(&self, name: &str, blob: &[u8], expected: Option<&[u8]>) -> CredentialResult<()> {
        self.trace.borrow_mut().push("set");
        let mut data = self.data.borrow_mut();
        if data.get(name).map(Vec::as_slice) != expected {
            return Err(CredentialError::DestinationNotEmpty);
        }
        if self.fail_second && name == "access_token_43@MXMOD" {
            return Err(CredentialError::CredentialIo);
        }
        data.insert(name.into(), blob.to_vec());
        if let Some(cancelled) = &self.cancel_on_set {
            cancelled.set(true);
        }
        Ok(())
    }
    fn delete_owned(&self, name: &str, expected: &[u8]) -> CredentialResult<()> {
        self.trace.borrow_mut().push("delete");
        let mut data = self.data.borrow_mut();
        if data.get(name).is_some_and(|v| v.as_slice() != expected) {
            return Err(CredentialError::DestinationNotEmpty);
        }
        data.remove(name);
        Ok(())
    }
}
struct FakeCapacity {
    trace: Trace,
    free: u64,
}
impl Capacity for FakeCapacity {
    fn available(&self, pinned_directory: &Path) -> Result<u64> {
        assert!(pinned_directory.ends_with("Local"));
        assert!(pinned_directory.is_dir());
        self.trace.borrow_mut().push("space");
        Ok(self.free)
    }
}
fn setup() -> (
    Trace,
    Rc<Cell<bool>>,
    FakeHost,
    FakeCredentials,
    FakeCapacity,
) {
    let trace = Rc::new(RefCell::new(Vec::new()));
    let cancelled = Rc::new(Cell::new(false));
    let host = FakeHost::new(&trace, &cancelled);
    let credentials = FakeCredentials::new(&trace);
    let capacity = FakeCapacity {
        trace: trace.clone(),
        free: u64::MAX,
    };
    (trace, cancelled, host, credentials, capacity)
}
fn run(
    f: &Fixture,
    host: &mut FakeHost,
    credentials: &FakeCredentials,
    capacity: &FakeCapacity,
) -> Result<ImportResult> {
    execute_held(
        &f.local,
        &f.url(),
        PASSWORD,
        &fingerprint(),
        credentials,
        host,
        capacity,
        |local| f.prepare(local, &credentials.trace),
    )
}
fn error(result: Result<ImportResult>, wanted: &str) {
    assert_eq!(result.unwrap_err().code(), wanted);
}
fn no_credentials(trace: &Trace) {
    assert!(
        !trace
            .borrow()
            .iter()
            .any(|v| matches!(*v, "inventory" | "read" | "set" | "delete"))
    );
}

#[test]
fn authenticated_import_prepares_then_rechecks_and_finishes_commit_despite_late_cancel() {
    let f = Fixture::new();
    let (trace, cancelled, mut host, mut credentials, capacity) = setup();
    credentials.cancel_on_set = Some(cancelled.clone());
    let result = run(&f, &mut host, &credentials, &capacity).unwrap();
    assert_eq!(result.result, "added");
    assert_eq!(result.profiles, 2);
    assert_eq!(
        &*trace.borrow(),
        &[
            "wait",
            "space",
            "closed",
            "prepare",
            "closed",
            "inventory",
            "set",
            "set"
        ]
    );
    assert_eq!(host.polls, 5);
    assert!(cancelled.get());
    assert_eq!(credentials.data.borrow().len(), 2);
    assert_eq!(fs::read(f.profile.join("auth_42")).unwrap(), AUTH);
    assert_eq!(fs::read(f.profile.join("auth_43")).unwrap(), AUTH);
    let config = fs::read(f.config()).unwrap();
    launcher_config::validate_config(&config).unwrap();
    cancelled.set(false);
    trace.borrow_mut().clear();
    let again = run(&f, &mut host, &credentials, &capacity).unwrap();
    assert_eq!(again.result, "already_present");
    assert_eq!(again.profiles, 0);
    assert!(!trace.borrow().contains(&"set"));
    assert_eq!(fs::read(f.config()).unwrap(), config);
}

#[test]
fn scalar_and_path_validation_precede_host_credentials_and_profile_writes() {
    let f = Fixture::new();
    let (trace, _, mut host, credentials, capacity) = setup();
    for (file, password, hash, expected) in [
        (f.url(), "short", fingerprint(), "invalid_password"),
        (f.url(), PASSWORD, "f".repeat(63), "invalid_request"),
        (
            "https://example.invalid/input.mdata".into(),
            PASSWORD,
            fingerprint(),
            "unsafe_path",
        ),
        (
            file_url(&f.local.join("ONEME/input.mdata")),
            PASSWORD,
            fingerprint(),
            "unsafe_path",
        ),
        (
            file_url(&f.profile.join("input.mdata")),
            PASSWORD,
            fingerprint(),
            "unsafe_path",
        ),
        (
            file_url(&f.local.join("mxmod/nested/input.mdata")),
            PASSWORD,
            fingerprint(),
            "unsafe_path",
        ),
        (
            file_url(&f.root.join("input.zip")),
            PASSWORD,
            fingerprint(),
            "invalid_archive",
        ),
    ] {
        error(
            execute_held(
                &f.local,
                &file,
                &password,
                &hash,
                &credentials,
                &mut host,
                &capacity,
                |_| panic!("invalid request reached preparation"),
            ),
            expected,
        );
    }
    error(
        execute_held(
            &f.root.join("missing/Local"),
            &f.url(),
            PASSWORD,
            &fingerprint(),
            &credentials,
            &mut host,
            &capacity,
            |_| panic!("missing Local was created"),
        ),
        "invalid_profile",
    );
    assert!(trace.borrow().is_empty());
    assert!(!f.profile.exists());
    assert!(!f.root.join("missing").exists());
    // A separately selected archive directory named MXMOD is not the target.
    let other = f.root.join("archived/MXMOD");
    fs::create_dir_all(&other).unwrap();
    fs::write(other.join("chosen.mdata"), archive()).unwrap();
    assert!(selected_source(&f.profile, &file_url(&other.join("chosen.mdata"))).is_ok());
}

#[test]
fn lease_busy_wait_failure_and_early_cancellation_never_reach_credentials() {
    let f = Fixture::new();
    let (trace, cancelled, mut host, credentials, capacity) = setup();
    error(
        with_lease::<()>(Err(LockError::Busy), || panic!("busy admission ran work")),
        "busy",
    );
    struct Lease(Rc<Cell<bool>>);
    impl Drop for Lease {
        fn drop(&mut self) {
            self.0.set(true);
        }
    }
    let released = Rc::new(Cell::new(false));
    let result = with_lease(Ok(Lease(released.clone())), || {
        assert!(!released.get());
        Err(ImportError("synthetic"))
    });
    error(result, "synthetic");
    assert!(released.get());
    for failure in [
        HostError::ClientStillRunning,
        HostError::ProcessScanFailed,
        HostError::Cancelled,
    ] {
        host.wait_error = Some(failure);
        error(run(&f, &mut host, &credentials, &capacity), failure.code());
    }
    assert_eq!(&*trace.borrow(), &["wait", "wait", "wait"]);
    trace.borrow_mut().clear();
    cancelled.set(true);
    error(run(&f, &mut host, &credentials, &capacity), "cancelled");
    assert!(trace.borrow().is_empty());
    assert!(!f.profile.exists());
}

#[test]
fn recovery_authentication_source_and_capacity_fail_before_any_credential_access() {
    let f = Fixture::new();
    let (trace, _, mut host, credentials, mut capacity) = setup();
    fs::write(f.marker(), b"malformed synthetic recovery marker").unwrap();
    error(
        run(&f, &mut host, &credentials, &capacity),
        "recovery_required",
    );
    assert_eq!(&*trace.borrow(), &["wait"]);
    fs::remove_file(f.marker()).unwrap();
    for (password, hash, expected) in [
        (PASSWORD, "0".repeat(64), "source_changed"),
        (
            "wrong-synthetic-service-password",
            fingerprint(),
            "authentication_failed",
        ),
    ] {
        trace.borrow_mut().clear();
        error(
            execute_held(
                &f.local,
                &f.url(),
                password,
                &hash,
                &credentials,
                &mut host,
                &capacity,
                |_| panic!("unauthenticated archive reached preparation"),
            ),
            expected,
        );
        assert_eq!(&*trace.borrow(), &["wait"]);
    }
    trace.borrow_mut().clear();
    capacity.free = RESERVE_BYTES - 1;
    error(
        run(&f, &mut host, &credentials, &capacity),
        "insufficient_space",
    );
    assert_eq!(&*trace.borrow(), &["wait", "space"]);
    no_credentials(&trace);
    assert!(!f.profile.exists());
}

#[test]
fn fresh_closed_cancel_and_recovery_checks_reject_before_apply_but_may_leave_valid_config() {
    for poll in [4, 5] {
        let f = Fixture::new();
        let (trace, _, mut host, credentials, capacity) = setup();
        host.cancel_at = Some(poll);
        error(run(&f, &mut host, &credentials, &capacity), "cancelled");
        no_credentials(&trace);
        assert_eq!(f.config().exists(), poll == 5);
        if poll == 5 {
            launcher_config::validate_config(&fs::read(f.config()).unwrap()).unwrap();
        }
    }
    for check in [1, 2] {
        let f = Fixture::new();
        let (trace, _, mut host, credentials, capacity) = setup();
        host.check_error = Some((check, HostError::ProcessScanFailed));
        error(
            run(&f, &mut host, &credentials, &capacity),
            "process_scan_failed",
        );
        no_credentials(&trace);
        assert_eq!(f.config().exists(), check == 2);
    }
    let f = Fixture::new();
    let (trace, _, mut host, credentials, capacity) = setup();
    error(
        execute_held(
            &f.local,
            &f.url(),
            PASSWORD,
            &fingerprint(),
            &credentials,
            &mut host,
            &capacity,
            |local| {
                f.prepare(local, &trace)?;
                fs::write(f.marker(), b"new synthetic recovery marker").unwrap();
                Ok(())
            },
        ),
        "recovery_required",
    );
    no_credentials(&trace);
    assert_eq!(host.checks, 1);
    launcher_config::validate_config(&fs::read(f.config()).unwrap()).unwrap();
    assert!(!f.profile.join("auth_42").exists());
}

#[test]
fn token_failure_rolls_back_auth_while_prepared_config_remains_valid() {
    let f = Fixture::new();
    let (trace, _, mut host, mut credentials, capacity) = setup();
    credentials.fail_second = true;
    error(run(&f, &mut host, &credentials, &capacity), "credential_io");
    assert!(credentials.data.borrow().is_empty());
    assert!(!f.profile.join("auth_42").exists());
    assert!(!f.profile.join("auth_43").exists());
    assert_eq!(
        &trace.borrow()[5..],
        &["inventory", "set", "set", "delete", "delete"]
    );
    launcher_config::validate_config(&fs::read(f.config()).unwrap()).unwrap();
}
