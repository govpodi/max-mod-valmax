//! Authenticated input for an explicitly confirmed compact MData import.
//! This module does not install a profile, open WinCred, discover paths, or
//! write plaintext staging. All sensitive output remains private process data
//! and becomes accessible only after terminal authentication, exact EOF and
//! compact-schema validation. Call on a worker: bounded scrypt can block.
use crate::{
    mdata_archive::{self as archive, ArchiveKind, MDataError, MDataPolicy},
    mdata_login::{self as login, LoginError, LoginTokens},
    mdata_preview::{MAX_ARCHIVE_BYTES, MAX_AUTH_TOTAL},
    selected_path::selected_file_url,
    session_archive::{self as session, ArchivePolicy, OwnedValue},
    settings_io::{SafeStorePath, StoreError},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    cell::RefCell,
    collections::BTreeMap,
    fmt,
    io::{self, Cursor, Write},
    rc::Rc,
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ImportArchiveError {
    InvalidRequest,
    UnsafePath,
    IoError,
    Busy,
    Archive(MDataError),
    Login(LoginError),
}
impl ImportArchiveError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::UnsafePath => "unsafe_path",
            Self::IoError => "io_error",
            Self::Busy => "busy",
            Self::Archive(error) => error.code(),
            Self::Login(error) => error.code(),
        }
    }
}
impl fmt::Display for ImportArchiveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for ImportArchiveError {}
impl From<MDataError> for ImportArchiveError {
    fn from(error: MDataError) -> Self {
        Self::Archive(error)
    }
}
impl From<LoginError> for ImportArchiveError {
    fn from(error: LoginError) -> Self {
        Self::Login(error)
    }
}
pub type Result<T> = std::result::Result<T, ImportArchiveError>;

/// Deliberately not Debug/Serialize/Clone: contains base64 credential values.
/// The shared OwnedValue wrapper erases owned strings on drop, best effort.
pub struct Manifest(OwnedValue);
impl Manifest {
    pub fn value(&self) -> &Value {
        &self.0.0
    }
}

/// Host-only transfer material, not a public response or diagnostic object.
/// Auth bytes are opaque, optional metadata; malformed auth JSON is preserved.
/// Tokens and auth use best-effort zeroization, not an allocator-wide guarantee.
pub struct DecodedLogin {
    pub manifest: Manifest,
    pub tokens: LoginTokens,
    pub auth: BTreeMap<String, Zeroizing<Vec<u8>>>,
}

fn store_error(error: StoreError) -> ImportArchiveError {
    match error {
        StoreError::UnsafePath => ImportArchiveError::UnsafePath,
        StoreError::StoreBusy => ImportArchiveError::Busy,
        // This mapping is used only for read_bounded's size violation, never
        // for settings JSON validation or write operations.
        StoreError::InvalidStore => MDataError::TooLarge.into(),
        _ => ImportArchiveError::IoError,
    }
}
fn fingerprint(value: &str) -> Result<[u8; 32]> {
    if value.len() != 64
        || !value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(ImportArchiveError::InvalidRequest);
    }
    let nibble = |b: u8| {
        if b.is_ascii_digit() {
            b - b'0'
        } else {
            b - b'a' + 10
        }
    };
    let mut result = [0u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        result[index] = nibble(pair[0]) * 16 + nibble(pair[1]);
    }
    Ok(result)
}
type SharedBytes = Rc<RefCell<Zeroizing<Vec<u8>>>>;
struct MemorySink {
    bytes: SharedBytes,
    expected: usize,
}
impl Write for MemorySink {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let mut result = self.bytes.borrow_mut();
        if bytes.len() > self.expected.saturating_sub(result.len()) {
            return Err(io::ErrorKind::InvalidData.into());
        }
        result.extend_from_slice(bytes);
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
fn decode_tokens(manifest: &Value) -> Result<LoginTokens> {
    let records = manifest["credentials"]
        .as_array()
        .ok_or(LoginError::InvalidPayload)?;
    if records.len() > login::MAX_ACCOUNTS {
        return Err(LoginError::AccountLimit.into());
    }
    let mut tokens = LoginTokens::new();
    for record in records {
        let name = record["name"]
            .as_str()
            .filter(|name| login::token_id(name).is_some())
            .ok_or(LoginError::InvalidPayload)?;
        let encoded = record["blob"].as_str().ok_or(LoginError::InvalidPayload)?;
        if encoded.len() > 4 * login::MAX_BLOB.div_ceil(3) {
            return Err(LoginError::InvalidPayload.into());
        }
        let bytes = Zeroizing::new(
            STANDARD
                .decode(encoded)
                .map_err(|_| LoginError::InvalidPayload)?,
        );
        if tokens.insert(name.to_owned(), bytes).is_some() {
            return Err(LoginError::InvalidPayload.into());
        }
    }
    Ok(tokens)
}

fn decode(bytes: &[u8], password: &str, expected: &[u8; 32]) -> Result<DecodedLogin> {
    if !(8..=MAX_ARCHIVE_BYTES).contains(&bytes.len()) {
        return Err(MDataError::TooLarge.into());
    }
    let actual = Sha256::digest(bytes);
    if !bool::from(actual.as_slice().ct_eq(expected)) {
        return Err(MDataError::SourceChanged.into());
    }
    // The fingerprint binds the confirmed file, but is not authentication.
    if bytes.starts_with(archive::MAGIC) || bytes.starts_with(session::MAGIC) {
        return Err(MDataError::UnsupportedVersion.into());
    }
    let credential_policy = ArchivePolicy::default();
    let file_policy = |path: &str| login::auth_id(path).is_some();
    let credential_name = |name: &str| login::token_id(name).is_some();
    let policy = MDataPolicy {
        credential_policy: &credential_policy,
        file_path_validator: &file_policy,
        credential_name_validator: Some(&credential_name),
    };
    let mut pending: BTreeMap<String, SharedBytes> = BTreeMap::new();
    let mut sink = |path: &str, size: u64| -> std::result::Result<Box<dyn Write>, MDataError> {
        if login::auth_id(path).is_none()
            || !(1..=login::MAX_AUTH_BYTES as u64).contains(&size)
            || pending.contains_key(path)
            || pending.len() >= login::MAX_ACCOUNTS
        {
            return Err(MDataError::InvalidPayload);
        }
        let mut data = Zeroizing::new(Vec::new());
        data.try_reserve_exact(size as usize)
            .map_err(|_| MDataError::IoError)?;
        let bytes = Rc::new(RefCell::new(data));
        pending.insert(path.to_owned(), bytes.clone());
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
    let manifest = Manifest(OwnedValue(archive::decrypt_stream(
        &mut Cursor::new(bytes),
        password,
        &mut sink,
        &policy,
        Some(&mut preflight),
        Some(ArchiveKind::Login),
    )?));
    // Every MemorySink is gone and every frame, terminal and EOF is verified.
    // Do not expose any of these values before the compact validator succeeds.
    let tokens = decode_tokens(manifest.value())?;
    let validated = login::validate(manifest.value(), &tokens)?;
    let mut auth = BTreeMap::new();
    for (path, bytes) in pending {
        let bytes = Rc::try_unwrap(bytes)
            .map_err(|_| ImportArchiveError::IoError)?
            .into_inner();
        if !validated
            .files
            .iter()
            .any(|entry| entry.path == path && entry.size == bytes.len())
        {
            return Err(LoginError::InvalidPayload.into());
        }
        auth.insert(path, bytes);
    }
    if auth.len() != validated.files.len() {
        return Err(LoginError::InvalidPayload.into());
    }
    Ok(DecodedLogin {
        manifest,
        tokens,
        auth,
    })
}

/// Validate request scalars before filesystem access. Read only the selected
/// regular, single-link file under pinned ancestors, capped at 2 MiB; its read
/// handle denies write/delete sharing. The fingerprint and decrypt use that
/// same bounded byte snapshot, never a second pathname open. No file is created.
pub fn load(file: &str, password: &str, expected_fingerprint: &str) -> Result<DecodedLogin> {
    session::password_bytes(password).map_err(MDataError::from)?;
    let expected = fingerprint(expected_fingerprint)?;
    let path = selected_file_url(file).map_err(|_| ImportArchiveError::UnsafePath)?;
    if path
        .components()
        .any(|part| part.as_os_str().eq_ignore_ascii_case("ONEME"))
    {
        return Err(ImportArchiveError::UnsafePath);
    }
    if !path
        .extension()
        .is_some_and(|extension| extension.eq_ignore_ascii_case("mdata"))
    {
        return Err(MDataError::InvalidArchive.into());
    }
    let safe = SafeStorePath::new(&path).map_err(store_error)?;
    let held = safe
        .begin(false)
        .map_err(store_error)?
        .ok_or(ImportArchiveError::IoError)?;
    let bytes = Zeroizing::new(
        held.read_bounded(MAX_ARCHIVE_BYTES)
            .map_err(store_error)?
            .ok_or(ImportArchiveError::IoError)?,
    );
    decode(&bytes, password, &expected)
}

#[cfg(all(test, windows))]
#[path = "mdata_import_archive_tests.rs"]
mod tests;
