//! Synthetic archives and explicitly owned fixtures; never real profiles/WinCred.
use super::*;
use serde_json::json;
use std::{
    fs,
    io::Read,
    os::windows::fs::OpenOptionsExt,
    path::{Path, PathBuf},
    sync::OnceLock,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-import-archive";
const PASSWORD: &str = "synthetic-import-archive-password";
const TOKEN: &[u8] = b"synthetic opaque token only";
const AUTH: &[u8] =
    br#"{"first_name":"Alice","last_name":"Example","private":"synthetic-private-only"}"#;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let name: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let root = PathBuf::from(BASE).join(name);
        fs::create_dir_all(&root).unwrap();
        Self(root)
    }
    fn write(&self, name: &str, bytes: &[u8]) -> (String, String) {
        let path = self.0.join(name);
        fs::write(&path, bytes).unwrap();
        (file_url(&path), digest(bytes))
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(BASE).unwrap();
        let own = fs::canonicalize(&self.0).unwrap();
        let name = self.0.file_name().unwrap().to_str().unwrap();
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
    let mut result = String::from("file:///");
    for byte in path.to_str().unwrap().replace('\\', "/").bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~/:".contains(&byte) {
            result.push(byte as char);
        } else {
            result.push_str(&format!("%{byte:02X}"));
        }
    }
    result
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn error(result: Result<DecodedLogin>, expected: &str) {
    match result {
        Err(error) => {
            assert_eq!(error.code(), expected);
            assert_eq!(error.to_string(), expected);
        }
        Ok(_) => panic!("invalid input returned private decoded material"),
    }
}
fn encrypted(names: &[&str], files: &[(&str, Vec<u8>)], kind: ArchiveKind) -> Vec<u8> {
    let manifest = json!({"schema":1,"sourceClient":session::SOURCE_CLIENT,"sourceVersion":session::SOURCE_VERSION,
        "credentials":names.iter().map(|name|json!({"name":name,"blob":STANDARD.encode(TOKEN)})).collect::<Vec<_>>(),
        "files":files.iter().map(|(path,bytes)|json!({"path":path,"size":bytes.len()})).collect::<Vec<_>>()});
    let default = ArchivePolicy::default();
    // The container can encode these safe records; compact importer policy is
    // independently narrower and must reject unrelated credentials/files.
    let file_policy = |path: &str| archive::valid_file_path(path);
    let credential_policy = |_: &str| true;
    let policy = MDataPolicy {
        credential_policy: &default,
        file_path_validator: &file_policy,
        credential_name_validator: Some(&credential_policy),
    };
    let mut source = |name: &str| -> std::result::Result<Box<dyn Read>, MDataError> {
        let bytes = files
            .iter()
            .find(|(path, _)| *path == name)
            .unwrap()
            .1
            .clone();
        Ok(Box::new(Cursor::new(bytes)))
    };
    let mut output = Vec::new();
    archive::encrypt_stream(&manifest, PASSWORD, &mut source, &mut output, &policy, kind).unwrap();
    output
}
fn valid() -> &'static [u8] {
    static BYTES: OnceLock<Vec<u8>> = OnceLock::new();
    BYTES.get_or_init(|| {
        encrypted(
            &[
                "access_token_2@MXMOD",
                "access_token_9007199254740993@MXMOD",
            ],
            &[("auth_9007199254740993", AUTH.to_vec())],
            ArchiveKind::Login,
        )
    })
}

#[test]
fn selected_authenticated_snapshot_returns_lossless_host_only_transfer_material() {
    let f = Fixture::new();
    let (file, fingerprint) = f.write("Сеанс # 100%.MDATA", valid());
    let decoded = load(&file, PASSWORD, &fingerprint).unwrap();
    assert_eq!(decoded.tokens.len(), 2);
    assert_eq!(decoded.tokens["access_token_2@MXMOD"].as_slice(), TOKEN);
    assert_eq!(
        decoded.tokens["access_token_9007199254740993@MXMOD"].as_slice(),
        TOKEN
    );
    assert_eq!(decoded.auth.len(), 1);
    assert_eq!(decoded.auth["auth_9007199254740993"].as_slice(), AUTH);
    let checked = login::validate(decoded.manifest.value(), &decoded.tokens).unwrap();
    assert_eq!(checked.account_ids, ["2", "9007199254740993"]);
    assert_eq!(checked.files[0].size, AUTH.len());
    assert_eq!(fs::read(f.0.join("Сеанс # 100%.MDATA")).unwrap(), valid());
    assert_eq!(
        fs::read_dir(&f.0).unwrap().count(),
        1,
        "no plaintext staging or result file"
    );
}

#[test]
fn malformed_optional_auth_is_preserved_as_opaque_bytes() {
    let f = Fixture::new();
    let malformed = [
        ("auth_1", b"\xff\x00not-json".to_vec()),
        ("auth_2", br#"{"first_name":"A","first_name":"B"}"#.to_vec()),
        ("auth_3", br#"{"first_name":"\ud800"}"#.to_vec()),
    ];
    let bytes = encrypted(
        &[
            "access_token_1@MXMOD",
            "access_token_2@MXMOD",
            "access_token_3@MXMOD",
        ],
        &malformed,
        ArchiveKind::Login,
    );
    let (file, fingerprint) = f.write("opaque.mdata", &bytes);
    let decoded = load(&file, PASSWORD, &fingerprint).unwrap();
    for (name, value) in malformed {
        assert_eq!(decoded.auth[name].as_slice(), value.as_slice());
    }
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 1);
}

#[test]
fn fingerprint_binds_same_ciphertext_before_decrypt_and_never_replaces_authentication() {
    let f = Fixture::new();
    let (file, original) = f.write("selected.mdata", valid());
    // A valid-length wrong password must not run before a changed-input check.
    error(
        load(&file, "synthetic-wrong-password", &"0".repeat(64)),
        "source_changed",
    );
    error(
        load(&file, "synthetic-wrong-password", &original),
        "authentication_failed",
    );
    let mut changed = valid().to_vec();
    *changed.last_mut().unwrap() ^= 1;
    let (_, current) = f.write("selected.mdata", &changed);
    error(load(&file, PASSWORD, &original), "source_changed");
    error(load(&file, PASSWORD, &current), "authentication_failed");
    for value in [
        String::new(),
        "a".repeat(63),
        "a".repeat(65),
        "A".repeat(64),
        "g".repeat(64),
        "é".repeat(32),
    ] {
        error(
            load("file:///C:/does-not-exist/selected.mdata", PASSWORD, &value),
            "invalid_request",
        );
    }
    assert_eq!(fs::read(f.0.join("selected.mdata")).unwrap(), changed);
}

#[test]
fn truncated_terminal_and_trailing_data_never_return_partial_decoded_state() {
    let f = Fixture::new();
    for (name, bytes, expected) in [
        (
            "terminal.mdata",
            valid()[..valid().len() - 1].to_vec(),
            "invalid_archive",
        ),
        (
            "no-terminal.mdata",
            valid()[..valid().len() - 21].to_vec(),
            "invalid_archive",
        ),
        ("tail.mdata", [valid(), b"\0"].concat(), "invalid_archive"),
    ] {
        let (file, fingerprint) = f.write(name, &bytes);
        error(load(&file, PASSWORD, &fingerprint), expected);
        assert_eq!(fs::read(f.0.join(name)).unwrap(), bytes);
    }
    assert_eq!(
        fs::read_dir(&f.0).unwrap().count(),
        3,
        "failure creates no decoded files"
    );
}

#[test]
fn password_and_selected_path_policy_reject_before_opening_or_creating_anything() {
    let f = Fixture::new();
    let missing = f.0.join("never-created/selected.mdata");
    let fingerprint = "0".repeat(64);
    for password in [
        "short".to_owned(),
        "x".repeat(1025),
        "é".repeat(513),
        "synthetic\0password".to_owned(),
    ] {
        error(
            load(&file_url(&missing), &password, &fingerprint),
            "invalid_password",
        );
    }
    for path in [
        "https://example.invalid/selected.mdata",
        "file://host/C:/selected.mdata",
        "file:///C:/ONEME/selected.mdata",
        "file:///C:/%4fNEME/selected.mdata",
        "file:///C:/../selected.mdata",
        "file:///C:/selected.mdata?query=1",
        "file:///C:/selected.mdata:stream",
        "file:///C:/%ff.mdata",
    ] {
        error(load(path, PASSWORD, &fingerprint), "unsafe_path");
    }
    for suffix in ["txt", "maxsession", "json"] {
        error(
            load(
                &file_url(&f.0.join(format!("absent.{suffix}"))),
                PASSWORD,
                &fingerprint,
            ),
            "invalid_archive",
        );
    }
    error(
        load(&file_url(&missing), PASSWORD, &fingerprint),
        "io_error",
    );
    assert!(!missing.parent().unwrap().exists());
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
}

#[test]
fn bounded_archive_reader_and_legacy_formats_have_finite_errors() {
    let f = Fixture::new();
    for (name, bytes) in [
        ("profile.mdata", archive::MAGIC.as_slice()),
        ("session.mdata", session::MAGIC.as_slice()),
    ] {
        let (file, fingerprint) = f.write(name, bytes);
        error(load(&file, PASSWORD, &fingerprint), "unsupported_version");
    }
    for bytes in [vec![], vec![0; 7], vec![0; MAX_ARCHIVE_BYTES + 1]] {
        let (file, fingerprint) = f.write("size.mdata", &bytes);
        error(load(&file, PASSWORD, &fingerprint), "too_large");
    }
    let bytes = vec![0; MAX_ARCHIVE_BYTES];
    let (file, fingerprint) = f.write("at-cap.mdata", &bytes);
    error(load(&file, PASSWORD, &fingerprint), "invalid_archive");
}

#[test]
fn compact_counts_auth_bounds_and_ownership_are_checked_after_container_authentication() {
    let f = Fixture::new();
    let cases = [
        (
            vec!["access_token_1@MXMOD"],
            vec![("auth_2", vec![1])],
            "invalid_payload",
        ),
        (
            vec!["access_token_1@MXMOD"],
            vec![("auth_1", vec![])],
            "invalid_payload",
        ),
        (
            vec!["access_token_1@MXMOD"],
            vec![("auth_1", vec![1; login::MAX_AUTH_BYTES + 1])],
            "invalid_payload",
        ),
        (
            vec![
                "access_token_1@MXMOD",
                "access_token_2@MXMOD",
                "access_token_3@MXMOD",
                "access_token_4@MXMOD",
            ],
            vec![],
            "account_limit",
        ),
        (
            vec!["access_token_1@MXMOD"],
            vec![("user1/history/synthetic.db", vec![1])],
            "policy_violation",
        ),
        (vec!["access_token_1@ONEME"], vec![], "policy_violation"),
    ];
    for (names, files, expected) in cases {
        let bytes = encrypted(&names, &files, ArchiveKind::Login);
        let (file, fingerprint) = f.write("rejected.mdata", &bytes);
        error(load(&file, PASSWORD, &fingerprint), expected);
    }
    let bytes = encrypted(
        &["access_token_1@MXMOD"],
        &[("auth_1", vec![1; login::MAX_AUTH_BYTES])],
        ArchiveKind::Login,
    );
    let (file, fingerprint) = f.write("boundary.mdata", &bytes);
    assert_eq!(
        load(&file, PASSWORD, &fingerprint).unwrap().auth["auth_1"].len(),
        login::MAX_AUTH_BYTES
    );
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 2);
}

#[test]
fn selected_hardlink_directory_and_writer_are_rejected_without_modification() {
    let f = Fixture::new();
    let (file, fingerprint) = f.write("selected.mdata", valid());
    let path = f.0.join("selected.mdata");
    let writer = fs::OpenOptions::new()
        .write(true)
        .share_mode(1)
        .open(&path)
        .unwrap();
    error(load(&file, PASSWORD, &fingerprint), "busy");
    drop(writer);
    let alias = f.0.join("alias.mdata");
    fs::hard_link(&path, &alias).unwrap();
    error(load(&file, PASSWORD, &fingerprint), "unsafe_path");
    fs::remove_file(alias).unwrap();
    let directory = f.0.join("directory.mdata");
    fs::create_dir(&directory).unwrap();
    error(
        load(&file_url(&directory), PASSWORD, &fingerprint),
        "unsafe_path",
    );
    assert_eq!(fs::read(path).unwrap(), valid());
}
