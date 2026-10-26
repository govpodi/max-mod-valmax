#![cfg(windows)]

use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Cursor, Read},
    os::windows::{fs::OpenOptionsExt, process::CommandExt},
    path::{Path, PathBuf},
    sync::OnceLock,
};
use valmax_core::{
    mdata_archive::{self as archive, ArchiveKind, MDataError, MDataPolicy},
    mdata_preview::MAX_ARCHIVE_BYTES,
    mdata_preview_file::{self as selected, PreviewFileError},
    session_archive::ArchivePolicy,
};

const PASSWORD: &str = "synthetic-selected-archive-password";
const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-preview-file";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("mdata-preview-file"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let name: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let path = fixture_base().join(name);
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn write(&self, name: &str, data: &[u8]) -> String {
        let path = self.0.join(name);
        fs::write(&path, data).unwrap();
        file_url(&path)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(fixture_base()).unwrap();
        let own = fs::canonicalize(&self.0).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
fn file_url(path: &Path) -> String {
    let mut result = String::from("file:///");
    for b in path.to_str().unwrap().replace('\\', "/").bytes() {
        if b.is_ascii_alphanumeric() || b"-._~/:".contains(&b) {
            result.push(b as char);
        } else {
            result.push_str(&format!("%{b:02X}"));
        }
    }
    result
}
fn encrypted() -> &'static [u8] {
    static BYTES: OnceLock<Vec<u8>> = OnceLock::new();
    BYTES.get_or_init(|| {
        let auth = br#"{"first_name":"Alice","last_name":"Example","short_name":"AE","private":"not in public projection"}"#;
        let manifest = json!({
            "schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
            "credentials":[{"name":"access_token_42@MXMOD","blob":STANDARD.encode(b"synthetic opaque credential")}],
            "files":[{"path":"auth_42","size":auth.len()}]
        });
        let default = ArchivePolicy::default();
        let file = |path: &str| path == "auth_42";
        let credential = |name: &str| name == "access_token_42@MXMOD";
        let policy = MDataPolicy {
            credential_policy: &default,
            file_path_validator: &file,
            credential_name_validator: Some(&credential),
        };
        let mut factory = |_: &str| -> Result<Box<dyn Read>, MDataError> {
            Ok(Box::new(Cursor::new(auth.to_vec())))
        };
        let mut result = Vec::new();
        archive::encrypt_stream(
            &manifest,
            PASSWORD,
            &mut factory,
            &mut result,
            &policy,
            ArchiveKind::Login,
        )
        .unwrap();
        result
    })
}

#[test]
fn selected_unicode_escaped_uppercase_extension_has_authenticated_metadata_only() {
    let f = Fixture::new();
    let name = "Архив # 100%.MDATA";
    let url = f.write(name, encrypted());
    let preview = selected::inspect(&url, PASSWORD).unwrap();
    assert_eq!(preview.schema, 1);
    assert_eq!(preview.kind, "login");
    assert_eq!(preview.profiles, 1);
    assert_eq!(preview.accounts[0].account_id, "42");
    assert_eq!(preview.accounts[0].display_name, "Alice Example");
    assert_eq!(preview.accounts[0].short_name, "AE");
    assert_eq!(
        preview.fingerprint,
        format!("{:x}", Sha256::digest(encrypted()))
    );
    let public = serde_json::to_string(&preview).unwrap();
    for secret in [
        PASSWORD,
        "not in public projection",
        "opaque credential",
        "access_token",
        "file:///",
    ] {
        assert!(!public.contains(secret));
    }
    assert_eq!(fs::read(f.0.join(name)).unwrap(), encrypted());
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 1);
}

#[test]
fn wrong_password_partial_tampered_and_trailing_data_never_return_preview() {
    let f = Fixture::new();
    let url = f.write("selected.mdata", encrypted());
    assert_eq!(
        selected::inspect(&url, "wrong synthetic password")
            .unwrap_err()
            .code(),
        "authentication_failed"
    );
    assert_eq!(
        selected::inspect(&url, "").unwrap_err().code(),
        "invalid_password"
    );
    let truncated = &encrypted()[..encrypted().len() - 1];
    let url = f.write("partial.mdata", truncated);
    assert!(selected::inspect(&url, PASSWORD).is_err());
    let mut changed = encrypted().to_vec();
    *changed.last_mut().unwrap() ^= 1;
    let url = f.write("changed.mdata", &changed);
    assert_eq!(
        selected::inspect(&url, PASSWORD).unwrap_err().code(),
        "authentication_failed"
    );
    let mut trailing = encrypted().to_vec();
    trailing.push(0);
    let url = f.write("trailing.mdata", &trailing);
    assert_eq!(
        selected::inspect(&url, PASSWORD).unwrap_err().code(),
        "invalid_archive"
    );
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 4);
}

#[test]
fn bounded_read_rejects_oversize_before_allocation_and_v1_is_unsupported() {
    let f = Fixture::new();
    for size in [
        0,
        7,
        MAX_ARCHIVE_BYTES + 1,
        MAX_ARCHIVE_BYTES + 2,
        128 * 1024 * 1024,
    ] {
        let path = f.0.join("size.mdata");
        fs::File::create(&path)
            .unwrap()
            .set_len(size as u64)
            .unwrap();
        assert_eq!(
            selected::inspect(&file_url(&path), PASSWORD)
                .unwrap_err()
                .code(),
            "too_large",
            "size={size}"
        );
    }
    // Exactly the cap is read, then the pure format parser rejects the prefix.
    let path = f.0.join("at-cap.mdata");
    fs::File::create(&path)
        .unwrap()
        .set_len(MAX_ARCHIVE_BYTES as u64)
        .unwrap();
    assert_eq!(
        selected::inspect(&file_url(&path), PASSWORD)
            .unwrap_err()
            .code(),
        "invalid_archive"
    );
    let url = f.write("old.mdata", archive::MAGIC);
    assert_eq!(
        selected::inspect(&url, PASSWORD).unwrap_err().code(),
        "unsupported_version"
    );
}

#[test]
fn invalid_selection_is_rejected_before_io_and_no_parent_is_created() {
    let f = Fixture::new();
    for url in [
        "file:///C:/ONEME/chosen.mdata",
        "file:///C:/oNeMe/chosen.mdata",
        "file:///C:/%4fNEME/chosen.mdata",
        "file://host/C:/chosen.mdata",
        "file:///C:/chosen.mdata?secret=unused",
        "file:///C:/../chosen.mdata",
    ] {
        assert_eq!(
            selected::inspect(url, PASSWORD),
            Err(PreviewFileError::UnsafePath)
        );
    }
    for suffix in ["json", "maxsession", "txt"] {
        assert_eq!(
            selected::inspect(&file_url(&f.0.join(format!("missing.{suffix}"))), PASSWORD),
            Err(PreviewFileError::InvalidArchive)
        );
    }
    let absent = f.0.join("missing-parent/chosen.mdata");
    assert_eq!(
        selected::inspect(&file_url(&absent), PASSWORD),
        Err(PreviewFileError::IoError)
    );
    assert!(!absent.parent().unwrap().exists());
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
}

#[test]
fn actual_missing_directory_hardlink_and_held_writer_fail_without_mutation() {
    let f = Fixture::new();
    let path = f.0.join("selected.mdata");
    let url = file_url(&path);
    assert_eq!(
        selected::inspect(&url, PASSWORD),
        Err(PreviewFileError::IoError)
    );
    fs::create_dir(&path).unwrap();
    assert!(selected::inspect(&url, PASSWORD).is_err());
    fs::remove_dir(&path).unwrap();
    fs::write(&path, archive::MAGIC).unwrap();
    let writer = fs::OpenOptions::new()
        .write(true)
        .share_mode(1)
        .open(&path)
        .unwrap();
    assert_eq!(
        selected::inspect(&url, PASSWORD),
        Err(PreviewFileError::Busy)
    );
    drop(writer);
    let alias = f.0.join("alias.mdata");
    fs::hard_link(&path, &alias).unwrap();
    assert_eq!(
        selected::inspect(&url, PASSWORD),
        Err(PreviewFileError::UnsafePath)
    );
    assert_eq!(fs::read(&path).unwrap(), archive::MAGIC);
    fs::remove_file(alias).unwrap();
    assert_eq!(
        selected::inspect(&url, PASSWORD).unwrap_err().code(),
        "unsupported_version"
    );
}

#[test]
fn actual_owned_junction_parent_is_not_followed() {
    let f = Fixture::new();
    let target = f.0.join("target");
    let link = f.0.join("junction");
    fs::create_dir(&target).unwrap();
    fs::write(target.join("selected.mdata"), archive::MAGIC).unwrap();
    let output = std::process::Command::new("cmd.exe")
        .args(["/c", "mklink", "/J"])
        // mklink treats forward-slash path components as command switches.
        .arg(link.to_str().unwrap().replace('/', "\\"))
        .arg(target.to_str().unwrap().replace('/', "\\"))
        .creation_flags(0x08000000)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "own synthetic junction must be created: {} {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let result = selected::inspect(&file_url(&link.join("selected.mdata")), PASSWORD);
    fs::remove_dir(&link).unwrap();
    assert_eq!(result, Err(PreviewFileError::UnsafePath));
    assert_eq!(
        fs::read(target.join("selected.mdata")).unwrap(),
        archive::MAGIC
    );
}
