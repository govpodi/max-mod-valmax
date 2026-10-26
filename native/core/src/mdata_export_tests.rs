//! Owned Windows fixtures only. No production WinCred adapter is constructed.
use super::*;
use crate::login_credentials::Result as CredentialResult;
use serde_json::json;
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    fs,
    os::windows::fs::OpenOptionsExt,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-export";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("mdata-export"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}
const PASSWORD: &str = "synthetic-export-password-only";
const ID: &str = "9007199254740993";
const AUTH_A: &[u8] = br#"{"first_name":"Alice","last_name":"Example","short_name":"AE","private":"synthetic-private-auth"}"#;
const AUTH_B: &[u8] = br#"{"first_name":"Blice","last_name":"Example","short_name":"BE","private":"synthetic-private-auth"}"#;

struct Fixture {
    root: PathBuf,
    profile: PathBuf,
    output: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let name: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let root = fixture_base().join(name);
        let profile = root.join("MXMOD");
        let output = root.join("selected-output");
        fs::create_dir_all(&profile).unwrap();
        fs::create_dir(&output).unwrap();
        fs::write(profile.join("config.json"), b"{}").unwrap();
        Self {
            root,
            profile,
            output,
        }
    }
    fn auth(&self, id: &str, bytes: &[u8]) {
        fs::write(self.profile.join(format!("auth_{id}")), bytes).unwrap();
    }
    fn destination(&self) -> PathBuf {
        self.output.join("chosen.mdata")
    }
    fn url(&self) -> String {
        file_url(&self.destination())
    }
    fn empty_output(&self) {
        assert_eq!(
            fs::read_dir(&self.output).unwrap().count(),
            0,
            "no published archive or abandoned temporary may remain"
        );
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(fixture_base()).unwrap();
        let own = fs::canonicalize(&self.root).unwrap();
        let name = self.root.file_name().unwrap().to_str().unwrap();
        assert!(
            own.starts_with(&base)
                && own != base
                && name.len() == 32
                && name.bytes().all(|b| b.is_ascii_hexdigit())
        );
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

type ReadHook = Box<dyn FnMut(usize, &str)>;
#[derive(Default)]
struct FakeCredentials {
    tokens: BTreeMap<String, Vec<u8>>,
    reads: RefCell<Vec<String>>,
    changing_token: Cell<bool>,
    hook: RefCell<Option<ReadHook>>,
}
impl FakeCredentials {
    fn token(mut self, id: &str, bytes: &[u8]) -> Self {
        self.tokens
            .insert(format!("access_token_{id}@MXMOD"), bytes.to_vec());
        self
    }
    fn seeded() -> Self {
        Self::default().token(ID, b"synthetic-opaque-token")
    }
    fn read_count(&self) -> usize {
        self.reads.borrow().len()
    }
}
impl LoginCredentials for FakeCredentials {
    fn owned_token_names(&self) -> CredentialResult<Vec<String>> {
        panic!("compact export must never enumerate credential names")
    }
    fn read(&self, name: &str) -> CredentialResult<Option<Zeroizing<Vec<u8>>>> {
        assert!(
            mdata_login::token_id(name).is_some(),
            "only canonical own tokens are read"
        );
        let index = {
            let mut reads = self.reads.borrow_mut();
            reads.push(name.into());
            reads.len()
        };
        if let Some(hook) = self.hook.borrow_mut().as_mut() {
            hook(index, name);
        }
        if self.changing_token.get() {
            return Ok(Some(Zeroizing::new(
                format!("synthetic-changing-{index}").into_bytes(),
            )));
        }
        Ok(self
            .tokens
            .get(name)
            .map(|value| Zeroizing::new(value.clone())))
    }
    fn set_owned(&self, _: &str, _: &[u8], _: Option<&[u8]>) -> CredentialResult<()> {
        panic!("export must not write credentials")
    }
    fn delete_owned(&self, _: &str, _: &[u8]) -> CredentialResult<()> {
        panic!("export must not delete credentials")
    }
}

type CapacityHook = Box<dyn FnMut(usize, &Path)>;
#[derive(Default)]
struct FakeCapacity {
    values: Vec<u64>,
    calls: RefCell<Vec<PathBuf>>,
    hook: RefCell<Option<CapacityHook>>,
}
impl FakeCapacity {
    fn values(values: &[u64]) -> Self {
        Self {
            values: values.into(),
            ..Self::default()
        }
    }
}
impl Capacity for FakeCapacity {
    fn available(&self, path: &Path) -> Result<u64> {
        let index = {
            let mut calls = self.calls.borrow_mut();
            calls.push(path.to_owned());
            calls.len()
        };
        if let Some(hook) = self.hook.borrow_mut().as_mut() {
            hook(index, path);
        }
        Ok(self.values.get(index - 1).copied().unwrap_or(u64::MAX))
    }
}
fn run(
    f: &Fixture,
    credentials: &dyn LoginCredentials,
    cancel: &Cancellation,
    capacity: &dyn Capacity,
) -> Result<ExportResult> {
    export(
        &f.profile,
        &f.url(),
        PASSWORD,
        None,
        credentials,
        cancel,
        capacity,
    )
}
fn code(result: Result<ExportResult>, expected: &str) {
    assert_eq!(result.unwrap_err().code(), expected);
}

#[test]
fn selected_compact_export_authenticates_and_leaves_other_data_untouched() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    f.auth("7", br#"{"first_name":"Unselected"}"#);
    f.auth("8", b"stale metadata without an active token");
    let history = f.profile.join(format!("user{ID}/history/synthetic.db"));
    fs::create_dir_all(history.parent().unwrap()).unwrap();
    fs::write(
        &history,
        b"synthetic history must never enter compact export",
    )
    .unwrap();
    // A forbidden read would fail sharing rather than silently succeeding.
    let held_history = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(&history)
        .unwrap();
    let credentials = FakeCredentials::seeded()
        .token("7", b"synthetic-unselected-token")
        .token("8", b"");
    let capacity = FakeCapacity::default();
    let result = export(
        &f.profile,
        &f.url(),
        PASSWORD,
        Some(&json!([ID])),
        &credentials,
        &Cancellation::default(),
        &capacity,
    )
    .unwrap();
    assert_eq!(
        (
            result.schema,
            result.state,
            result.action,
            result.accepted,
            result.result,
            result.profiles
        ),
        (1, "done", "export", true, "exported", 1)
    );
    let bytes = fs::read(f.destination()).unwrap();
    assert!(bytes.starts_with(archive::LOGIN_MAGIC));
    assert_eq!(result.archive_bytes, bytes.len() as u64);
    assert!(bytes.len() <= MAX_ARCHIVE_BYTES);
    let preview = crate::mdata_preview::inspect(&bytes, PASSWORD).unwrap();
    assert_eq!(preview.profiles, 1);
    assert_eq!(preview.accounts[0].account_id, ID);
    assert_eq!(preview.accounts[0].display_name, "Alice Example");
    assert_eq!(preview.accounts[0].short_name, "AE");
    assert_eq!(preview.fingerprint, crate::settings_io::sha256(&bytes));
    assert!(credentials.read_count() >= 4);
    assert!(
        credentials
            .reads
            .borrow()
            .iter()
            .all(|name| name == &format!("access_token_{ID}@MXMOD"))
    );
    let public =
        serde_json::to_string(&result).unwrap() + &serde_json::to_string(&preview).unwrap();
    for secret in [
        PASSWORD,
        "synthetic-opaque-token",
        "synthetic-private-auth",
        "access_token_",
        "file:///",
    ] {
        assert!(!public.contains(secret));
    }
    drop(held_history);
    assert_eq!(
        fs::read(history).unwrap(),
        b"synthetic history must never enter compact export"
    );
    assert_eq!(
        fs::read(f.profile.join(format!("auth_{ID}"))).unwrap(),
        AUTH_A
    );
    assert_eq!(
        fs::read(f.profile.join("auth_7")).unwrap(),
        br#"{"first_name":"Unselected"}"#
    );
    assert_eq!(fs::read_dir(&f.output).unwrap().count(), 1);
}

#[test]
fn invalid_request_paths_password_and_selection_precede_credential_access() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    let credentials = FakeCredentials::seeded();
    let capacity = FakeCapacity::default();
    let cancel = Cancellation::default();
    code(
        export(
            &f.profile,
            &f.url(),
            "short",
            None,
            &credentials,
            &cancel,
            &capacity,
        ),
        "invalid_password",
    );
    for selected in [
        json!([]),
        json!([ID, ID]),
        json!([42]),
        json!(["01"]),
        json!(["1", "2", "3", "4"]),
    ] {
        code(
            export(
                &f.profile,
                &f.url(),
                PASSWORD,
                Some(&selected),
                &credentials,
                &cancel,
                &capacity,
            ),
            "invalid_request",
        );
    }
    let inside = file_url(&f.profile.join("chosen.mdata"));
    let wrong_suffix = file_url(&f.output.join("chosen.txt"));
    for url in [
        "https://example.invalid/a.mdata",
        "file://host/C:/a.mdata",
        "file:///C:/ONEME/a.mdata",
        "file:///C:/a.mdata?extra=1",
        &inside,
        &wrong_suffix,
    ] {
        code(
            export(
                &f.profile,
                url,
                PASSWORD,
                None,
                &credentials,
                &cancel,
                &capacity,
            ),
            "unsafe_path",
        );
    }
    assert_eq!(credentials.read_count(), 0);
    assert!(capacity.calls.borrow().is_empty());
    f.empty_output();
}

#[test]
fn existing_output_and_recovery_latch_fail_before_credential_or_capacity_access() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    let credentials = FakeCredentials::seeded();
    let capacity = FakeCapacity::default();
    for bytes in [b"".as_slice(), b"existing selected file".as_slice()] {
        fs::write(f.destination(), bytes).unwrap();
        code(
            run(&f, &credentials, &Cancellation::default(), &capacity),
            "file_exists",
        );
        assert_eq!(fs::read(f.destination()).unwrap(), bytes);
    }
    fs::remove_file(f.destination()).unwrap();
    let marker = mdata_recovery_state::write(
        &f.profile,
        &f.root
            .join(mdata_recovery_state::BACKUP_DIRECTORY)
            .join("synthetic.mdata"),
    )
    .unwrap();
    code(
        run(&f, &credentials, &Cancellation::default(), &capacity),
        "recovery_required",
    );
    mdata_recovery_state::clear(&f.profile, &marker).unwrap();
    fs::write(f.root.join(mdata_recovery_state::FILENAME), b"{").unwrap();
    code(
        run(&f, &credentials, &Cancellation::default(), &capacity),
        "recovery_required",
    );
    assert_eq!(credentials.read_count(), 0);
    assert!(capacity.calls.borrow().is_empty());
    f.empty_output();
}

#[test]
fn low_profile_or_destination_space_is_early_and_late_space_loss_does_not_publish() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    let credentials = FakeCredentials::seeded();
    for (values, expected_calls) in [
        (vec![RESERVE_BYTES - 1], 1),
        (
            vec![u64::MAX, RESERVE_BYTES + MAX_ARCHIVE_BYTES as u64 - 1],
            2,
        ),
    ] {
        let capacity = FakeCapacity::values(&values);
        code(
            run(&f, &credentials, &Cancellation::default(), &capacity),
            "insufficient_space",
        );
        assert_eq!(capacity.calls.borrow().len(), expected_calls);
        assert_eq!(credentials.read_count(), 0);
        f.empty_output();
    }
    let capacity = FakeCapacity::values(&[u64::MAX, u64::MAX, u64::MAX, 0]);
    code(
        run(&f, &credentials, &Cancellation::default(), &capacity),
        "insufficient_space",
    );
    assert_eq!(capacity.calls.borrow().len(), 4);
    assert!(credentials.read_count() >= 4);
    f.empty_output();
}

#[test]
fn endlessly_changing_token_has_three_attempt_bound_and_no_output() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    let credentials = FakeCredentials::seeded();
    credentials.changing_token.set(true);
    code(
        run(
            &f,
            &credentials,
            &Cancellation::default(),
            &FakeCapacity::default(),
        ),
        "profile_changed",
    );
    // Two reads detect each inconsistent capture; no encryption is needed.
    assert_eq!(credentials.read_count(), 6);
    f.empty_output();
}

#[test]
fn same_size_auth_changes_between_captures_are_not_published_after_retry_limit() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    assert_eq!(AUTH_A.len(), AUTH_B.len());
    let credentials = FakeCredentials::seeded();
    let auth = f.profile.join(format!("auth_{ID}"));
    *credentials.hook.borrow_mut() = Some(Box::new(move |index, _| {
        // The second capture of every attempt sees new same-sized content.
        if index % 4 == 3 {
            let next = if fs::read(&auth).unwrap() == AUTH_A {
                AUTH_B
            } else {
                AUTH_A
            };
            fs::write(&auth, next).unwrap();
        }
    }));
    code(
        run(
            &f,
            &credentials,
            &Cancellation::default(),
            &FakeCapacity::default(),
        ),
        "profile_changed",
    );
    assert_eq!(credentials.read_count(), 12);
    f.empty_output();
}

#[test]
fn source_limits_and_no_active_accounts_never_leave_partial_archives() {
    let f = Fixture::new();
    let credentials = FakeCredentials::seeded();
    f.auth(ID, &vec![b'x'; mdata_login::MAX_AUTH_BYTES + 1]);
    code(
        run(
            &f,
            &credentials,
            &Cancellation::default(),
            &FakeCapacity::default(),
        ),
        "invalid_profile",
    );
    assert_eq!(credentials.read_count(), 0);
    fs::remove_file(f.profile.join(format!("auth_{ID}"))).unwrap();
    for id in 1..=capture::MAX_CANDIDATES + 1 {
        f.auth(&id.to_string(), b"{}");
    }
    code(
        run(
            &f,
            &credentials,
            &Cancellation::default(),
            &FakeCapacity::default(),
        ),
        "invalid_profile",
    );
    assert_eq!(credentials.read_count(), 0);
    for id in 1..=capture::MAX_CANDIDATES + 1 {
        fs::remove_file(f.profile.join(format!("auth_{id}"))).unwrap();
    }
    let mut four = FakeCredentials::default();
    for id in ["1", "2", "3", "4"] {
        f.auth(id, b"{}");
        four = four.token(id, b"synthetic-token");
    }
    code(
        run(
            &f,
            &four,
            &Cancellation::default(),
            &FakeCapacity::default(),
        ),
        "account_limit",
    );
    assert_eq!(four.read_count(), 4);
    let empty = FakeCredentials::default().token("1", b"");
    code(
        run(
            &f,
            &empty,
            &Cancellation::default(),
            &FakeCapacity::default(),
        ),
        "no_accounts",
    );
    f.empty_output();
}

#[test]
fn cancellation_before_io_during_cipher_write_and_before_commit_is_terminal() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    let credentials = FakeCredentials::seeded();
    let cancel = Cancellation::default();
    cancel.cancel();
    let capacity = FakeCapacity::default();
    code(run(&f, &credentials, &cancel, &capacity), "cancelled");
    assert_eq!(credentials.read_count(), 0);
    assert!(capacity.calls.borrow().is_empty());
    let mut cipher = Ciphertext {
        bytes: Zeroizing::new(b"prefix".to_vec()),
        cancel: &cancel,
    };
    // Interrupted makes Write::write_all retry forever. Cancellation must be
    // a non-retryable I/O error, and may not append any partial bytes.
    assert_eq!(
        cipher.write_all(b"must-not-append").unwrap_err().kind(),
        io::ErrorKind::Other
    );
    assert_eq!(cipher.bytes.as_slice(), b"prefix");
    let cancel = Cancellation::default();
    let to_cancel = cancel.clone();
    let capacity = FakeCapacity::default();
    *capacity.hook.borrow_mut() = Some(Box::new(move |index, _| {
        if index == 4 {
            to_cancel.cancel();
        }
    }));
    code(run(&f, &credentials, &cancel, &capacity), "cancelled");
    assert!(credentials.read_count() >= 4);
    f.empty_output();
}

#[test]
fn late_destination_collision_preserves_other_writer_without_temp_leaks() {
    let f = Fixture::new();
    f.auth(ID, AUTH_A);
    let credentials = FakeCredentials::seeded();
    let capacity = FakeCapacity::default();
    let destination = f.destination();
    *capacity.hook.borrow_mut() = Some(Box::new(move |index, _| {
        if index == 4 {
            fs::write(&destination, b"other writer's synthetic result").unwrap();
        }
    }));
    code(
        run(&f, &credentials, &Cancellation::default(), &capacity),
        "file_exists",
    );
    assert_eq!(
        fs::read(f.destination()).unwrap(),
        b"other writer's synthetic result"
    );
    assert_eq!(fs::read_dir(&f.output).unwrap().count(), 1);
    // Even after a preceding absence check, the handle-based publication must
    // still reject replacement, including an empty existing destination.
    let safe = SafeStorePath::new(f.output.join("late-empty.mdata")).unwrap();
    let held = safe.begin(false).unwrap().unwrap();
    new_output(&held).unwrap();
    fs::write(held.path(), b"").unwrap();
    assert_eq!(
        held.atomic_create(b"new ciphertext"),
        Err(StoreError::FileExists)
    );
    assert!(fs::read(held.path()).unwrap().is_empty());
    assert_eq!(fs::read_dir(&f.output).unwrap().count(), 2);
}

#[test]
fn service_busy_drop_and_shutdown_keep_admission_and_active_cancellation_consistent() {
    let f = Fixture::new();
    let credentials = FakeCredentials::default();
    let service = MDataExportService::default();
    assert!(!service.slot.lock().unwrap().closed);
    assert!(service.slot.lock().unwrap().active.is_none());
    let first = service.admit().unwrap();
    assert!(matches!(service.admit(), Err(ExportError("busy"))));
    code(
        service.execute(&f.profile, &f.url(), PASSWORD, None, &credentials),
        "busy",
    );
    drop(first);
    assert!(service.slot.lock().unwrap().active.is_none());
    let second = service.admit().unwrap();
    service.cancel_pending();
    assert!(second.cancel.check().is_err());
    assert!(matches!(service.admit(), Err(ExportError("cancelled"))));
    drop(second);
    assert!(service.slot.lock().unwrap().active.is_none());
    service.cancel_pending();
    code(
        service.execute(&f.profile, &f.url(), PASSWORD, None, &credentials),
        "cancelled",
    );
    assert!(service.slot.lock().unwrap().closed);
    assert_eq!(credentials.read_count(), 0);
    f.empty_output();
}
