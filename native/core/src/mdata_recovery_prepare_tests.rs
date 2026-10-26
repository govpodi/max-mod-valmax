use super::*;
use crate::{
    mdata_archive::{self, ArchiveKind, MDataError, MDataPolicy},
    mdata_profile, mdata_recovery_state,
};
use serde_json::json;
use std::{
    fs,
    io::{Cursor, Read},
    path::PathBuf,
    sync::OnceLock,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-recovery-prepare";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("mdata-recovery-prepare"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}
const PASSWORD: &str = "Synthetic-Recovery-Prepare-123";
struct Fixture {
    parent: PathBuf,
    root: PathBuf,
    backup: PathBuf,
    marker: RecoveryMarker,
}
impl Fixture {
    fn new(archive: &[u8], existing: bool) -> Self {
        let parent = fixture_base().join(crate::startup_status::new_operation_id().unwrap());
        let root = parent.join("MXMOD");
        let backup = parent
            .join(mdata_recovery_state::BACKUP_DIRECTORY)
            .join("synthetic.mdata");
        fs::create_dir_all(backup.parent().unwrap()).unwrap();
        fs::write(&backup, archive).unwrap();
        if existing {
            fs::create_dir(&root).unwrap();
            fs::write(root.join("sentinel"), b"SYNTHETIC UNCHANGED PROFILE").unwrap();
        }
        let marker = mdata_recovery_state::write(&root, &backup).unwrap();
        Self {
            parent,
            root,
            backup,
            marker,
        }
    }
    fn no_stage(&self) {
        assert!(!fs::read_dir(&self.parent).unwrap().any(|entry| {
            entry
                .unwrap()
                .file_name()
                .to_string_lossy()
                .starts_with(".MXMOD-MData-stage-")
        }));
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(fixture_base()).unwrap();
        let own = fs::canonicalize(&self.parent).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
fn error<T>(result: Result<T>) -> PrepareError {
    match result {
        Err(error) => error,
        Ok(_) => panic!("expected finite preparation error"),
    }
}
fn archive() -> &'static Vec<u8> {
    static ARCHIVE: OnceLock<Vec<u8>> = OnceLock::new();
    ARCHIVE.get_or_init(|| {
        let manifest = json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
            "credentials":[{"name":"access_token_42@MXMOD","blob":"U1lOVEhFVElD"}],"files":[{"path":"user42/settings","size":3}]});
        let credentials = mdata_profile::credential_policy();
        let policy = MDataPolicy {
            credential_policy: &credentials,
            file_path_validator: &mdata_profile::included,
            credential_name_validator: Some(&mdata_profile::allowed_name),
        };
        let mut result = Vec::new();
        mdata_archive::encrypt_stream(
            &manifest,
            PASSWORD,
            &mut |_| Ok(Box::new(Cursor::new(vec![1u8, 2, 3])) as Box<dyn Read>),
            &mut result,
            &policy,
            ArchiveKind::Profile,
        )
        .unwrap();
        result
    })
}

#[test]
fn pinned_backup_to_prepared_config_preserves_source_marker_and_live_profile() {
    let f = Fixture::new(archive(), true);
    let mut prepared = prepare(&f.root, &f.marker, PASSWORD).unwrap();
    assert_eq!(prepared.file_count(), 2);
    assert_eq!(prepared.credentials().unwrap().len(), 1);
    prepared.revalidate().unwrap();
    assert!(fs::OpenOptions::new().write(true).open(&f.backup).is_err());
    assert!(fs::rename(&f.backup, f.backup.with_extension("moved")).is_err());
    assert!(fs::read(f.root.join("sentinel")).unwrap() == b"SYNTHETIC UNCHANGED PROFILE");
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 1);
    assert_eq!(
        mdata_recovery_state::read(&f.root).unwrap(),
        Some(f.marker.clone())
    );
    prepared.discard().unwrap();
    assert!(prepared.credentials().is_err());
    assert_eq!(
        prepared.revalidate(),
        Err(PrepareError::Stage(StageError::InvalidRequest))
    );
    drop(prepared);
    assert!(fs::read(&f.backup).unwrap() == *archive());
    f.no_stage();
}

#[test]
fn interrupted_recovery_with_missing_mxmod_can_prepare_without_creating_it() {
    let f = Fixture::new(archive(), false);
    let prepared = prepare(&f.root, &f.marker, PASSWORD).unwrap();
    prepared.revalidate().unwrap();
    assert!(!f.root.exists());
    drop(prepared);
    assert!(!f.root.exists());
    f.no_stage();
}

#[test]
fn foreign_marker_and_wrong_password_fail_without_changing_recovery_files() {
    let f = Fixture::new(archive(), true);
    let mut foreign = f.marker.clone();
    foreign.operation_id = crate::startup_status::new_operation_id().unwrap();
    assert!(matches!(
        error(prepare(&f.root, &foreign, PASSWORD)),
        PrepareError::Source(_)
    ));
    assert_eq!(
        error(prepare(&f.root, &f.marker, "Wrong-Synthetic-Password")),
        PrepareError::Stage(StageError::Archive(MDataError::AuthenticationFailed))
    );
    f.no_stage();
    assert_eq!(
        mdata_recovery_state::read(&f.root).unwrap(),
        Some(f.marker.clone())
    );
    assert!(fs::read(&f.backup).unwrap() == *archive());
}

#[test]
fn marker_change_after_preparation_invalidates_result_but_never_erases_marker() {
    let f = Fixture::new(archive(), true);
    let prepared = prepare(&f.root, &f.marker, PASSWORD).unwrap();
    let mut changed = f.marker.clone();
    changed.operation_id = crate::startup_status::new_operation_id().unwrap();
    let marker_path = f.parent.join(mdata_recovery_state::FILENAME);
    fs::write(&marker_path, serde_json::to_vec(&changed).unwrap()).unwrap();
    assert!(matches!(
        prepared.revalidate(),
        Err(PrepareError::Source(_))
    ));
    drop(prepared);
    f.no_stage();
    assert_eq!(mdata_recovery_state::read(&f.root).unwrap(), Some(changed));
}

#[test]
fn damaged_authenticated_terminal_leaves_no_prepared_output() {
    let mut bytes = archive().clone();
    *bytes.last_mut().unwrap() ^= 1;
    let f = Fixture::new(&bytes, false);
    assert_eq!(
        error(prepare(&f.root, &f.marker, PASSWORD)),
        PrepareError::Stage(StageError::Archive(MDataError::AuthenticationFailed))
    );
    f.no_stage();
    assert!(fs::read(&f.backup).unwrap() == bytes);
    assert!(mdata_recovery_state::read(&f.root).unwrap().is_some());
}

#[test]
fn authenticated_backup_keys_apply_and_rollback_using_only_an_in_memory_store() {
    use crate::mdata_credential_transaction::CredentialTransaction;
    use crate::mdata_credentials::{CredentialError, RecoveryCredentials};
    use std::{cell::RefCell, collections::BTreeMap};
    use zeroize::Zeroizing;
    struct Memory(RefCell<BTreeMap<String, Vec<u8>>>);
    impl RecoveryCredentials for Memory {
        fn owned_names(&self) -> crate::mdata_credentials::Result<Vec<String>> {
            Ok(self.0.borrow().keys().cloned().collect())
        }
        fn read(&self, name: &str) -> crate::mdata_credentials::Result<Option<Zeroizing<Vec<u8>>>> {
            Ok(self.0.borrow().get(name).cloned().map(Zeroizing::new))
        }
        fn set_owned(
            &self,
            name: &str,
            value: &[u8],
            expected: Option<&[u8]>,
        ) -> crate::mdata_credentials::Result<()> {
            let mut values = self.0.borrow_mut();
            if values.get(name).map(Vec::as_slice) != expected {
                return Err(CredentialError::DestinationNotEmpty);
            }
            values.insert(name.to_owned(), value.to_vec());
            Ok(())
        }
        fn delete_owned(
            &self,
            name: &str,
            expected: &[u8],
        ) -> crate::mdata_credentials::Result<()> {
            let mut values = self.0.borrow_mut();
            if values.get(name).map(Vec::as_slice) != Some(expected) {
                return Err(CredentialError::DestinationNotEmpty);
            }
            values.remove(name);
            Ok(())
        }
    }
    let f = Fixture::new(archive(), true);
    let prepared = prepare(&f.root, &f.marker, PASSWORD).unwrap();
    let store = Memory(RefCell::new(BTreeMap::from([(
        "access_token_99@MXMOD".to_owned(),
        b"OLD SYNTHETIC".to_vec(),
    )])));
    let mut tx = CredentialTransaction::prepare(&store, prepared.credentials().unwrap()).unwrap();
    tx.apply().unwrap();
    assert_eq!(store.0.borrow().len(), 1);
    assert_eq!(
        store.0.borrow().get("access_token_42@MXMOD").unwrap(),
        b"SYNTHETIC"
    );
    tx.rollback().unwrap();
    assert_eq!(
        *store.0.borrow(),
        BTreeMap::from([(
            "access_token_99@MXMOD".to_owned(),
            b"OLD SYNTHETIC".to_vec()
        )])
    );
    assert_eq!(
        mdata_recovery_state::read(&f.root).unwrap(),
        Some(f.marker.clone())
    );
    assert_eq!(
        fs::read(f.root.join("sentinel")).unwrap(),
        b"SYNTHETIC UNCHANGED PROFILE"
    );
    drop(tx);
    drop(prepared);
    f.no_stage();
}
