use super::*;
use crate::{
    mdata_archive::{self, ArchiveKind, MDataPolicy},
    mdata_credentials::CredentialError,
    mdata_profile,
};
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    fs,
    io::{Cursor, Read},
    path::PathBuf,
    sync::OnceLock,
};
use zeroize::Zeroizing;

const BASE: &str = "C:/ValMAXBuild/fixtures/transfer-recovery-ffi";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("transfer-recovery-ffi"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}
const PASSWORD: &str = "Synthetic-Recovery-FFI-123";
const KEY: &str = "access_token_42@MXMOD";
struct Fixture {
    local: PathBuf,
    root: PathBuf,
    marker: RecoveryMarker,
}
impl Fixture {
    fn new() -> Self {
        static ARCHIVE: OnceLock<Vec<u8>> = OnceLock::new();
        let bytes = ARCHIVE.get_or_init(|| {
            let manifest = json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
                "credentials":[{"name":KEY,"blob":"U1lOVEhFVElD"}],"files":[{"path":"user42/settings","size":3}]});
            let credentials = mdata_profile::credential_policy();
            let policy = MDataPolicy { credential_policy:&credentials, file_path_validator:&mdata_profile::included,
                credential_name_validator:Some(&mdata_profile::allowed_name) };
            let mut output=Vec::new();
            mdata_archive::encrypt_stream(&manifest,PASSWORD,&mut |_| Ok(Box::new(Cursor::new(vec![1u8,2,3])) as Box<dyn Read>),
                &mut output,&policy,ArchiveKind::Profile).unwrap();
            output
        });
        let local = fixture_base().join(crate::startup_status::new_operation_id().unwrap());
        let root = local.join("MXMOD");
        let backup = local
            .join(mdata_recovery_state::BACKUP_DIRECTORY)
            .join("synthetic.mdata");
        fs::create_dir_all(backup.parent().unwrap()).unwrap();
        fs::write(&backup, bytes).unwrap();
        fs::create_dir(&root).unwrap();
        fs::write(root.join("old"), b"OLD SYNTHETIC PROFILE").unwrap();
        let marker = mdata_recovery_state::write(&root, &backup).unwrap();
        Self {
            local,
            root,
            marker,
        }
    }
    fn local(&self) -> &[u8] {
        self.local.to_str().unwrap().as_bytes()
    }
    fn request(&self, password: &str) -> Vec<u8> {
        serde_json::to_vec(
            &json!({"password":password,"fingerprint":marker_fingerprint(&self.marker).unwrap()}),
        )
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(fixture_base()).unwrap();
        let own = fs::canonicalize(&self.local).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
#[derive(Default)]
struct Memory {
    values: RefCell<BTreeMap<String, Vec<u8>>>,
    reads: Cell<usize>,
}
impl RecoveryCredentials for Memory {
    fn owned_names(&self) -> crate::mdata_credentials::Result<Vec<String>> {
        self.reads.set(self.reads.get() + 1);
        Ok(self.values.borrow().keys().cloned().collect())
    }
    fn read(&self, name: &str) -> crate::mdata_credentials::Result<Option<Zeroizing<Vec<u8>>>> {
        self.reads.set(self.reads.get() + 1);
        Ok(self.values.borrow().get(name).cloned().map(Zeroizing::new))
    }
    fn set_owned(
        &self,
        name: &str,
        blob: &[u8],
        expected: Option<&[u8]>,
    ) -> crate::mdata_credentials::Result<()> {
        let mut values = self.values.borrow_mut();
        if values.get(name).map(Vec::as_slice) != expected {
            return Err(CredentialError::DestinationNotEmpty);
        }
        values.insert(name.into(), blob.into());
        Ok(())
    }
    fn delete_owned(&self, name: &str, expected: &[u8]) -> crate::mdata_credentials::Result<()> {
        let mut values = self.values.borrow_mut();
        if values.get(name).map(Vec::as_slice) != Some(expected) {
            return Err(CredentialError::DestinationNotEmpty);
        }
        values.remove(name);
        Ok(())
    }
}
#[derive(Default)]
struct Calls {
    calls: usize,
    closed: u32,
    cancel: u32,
}
unsafe extern "C" fn closed(ctx: *mut c_void) -> u32 {
    let c = unsafe { &mut *ctx.cast::<Calls>() };
    c.calls += 1;
    c.closed
}
unsafe extern "C" fn cancelled(ctx: *mut c_void) -> u32 {
    let c = unsafe { &mut *ctx.cast::<Calls>() };
    c.calls += 1;
    c.cancel
}
fn host(calls: &mut Calls) -> TransferHost {
    TransferHost {
        size: 40,
        version: 1,
        context: (calls as *mut Calls).cast(),
        wait_closed: Some(closed),
        check_closed: Some(closed),
        cancelled: Some(cancelled),
    }
}
fn buffer() -> CoreBuffer {
    CoreBuffer {
        data: ptr::null_mut(),
        len: 0,
        capacity: 0,
    }
}
unsafe fn take(out: &mut CoreBuffer) -> Value {
    let result =
        serde_json::from_slice(unsafe { slice::from_raw_parts(out.data, out.len) }).unwrap();
    unsafe {
        crate::ffi::valmax_core_buffer_free(out);
    }
    result
}

#[test]
fn recovery_abi_invalid_and_exact_schema_fail_before_callbacks() {
    let mut out = buffer();
    let mut calls = Calls::default();
    let host = host(&mut calls);
    unsafe {
        assert_eq!(valmax_transfer_recovery_status(ptr::null(), 1, &mut out), 1);
        assert_eq!(
            valmax_transfer_recover(
                b"C:/own".as_ptr(),
                6,
                b"{}".as_ptr(),
                2,
                ptr::null(),
                &mut out
            ),
            1
        );
        let bad = TransferHost { version: 2, ..host };
        assert_eq!(
            valmax_transfer_recover(b"C:/own".as_ptr(), 6, b"{}".as_ptr(), 2, &bad, &mut out),
            1
        );
        assert!(out.data.is_null());
        for value in [
            b"{}".as_slice(),
            br#"{"password":"abcdefghijkl","fingerprint":"x","closed":true}"#,
            br#"{"password":"a","password":"b","fingerprint":"x"}"#,
            b"\xff",
        ] {
            assert_eq!(
                valmax_transfer_recover(
                    b"C:/own".as_ptr(),
                    6,
                    value.as_ptr(),
                    value.len(),
                    &host,
                    &mut out
                ),
                0
            );
            assert_eq!(take(&mut out), error("invalid_request"));
        }
    }
    assert_eq!(calls.calls, 0);
}
#[test]
fn recovery_status_outputs_no_paths_and_stale_marker_cannot_apply() {
    let fixture = Fixture::new();
    let mut out = buffer();
    unsafe {
        assert_eq!(
            valmax_transfer_recovery_status(
                fixture.local().as_ptr(),
                fixture.local().len(),
                &mut out
            ),
            0
        );
    }
    let status = unsafe { take(&mut out) };
    assert_eq!(
        status,
        json!({"schema":1,"ok":true,"result":{"required":true,"fingerprint":marker_fingerprint(&fixture.marker).unwrap()}})
    );
    let text = status.to_string();
    assert!(!text.contains("MXMOD"));
    assert!(!text.contains("backup"));
    assert!(!text.contains(KEY));
    let mut calls = Calls::default();
    let mut host = host(&mut calls);
    let memory = Memory::default();
    let request =
        serde_json::to_vec(&json!({"password":PASSWORD,"fingerprint":"0".repeat(64)})).unwrap();
    assert_eq!(
        recover(fixture.local(), &request, &mut host, &memory),
        error("source_changed")
    );
    assert_eq!(calls.calls, 0);
    assert_eq!(memory.reads.get(), 0);
    mdata_recovery_state::clear(&fixture.root, &fixture.marker).unwrap();
    assert_eq!(
        recovery_status(fixture.local()),
        json!({"schema":1,"ok":true,"result":{"required":false,"fingerprint":""}})
    );
    assert_eq!(
        recover(
            fixture.local(),
            &fixture.request(PASSWORD),
            &mut host,
            &memory
        ),
        error("recovery_not_required")
    );
    fs::write(fixture.local.join(mdata_recovery_state::FILENAME), b"{").unwrap();
    assert_eq!(recovery_status(fixture.local()), error("recovery_required"));
}
#[test]
fn recovery_glue_invokes_real_coordinator_with_memory_credentials_only() {
    let fixture = Fixture::new();
    let memory = Memory::default();
    let mut calls = Calls::default();
    let mut host = host(&mut calls);
    assert_eq!(
        recover(
            fixture.local(),
            &fixture.request("synthetic-wrong-password"),
            &mut host,
            &memory
        ),
        error("authentication_failed")
    );
    assert_eq!(memory.reads.get(), 0);
    assert!(fixture.root.join("old").is_file());
    let result = recover(
        fixture.local(),
        &fixture.request(PASSWORD),
        &mut host,
        &memory,
    );
    assert_eq!(
        result,
        json!({"schema":1,"ok":true,"result":{"schema":1,"result":"restored","files":2}})
    );
    assert_eq!(
        fs::read(fixture.root.join("user42/settings")).unwrap(),
        vec![1, 2, 3]
    );
    assert!(!fixture.root.join("old").exists());
    assert_eq!(memory.values.borrow().get(KEY).unwrap(), b"SYNTHETIC");
    assert!(mdata_recovery_state::read(&fixture.root).unwrap().is_none());
    assert!(calls.calls > 0);
    assert!(!result.to_string().contains(KEY));
}
#[test]
fn recovery_closed_scan_and_cancel_are_native_preconditions() {
    let fixture = Fixture::new();
    let memory = Memory::default();
    for (closed_code, cancel_code, error_code) in [
        (1, 0, "client_still_running"),
        (2, 0, "process_scan_failed"),
        (0, 1, "cancelled"),
    ] {
        let mut calls = Calls {
            closed: closed_code,
            cancel: cancel_code,
            ..Calls::default()
        };
        let mut host = host(&mut calls);
        assert_eq!(
            recover(
                fixture.local(),
                &fixture.request(PASSWORD),
                &mut host,
                &memory
            ),
            error(error_code)
        );
        assert_eq!(memory.reads.get(), 0);
        assert!(fixture.root.join("old").is_file());
        assert_eq!(
            mdata_recovery_state::read(&fixture.root).unwrap(),
            Some(fixture.marker.clone())
        );
    }
}
