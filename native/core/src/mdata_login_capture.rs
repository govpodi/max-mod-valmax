//! Explicit, read-only compact login capture. Only direct auth_<ID> metadata
//! and exact access-token names are consulted. No recursive profile scan,
//! credential enumeration, cache/history read, import or profile creation.
//! The caller supplies the transfer mutex. Repeated checks form a bounded live
//! snapshot, not an atomic transaction with the running client or WinCred.

use crate::{
    login_credentials::LoginCredentials,
    mdata_login::{self as login, AuthFile, LoginError, LoginTokens},
    session_archive::{OwnedValue, SOURCE_CLIENT, SOURCE_VERSION},
    settings_io::{GuardedStore, SafeStorePath, StoreError},
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fmt,
    path::{Path, PathBuf},
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

pub const MAX_CANDIDATES: usize = 63;
/// Enumeration is metadata-only and additionally bounded even when the root
/// contains unrelated entries. Exceeding this limit never returns a partial list.
pub const MAX_DIRECTORY_ENTRIES: usize = 10_000;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CaptureError {
    InvalidRequest,
    InvalidProfile,
    InvalidPayload,
    UnsafePath,
    ProfileChanged,
    AccountLimit,
    NoAccounts,
    CredentialIo,
    IoError,
}
impl CaptureError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidProfile => "invalid_profile",
            Self::InvalidPayload => "invalid_payload",
            Self::UnsafePath => "unsafe_path",
            Self::ProfileChanged => "profile_changed",
            Self::AccountLimit => "account_limit",
            Self::NoAccounts => "no_accounts",
            Self::CredentialIo => "credential_io",
            Self::IoError => "io_error",
        }
    }
}
impl fmt::Display for CaptureError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for CaptureError {}
type Result<T> = std::result::Result<T, CaptureError>;
fn store_error(error: StoreError) -> CaptureError {
    match error {
        StoreError::UnsafePath => CaptureError::UnsafePath,
        StoreError::InvalidStore => CaptureError::ProfileChanged,
        _ => CaptureError::IoError,
    }
}
fn login_error(error: LoginError) -> CaptureError {
    match error {
        LoginError::InvalidRequest => CaptureError::InvalidRequest,
        LoginError::AccountLimit => CaptureError::AccountLimit,
        LoginError::NoAccounts => CaptureError::NoAccounts,
        _ => CaptureError::InvalidPayload,
    }
}

/// Sensitive manifest: deliberately no Debug/Display or unguarded owned clone.
/// Owned strings are erased on drop as a best effort, not a guarantee that
/// allocator/compiler/library copies have all been erased.
pub struct Manifest(OwnedValue);
impl Manifest {
    pub fn value(&self) -> &Value {
        &self.0.0
    }
}
/// Private transfer material, never a public status/diagnostic object.
pub struct Snapshot {
    pub manifest: Manifest,
    pub tokens: LoginTokens,
    pub auth: BTreeMap<String, Zeroizing<Vec<u8>>>,
}

struct HeldProfile {
    root: PathBuf,
    _guard: GuardedStore,
    #[cfg(windows)]
    directory: win::Directory,
}
impl HeldProfile {
    fn open(profile: &Path) -> Result<Self> {
        let safe = SafeStorePath::new(profile.join("config.json")).map_err(store_error)?;
        let root = safe
            .path()
            .parent()
            .ok_or(CaptureError::UnsafePath)?
            .to_owned();
        if root.file_name().is_none_or(|name| name != "MXMOD")
            || root
                .components()
                .any(|c| c.as_os_str().eq_ignore_ascii_case("ONEME"))
        {
            return Err(CaptureError::UnsafePath);
        }
        let guard = safe
            .begin(false)
            .map_err(store_error)?
            .ok_or(CaptureError::InvalidProfile)?;
        #[cfg(windows)]
        {
            let directory = win::Directory::open(&root)?;
            Ok(Self {
                root,
                _guard: guard,
                directory,
            })
        }
        #[cfg(not(windows))]
        {
            let _ = (root, guard);
            Err(CaptureError::IoError)
        }
    }
    fn listing(&self) -> Result<Vec<AuthFile>> {
        #[cfg(windows)]
        {
            self.directory.listing(&self.root)
        }
        #[cfg(not(windows))]
        {
            Err(CaptureError::IoError)
        }
    }
    fn read_auth(&self, name: &str, expected: usize) -> Result<Zeroizing<Vec<u8>>> {
        if login::auth_id(name).is_none() {
            return Err(CaptureError::InvalidPayload);
        }
        let safe = SafeStorePath::new(self.root.join(name)).map_err(store_error)?;
        let held = safe
            .begin(false)
            .map_err(store_error)?
            .ok_or(CaptureError::ProfileChanged)?;
        let bytes = Zeroizing::new(
            held.read_bounded(login::MAX_AUTH_BYTES + 1)
                .map_err(store_error)?
                .ok_or(CaptureError::ProfileChanged)?,
        );
        if !(1..=login::MAX_AUTH_BYTES).contains(&bytes.len()) || bytes.len() != expected {
            return Err(CaptureError::ProfileChanged);
        }
        Ok(bytes)
    }
}

/// Lists direct auth-file names and sizes only; never reads file contents or
/// credentials. Exact root must exist and end in the case-sensitive MXMOD name.
pub fn listing(profile: &Path) -> Result<Vec<AuthFile>> {
    HeldProfile::open(profile)?.listing()
}

fn credential_read(
    credentials: &dyn LoginCredentials,
    name: &str,
) -> Result<Option<Zeroizing<Vec<u8>>>> {
    credentials.read(name).map_err(|error| match error.code() {
        "invalid_payload" => CaptureError::InvalidPayload,
        _ => CaptureError::CredentialIo,
    })
}
fn same_token(a: &Option<Zeroizing<Vec<u8>>>, b: &Option<Zeroizing<Vec<u8>>>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(a), Some(b)) => bool::from(a.as_slice().ct_eq(b.as_slice())),
        _ => false,
    }
}

/// Selection is validated before filesystem or credential access. Every token
/// read is derived from a canonical direct auth filename; even unselected/stale
/// auth files must satisfy listing policy. A change discards the entire result.
pub fn capture(
    profile: &Path,
    credentials: &dyn LoginCredentials,
    selection: Option<&Value>,
) -> Result<Snapshot> {
    let selected = selection
        .map(login::selected_account_ids)
        .transpose()
        .map_err(login_error)?;
    let held = HeldProfile::open(profile)?;
    let candidates = held.listing()?;
    if selected.as_ref().is_some_and(|ids| {
        ids.iter().any(|id| {
            !candidates
                .iter()
                .any(|entry| login::auth_id(&entry.path) == Some(id.as_str()))
        })
    }) {
        return Err(CaptureError::InvalidRequest);
    }
    let mut tokens = LoginTokens::new();
    let mut auth = BTreeMap::new();
    let mut observed = BTreeMap::new();
    for entry in &candidates {
        let id = login::auth_id(&entry.path).ok_or(CaptureError::InvalidProfile)?;
        if selected
            .as_ref()
            .is_some_and(|ids| !ids.iter().any(|selected| selected == id))
        {
            continue;
        }
        let name = format!("access_token_{id}@MXMOD");
        let blob = credential_read(credentials, &name)?;
        if blob.as_ref().is_none_or(|bytes| bytes.is_empty()) {
            observed.insert(name, blob);
            if selected.is_some() {
                return Err(CaptureError::NoAccounts);
            }
            continue;
        }
        let value = blob.as_ref().unwrap();
        if value.len() > login::MAX_BLOB {
            return Err(CaptureError::InvalidProfile);
        }
        tokens.insert(name.clone(), value.clone());
        observed.insert(name, blob);
        if tokens.len() > login::MAX_ACCOUNTS {
            return Err(CaptureError::AccountLimit);
        }
        auth.insert(entry.path.clone(), held.read_auth(&entry.path, entry.size)?);
    }
    if tokens.is_empty() {
        return Err(CaptureError::NoAccounts);
    }
    if held.listing()? != candidates {
        return Err(CaptureError::ProfileChanged);
    }
    for (name, previous) in &observed {
        if !same_token(&credential_read(credentials, name)?, previous) {
            return Err(CaptureError::ProfileChanged);
        }
    }
    for (name, bytes) in &auth {
        let current = held.read_auth(name, bytes.len())?;
        if !bool::from(current.as_slice().ct_eq(bytes.as_slice())) {
            return Err(CaptureError::ProfileChanged);
        }
    }
    let mut manifest = Manifest(OwnedValue(json!({"schema":1,"sourceClient":SOURCE_CLIENT,
        "sourceVersion":SOURCE_VERSION,"files":[],"credentials":[]})));
    for (name, bytes) in &auth {
        manifest.0.0["files"]
            .as_array_mut()
            .unwrap()
            .push(json!({"path":name,"size":bytes.len()}));
    }
    for (name, bytes) in &tokens {
        let encoded = Zeroizing::new(STANDARD.encode(bytes));
        manifest.0.0["credentials"]
            .as_array_mut()
            .unwrap()
            .push(json!({"name":name,"blob":encoded.as_str()}));
    }
    login::validate(manifest.value(), &tokens).map_err(login_error)?;
    Ok(Snapshot {
        manifest,
        tokens,
        auth,
    })
}

#[cfg(windows)]
#[path = "mdata_login_capture/win.rs"]
mod win;
