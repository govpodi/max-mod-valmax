//! Authenticated, memory-only preview of compact MData v2 archives.
//! No file paths, credential-store access, profile writes or partial UI output.
//! Display projection happens only after decrypt_stream verified terminal+EOF.
//! Malformed UTF-16 surrogate escapes in auth JSON are stricter than Python:
//! metadata becomes empty instead of replacing those invalid scalars in names.

use crate::{
    mdata_archive::{self as archive, ArchiveKind, MDataError, MDataPolicy},
    mdata_login::{self as login, LoginError, LoginTokens},
    session_archive::{self as session, ArchivePolicy, OwnedValue},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    cell::RefCell,
    collections::BTreeMap,
    fmt,
    io::{self, Cursor, Write},
    rc::Rc,
};
use zeroize::Zeroizing;

pub const MAX_ARCHIVE_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_AUTH_TOTAL: usize = login::MAX_ACCOUNTS * login::MAX_AUTH_BYTES;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PreviewError {
    Archive(MDataError),
    Login(LoginError),
}
impl PreviewError {
    pub fn code(self) -> &'static str {
        match self {
            Self::Archive(error) => error.code(),
            Self::Login(error) => error.code(),
        }
    }
}
impl fmt::Display for PreviewError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for PreviewError {}
impl From<MDataError> for PreviewError {
    fn from(error: MDataError) -> Self {
        Self::Archive(error)
    }
}
impl From<LoginError> for PreviewError {
    fn from(error: LoginError) -> Self {
        Self::Login(error)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AccountPreview {
    pub account_id: String,
    pub display_name: String,
    pub short_name: String,
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Preview {
    pub schema: u8,
    pub kind: &'static str,
    pub profiles: usize,
    pub accounts: Vec<AccountPreview>,
    pub fingerprint: String,
}

type AuthBytes = Rc<RefCell<Zeroizing<Vec<u8>>>>;
struct MemorySink {
    bytes: AuthBytes,
    expected: usize,
}
impl Write for MemorySink {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let mut out = self.bytes.borrow_mut();
        if bytes.len() > self.expected.saturating_sub(out.len()) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        out.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        if self.bytes.borrow().len() == self.expected {
            Ok(())
        } else {
            Err(io::ErrorKind::InvalidData.into())
        }
    }
}
fn format_control(c: char) -> bool {
    // Small explicit Unicode 16.0 Cf range set, matching the Python client.
    // Rust char cannot contain Cs. Cc is handled by char::is_control below.
    matches!(c as u32, 0x00ad | 0x0600..=0x0605 | 0x061c | 0x06dd | 0x070f
        | 0x0890..=0x0891 | 0x08e2 | 0x180e | 0x200b..=0x200f | 0x202a..=0x202e
        | 0x2060..=0x2064 | 0x2066..=0x206f | 0xfeff | 0xfff9..=0xfffb
        | 0x110bd | 0x110cd | 0x13430..=0x1343f | 0x1bca0..=0x1bca3
        | 0x1d173..=0x1d17a | 0xe0001 | 0xe0020..=0xe007f)
}
fn name(value: &Value, limit: usize) -> String {
    let Some(raw) = value.as_str() else {
        return String::new();
    };
    let filtered: String = raw
        .chars()
        .take(limit)
        .map(|c| {
            if c.is_control() || format_control(c) {
                ' '
            } else {
                c
            }
        })
        .collect();
    filtered.split_whitespace().collect::<Vec<_>>().join(" ")
}
fn metadata(bytes: &[u8]) -> (String, String) {
    let Ok(value) = session::parse_json(bytes, "invalid_payload") else {
        return (String::new(), String::new());
    };
    let value = OwnedValue(value);
    if !value.0.is_object() {
        return (String::new(), String::new());
    }
    let first = name(&value.0["first_name"], 128);
    let last = name(&value.0["last_name"], 128);
    let display = format!("{first} {last}").trim().chars().take(256).collect();
    (display, name(&value.0["short_name"], 32))
}
fn tokens(manifest: &Value) -> Result<LoginTokens, LoginError> {
    let records = manifest["credentials"]
        .as_array()
        .ok_or(LoginError::InvalidPayload)?;
    if records.len() > login::MAX_ACCOUNTS {
        return Err(LoginError::AccountLimit);
    }
    let mut result = LoginTokens::new();
    for record in records {
        let key = record["name"].as_str().ok_or(LoginError::InvalidPayload)?;
        let encoded = record["blob"].as_str().ok_or(LoginError::InvalidPayload)?;
        if encoded.len() > 4 * login::MAX_BLOB.div_ceil(3) {
            return Err(LoginError::InvalidPayload);
        }
        let value = Zeroizing::new(
            STANDARD
                .decode(encoded)
                .map_err(|_| LoginError::InvalidPayload)?,
        );
        if result.insert(key.to_owned(), value).is_some() {
            return Err(LoginError::InvalidPayload);
        }
    }
    Ok(result)
}
/// Caller supplies bytes from an explicitly selected, independently held file.
/// The caller must bound that read at MAX_ARCHIVE_BYTES+1; this pure API neither
/// accepts a path nor performs I/O. A recognizable v1 prefix is unsupported,
/// never an unauthenticated source of account rows. Password is never logged.
pub fn inspect(bytes: &[u8], password: &str) -> Result<Preview, PreviewError> {
    if bytes.starts_with(archive::MAGIC) {
        return Err(MDataError::UnsupportedVersion.into());
    }
    if !(8..=MAX_ARCHIVE_BYTES).contains(&bytes.len()) {
        return Err(MDataError::TooLarge.into());
    }
    let credential_policy = ArchivePolicy::default();
    let file_policy = |path: &str| login::auth_id(path).is_some();
    let credential_name = |name: &str| login::token_id(name).is_some();
    let policy = MDataPolicy {
        credential_policy: &credential_policy,
        file_path_validator: &file_policy,
        credential_name_validator: Some(&credential_name),
    };
    let mut auth: BTreeMap<String, AuthBytes> = BTreeMap::new();
    let mut sink = |path: &str, size: u64| -> Result<Box<dyn Write>, MDataError> {
        if login::auth_id(path).is_none()
            || !(1..=login::MAX_AUTH_BYTES as u64).contains(&size)
            || auth.contains_key(path)
            || auth.len() >= login::MAX_ACCOUNTS
        {
            return Err(MDataError::InvalidPayload);
        }
        let mut data = Zeroizing::new(Vec::new());
        data.try_reserve_exact(size as usize)
            .map_err(|_| MDataError::IoError)?;
        let bytes = Rc::new(RefCell::new(data));
        auth.insert(path.to_owned(), bytes.clone());
        Ok(Box::new(MemorySink {
            bytes,
            expected: size as usize,
        }))
    };
    let mut preflight = |total: u64, count: usize| {
        if count > login::MAX_ACCOUNTS || total > MAX_AUTH_TOTAL as u64 {
            Err(MDataError::TooLarge)
        } else {
            Ok(())
        }
    };
    let mut source = Cursor::new(bytes);
    let manifest = OwnedValue(archive::decrypt_stream(
        &mut source,
        password,
        &mut sink,
        &policy,
        Some(&mut preflight),
        Some(ArchiveKind::Login),
    )?);
    // No account projection before this point: terminal tag and exact EOF have
    // succeeded, and even then the compact manifest/token contract must match.
    let credentials = tokens(&manifest.0)?;
    let checked = login::validate(&manifest.0, &credentials)?;
    let mut accounts = Vec::with_capacity(checked.account_ids.len());
    for account_id in checked.account_ids {
        let (display_name, short_name) = auth
            .get(&format!("auth_{account_id}"))
            .map(|bytes| metadata(&bytes.borrow()))
            .unwrap_or_default();
        accounts.push(AccountPreview {
            account_id,
            display_name,
            short_name,
        });
    }
    Ok(Preview {
        schema: 1,
        kind: "login",
        profiles: accounts.len(),
        accounts,
        fingerprint: format!("{:x}", Sha256::digest(bytes)),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::io::Read;
    const PASSWORD: &str = "synthetic-preview-password";
    fn encrypted(records: &[(&str, Option<Vec<u8>>)], kind: ArchiveKind) -> Vec<u8> {
        let manifest = json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
            "credentials":records.iter().map(|(id,_)|json!({"name":format!("access_token_{id}@MXMOD"),"blob":STANDARD.encode(b"synthetic opaque credential")})).collect::<Vec<_>>(),
            "files":records.iter().filter_map(|(id,bytes)|bytes.as_ref().map(|b|json!({"path":format!("auth_{id}"),"size":b.len()}))).collect::<Vec<_>>()});
        let default = ArchivePolicy::default();
        let file = |p: &str| login::auth_id(p).is_some();
        let credential = |n: &str| login::token_id(n).is_some();
        let policy = MDataPolicy {
            credential_policy: &default,
            file_path_validator: &file,
            credential_name_validator: Some(&credential),
        };
        let mut factory = |name: &str| -> Result<Box<dyn Read>, MDataError> {
            let data = records
                .iter()
                .find(|(id, _)| format!("auth_{id}") == name)
                .unwrap()
                .1
                .clone()
                .unwrap();
            Ok(Box::new(Cursor::new(data)))
        };
        let mut output = Vec::new();
        archive::encrypt_stream(
            &manifest,
            PASSWORD,
            &mut factory,
            &mut output,
            &policy,
            kind,
        )
        .unwrap();
        output
    }
    #[test]
    fn fully_authenticated_compact_projection_and_numeric_order() {
        let bytes=encrypted(&[("9007199254740993",Some(br#"{"first_name":"Alice","last_name":"Example","short_name":"AE","secret":"never project"}"#.to_vec())),("2",None)],ArchiveKind::Login);
        let value = inspect(&bytes, PASSWORD).unwrap();
        assert_eq!(value.profiles, 2);
        assert_eq!(
            value.accounts[0],
            AccountPreview {
                account_id: "2".into(),
                display_name: String::new(),
                short_name: String::new()
            }
        );
        assert_eq!(value.accounts[1].display_name, "Alice Example");
        assert_eq!(value.accounts[1].short_name, "AE");
        assert_eq!(value.fingerprint, format!("{:x}", Sha256::digest(&bytes)));
        let public = serde_json::to_string(&value).unwrap();
        assert!(!public.contains("never project"));
        assert!(!public.contains("credential"));
        assert!(public.contains("accountId"));
        assert!(public.contains("displayName"));
    }
    #[test]
    fn metadata_controls_unicode_limits_plain_html_and_malformed() {
        let raw=serde_json::to_vec(&json!({"first_name":"  Алиса\u{202e}\n😀 ","last_name":"<b>Example</b>\u{200b}","short_name":"A\u{2066}B\u{e0001}"})).unwrap();
        assert_eq!(
            metadata(&raw),
            ("Алиса 😀 <b>Example</b>".into(), "A B".into())
        );
        let raw=serde_json::to_vec(&json!({"first_name":"😀".repeat(140),"last_name":"Я".repeat(140),"short_name":"é".repeat(40)})).unwrap();
        let (display, short) = metadata(&raw);
        assert_eq!(display.chars().count(), 256);
        assert_eq!(short.chars().count(), 32);
        for data in [
            b"not json".as_slice(),
            br#"{"first_name":"A","first_name":"B"}"#,
            br#"["A"]"#,
            br#"{"first_name":"\ud800","last_name":"B"}"#,
        ] {
            assert_eq!(metadata(data), (String::new(), String::new()));
        }
        assert_eq!(
            metadata(br#"{"first_name":true,"last_name":"B","short_name":42}"#),
            ("B".into(), String::new())
        );
    }
    #[test]
    fn terminal_eof_tamper_and_password_never_return_preview() {
        let bytes = encrypted(
            &[(
                "2",
                Some(br#"{"first_name":"Visible only after terminal"}"#.to_vec()),
            )],
            ArchiveKind::Login,
        );
        assert_eq!(
            inspect(&bytes, "wrong password"),
            Err(PreviewError::Archive(MDataError::AuthenticationFailed))
        );
        for length in [8, bytes.len() - 1, bytes.len() - 21] {
            assert!(inspect(&bytes[..length], PASSWORD).is_err());
        }
        let mut tampered = bytes.clone();
        *tampered.last_mut().unwrap() ^= 1;
        assert_eq!(
            inspect(&tampered, PASSWORD),
            Err(PreviewError::Archive(MDataError::AuthenticationFailed))
        );
        let mut trailing = bytes;
        trailing.push(0);
        assert_eq!(
            inspect(&trailing, PASSWORD),
            Err(PreviewError::Archive(MDataError::InvalidArchive))
        );
    }
    #[test]
    fn legacy_and_archive_bounds_are_finite() {
        assert_eq!(
            inspect(archive::MAGIC, PASSWORD),
            Err(PreviewError::Archive(MDataError::UnsupportedVersion))
        );
        for bytes in [vec![], vec![0; 7], vec![0; MAX_ARCHIVE_BYTES + 1]] {
            assert_eq!(
                inspect(&bytes, PASSWORD),
                Err(PreviewError::Archive(MDataError::TooLarge))
            );
        }
        assert_eq!(
            inspect(&[0; 10], PASSWORD),
            Err(PreviewError::Archive(MDataError::InvalidArchive))
        );
    }
    #[test]
    fn optional_auth_is_opaque_and_file_size_bounded() {
        let bytes = encrypted(
            &[("2", Some(vec![b'x'; login::MAX_AUTH_BYTES]))],
            ArchiveKind::Login,
        );
        assert_eq!(
            inspect(&bytes, PASSWORD).unwrap().accounts[0].display_name,
            ""
        );
        let bytes = encrypted(
            &[("2", Some(vec![b'x'; login::MAX_AUTH_BYTES + 1]))],
            ArchiveKind::Login,
        );
        assert_eq!(
            inspect(&bytes, PASSWORD),
            Err(PreviewError::Archive(MDataError::InvalidPayload))
        );
    }
    #[test]
    fn too_many_accounts_and_files_remain_distinct_contract_errors() {
        let bytes = encrypted(
            &[("1", None), ("2", None), ("3", None), ("4", None)],
            ArchiveKind::Login,
        );
        assert_eq!(
            inspect(&bytes, PASSWORD),
            Err(PreviewError::Login(LoginError::AccountLimit))
        );
        let bytes = encrypted(
            &[
                ("1", Some(vec![1])),
                ("2", Some(vec![1])),
                ("3", Some(vec![1])),
                ("4", Some(vec![1])),
            ],
            ArchiveKind::Login,
        );
        assert_eq!(
            inspect(&bytes, PASSWORD),
            Err(PreviewError::Archive(MDataError::TooLarge))
        );
    }
}
