//! The shared adapter runs against fake, owned C-layout allocations exclusively.
//! No WindowsCredentials method or real credential API is invoked by these tests.
use super::*;
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
};

const OWN: &str = "history_storage_key_42@MXMOD";
fn bytes() -> Zeroizing<Vec<u8>> {
    Zeroizing::new(vec![0x17, 0x03, 0x55, 0x80])
}
fn raw(target: *mut u16, blob: *mut u8, size: u32, kind: u32) -> RawCredential {
    RawCredential {
        flags: 0,
        kind,
        target_name: target,
        comment: ptr::null_mut(),
        last_written: FileTime::default(),
        blob_size: size,
        blob,
        persist: LOCAL_MACHINE,
        attribute_count: 0,
        attributes: ptr::null_mut(),
        target_alias: ptr::null_mut(),
        username: ptr::null_mut(),
    }
}
struct Record {
    entry: Box<RawCredential>,
    _target: Vec<u16>,
    blob: Zeroizing<Vec<u8>>,
}
impl Record {
    fn new(name: &str, blob: &[u8], kind: u32) -> Self {
        let mut target: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let mut copy = Zeroizing::new(blob.to_vec());
        let entry = Box::new(raw(
            target.as_mut_ptr(),
            if copy.is_empty() {
                ptr::null_mut()
            } else {
                copy.as_mut_ptr()
            },
            copy.len() as u32,
            kind,
        ));
        Self {
            entry,
            _target: target,
            blob: copy,
        }
    }
}
enum Block {
    Read(Record),
    Enumeration {
        _records: Vec<Record>,
        _table: Vec<*mut RawCredential>,
    },
}
#[derive(Clone, Copy)]
enum ReadFault {
    None,
    Status(u32),
    Null,
    WrongType,
    WrongTarget,
    NullName,
    InvalidUtf16,
    NullBlob,
    Oversized,
}
#[derive(Clone, Copy)]
enum EnumFault {
    None,
    Status(u32),
    Null,
    Count,
    NullEntry,
    NullName,
    InvalidUtf16,
}
struct FakeApi {
    values: RefCell<BTreeMap<String, Zeroizing<Vec<u8>>>>,
    names: RefCell<Vec<(String, u32)>>,
    allocations: RefCell<BTreeMap<usize, Block>>,
    calls: RefCell<Vec<&'static str>>,
    read_fault: Cell<ReadFault>,
    enum_fault: Cell<EnumFault>,
    write_status: Cell<u32>,
    delete_status: Cell<u32>,
    read_wipes: RefCell<Vec<bool>>,
    enum_frees: Cell<usize>,
    write_wipes: RefCell<Vec<bool>>,
    caller_input: Cell<usize>,
    separate_copy: Cell<bool>,
    write_layout: Cell<bool>,
    exact_filter: Cell<bool>,
}
impl Default for FakeApi {
    fn default() -> Self {
        Self {
            values: Default::default(),
            names: Default::default(),
            allocations: Default::default(),
            calls: Default::default(),
            read_fault: Cell::new(ReadFault::None),
            enum_fault: Cell::new(EnumFault::None),
            write_status: Cell::new(0),
            delete_status: Cell::new(0),
            read_wipes: Default::default(),
            enum_frees: Cell::new(0),
            write_wipes: Default::default(),
            caller_input: Cell::new(0),
            separate_copy: Cell::new(false),
            write_layout: Cell::new(false),
            exact_filter: Cell::new(false),
        }
    }
}
impl Drop for FakeApi {
    fn drop(&mut self) {
        assert!(
            self.allocations.get_mut().is_empty(),
            "all API allocations must be freed"
        );
    }
}
impl FakeApi {
    fn insert(&self, name: &str, value: &[u8]) {
        self.values
            .borrow_mut()
            .insert(name.into(), Zeroizing::new(value.to_vec()));
    }
}
// Every pointer below refers to a live fake-owned buffer, except deliberately
// unreadable enumeration blob pointers that the adapter must never dereference.
unsafe impl WinApi for FakeApi {
    unsafe fn enumerate(
        &self,
        filter: *const u16,
        count: *mut u32,
        out: *mut *mut *mut RawCredential,
    ) -> u32 {
        self.calls.borrow_mut().push("enumerate");
        self.exact_filter.set(filter.is_null());
        if let EnumFault::Status(status) = self.enum_fault.get() {
            return status;
        }
        if matches!(self.enum_fault.get(), EnumFault::Null) {
            unsafe {
                *count = 1;
                *out = ptr::null_mut();
            }
            return 0;
        }
        let mut records: Vec<_> = self
            .names
            .borrow()
            .iter()
            .map(|(name, kind)| {
                let mut record = Record::new(name, &[], *kind);
                record.entry.blob = 1usize as *mut u8;
                record.entry.blob_size = u32::MAX;
                if *kind != GENERIC {
                    // Even its name must not be dereferenced for non-generic
                    // entries; only generic target metadata is relevant.
                    record.entry.target_name = 1usize as *mut u16;
                } else if matches!(self.enum_fault.get(), EnumFault::NullName) {
                    record.entry.target_name = ptr::null_mut();
                } else if matches!(self.enum_fault.get(), EnumFault::InvalidUtf16) {
                    record._target[0] = 0xd800;
                }
                record
            })
            .collect();
        let mut table: Vec<_> = records
            .iter_mut()
            .map(|r| r.entry.as_mut() as *mut RawCredential)
            .collect();
        if matches!(self.enum_fault.get(), EnumFault::NullEntry) {
            table.push(ptr::null_mut());
        }
        if table.is_empty() {
            table.push(ptr::null_mut());
        }
        let actual = if matches!(self.enum_fault.get(), EnumFault::Count) {
            MAX_ENUM_ENTRIES + 1
        } else if matches!(self.enum_fault.get(), EnumFault::NullEntry) {
            records.len() + 1
        } else {
            records.len()
        };
        let pointer = table.as_mut_ptr();
        self.allocations.borrow_mut().insert(
            pointer as usize,
            Block::Enumeration {
                _records: records,
                _table: table,
            },
        );
        unsafe {
            *count = actual as u32;
            *out = pointer;
        }
        0
    }
    unsafe fn read(&self, name: *const u16, out: *mut *mut RawCredential) -> u32 {
        self.calls.borrow_mut().push("read");
        if let ReadFault::Status(status) = self.read_fault.get() {
            return status;
        }
        if matches!(self.read_fault.get(), ReadFault::Null) {
            unsafe {
                *out = ptr::null_mut();
            }
            return 0;
        }
        let name = unsafe { target(name) }.unwrap();
        let values = self.values.borrow();
        // Model Windows case-insensitive lookup while retaining the actual
        // stored target spelling in the returned allocation.
        let Some((stored_name, value)) = values
            .iter()
            .find(|(key, _)| key.eq_ignore_ascii_case(&name))
        else {
            return NOT_FOUND;
        };
        let mut record = Record::new(stored_name, value, GENERIC);
        match self.read_fault.get() {
            ReadFault::WrongType => record.entry.kind = 2,
            ReadFault::WrongTarget => {
                record._target[0] = u16::from(b'X');
            }
            ReadFault::NullName => record.entry.target_name = ptr::null_mut(),
            ReadFault::InvalidUtf16 => record._target[0] = 0xd800,
            ReadFault::NullBlob => record.entry.blob = ptr::null_mut(),
            ReadFault::Oversized => record.entry.blob_size = (MAX_BLOB + 1) as u32,
            _ => {}
        }
        let pointer = record.entry.as_mut() as *mut RawCredential;
        self.allocations
            .borrow_mut()
            .insert(pointer as usize, Block::Read(record));
        unsafe {
            *out = pointer;
        }
        0
    }
    unsafe fn write(&self, entry: *const RawCredential) -> u32 {
        self.calls.borrow_mut().push("write");
        let entry = unsafe { &*entry };
        let name = unsafe { target(entry.target_name) }.unwrap();
        self.write_layout.set(
            entry.flags == 0
                && entry.kind == GENERIC
                && entry.persist == LOCAL_MACHINE
                && entry.comment.is_null()
                && entry.last_written.low == 0
                && entry.last_written.high == 0
                && entry.attribute_count == 0
                && entry.attributes.is_null()
                && entry.target_alias.is_null()
                && !entry.username.is_null()
                && unsafe { *entry.username } == 0,
        );
        self.separate_copy
            .set(entry.blob_size == 0 || entry.blob as usize != self.caller_input.get());
        if self.write_status.get() != 0 {
            return self.write_status.get();
        }
        let blob = if entry.blob_size == 0 {
            &[][..]
        } else {
            unsafe { std::slice::from_raw_parts(entry.blob, entry.blob_size as usize) }
        };
        self.insert(&name, blob);
        0
    }
    unsafe fn delete(&self, name: *const u16) -> u32 {
        self.calls.borrow_mut().push("delete");
        if self.delete_status.get() != 0 {
            return self.delete_status.get();
        }
        let name = unsafe { target(name) }.unwrap();
        self.values.borrow_mut().remove(&name);
        0
    }
    unsafe fn free(&self, pointer: *mut c_void) {
        self.calls.borrow_mut().push("free");
        let block = self
            .allocations
            .borrow_mut()
            .remove(&(pointer as usize))
            .expect("only owned pointer freed once");
        match &block {
            Block::Read(record) => self
                .read_wipes
                .borrow_mut()
                .push(record.blob.iter().all(|b| *b == 0)),
            Block::Enumeration { .. } => self.enum_frees.set(self.enum_frees.get() + 1),
        }
        drop(block);
    }
    fn write_copy_wiped(&self, bytes: &[u8]) {
        self.write_wipes
            .borrow_mut()
            .push(bytes.iter().all(|b| *b == 0));
    }
}

#[test]
fn complete_policy_and_blob_limits_precede_the_native_boundary() {
    let adapter = Adapter(FakeApi::default());
    for name in [
        "",
        "access_token_0@MXMOD",
        "history_storage_key_01@MXMOD",
        "calls_token_-1@MXMOD",
        "calls_session_9223372036854775808@MXMOD",
        "ACCESS_TOKEN_42@MXMOD",
        "access_token_42@mxmod",
        "access_token_42@ONEME",
        "app_data_storage_key_common@MXMOD",
        "other_Common@MXMOD",
        "web-42_42@MXMOD",
        "web-42_01@MXMOD",
        "web-42_9223372036854775808@MXMOD",
        "é-42_0@MXMOD",
        "web\n-42_0@MXMOD",
        "access_token_42@MXMOD\0",
    ] {
        assert!(matches!(
            adapter.read(name),
            Err(CredentialError::InvalidPayload)
        ));
        assert!(matches!(
            adapter.set_owned(name, &[], None),
            Err(CredentialError::InvalidPayload)
        ));
        assert!(matches!(
            adapter.delete_owned(name, &[]),
            Err(CredentialError::InvalidPayload)
        ));
    }
    let over = Zeroizing::new(vec![0x31; MAX_BLOB + 1]);
    assert!(matches!(
        adapter.set_owned(OWN, &over, None),
        Err(CredentialError::InvalidPayload)
    ));
    assert!(matches!(
        adapter.set_owned(OWN, &[], Some(&over)),
        Err(CredentialError::InvalidPayload)
    ));
    assert!(matches!(
        adapter.delete_owned(OWN, &over),
        Err(CredentialError::InvalidPayload)
    ));
    assert!(adapter.0.calls.borrow().is_empty());
    for name in [
        "access_token_9223372036854775807@MXMOD",
        OWN,
        "calls_token_42@MXMOD",
        "calls_session_42@MXMOD",
        "app_data_storage_key_Common@MXMOD",
        "multi_state_enabled_Common@MXMOD",
        "web-prefix-with-dashes-42_0@MXMOD",
        "Web-9223372036854775807_9223372036854775806@MXMOD",
    ] {
        let value = bytes();
        adapter.set_owned(name, &value, None).unwrap();
        assert!(adapter
            .read(name)
            .unwrap()
            .is_some_and(|b| bool::from(b.as_slice().ct_eq(value.as_slice()))));
        adapter.delete_owned(name, &value).unwrap();
    }
    assert!(adapter.0.values.borrow().is_empty());
}

#[test]
fn inventory_keeps_all_owned_kinds_and_never_dereferences_any_blob() {
    let adapter = Adapter(FakeApi::default());
    let own = [
        "access_token_10@MXMOD",
        "history_storage_key_9@MXMOD",
        "calls_token_9@MXMOD",
        "calls_session_9@MXMOD",
        "app_data_storage_key_Common@MXMOD",
        "multi_state_enabled_Common@MXMOD",
        "browser-client-9_1@MXMOD",
    ];
    let mut names: Vec<_> = own
        .iter()
        .map(|name| ((*name).to_owned(), GENERIC))
        .collect();
    names.extend([
        ("access_token_1@ONEME".into(), GENERIC),
        ("access_token_42@mxmod".into(), GENERIC), // Exact legacy suffix semantics.
        ("foreign_service".into(), GENERIC),
        ("ignored-non-generic@MXMOD".into(), 2),
    ]);
    *adapter.0.names.borrow_mut() = names;
    let mut expected: Vec<_> = own.iter().map(|s| (*s).to_owned()).collect();
    expected.sort();
    assert_eq!(adapter.owned_names().unwrap(), expected);
    assert!(adapter.0.exact_filter.get()); // NULL filter, all metadata only.
    assert_eq!(*adapter.0.calls.borrow(), vec!["enumerate", "free"]);
    assert_eq!(adapter.0.enum_frees.get(), 1);
    adapter.0.enum_fault.set(EnumFault::Status(NOT_FOUND));
    assert!(adapter.owned_names().unwrap().is_empty());
    adapter.0.enum_fault.set(EnumFault::Status(5));
    assert!(matches!(
        adapter.owned_names(),
        Err(CredentialError::CredentialIo)
    ));
    assert!(adapter.0.allocations.borrow().is_empty());
}

#[test]
fn inventory_rejects_unknown_owned_names_aliases_and_resource_overflow() {
    let adapter = Adapter(FakeApi::default());
    for name in [
        "unknown@MXMOD",
        "access_token_0@MXMOD",
        "ACCESS_TOKEN_42@MXMOD",
        "calls_token_9223372036854775808@MXMOD",
        "web-42_42@MXMOD",
    ] {
        *adapter.0.names.borrow_mut() = vec![(name.into(), GENERIC)];
        assert!(matches!(
            adapter.owned_names(),
            Err(CredentialError::InvalidPayload)
        ));
    }
    for names in [
        vec![OWN, OWN],
        vec!["browser-42_0@MXMOD", "BROWSER-42_0@MXMOD"],
    ] {
        *adapter.0.names.borrow_mut() = names.into_iter().map(|s| (s.into(), GENERIC)).collect();
        assert!(matches!(
            adapter.owned_names(),
            Err(CredentialError::InvalidPayload)
        ));
    }
    *adapter.0.names.borrow_mut() = (1..=256)
        .map(|id| (format!("calls_session_{id}@MXMOD"), GENERIC))
        .collect();
    assert_eq!(adapter.owned_names().unwrap().len(), 256);
    adapter
        .0
        .names
        .borrow_mut()
        .push(("app_data_storage_key_Common@MXMOD".into(), GENERIC));
    assert!(matches!(
        adapter.owned_names(),
        Err(CredentialError::InvalidPayload)
    ));
    *adapter.0.names.borrow_mut() = vec![(OWN.into(), GENERIC)];
    for fault in [
        EnumFault::Null,
        EnumFault::Count,
        EnumFault::NullEntry,
        EnumFault::NullName,
        EnumFault::InvalidUtf16,
    ] {
        adapter.0.enum_fault.set(fault);
        assert!(matches!(
            adapter.owned_names(),
            Err(CredentialError::CredentialIo)
        ));
    }
    adapter.0.enum_fault.set(EnumFault::None);
    *adapter.0.names.borrow_mut() = vec![("a".repeat(MAX_TARGET_UNITS + 1), GENERIC)];
    assert!(matches!(
        adapter.owned_names(),
        Err(CredentialError::CredentialIo)
    ));
    *adapter.0.names.borrow_mut() = (0..33)
        .map(|_| ("a".repeat(MAX_TARGET_UNITS), GENERIC))
        .collect();
    assert!(matches!(
        adapter.owned_names(),
        Err(CredentialError::CredentialIo)
    ));
    assert!(adapter.0.allocations.borrow().is_empty());
}

#[test]
fn exact_read_copies_wipes_frees_and_preserves_empty_vs_missing() {
    let adapter = Adapter(FakeApi::default());
    let value = Zeroizing::new(vec![0x52; MAX_BLOB]);
    adapter.0.insert(OWN, &value);
    let copy = adapter.read(OWN).unwrap().unwrap();
    assert!(bool::from(copy.as_slice().ct_eq(value.as_slice())));
    assert_eq!(*adapter.0.read_wipes.borrow(), vec![true]);
    adapter.0.insert(OWN, &[]);
    assert!(adapter.read(OWN).unwrap().is_some_and(|b| b.is_empty()));
    adapter.0.values.borrow_mut().clear();
    assert!(adapter.read(OWN).unwrap().is_none());
    adapter.0.read_fault.set(ReadFault::Status(5));
    assert!(matches!(
        adapter.read(OWN),
        Err(CredentialError::CredentialIo)
    ));
    assert!(adapter.0.allocations.borrow().is_empty());
}

#[test]
fn read_invalid_metadata_is_finite_and_only_valid_buffers_are_wiped() {
    let adapter = Adapter(FakeApi::default());
    adapter.0.insert(OWN, &bytes());
    for fault in [
        ReadFault::WrongType,
        ReadFault::WrongTarget,
        ReadFault::NullName,
        ReadFault::InvalidUtf16,
    ] {
        adapter.0.read_fault.set(fault);
        assert!(matches!(
            adapter.read(OWN),
            Err(CredentialError::CredentialIo)
        ));
        assert!(adapter.0.read_wipes.borrow().last().copied().unwrap());
    }
    for fault in [ReadFault::NullBlob, ReadFault::Oversized] {
        adapter.0.read_fault.set(fault);
        assert!(matches!(
            adapter.read(OWN),
            Err(CredentialError::CredentialIo)
        ));
        // Corrupt size/pointer cannot authorize an unsafe wipe. Fake owner
        // still owns and zeroizes its underlying storage when freeing it.
        assert!(!adapter.0.read_wipes.borrow().last().copied().unwrap());
    }
    adapter.0.read_fault.set(ReadFault::Null);
    assert!(matches!(
        adapter.read(OWN),
        Err(CredentialError::CredentialIo)
    ));
    assert!(adapter.0.allocations.borrow().is_empty());
}

#[test]
fn wincred_case_insensitive_aliases_cannot_be_overwritten_or_deleted() {
    for (actual, requested) in [
        ("access_token_42@mxmod", "access_token_42@MXMOD"),
        ("ACCESS_TOKEN_42@MXMOD", "access_token_42@MXMOD"),
        (
            "app_data_storage_key_common@MXMOD",
            "app_data_storage_key_Common@MXMOD",
        ),
        ("WEB-42_0@MXMOD", "web-42_0@MXMOD"),
    ] {
        let adapter = Adapter(FakeApi::default());
        let original = bytes();
        adapter.0.insert(actual, &original);
        assert!(matches!(
            adapter.read(requested),
            Err(CredentialError::CredentialIo)
        ));
        for expected in [None, Some(original.as_slice())] {
            assert!(matches!(
                adapter.set_owned(requested, &[], expected),
                Err(CredentialError::CredentialIo)
            ));
        }
        assert!(matches!(
            adapter.delete_owned(requested, &original),
            Err(CredentialError::CredentialIo)
        ));
        assert!(!adapter
            .0
            .calls
            .borrow()
            .iter()
            .any(|c| matches!(*c, "write" | "delete")));
        assert!(adapter.0.read_wipes.borrow().iter().all(|w| *w));
        assert!(bool::from(
            adapter.0.values.borrow()[actual]
                .as_slice()
                .ct_eq(original.as_slice())
        ));
    }
}

#[test]
fn expected_value_write_uses_zeroized_local_copy_and_restores_previous_empty() {
    let adapter = Adapter(FakeApi::default());
    let original = Zeroizing::new(vec![0x17; MAX_BLOB]);
    adapter.0.caller_input.set(original.as_ptr() as usize);
    adapter.set_owned(OWN, &original, None).unwrap();
    assert!(adapter.0.write_layout.get() && adapter.0.separate_copy.get());
    assert_eq!(*adapter.0.write_wipes.borrow(), vec![true]);
    assert!(original.iter().any(|b| *b != 0));
    let writes = adapter
        .0
        .calls
        .borrow()
        .iter()
        .filter(|c| **c == "write")
        .count();
    for expected in [None, Some(&[][..]), Some(&[1, 2, 3][..])] {
        assert!(matches!(
            adapter.set_owned(OWN, &[], expected),
            Err(CredentialError::DestinationNotEmpty)
        ));
    }
    assert_eq!(
        adapter
            .0
            .calls
            .borrow()
            .iter()
            .filter(|c| **c == "write")
            .count(),
        writes
    );
    adapter.set_owned(OWN, &[], Some(&original)).unwrap();
    assert!(adapter.read(OWN).unwrap().is_some_and(|b| b.is_empty()));
    assert!(matches!(
        adapter.set_owned(OWN, &original, None),
        Err(CredentialError::DestinationNotEmpty)
    ));
    adapter.set_owned(OWN, &original, Some(&[])).unwrap();
    adapter.0.write_status.set(5);
    assert!(matches!(
        adapter.set_owned(OWN, &bytes(), Some(&original)),
        Err(CredentialError::CredentialIo)
    ));
    assert!(adapter.0.write_wipes.borrow().iter().all(|w| *w));
    assert!(adapter
        .read(OWN)
        .unwrap()
        .is_some_and(|b| bool::from(b.as_slice().ct_eq(original.as_slice()))));
}

#[test]
fn expected_delete_distinguishes_pre_attempt_conflicts_from_ambiguous_native_failure() {
    let adapter = Adapter(FakeApi::default());
    let original = bytes();
    adapter.0.insert(OWN, &original);
    let snapshot = adapter.read(OWN).unwrap().unwrap();
    // Model an unrelated writer deleting a record after the coordinator's
    // snapshot but before its first mutation. This must not become our success.
    adapter.0.values.borrow_mut().remove(OWN);
    assert!(matches!(
        adapter.delete_owned(OWN, &snapshot),
        Err(CredentialError::DestinationNotEmpty)
    ));
    assert!(!adapter.0.calls.borrow().contains(&"delete"));
    adapter.0.insert(OWN, &original);
    assert!(matches!(
        adapter.delete_owned(OWN, &[1, 2, 3]),
        Err(CredentialError::DestinationNotEmpty)
    ));
    assert!(!adapter.0.calls.borrow().contains(&"delete"));
    adapter.0.delete_status.set(5);
    assert!(matches!(
        adapter.delete_owned(OWN, &original),
        Err(CredentialError::CredentialIo)
    ));
    adapter.0.delete_status.set(NOT_FOUND);
    assert!(matches!(
        adapter.delete_owned(OWN, &original),
        Err(CredentialError::CredentialIo)
    ));
    adapter.0.delete_status.set(0);
    adapter.delete_owned(OWN, &original).unwrap();
    assert!(adapter.read(OWN).unwrap().is_none());
    // Repeated deletion of an already-absent record is likewise a pre-attempt
    // conflict, never another successful deletion for the rollback ledger.
    let deletes = adapter
        .0
        .calls
        .borrow()
        .iter()
        .filter(|c| **c == "delete")
        .count();
    assert!(matches!(
        adapter.delete_owned(OWN, &original),
        Err(CredentialError::DestinationNotEmpty)
    ));
    assert_eq!(
        adapter
            .0
            .calls
            .borrow()
            .iter()
            .filter(|c| **c == "delete")
            .count(),
        deletes
    );
    adapter.0.insert(OWN, &[]);
    adapter.delete_owned(OWN, &[]).unwrap();
    assert!(adapter.read(OWN).unwrap().is_none());
    assert!(adapter.0.read_wipes.borrow().iter().all(|w| *w));
}

#[test]
fn x64_layout_and_error_display_contain_only_finite_metadata() {
    if cfg!(target_pointer_width = "64") {
        assert_eq!(std::mem::size_of::<RawCredential>(), 80);
        assert_eq!(std::mem::offset_of!(RawCredential, last_written), 24);
        assert_eq!(std::mem::offset_of!(RawCredential, persist), 48);
        assert_eq!(std::mem::offset_of!(RawCredential, attributes), 56);
        assert_eq!(std::mem::offset_of!(RawCredential, target_alias), 64);
    }
    assert_eq!(
        CredentialError::InvalidPayload.to_string(),
        "invalid_payload"
    );
    assert_eq!(CredentialError::CredentialIo.to_string(), "credential_io");
    assert_eq!(
        CredentialError::DestinationNotEmpty.to_string(),
        "destination_not_empty"
    );
}
