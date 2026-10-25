//! Actual encrypted archive + filesystem transaction, synthetic credentials only.
use super::*;
use crate::{
    mdata_archive::{self as archive, ArchiveKind, MDataPolicy},
    mdata_import_archive,
    mdata_login_storage::LoginStorage,
    session_archive::ArchivePolicy,
};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Cursor, Read},
    path::PathBuf,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/login-import-end-to-end";
const PASSWORD: &str = "synthetic compact import password";
struct Fixture {
    base: PathBuf,
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let name: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let base = PathBuf::from(BASE).join(name);
        let root = base.join("MXMOD");
        fs::create_dir_all(&root).unwrap();
        Self { base, root }
    }
    fn archive(&self, manifest: &Value, auth: &AuthFiles) -> mdata_import_archive::DecodedLogin {
        let defaults = ArchivePolicy::default();
        let file_policy = |name: &str| login::auth_id(name).is_some();
        let token_policy = |name: &str| login::token_id(name).is_some();
        let policy = MDataPolicy {
            credential_policy: &defaults,
            file_path_validator: &file_policy,
            credential_name_validator: Some(&token_policy),
        };
        let mut source = |name: &str| -> std::result::Result<Box<dyn Read>, archive::MDataError> {
            Ok(Box::new(Cursor::new(auth[name].to_vec())))
        };
        let mut encrypted = Vec::new();
        archive::encrypt_stream(
            manifest,
            PASSWORD,
            &mut source,
            &mut encrypted,
            &policy,
            ArchiveKind::Login,
        )
        .unwrap();
        let path = self.base.join("selected.mdata");
        fs::write(&path, &encrypted).unwrap();
        let url = format!("file:///{}", path.to_str().unwrap().replace('\\', "/"));
        let fingerprint = format!("{:x}", Sha256::digest(&encrypted));
        mdata_import_archive::load(&url, PASSWORD, &fingerprint).unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(BASE).unwrap();
        let own = fs::canonicalize(&self.base).unwrap();
        let name = self.base.file_name().unwrap().to_str().unwrap();
        assert!(
            own.starts_with(base)
                && name.len() == 32
                && name.bytes().all(|b| b.is_ascii_hexdigit())
        );
        fs::remove_dir_all(own).unwrap();
    }
}

#[test]
fn encrypted_two_account_import_preserves_stale_metadata_and_active_account() {
    let fixture = Fixture::new();
    fs::create_dir(fixture.root.join("user1")).unwrap();
    fs::write(fixture.root.join("user1/synthetic-history"), b"preserved").unwrap();
    fs::write(fixture.root.join("auth_1"), b"old opaque auth\xff").unwrap();
    fs::write(
        fixture.root.join("config.json"),
        b"synthetic untouched config",
    )
    .unwrap();
    let trace = Trace::default();
    let credentials = FakeCredentials::new(&trace);
    credentials.put("1", &[]);
    credentials.put("3", b"synthetic existing active");
    let (manifest, _, auth) = incoming(&[("1", Some(AUTH)), ("9007199254740993", Some(AUTH))]);
    let decoded = fixture.archive(&manifest, &auth);
    let mut storage = LoginStorage::open(&fixture.root).unwrap();
    let result = apply(
        decoded.manifest.value(),
        &decoded.tokens,
        &decoded.auth,
        &credentials,
        &mut storage,
    )
    .unwrap();
    assert_eq!(
        result,
        ImportResult {
            schema: 1,
            result: "added",
            profiles: 2
        }
    );
    assert_eq!(credentials.value("1"), Some(incoming_token("1")));
    assert_eq!(
        credentials.value("9007199254740993"),
        Some(incoming_token("9007199254740993"))
    );
    assert_eq!(
        credentials.value("3"),
        Some(b"synthetic existing active".to_vec())
    );
    assert_eq!(
        fs::read(fixture.root.join("auth_1")).unwrap(),
        b"old opaque auth\xff"
    );
    assert_eq!(
        fs::read(fixture.root.join("auth_9007199254740993")).unwrap(),
        AUTH
    );
    assert_eq!(
        fs::read(fixture.root.join("user1/synthetic-history")).unwrap(),
        b"preserved"
    );
    assert_eq!(
        fs::read(fixture.root.join("config.json")).unwrap(),
        b"synthetic untouched config"
    );
    assert!(!fixture.root.join("user9007199254740993").exists());
    let mut repeated = LoginStorage::open(&fixture.root).unwrap();
    let again = apply(
        decoded.manifest.value(),
        &decoded.tokens,
        &decoded.auth,
        &credentials,
        &mut repeated,
    )
    .unwrap();
    assert_eq!(
        again,
        ImportResult {
            schema: 1,
            result: "already_present",
            profiles: 0
        }
    );
}

#[test]
fn encrypted_import_postwrite_error_restores_empty_target_and_removes_only_owned_auth() {
    let fixture = Fixture::new();
    fs::create_dir(fixture.root.join("user1")).unwrap();
    fs::write(fixture.root.join("user1/sentinel"), b"untouched").unwrap();
    let trace = Trace::default();
    let credentials = FakeCredentials::new(&trace);
    credentials.put("1", &[]);
    *credentials.fault.borrow_mut() = Some(WriteFault::After(target("2")));
    let (manifest, _, auth) = incoming(&[("1", Some(AUTH)), ("2", Some(AUTH))]);
    let decoded = fixture.archive(&manifest, &auth);
    let mut storage = LoginStorage::open(&fixture.root).unwrap();
    assert_eq!(
        apply(
            decoded.manifest.value(),
            &decoded.tokens,
            &decoded.auth,
            &credentials,
            &mut storage
        ),
        Err(ImportError::CredentialIo)
    );
    assert_eq!(credentials.value("1"), Some(Vec::new()));
    assert_eq!(credentials.value("2"), None);
    assert!(!fixture.root.join("auth_1").exists());
    assert!(!fixture.root.join("auth_2").exists());
    assert_eq!(
        fs::read(fixture.root.join("user1/sentinel")).unwrap(),
        b"untouched"
    );
    assert!(fixture.base.join("selected.mdata").exists());
}

#[test]
fn failed_token_rollback_retains_metadata_and_explicit_retry_completes_missing_account() {
    let fixture = Fixture::new();
    let trace = Trace::default();
    let mut credentials = FakeCredentials::new(&trace);
    credentials.put("1", &[]);
    credentials.fail_delete.insert(target("2"));
    *credentials.fault.borrow_mut() = Some(WriteFault::After(target("2")));
    let (manifest, _, auth) = incoming(&[("1", Some(AUTH)), ("2", Some(AUTH))]);
    let decoded = fixture.archive(&manifest, &auth);
    let mut storage = LoginStorage::open(&fixture.root).unwrap();
    assert_eq!(
        apply(
            decoded.manifest.value(),
            &decoded.tokens,
            &decoded.auth,
            &credentials,
            &mut storage
        ),
        Err(ImportError::RollbackFailed)
    );
    drop(storage);
    assert_eq!(credentials.value("1"), Some(Vec::new()));
    assert_eq!(credentials.value("2"), Some(incoming_token("2")));
    assert_eq!(fs::read(fixture.root.join("auth_1")).unwrap(), AUTH);
    assert_eq!(fs::read(fixture.root.join("auth_2")).unwrap(), AUTH);
    credentials.fail_delete.clear();
    let mut retry = LoginStorage::open(&fixture.root).unwrap();
    assert_eq!(
        apply(
            decoded.manifest.value(),
            &decoded.tokens,
            &decoded.auth,
            &credentials,
            &mut retry
        )
        .unwrap(),
        ImportResult {
            schema: 1,
            result: "added",
            profiles: 1
        }
    );
    assert_eq!(credentials.value("1"), Some(incoming_token("1")));
    assert_eq!(credentials.value("2"), Some(incoming_token("2")));
}
