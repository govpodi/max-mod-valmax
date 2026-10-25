//! Pure transaction tests: fake credentials/storage, no profiles or WinCred.
#[cfg(windows)]
#[path = "mdata_login_import_file_tests.rs"]
mod file_tests;
use super::*;
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::json;
use std::{cell::RefCell, rc::Rc};

const AUTH: &[u8] = b"opaque synthetic auth, not JSON";
type Trace = Rc<RefCell<Vec<Event>>>;
#[derive(Clone, Debug, PartialEq, Eq)]
enum Event {
    Inventory,
    Read(String),
    Prepare(Vec<String>),
    Ensure(String, Option<usize>),
    Revalidate(String),
    // Only lengths and the empty-replacement flag enter the event log.
    Set(String, bool, Option<usize>),
    Delete(String),
    Rollback,
    Commit,
}
fn target(id: &str) -> String {
    format!("access_token_{id}@MXMOD")
}
fn incoming_token(id: &str) -> Vec<u8> {
    format!("synthetic-incoming-{id}").into_bytes()
}
fn incoming(rows: &[(&str, Option<&[u8]>)]) -> (Value, LoginTokens, AuthFiles) {
    let mut tokens = LoginTokens::new();
    let mut auth = AuthFiles::new();
    for (id, bytes) in rows {
        tokens.insert(target(id), Zeroizing::new(incoming_token(id)));
        if let Some(bytes) = bytes {
            auth.insert(format!("auth_{id}"), Zeroizing::new(bytes.to_vec()));
        }
    }
    let manifest = json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
        "credentials":tokens.iter().map(|(name,bytes)|json!({"name":name,"blob":STANDARD.encode(bytes)})).collect::<Vec<_>>(),
        "files":auth.iter().map(|(path,bytes)|json!({"path":path,"size":bytes.len()})).collect::<Vec<_>>()});
    (manifest, tokens, auth)
}

enum WriteFault {
    Before(String),
    After(String),
    ForeignEqual(String),
    ForeignDifferent(String),
}
impl WriteFault {
    fn matches(&self, name: &str) -> bool {
        match self {
            Self::Before(target)
            | Self::After(target)
            | Self::ForeignEqual(target)
            | Self::ForeignDifferent(target) => target == name,
        }
    }
}
struct FakeCredentials {
    trace: Trace,
    data: RefCell<BTreeMap<String, Vec<u8>>>,
    names: Option<Vec<String>>,
    fault: RefCell<Option<WriteFault>>,
    fail_delete: BTreeSet<String>,
    fail_restore_empty: BTreeSet<String>,
}
impl FakeCredentials {
    fn new(trace: &Trace) -> Self {
        Self {
            trace: trace.clone(),
            data: RefCell::new(BTreeMap::new()),
            names: None,
            fault: RefCell::new(None),
            fail_delete: BTreeSet::new(),
            fail_restore_empty: BTreeSet::new(),
        }
    }
    fn put(&self, id: &str, bytes: &[u8]) {
        self.data.borrow_mut().insert(target(id), bytes.to_vec());
    }
    fn value(&self, id: &str) -> Option<Vec<u8>> {
        self.data.borrow().get(&target(id)).cloned()
    }
}
impl LoginCredentials for FakeCredentials {
    fn owned_token_names(&self) -> crate::login_credentials::Result<Vec<String>> {
        self.trace.borrow_mut().push(Event::Inventory);
        Ok(self
            .names
            .clone()
            .unwrap_or_else(|| self.data.borrow().keys().cloned().collect()))
    }
    fn read(&self, name: &str) -> crate::login_credentials::Result<Option<Zeroizing<Vec<u8>>>> {
        assert!(
            login::token_id(name).is_some(),
            "non-token credential values must never be read"
        );
        self.trace.borrow_mut().push(Event::Read(name.into()));
        Ok(self
            .data
            .borrow()
            .get(name)
            .map(|bytes| Zeroizing::new(bytes.clone())))
    }
    fn set_owned(
        &self,
        name: &str,
        bytes: &[u8],
        expected: Option<&[u8]>,
    ) -> crate::login_credentials::Result<()> {
        self.trace.borrow_mut().push(Event::Set(
            name.into(),
            bytes.is_empty(),
            expected.map(<[u8]>::len),
        ));
        if self.data.borrow().get(name).map(Vec::as_slice) != expected {
            return Err(CredentialError::DestinationNotEmpty);
        }
        if bytes.is_empty() && self.fail_restore_empty.contains(name) {
            return Err(CredentialError::CredentialIo);
        }
        let triggered = self
            .fault
            .borrow()
            .as_ref()
            .is_some_and(|fault| fault.matches(name));
        if triggered {
            match self.fault.borrow_mut().take().unwrap() {
                WriteFault::Before(_) => return Err(CredentialError::CredentialIo),
                WriteFault::After(_) => {
                    self.data.borrow_mut().insert(name.into(), bytes.to_vec());
                    return Err(CredentialError::CredentialIo);
                }
                WriteFault::ForeignEqual(_) => {
                    // Another writer won before our mutation, even though the
                    // new bytes happen to equal this import's desired token.
                    self.data.borrow_mut().insert(name.into(), bytes.to_vec());
                    return Err(CredentialError::DestinationNotEmpty);
                }
                WriteFault::ForeignDifferent(_) => {
                    self.data
                        .borrow_mut()
                        .insert(name.into(), b"synthetic-foreign-replacement".to_vec());
                    return Err(CredentialError::CredentialIo);
                }
            }
        }
        self.data.borrow_mut().insert(name.into(), bytes.to_vec());
        Ok(())
    }
    fn delete_owned(&self, name: &str, expected: &[u8]) -> crate::login_credentials::Result<()> {
        self.trace.borrow_mut().push(Event::Delete(name.into()));
        if self.fail_delete.contains(name) {
            return Err(CredentialError::CredentialIo);
        }
        let mut data = self.data.borrow_mut();
        match data.get(name) {
            None => Ok(()),
            Some(current) if current == expected => {
                data.remove(name);
                Ok(())
            }
            Some(_) => Err(CredentialError::CredentialIo),
        }
    }
}

struct FakeStorage {
    trace: Trace,
    auth: BTreeMap<String, Vec<u8>>,
    created: BTreeSet<String>,
    prepared: Vec<String>,
    fail_prepare: bool,
    fail_ensure: Option<String>,
    fail_revalidate: Option<String>,
    fail_rollback: bool,
    committed: bool,
    rollback_calls: usize,
}
impl FakeStorage {
    fn new(trace: &Trace) -> Self {
        Self {
            trace: trace.clone(),
            auth: BTreeMap::new(),
            created: BTreeSet::new(),
            prepared: Vec::new(),
            fail_prepare: false,
            fail_ensure: None,
            fail_revalidate: None,
            fail_rollback: false,
            committed: false,
            rollback_calls: 0,
        }
    }
}
impl ImportStorage for FakeStorage {
    fn prepare(&mut self, ids: &[String]) -> Result<()> {
        self.trace.borrow_mut().push(Event::Prepare(ids.to_vec()));
        if self.fail_prepare {
            return Err(ImportError::ProfileCollision);
        }
        self.prepared = ids.to_vec();
        Ok(())
    }
    fn ensure_auth(&mut self, id: &str, incoming: Option<&[u8]>) -> Result<()> {
        self.trace
            .borrow_mut()
            .push(Event::Ensure(id.into(), incoming.map(<[u8]>::len)));
        assert!(self.prepared.iter().any(|prepared| prepared == id));
        if !self.auth.contains_key(id) {
            if let Some(bytes) = incoming {
                self.auth.insert(id.into(), bytes.to_vec());
                self.created.insert(id.into());
            }
        }
        // A failed flush can leave a newly created auth object owned by us.
        if self.fail_ensure.as_deref() == Some(id) {
            return Err(ImportError::IoError);
        }
        Ok(())
    }
    fn revalidate(&mut self, id: &str) -> Result<()> {
        self.trace.borrow_mut().push(Event::Revalidate(id.into()));
        if self.fail_revalidate.as_deref() == Some(id) {
            return Err(ImportError::ProfileChanged);
        }
        Ok(())
    }
    fn rollback(&mut self) -> Result<()> {
        self.trace.borrow_mut().push(Event::Rollback);
        self.rollback_calls += 1;
        if self.fail_rollback {
            return Err(ImportError::RollbackFailed);
        }
        for id in std::mem::take(&mut self.created) {
            self.auth.remove(&id);
        }
        Ok(())
    }
    fn commit(&mut self) {
        self.trace.borrow_mut().push(Event::Commit);
        self.committed = true;
        self.created.clear();
    }
}
fn fakes() -> (Trace, FakeCredentials, FakeStorage) {
    let trace = Trace::default();
    let credentials = FakeCredentials::new(&trace);
    let storage = FakeStorage::new(&trace);
    (trace, credentials, storage)
}
fn outcome(result: Result<ImportResult>, code: &str) {
    assert_eq!(result.unwrap_err().code(), code);
}

#[test]
fn invalid_semantic_manifest_and_exact_auth_map_fail_before_any_adapter_call() {
    for case in 0..7 {
        let (trace, credentials, mut storage) = fakes();
        let (mut manifest, mut tokens, mut auth) = incoming(&[("42", Some(AUTH))]);
        match case {
            0 => manifest["schema"] = json!(true),
            1 => {
                tokens.get_mut(&target("42")).unwrap().push(b'x');
            }
            2 => {
                auth.clear();
            }
            3 => {
                auth.insert("auth_43".into(), Zeroizing::new(AUTH.to_vec()));
            }
            4 => {
                auth.get_mut("auth_42").unwrap().push(b'x');
            }
            5 => manifest["files"][0]["path"] = json!("../auth_42"),
            6 => {
                let duplicate = manifest["credentials"][0].clone();
                manifest["credentials"]
                    .as_array_mut()
                    .unwrap()
                    .push(duplicate);
            }
            _ => unreachable!(),
        }
        outcome(
            apply(&manifest, &tokens, &auth, &credentials, &mut storage),
            "invalid_payload",
        );
        assert!(trace.borrow().is_empty());
        assert!(credentials.data.borrow().is_empty());
        assert!(storage.auth.is_empty());
    }
}

#[test]
fn existing_active_account_is_not_replaced_or_prepared() {
    let (trace, credentials, mut storage) = fakes();
    credentials.put("42", b"synthetic-existing-different-token");
    storage
        .auth
        .insert("42".into(), b"synthetic-existing-auth".to_vec());
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH))]);
    assert_eq!(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage).unwrap(),
        ImportResult {
            schema: 1,
            result: "already_present",
            profiles: 0
        }
    );
    assert_eq!(
        *trace.borrow(),
        [Event::Inventory, Event::Read(target("42"))]
    );
    assert_eq!(
        credentials.value("42").unwrap(),
        b"synthetic-existing-different-token"
    );
    assert_eq!(storage.auth["42"], b"synthetic-existing-auth");
    assert!(!storage.committed);
}

#[test]
fn absent_and_empty_targets_add_in_order_after_all_preflight_and_auth_revalidation() {
    let (trace, credentials, mut storage) = fakes();
    credentials.put("10", b"");
    storage
        .auth
        .insert("10".into(), b"stale unmatched auth".to_vec());
    let opaque = b"\xff\0not-json";
    let (manifest, tokens, auth) = incoming(&[("10", Some(AUTH)), ("9", Some(opaque))]);
    assert_eq!(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage).unwrap(),
        ImportResult {
            schema: 1,
            result: "added",
            profiles: 2
        }
    );
    assert_eq!(
        *trace.borrow(),
        [
            Event::Inventory,
            Event::Read(target("10")),
            Event::Prepare(vec!["9".into(), "10".into()]),
            Event::Ensure("9".into(), Some(opaque.len())),
            Event::Revalidate("9".into()),
            Event::Set(target("9"), false, None),
            Event::Ensure("10".into(), Some(AUTH.len())),
            Event::Revalidate("10".into()),
            Event::Set(target("10"), false, Some(0)),
            Event::Commit,
        ]
    );
    assert_eq!(credentials.value("9").unwrap(), incoming_token("9"));
    assert_eq!(credentials.value("10").unwrap(), incoming_token("10"));
    assert_eq!(storage.auth["9"], opaque);
    assert_eq!(storage.auth["10"], b"stale unmatched auth");
    assert!(storage.committed);
}

#[test]
fn token_only_login_and_unrelated_inventory_names_do_not_read_other_credentials() {
    let (trace, mut credentials, mut storage) = fakes();
    credentials.names = Some(vec![
        "access_token_42@MXMOD".into(),
        "app_data_storage_key_Common@MXMOD".into(),
        "history_storage_key_42@MXMOD".into(),
        "access_token_7@ONEME".into(),
        "synthetic-web-5_2@MXMOD".into(),
    ]);
    let (manifest, tokens, auth) = incoming(&[("42", None)]);
    assert_eq!(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage)
            .unwrap()
            .profiles,
        1
    );
    assert_eq!(
        *trace.borrow(),
        [
            Event::Inventory,
            Event::Read(target("42")),
            Event::Prepare(vec!["42".into()]),
            Event::Ensure("42".into(), None),
            Event::Revalidate("42".into()),
            Event::Set(target("42"), false, None),
            Event::Commit
        ]
    );
    assert!(storage.auth.is_empty());
}

#[test]
fn inventory_alias_duplicate_and_union_limit_fail_before_file_prepare() {
    for names in [
        vec![target("1"), target("1")],
        vec![target("1"), "ACCESS_TOKEN_2@MXMOD".into()],
        vec![target("1"), "access_token_02@MXMOD".into()],
    ] {
        let (trace, mut credentials, mut storage) = fakes();
        credentials.names = Some(names);
        let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH))]);
        outcome(
            apply(&manifest, &tokens, &auth, &credentials, &mut storage),
            "invalid_profile",
        );
        assert_eq!(*trace.borrow(), [Event::Inventory]);
    }
    let (trace, credentials, mut storage) = fakes();
    for id in ["1", "2", "3"] {
        credentials.put(id, b"synthetic-active");
    }
    let before = credentials.data.borrow().clone();
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "account_limit",
    );
    assert_eq!(trace.borrow().len(), 4);
    assert!(
        trace
            .borrow()
            .iter()
            .all(|event| matches!(event, Event::Inventory | Event::Read(_)))
    );
    assert_eq!(*credentials.data.borrow(), before);
    assert!(storage.prepared.is_empty());
}

#[test]
fn failed_second_token_rolls_back_reverse_order_and_restores_an_existing_empty_target() {
    let (trace, credentials, mut storage) = fakes();
    credentials.put("42", b"");
    *credentials.fault.borrow_mut() = Some(WriteFault::Before(target("43")));
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH)), ("43", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "credential_io",
    );
    assert_eq!(credentials.value("42"), Some(vec![]));
    assert_eq!(credentials.value("43"), None);
    let events = trace.borrow();
    assert_eq!(
        &events[events.len() - 4..],
        &[
            Event::Delete(target("43")),
            Event::Read(target("42")),
            Event::Set(target("42"), true, Some(incoming_token("42").len())),
            Event::Rollback
        ]
    );
    assert!(storage.auth.is_empty());
    assert_eq!(storage.rollback_calls, 1);
    assert!(!storage.committed);
}

#[test]
fn a_native_error_after_commit_is_included_in_the_rollback_ledger() {
    let (trace, credentials, mut storage) = fakes();
    *credentials.fault.borrow_mut() = Some(WriteFault::After(target("43")));
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH)), ("43", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "credential_io",
    );
    assert!(credentials.data.borrow().is_empty());
    let events = trace.borrow();
    assert_eq!(
        &events[events.len() - 3..],
        &[
            Event::Delete(target("43")),
            Event::Delete(target("42")),
            Event::Rollback
        ]
    );
    assert!(storage.auth.is_empty());
}

#[test]
fn prewrite_conflict_never_deletes_a_foreign_byte_identical_token() {
    let (trace, credentials, mut storage) = fakes();
    *credentials.fault.borrow_mut() = Some(WriteFault::ForeignEqual(target("43")));
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH)), ("43", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "destination_not_empty",
    );
    assert_eq!(credentials.value("43").unwrap(), incoming_token("43"));
    assert_eq!(credentials.value("42"), None);
    assert!(!trace.borrow().contains(&Event::Delete(target("43"))));
    let events = trace.borrow();
    assert_eq!(
        &events[events.len() - 2..],
        &[Event::Delete(target("42")), Event::Rollback]
    );
    assert!(!storage.committed);
}

#[test]
fn failed_token_rollback_preserves_auth_and_continues_reversing_other_tokens() {
    let (trace, mut credentials, mut storage) = fakes();
    credentials.fail_delete.insert(target("43"));
    *credentials.fault.borrow_mut() = Some(WriteFault::After(target("43")));
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH)), ("43", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "rollback_failed",
    );
    assert_eq!(credentials.value("43").unwrap(), incoming_token("43"));
    assert_eq!(credentials.value("42"), None);
    assert!(trace.borrow().contains(&Event::Delete(target("42"))));
    assert_eq!(
        storage.rollback_calls, 0,
        "no auth removal while a new token may remain active"
    );
    assert_eq!(storage.auth.len(), 2);
    assert!(!storage.committed);

    let (_, mut credentials, mut storage) = fakes();
    credentials.put("42", b"");
    credentials.fail_restore_empty.insert(target("42"));
    *credentials.fault.borrow_mut() = Some(WriteFault::After(target("42")));
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "rollback_failed",
    );
    assert_eq!(credentials.value("42").unwrap(), incoming_token("42"));
    assert_eq!(storage.rollback_calls, 0);
    assert_eq!(storage.auth["42"], AUTH);
}

#[test]
fn unexpected_foreign_value_during_rollback_is_preserved_with_its_auth() {
    let (trace, credentials, mut storage) = fakes();
    *credentials.fault.borrow_mut() = Some(WriteFault::ForeignDifferent(target("42")));
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "rollback_failed",
    );
    assert_eq!(
        credentials.value("42").unwrap(),
        b"synthetic-foreign-replacement"
    );
    assert!(trace.borrow().contains(&Event::Delete(target("42"))));
    assert_eq!(storage.rollback_calls, 0);
    assert_eq!(storage.auth["42"], AUTH);
}

#[test]
fn preflight_ensure_and_revalidate_failures_cannot_leave_our_tokens_committed() {
    for failure in 0..3 {
        let (trace, credentials, mut storage) = fakes();
        storage
            .auth
            .insert("7".into(), b"untouched stale metadata".to_vec());
        match failure {
            0 => storage.fail_prepare = true,
            1 => storage.fail_ensure = Some("43".into()),
            2 => storage.fail_revalidate = Some("43".into()),
            _ => unreachable!(),
        }
        let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH)), ("43", Some(AUTH))]);
        outcome(
            apply(&manifest, &tokens, &auth, &credentials, &mut storage),
            ["profile_collision", "io_error", "profile_changed"][failure],
        );
        assert!(credentials.data.borrow().is_empty());
        assert_eq!(
            storage.auth,
            BTreeMap::from([("7".into(), b"untouched stale metadata".to_vec())])
        );
        assert!(
            !trace
                .borrow()
                .iter()
                .any(|event| matches!(event, Event::Set(name, _, _) if name == &target("43")))
        );
        assert_eq!(storage.rollback_calls, usize::from(failure != 0));
        assert!(!storage.committed);
        if failure == 0 {
            assert_eq!(
                *trace.borrow(),
                [
                    Event::Inventory,
                    Event::Prepare(vec!["42".into(), "43".into()])
                ]
            );
        }
    }
}

#[test]
fn storage_rollback_failure_is_explicit_after_tokens_have_been_removed() {
    let (trace, credentials, mut storage) = fakes();
    storage.fail_rollback = true;
    *credentials.fault.borrow_mut() = Some(WriteFault::After(target("42")));
    let (manifest, tokens, auth) = incoming(&[("42", Some(AUTH))]);
    outcome(
        apply(&manifest, &tokens, &auth, &credentials, &mut storage),
        "rollback_failed",
    );
    assert!(credentials.data.borrow().is_empty());
    assert_eq!(storage.auth["42"], AUTH);
    assert_eq!(storage.rollback_calls, 1);
    let events = trace.borrow();
    assert_eq!(
        &events[events.len() - 2..],
        &[Event::Delete(target("42")), Event::Rollback]
    );
    assert!(!storage.committed);
}
