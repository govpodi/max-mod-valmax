use super::*;
use crate::{
    mdata_archive::{self, ArchiveKind, MDataPolicy},
    mdata_credentials::CredentialError,
    mdata_import::HostError,
    mdata_profile,
};
use serde_json::json;
use std::{
    cell::{Cell, RefCell},
    collections::BTreeMap,
    fs,
    io::{Cursor, Read},
    path::PathBuf,
    sync::OnceLock,
};
use zeroize::Zeroizing;

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-recovery-apply";
const PASSWORD: &str = "Synthetic-Recovery-Apply-123";
const KEY: &str = "access_token_42@MXMOD";
fn archive() -> &'static Vec<u8> {
    static DATA: OnceLock<Vec<u8>> = OnceLock::new();
    DATA.get_or_init(|| {
        let manifest = json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
            "credentials":[{"name":KEY,"blob":"U1lOVEhFVElD"}],"files":[{"path":"user42/settings","size":3}]});
        let credentials = mdata_profile::credential_policy();
        let policy = MDataPolicy { credential_policy:&credentials, file_path_validator:&mdata_profile::included,
            credential_name_validator:Some(&mdata_profile::allowed_name) };
        let mut output=Vec::new();
        mdata_archive::encrypt_stream(&manifest,PASSWORD,&mut |_| Ok(Box::new(Cursor::new(vec![1u8,2,3])) as Box<dyn Read>),
            &mut output,&policy,ArchiveKind::Profile).unwrap();
        output
    })
}
struct Fixture {
    parent: PathBuf,
    root: PathBuf,
    marker: RecoveryMarker,
}
impl Fixture {
    fn new(existing: bool) -> Self {
        let parent = PathBuf::from(BASE).join(crate::startup_status::new_operation_id().unwrap());
        let root = parent.join("MXMOD");
        let backup = parent
            .join(mdata_recovery_state::BACKUP_DIRECTORY)
            .join("synthetic.mdata");
        fs::create_dir_all(backup.parent().unwrap()).unwrap();
        fs::write(&backup, archive()).unwrap();
        if existing {
            fs::create_dir(&root).unwrap();
            fs::write(root.join("old"), b"OLD SYNTHETIC PROFILE").unwrap();
        }
        let marker = mdata_recovery_state::write(&root, &backup).unwrap();
        Self {
            parent,
            root,
            marker,
        }
    }
    fn prefixed(&self, prefix: &str) -> Vec<PathBuf> {
        fs::read_dir(&self.parent)
            .unwrap()
            .map(|e| e.unwrap().path())
            .filter(|p| p.file_name().unwrap().to_string_lossy().starts_with(prefix))
            .collect()
    }
    fn marker_intact(&self) {
        assert_eq!(
            mdata_recovery_state::read(&self.root).unwrap(),
            Some(self.marker.clone())
        );
    }
    fn run(&self, store: &Memory, host: &mut Host) -> Result<RecoveryResult> {
        execute_held(
            &self.root,
            &self.marker,
            PASSWORD,
            store,
            host,
            |root, marker| {
                mdata_recovery_state::clear(root, marker).map_err(|e| RecoveryError(e.code()))
            },
        )
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(BASE).unwrap();
        let own = fs::canonicalize(&self.parent).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
#[derive(Default)]
struct Memory {
    values: RefCell<BTreeMap<String, Vec<u8>>>,
    reads: Cell<usize>,
    writes: Cell<usize>,
    fail_write: Cell<Option<usize>>,
    after_first: RefCell<Option<Box<dyn FnOnce()>>>,
}
impl Memory {
    fn change(
        &self,
        name: &str,
        new: Option<&[u8]>,
        expected: Option<&[u8]>,
    ) -> crate::mdata_credentials::Result<()> {
        self.writes.set(self.writes.get() + 1);
        if self.fail_write.get() == Some(self.writes.get()) {
            return Err(CredentialError::CredentialIo);
        }
        let mut values = self.values.borrow_mut();
        if values.get(name).map(Vec::as_slice) != expected {
            return Err(CredentialError::DestinationNotEmpty);
        }
        if let Some(value) = new {
            values.insert(name.to_owned(), value.to_vec());
        } else {
            values.remove(name);
        }
        drop(values);
        if self.writes.get() == 1 {
            if let Some(callback) = self.after_first.borrow_mut().take() {
                callback();
            }
        }
        Ok(())
    }
}
impl RecoveryCredentials for Memory {
    fn owned_names(&self) -> crate::mdata_credentials::Result<Vec<String>> {
        Ok(self.values.borrow().keys().cloned().collect())
    }
    fn read(&self, name: &str) -> crate::mdata_credentials::Result<Option<Zeroizing<Vec<u8>>>> {
        self.reads.set(self.reads.get() + 1);
        Ok(self.values.borrow().get(name).cloned().map(Zeroizing::new))
    }
    fn set_owned(
        &self,
        name: &str,
        value: &[u8],
        expected: Option<&[u8]>,
    ) -> crate::mdata_credentials::Result<()> {
        self.change(name, Some(value), expected)
    }
    fn delete_owned(&self, name: &str, expected: &[u8]) -> crate::mdata_credentials::Result<()> {
        self.change(name, None, Some(expected))
    }
}
#[derive(Default)]
struct Host {
    checks: usize,
    wait_fail: bool,
    fail_check: Option<usize>,
    cancel: bool,
    cancels: usize,
}
impl ImportHost for Host {
    fn wait_closed(&mut self) -> std::result::Result<(), HostError> {
        if self.wait_fail {
            Err(HostError::ClientStillRunning)
        } else {
            Ok(())
        }
    }
    fn check_closed(&mut self) -> std::result::Result<(), HostError> {
        self.checks += 1;
        if self.fail_check == Some(self.checks) {
            Err(HostError::ProcessScanFailed)
        } else {
            Ok(())
        }
    }
    fn cancelled(&mut self) -> bool {
        self.cancels += 1;
        self.cancel
    }
}

#[test]
fn full_recovery_publishes_profile_and_keys_retains_backup_and_clears_exact_marker() {
    for existing in [false, true] {
        let f = Fixture::new(existing);
        let store = Memory::default();
        let mut host = Host::default();
        let result = f.run(&store, &mut host).unwrap();
        assert_eq!(
            result,
            RecoveryResult {
                schema: 1,
                result: "restored",
                files: 2
            }
        );
        assert_eq!(host.checks, 3);
        assert_eq!(host.cancels, 4);
        assert_eq!(fs::read(f.root.join("user42/settings")).unwrap(), [1, 2, 3]);
        let config: serde_json::Value =
            serde_json::from_slice(&fs::read(f.root.join("config.json")).unwrap()).unwrap();
        assert_eq!(config, crate::launcher_config::make_config().unwrap());
        assert_eq!(store.values.borrow().get(KEY).unwrap(), b"SYNTHETIC");
        assert!(mdata_recovery_state::read(&f.root).unwrap().is_none());
        assert_eq!(fs::read(&f.marker.backup_file).unwrap(), *archive());
        assert!(f.prefixed(".MXMOD-MData-stage-").is_empty());
        let old = f.prefixed("MXMOD-MData-before-");
        assert_eq!(old.len(), usize::from(existing));
        if existing {
            assert_eq!(
                fs::read(old[0].join("old")).unwrap(),
                b"OLD SYNTHETIC PROFILE"
            );
        }
    }
}

#[test]
fn admission_and_fresh_process_scan_failures_never_mutate_credentials_or_live_profile() {
    for check in 0..=4 {
        let f = Fixture::new(true);
        let store = Memory::default();
        let mut host = Host {
            cancel: check == 0,
            wait_fail: check == 4,
            fail_check: Some(check),
            ..Default::default()
        };
        assert!(f.run(&store, &mut host).is_err());
        assert_eq!(store.writes.get(), 0);
        assert_eq!(
            fs::read(f.root.join("old")).unwrap(),
            b"OLD SYNTHETIC PROFILE"
        );
        assert!(f.prefixed("MXMOD-MData-before-").is_empty());
        f.marker_intact();
    }
}

#[test]
fn wrong_password_leaves_no_stage_or_mutations() {
    let f = Fixture::new(true);
    let store = Memory::default();
    let result = execute_held(
        &f.root,
        &f.marker,
        "Wrong-Synthetic-Password",
        &store,
        &mut Host::default(),
        |_, _| panic!("must not clear"),
    );
    assert_eq!(result.unwrap_err().code(), "authentication_failed");
    assert_eq!(store.writes.get(), 0);
    assert!(f.prefixed(".MXMOD-MData-stage-").is_empty());
    f.marker_intact();
}

#[test]
fn marker_clear_failure_does_not_rollback_a_committed_recovery() {
    let f = Fixture::new(true);
    let store = Memory::default();
    let result = execute_held(
        &f.root,
        &f.marker,
        PASSWORD,
        &store,
        &mut Host::default(),
        |_, _| Err(RecoveryError("io_error")),
    );
    assert_eq!(result, Err(RecoveryError("recovery_required")));
    assert_eq!(fs::read(f.root.join("user42/settings")).unwrap(), [1, 2, 3]);
    assert_eq!(store.values.borrow().get(KEY).unwrap(), b"SYNTHETIC");
    assert_eq!(f.prefixed("MXMOD-MData-before-").len(), 1);
    f.marker_intact();
}

#[test]
fn a_foreign_destination_created_after_admission_is_preserved_and_keys_roll_back() {
    let f = Fixture::new(false);
    let store = Memory::default();
    let root = f.root.clone();
    *store.after_first.borrow_mut() = Some(Box::new(move || {
        fs::create_dir(&root).unwrap();
        fs::write(root.join("foreign"), b"UNRELATED").unwrap();
    }));
    assert!(f.run(&store, &mut Host::default()).is_err());
    assert!(store.values.borrow().is_empty());
    assert_eq!(fs::read(f.root.join("foreign")).unwrap(), b"UNRELATED");
    assert!(!f.root.join("config.json").exists());
    f.marker_intact();
}

#[test]
fn second_rename_failure_restores_old_root_even_when_credential_rollback_fails() {
    use std::rc::Rc;
    for fail_keys in [false, true] {
        let f = Fixture::new(true);
        let store = Memory::default();
        if fail_keys {
            store.fail_write.set(Some(2));
        }
        let held = Rc::new(RefCell::new(None));
        let capture = Rc::clone(&held);
        let parent = f.parent.clone();
        *store.after_first.borrow_mut() = Some(Box::new(move || {
            let stage = fs::read_dir(&parent)
                .unwrap()
                .map(|e| e.unwrap().path())
                .find(|p| {
                    p.file_name()
                        .unwrap()
                        .to_string_lossy()
                        .starts_with(".MXMOD-MData-stage-")
                })
                .unwrap();
            use std::os::windows::fs::OpenOptionsExt;
            *capture.borrow_mut() = Some(
                fs::OpenOptions::new()
                    .read(true)
                    .share_mode(1)
                    .open(stage.join("user42/settings"))
                    .unwrap(),
            );
        }));
        let result = f.run(&store, &mut Host::default());
        assert!(result.is_err());
        if fail_keys {
            assert_eq!(result.unwrap_err().code(), "rollback_failed");
        } else {
            assert!(store.values.borrow().is_empty());
        }
        assert_eq!(
            fs::read(f.root.join("old")).unwrap(),
            b"OLD SYNTHETIC PROFILE"
        );
        assert!(f.prefixed("MXMOD-MData-before-").is_empty());
        assert_eq!(f.prefixed(".MXMOD-MData-stage-").len(), 1);
        f.marker_intact();
        drop(held.borrow_mut().take());
    }
}

#[test]
fn shared_lease_lifetime_spans_work_and_busy_prevents_admission() {
    struct Lease<'a>(&'a Cell<bool>);
    impl Drop for Lease<'_> {
        fn drop(&mut self) {
            self.0.set(false);
        }
    }
    let active = Cell::new(true);
    let result = with_lease(Ok(Lease(&active)), || {
        assert!(active.get());
        Err(RecoveryError("synthetic_failure"))
    });
    assert_eq!(result, Err(RecoveryError("synthetic_failure")));
    assert!(!active.get());
    let result = with_lease::<()>(Err(LockError::Busy), || panic!("busy must not enter"));
    assert_eq!(result, Err(RecoveryError("busy")));
}
