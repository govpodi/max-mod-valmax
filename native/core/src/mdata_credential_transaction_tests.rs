use super::*;
use std::cell::{Cell, RefCell};

const A: &str = "access_token_1@MXMOD";
const B: &str = "calls_token_1@MXMOD";
const C: &str = "history_storage_key_1@MXMOD";
const D: &str = "multi_state_enabled_Common@MXMOD";

#[derive(Clone, Copy)]
enum Fault {
    Before,
    After,
    Conflict,
    ForeignAfter,
}
#[derive(Default)]
struct Fake {
    values: RefCell<BTreeMap<String, Vec<u8>>>,
    names: RefCell<Option<Vec<String>>>,
    faults: RefCell<BTreeMap<usize, Fault>>,
    read_fault: Cell<Option<usize>>,
    reads: Cell<usize>,
    inventories: Cell<usize>,
    writes: Cell<usize>,
    write_order: RefCell<Vec<String>>,
}
impl Fake {
    fn new(entries: &[(&str, &[u8])]) -> Self {
        Self {
            values: RefCell::new(
                entries
                    .iter()
                    .map(|(name, value)| (name.to_string(), value.to_vec()))
                    .collect(),
            ),
            ..Default::default()
        }
    }
    fn fault(&self, number: usize, fault: Fault) {
        self.faults.borrow_mut().insert(number, fault);
    }
    fn put(&self, name: &str, value: &[u8]) {
        self.values
            .borrow_mut()
            .insert(name.to_owned(), value.to_vec());
    }
    fn change(
        &self,
        name: &str,
        new: Option<&[u8]>,
        expected: Option<&[u8]>,
    ) -> std::result::Result<(), CredentialError> {
        let number = self.writes.get() + 1;
        self.writes.set(number);
        self.write_order.borrow_mut().push(name.to_owned());
        let mut values = self.values.borrow_mut();
        if values.get(name).map(Vec::as_slice) != expected {
            return Err(CredentialError::DestinationNotEmpty);
        }
        let fault = self.faults.borrow_mut().remove(&number);
        match fault {
            Some(Fault::Before) => return Err(CredentialError::CredentialIo),
            Some(Fault::Conflict) => {
                // Even identical-to-incoming content remains someone else's
                // write when the adapter proves our operation never happened.
                values.insert(name.to_owned(), new.unwrap_or(b"foreign").to_vec());
                return Err(CredentialError::DestinationNotEmpty);
            }
            Some(Fault::ForeignAfter) => {
                values.insert(name.to_owned(), b"foreign".to_vec());
                return Err(CredentialError::CredentialIo);
            }
            _ => {}
        }
        match new {
            Some(value) => {
                values.insert(name.to_owned(), value.to_vec());
            }
            None => {
                values.remove(name);
            }
        }
        if matches!(fault, Some(Fault::After)) {
            Err(CredentialError::CredentialIo)
        } else {
            Ok(())
        }
    }
}
impl RecoveryCredentials for Fake {
    fn owned_names(&self) -> std::result::Result<Vec<String>, CredentialError> {
        self.inventories.set(self.inventories.get() + 1);
        Ok(self
            .names
            .borrow()
            .clone()
            .unwrap_or_else(|| self.values.borrow().keys().cloned().collect()))
    }
    fn read(&self, name: &str) -> std::result::Result<Option<Zeroizing<Vec<u8>>>, CredentialError> {
        self.reads.set(self.reads.get() + 1);
        if self.read_fault.get() == Some(self.reads.get()) {
            return Err(CredentialError::CredentialIo);
        }
        Ok(self.values.borrow().get(name).cloned().map(Zeroizing::new))
    }
    fn set_owned(
        &self,
        name: &str,
        value: &[u8],
        expected: Option<&[u8]>,
    ) -> std::result::Result<(), CredentialError> {
        self.change(name, Some(value), expected)
    }
    fn delete_owned(
        &self,
        name: &str,
        expected: &[u8],
    ) -> std::result::Result<(), CredentialError> {
        self.change(name, None, Some(expected))
    }
}
fn keys(entries: &[(&str, &[u8])]) -> RecoveryKeys {
    RecoveryKeys::new(
        entries
            .iter()
            .map(|(name, value)| (name.to_string(), Zeroizing::new(value.to_vec())))
            .collect(),
    )
    .unwrap()
}
fn assert_values(store: &Fake, expected: &[(&str, &[u8])]) {
    assert_eq!(
        *store.values.borrow(),
        Fake::new(expected).values.into_inner()
    );
}
const OLD: &[(&str, &[u8])] = &[(A, b"old"), (B, b""), (C, b"remove")];
const NEW: &[(&str, &[u8])] = &[(A, b"new"), (B, b"nonempty"), (D, b"")];

#[test]
fn mixed_replacement_removal_addition_and_empty_roundtrip() {
    let store = Fake::new(OLD);
    let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
    assert_eq!(tx.change_count(), 4);
    assert_eq!(store.writes.get(), 0);
    tx.apply().unwrap();
    assert_values(&store, NEW);
    tx.rollback().unwrap();
    assert_values(&store, OLD);
    assert_eq!(&store.write_order.borrow()[4..], &[D, C, B, A]);
    let writes = store.writes.get();
    tx.rollback().unwrap();
    assert_eq!(store.writes.get(), writes);
    assert_eq!(tx.apply(), Err(TransactionError::InvalidRequest));
    assert_eq!(tx.commit(), Err(TransactionError::InvalidRequest));
}

#[test]
fn committed_snapshot_survives_drop_and_cannot_rollback() {
    let store = Fake::new(OLD);
    let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
    assert_eq!(tx.commit(), Err(TransactionError::InvalidRequest));
    tx.apply().unwrap();
    assert_eq!(tx.apply(), Err(TransactionError::InvalidRequest));
    tx.commit().unwrap();
    assert_eq!(tx.state(), TransactionState::Committed);
    assert_eq!(tx.rollback(), Err(TransactionError::InvalidRequest));
    assert_eq!(tx.commit(), Err(TransactionError::InvalidRequest));
    let counts = (store.reads.get(), store.writes.get());
    drop(tx);
    assert_eq!(counts, (store.reads.get(), store.writes.get()));
    assert_values(&store, NEW);
}

#[test]
fn drop_has_no_implicit_store_access_even_after_apply() {
    let store = Fake::new(OLD);
    let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
    tx.apply().unwrap();
    let counts = (store.reads.get(), store.writes.get());
    drop(tx);
    assert_eq!(counts, (store.reads.get(), store.writes.get()));
    assert_values(&store, NEW);
}

#[test]
fn every_before_and_after_write_failure_restores_original_snapshot() {
    for number in 1..=4 {
        for fault in [Fault::Before, Fault::After] {
            let store = Fake::new(OLD);
            store.fault(number, fault);
            let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
            assert_eq!(tx.apply(), Err(TransactionError::CredentialIo));
            assert_eq!(tx.state(), TransactionState::RolledBack);
            assert_values(&store, OLD);
        }
    }
}

#[test]
fn prewrite_conflict_does_not_revert_identical_foreign_value() {
    let store = Fake::new(OLD);
    store.fault(2, Fault::Conflict);
    let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
    assert_eq!(tx.apply(), Err(TransactionError::DestinationNotEmpty));
    assert_eq!(tx.state(), TransactionState::RolledBack);
    assert_values(&store, &[(A, b"old"), (B, b"nonempty"), (C, b"remove")]);
    assert_eq!(&*store.write_order.borrow(), &[A, B, A]);
}

#[test]
fn unknown_after_value_is_preserved_and_other_changes_are_rolled_back() {
    let store = Fake::new(OLD);
    store.fault(3, Fault::ForeignAfter);
    let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
    assert_eq!(tx.apply(), Err(TransactionError::RollbackFailed));
    assert_eq!(tx.state(), TransactionState::RollbackFailed);
    assert_values(&store, &[(A, b"old"), (B, b""), (C, b"foreign")]);
    assert_eq!(tx.commit(), Err(TransactionError::InvalidRequest));
    // Explicit external restoration of the old value permits safe retry.
    store.put(C, b"remove");
    tx.rollback().unwrap();
    assert_values(&store, OLD);
}

#[test]
fn failed_rollback_continues_and_can_be_explicitly_retried() {
    for fault in [Fault::Before, Fault::After] {
        let store = Fake::new(OLD);
        let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
        tx.apply().unwrap();
        store.fault(5, fault);
        assert_eq!(tx.rollback(), Err(TransactionError::RollbackFailed));
        assert_eq!(store.values.borrow().get(A).unwrap(), b"old");
        assert_eq!(store.values.borrow().get(B).unwrap(), b"");
        tx.rollback().unwrap();
        assert_values(&store, OLD);
    }
}

#[test]
fn retry_does_not_reclaim_a_successfully_restored_entry() {
    let store = Fake::new(OLD);
    let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
    tx.apply().unwrap();
    store.fault(5, Fault::Before);
    assert_eq!(tx.rollback(), Err(TransactionError::RollbackFailed));
    store.put(A, b"new");
    let writes = store.writes.get();
    tx.rollback().unwrap();
    assert_eq!(store.writes.get(), writes + 1);
    assert_values(&store, &[(A, b"new"), (B, b""), (C, b"remove")]);
}

#[test]
fn read_failure_during_rollback_preserves_that_entry_and_restores_others() {
    let store = Fake::new(OLD);
    let mut tx = CredentialTransaction::prepare(&store, keys(NEW)).unwrap();
    tx.apply().unwrap();
    store.read_fault.set(Some(store.reads.get() + 1));
    assert_eq!(tx.rollback(), Err(TransactionError::RollbackFailed));
    assert_values(&store, &[(A, b"old"), (B, b""), (C, b"remove"), (D, b"")]);
    tx.rollback().unwrap();
    assert_values(&store, OLD);
}

#[test]
fn changed_snapshot_prevents_first_write_including_unchanged_keys() {
    for mode in 0..3 {
        let store = Fake::new(OLD);
        let mut tx = CredentialTransaction::prepare(&store, keys(OLD)).unwrap();
        match mode {
            0 => store.put(A, b"different"),
            1 => store.put(D, b"added"),
            _ => {
                store.values.borrow_mut().remove(C);
            }
        }
        assert_eq!(tx.apply(), Err(TransactionError::ProfileChanged));
        assert_eq!(store.writes.get(), 0);
        assert_eq!(tx.state(), TransactionState::Prepared);
    }
}

#[test]
fn invalid_inventory_is_rejected_before_any_value_read() {
    for names in [
        vec![A, "access_token_2@ONEME"],
        vec![A, A],
        vec!["x-3_2@MXMOD", "X-3_2@MXMOD"],
        vec!["unknown@MXMOD"],
    ] {
        let store = Fake::default();
        *store.names.borrow_mut() = Some(names.into_iter().map(str::to_owned).collect());
        assert!(matches!(
            CredentialTransaction::prepare(&store, keys(&[])),
            Err(TransactionError::InvalidPayload)
        ));
        assert_eq!(store.reads.get(), 0);
        assert_eq!(store.writes.get(), 0);
    }
}

#[test]
fn cross_snapshot_case_alias_is_rejected_before_first_mutation() {
    let store = Fake::new(&[("a-3_2@MXMOD", b"old")]);
    assert!(matches!(
        CredentialTransaction::prepare(&store, keys(&[("A-3_2@MXMOD", b"new")])),
        Err(TransactionError::InvalidPayload)
    ));
    assert_eq!(store.writes.get(), 0);
    assert_values(&store, &[("a-3_2@MXMOD", b"old")]);
}

#[test]
fn missing_or_unreadable_snapshot_entry_prevents_mutations() {
    let store = Fake::default();
    *store.names.borrow_mut() = Some(vec![A.to_owned()]);
    assert!(matches!(
        CredentialTransaction::prepare(&store, keys(&[])),
        Err(TransactionError::ProfileChanged)
    ));
    store.read_fault.set(Some(2));
    assert!(matches!(
        CredentialTransaction::prepare(&store, keys(&[])),
        Err(TransactionError::CredentialIo)
    ));
    assert_eq!(store.writes.get(), 0);
}

#[test]
fn full_snapshot_limit_is_256_and_not_compact_three_account_limit() {
    let incoming: BTreeMap<_, _> = (1..=256)
        .map(|id| (format!("access_token_{id}@MXMOD"), Zeroizing::new(vec![42])))
        .collect();
    let store = Fake::default();
    let mut tx =
        CredentialTransaction::prepare(&store, RecoveryKeys::new(incoming).unwrap()).unwrap();
    tx.apply().unwrap();
    assert_eq!(store.values.borrow().len(), 256);
    tx.rollback().unwrap();
    assert!(store.values.borrow().is_empty());
    let too_many = (1..=257)
        .map(|id| (format!("access_token_{id}@MXMOD"), Zeroizing::new(vec![])))
        .collect();
    assert!(matches!(
        RecoveryKeys::new(too_many),
        Err(TransactionError::InvalidPayload)
    ));
}

#[test]
fn disjoint_full_snapshots_allow_512_changes_and_rollback() {
    let store = Fake::default();
    for id in 257..=512 {
        store.put(&format!("access_token_{id}@MXMOD"), b"old");
    }
    let original = store.values.borrow().clone();
    let incoming = (1..=256)
        .map(|id| (format!("access_token_{id}@MXMOD"), Zeroizing::new(vec![42])))
        .collect();
    let mut tx =
        CredentialTransaction::prepare(&store, RecoveryKeys::new(incoming).unwrap()).unwrap();
    assert_eq!(tx.change_count(), 512);
    tx.apply().unwrap();
    assert_eq!(store.values.borrow().len(), 256);
    tx.rollback().unwrap();
    assert_eq!(*store.values.borrow(), original);
}

#[test]
fn oversize_blobs_rejected_on_input_and_current_store() {
    assert!(matches!(
        RecoveryKeys::new(BTreeMap::from([(
            A.to_owned(),
            Zeroizing::new(vec![0; MAX_BLOB + 1])
        )])),
        Err(TransactionError::InvalidPayload)
    ));
    let store = Fake::new(&[(A, &vec![0; MAX_BLOB + 1])]);
    assert!(matches!(
        CredentialTransaction::prepare(&store, keys(&[])),
        Err(TransactionError::InvalidPayload)
    ));
    assert_eq!(store.writes.get(), 0);
}

#[test]
fn empty_and_identical_recovery_need_no_writes() {
    for values in [&[][..], OLD] {
        let store = Fake::new(values);
        let mut tx = CredentialTransaction::prepare(&store, keys(values)).unwrap();
        assert_eq!(tx.change_count(), 0);
        tx.apply().unwrap();
        tx.commit().unwrap();
        assert_eq!(store.writes.get(), 0);
        assert_values(&store, values);
    }
}
