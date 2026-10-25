#![cfg(windows)]
use serde_json::json;
use std::{
    cell::RefCell,
    collections::BTreeMap,
    fs,
    os::windows::{fs::OpenOptionsExt, process::CommandExt},
    path::PathBuf,
    rc::Rc,
};
use valmax_core::{
    login_credentials::{CredentialError, LoginCredentials, Result as CredentialResult},
    mdata_login::{self as login, AuthFile},
    mdata_login_capture::{self as capture, CaptureError},
};
use zeroize::Zeroizing;

const BASE: &str = "C:/ValMAXBuild/fixtures/login-capture";
struct Fixture {
    base: PathBuf,
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let id: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let base = PathBuf::from(BASE).join(id);
        let root = base.join("MXMOD");
        fs::create_dir_all(&root).unwrap();
        Self { base, root }
    }
    fn auth(&self, id: &str, bytes: &[u8]) {
        fs::write(self.root.join(format!("auth_{id}")), bytes).unwrap();
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(BASE).unwrap();
        let own = fs::canonicalize(&self.base).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
type Data = Rc<RefCell<BTreeMap<String, Vec<u8>>>>;
#[derive(Default)]
struct FakeCredentials {
    data: Data,
    reads: RefCell<Vec<String>>,
    hook: Option<Box<dyn Fn(&str, usize)>>,
    fail: Option<CredentialError>,
}
impl FakeCredentials {
    fn seed(&self, id: &str, bytes: &[u8]) {
        self.data
            .borrow_mut()
            .insert(format!("access_token_{id}@MXMOD"), bytes.to_vec());
    }
}
impl LoginCredentials for FakeCredentials {
    fn owned_token_names(&self) -> CredentialResult<Vec<String>> {
        panic!("capture must not enumerate credentials")
    }
    fn read(&self, name: &str) -> CredentialResult<Option<Zeroizing<Vec<u8>>>> {
        assert!(
            login::token_id(name).is_some(),
            "only exact owned token targets"
        );
        let count = {
            let mut reads = self.reads.borrow_mut();
            reads.push(name.into());
            reads.len()
        };
        if let Some(hook) = &self.hook {
            hook(name, count);
        }
        if let Some(error) = self.fail {
            return Err(error);
        }
        Ok(self
            .data
            .borrow()
            .get(name)
            .map(|value| Zeroizing::new(value.clone())))
    }
    fn set_owned(&self, _: &str, _: &[u8], _: Option<&[u8]>) -> CredentialResult<()> {
        panic!("capture is read-only")
    }
    fn delete_owned(&self, _: &str, _: &[u8]) -> CredentialResult<()> {
        panic!("capture is read-only")
    }
}
fn error(result: Result<capture::Snapshot, CaptureError>) -> CaptureError {
    result.err().expect("no partial snapshot may escape")
}

#[test]
fn capture_only_direct_live_auth_and_exact_tokens_with_stale_entries() {
    let f = Fixture::new();
    let credentials = FakeCredentials::default();
    f.auth("42", b"opaque synthetic auth");
    f.auth("43", b"stale metadata");
    f.auth("44", b"empty-token metadata");
    credentials.seed("42", b"synthetic opaque token");
    credentials.seed("44", b"");
    fs::write(f.root.join("config.json"), b"not readable configuration").unwrap();
    fs::create_dir_all(f.root.join("user42/history")).unwrap();
    fs::write(
        f.root.join("user42/history/auth_bad"),
        b"must not be scanned",
    )
    .unwrap();
    fs::create_dir(f.root.join("cache")).unwrap();
    let history = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(f.root.join("user42/history/auth_bad"))
        .unwrap();
    let config = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(f.root.join("config.json"))
        .unwrap();
    let snapshot = capture::capture(&f.root, &credentials, None).unwrap();
    assert_eq!(
        snapshot.auth.keys().map(String::as_str).collect::<Vec<_>>(),
        vec!["auth_42"]
    );
    assert_eq!(
        snapshot.auth["auth_42"].as_slice(),
        b"opaque synthetic auth"
    );
    assert_eq!(snapshot.tokens.len(), 1);
    assert_eq!(
        snapshot.manifest.value()["files"],
        json!([{"path":"auth_42","size":21}])
    );
    login::validate(snapshot.manifest.value(), &snapshot.tokens).unwrap();
    assert_eq!(
        *credentials.reads.borrow(),
        vec![
            "access_token_42@MXMOD",
            "access_token_43@MXMOD",
            "access_token_44@MXMOD",
            "access_token_42@MXMOD",
            "access_token_43@MXMOD",
            "access_token_44@MXMOD"
        ]
    );
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 6);
    drop((history, config));
}

#[test]
fn metadata_listing_does_not_read_contents_and_rejects_invalid_stale_names() {
    let f = Fixture::new();
    f.auth("42", b"{}{}");
    // A handle denying data-read sharing does not prevent attribute-only opens.
    let held = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(f.root.join("auth_42"))
        .unwrap();
    assert_eq!(
        capture::listing(&f.root).unwrap(),
        vec![AuthFile {
            path: "auth_42".into(),
            size: 4
        }]
    );
    drop(held);
    fs::remove_file(f.root.join("auth_42")).unwrap();
    for name in [
        "auth_0",
        "auth_01",
        "auth_bad",
        "Auth_42",
        "auth_9223372036854775808",
    ] {
        fs::write(f.root.join(name), b"{}").unwrap();
        assert_eq!(capture::listing(&f.root), Err(CaptureError::InvalidProfile));
        fs::remove_file(f.root.join(name)).unwrap();
    }
    for id in 1..=capture::MAX_CANDIDATES {
        f.auth(&id.to_string(), b"{}");
    }
    assert_eq!(
        capture::listing(&f.root).unwrap().len(),
        capture::MAX_CANDIDATES
    );
    f.auth("64", b"{}");
    assert_eq!(capture::listing(&f.root), Err(CaptureError::InvalidProfile));
}

#[test]
fn size_bounds_three_live_accounts_and_explicit_selection() {
    let f = Fixture::new();
    let credentials = FakeCredentials::default();
    for size in [0, login::MAX_AUTH_BYTES + 1] {
        fs::File::create(f.root.join("auth_42"))
            .unwrap()
            .set_len(size as u64)
            .unwrap();
        assert_eq!(capture::listing(&f.root), Err(CaptureError::InvalidProfile));
    }
    f.auth("42", &vec![b'X'; login::MAX_AUTH_BYTES]);
    credentials.seed("42", &vec![7; login::MAX_BLOB]);
    assert_eq!(
        capture::capture(&f.root, &credentials, None).unwrap().auth["auth_42"].len(),
        login::MAX_AUTH_BYTES
    );
    credentials.seed("42", &vec![7; login::MAX_BLOB + 1]);
    assert_eq!(
        error(capture::capture(&f.root, &credentials, None)),
        CaptureError::InvalidProfile
    );
    credentials.seed("42", b"ok");
    for id in [43, 44, 45] {
        f.auth(&id.to_string(), b"{}");
        credentials.seed(&id.to_string(), b"synthetic");
    }
    assert_eq!(
        error(capture::capture(&f.root, &credentials, None)),
        CaptureError::AccountLimit
    );
    credentials.reads.borrow_mut().clear();
    let snapshot = capture::capture(&f.root, &credentials, Some(&json!(["45", "42"]))).unwrap();
    assert_eq!(snapshot.auth.len(), 2);
    assert_eq!(
        *credentials.reads.borrow(),
        vec![
            "access_token_42@MXMOD",
            "access_token_45@MXMOD",
            "access_token_42@MXMOD",
            "access_token_45@MXMOD"
        ]
    );
}

#[test]
fn selection_and_root_rejection_precede_native_reads_or_creation() {
    let f = Fixture::new();
    let credentials = FakeCredentials::default();
    for selection in [
        json!([]),
        json!(["01"]),
        json!([42]),
        json!(["42", "42"]),
        json!(["1", "2", "3", "4"]),
    ] {
        assert_eq!(
            error(capture::capture(
                &f.base.join("missing/MXMOD"),
                &credentials,
                Some(&selection)
            )),
            CaptureError::InvalidRequest
        );
    }
    assert!(!f.base.join("missing").exists());
    assert_eq!(
        error(capture::capture(
            &f.root,
            &credentials,
            Some(&json!(["42"]))
        )),
        CaptureError::InvalidRequest
    );
    for root in [
        f.base.join("mxmod"),
        f.base.join("ONEME/MXMOD"),
        f.base.join("other"),
    ] {
        assert_eq!(capture::listing(&root), Err(CaptureError::UnsafePath));
    }
    assert_eq!(
        capture::listing(&f.base.join("missing/MXMOD")),
        Err(CaptureError::InvalidProfile)
    );
    assert!(credentials.reads.borrow().is_empty());
    f.auth("42", b"{}");
    assert_eq!(
        error(capture::capture(&f.root, &credentials, None)),
        CaptureError::NoAccounts
    );
    credentials.seed("42", b"");
    assert_eq!(
        error(capture::capture(
            &f.root,
            &credentials,
            Some(&json!(["42"]))
        )),
        CaptureError::NoAccounts
    );
}

#[test]
fn repeat_checks_detect_token_auth_and_listing_changes() {
    for mode in 0..4 {
        let f = Fixture::new();
        f.auth("42", b"AAAA");
        let mut credentials = FakeCredentials::default();
        credentials.seed("42", b"synthetic");
        if mode == 3 {
            f.auth("43", b"stale");
        }
        let root = f.root.clone();
        let data = credentials.data.clone();
        credentials.hook = Some(Box::new(move |name, count| match mode {
            0 if count == 2 => {
                data.borrow_mut().insert(name.into(), b"changed".to_vec());
            }
            1 if count == 2 => {
                fs::write(root.join("auth_42"), b"BBBB").unwrap();
            }
            2 if count == 1 => {
                fs::write(root.join("auth_44"), b"new metadata").unwrap();
            }
            3 if count == 4 => {
                data.borrow_mut()
                    .insert(name.into(), b"newly active".to_vec());
            }
            _ => {}
        }));
        assert_eq!(
            error(capture::capture(&f.root, &credentials, None)),
            CaptureError::ProfileChanged,
            "mode={mode}"
        );
    }
}

#[test]
fn held_root_and_ancestors_prevent_replacement_during_capture() {
    let f = Fixture::new();
    f.auth("42", b"{}");
    let mut credentials = FakeCredentials::default();
    credentials.seed("42", b"synthetic");
    let root = f.root.clone();
    let parent = f.base.clone();
    credentials.hook = Some(Box::new(move |_, _| {
        assert!(fs::rename(&root, root.with_file_name("moved-MXMOD")).is_err());
        assert!(fs::rename(&parent, parent.with_extension("moved")).is_err());
    }));
    assert_eq!(
        capture::capture(&f.root, &credentials, None)
            .unwrap()
            .auth
            .len(),
        1
    );
    assert_eq!(fs::read(f.root.join("auth_42")).unwrap(), b"{}");
}

#[test]
fn hardlinks_directories_reparse_and_credential_failures_are_finite() {
    let f = Fixture::new();
    let auth = f.root.join("auth_42");
    fs::create_dir(&auth).unwrap();
    assert_eq!(capture::listing(&f.root), Err(CaptureError::UnsafePath));
    fs::remove_dir(&auth).unwrap();
    let other = f.base.join("own-source");
    fs::write(&other, b"{}").unwrap();
    fs::hard_link(&other, &auth).unwrap();
    assert_eq!(capture::listing(&f.root), Err(CaptureError::UnsafePath));
    fs::remove_file(&auth).unwrap();
    f.auth("42", b"{}");
    let mut credentials = FakeCredentials::default();
    credentials.fail = Some(CredentialError::CredentialIo);
    assert_eq!(
        error(capture::capture(&f.root, &credentials, None)),
        CaptureError::CredentialIo
    );
    let link = f.base.join("junction");
    let output = std::process::Command::new("cmd.exe")
        .args(["/c", "mklink", "/J"])
        .arg(link.to_str().unwrap().replace('/', "\\"))
        .arg(f.base.to_str().unwrap().replace('/', "\\"))
        .creation_flags(0x08000000)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "own junction required: {} {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result = capture::listing(&link.join("MXMOD"));
    fs::remove_dir(&link).unwrap();
    assert_eq!(result, Err(CaptureError::UnsafePath));
    assert_eq!(fs::read(other).unwrap(), b"{}");
}
