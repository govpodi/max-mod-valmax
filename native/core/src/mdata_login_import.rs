//! Compact login-add transaction, invoked only by the native transfer owner.
//! The caller must hold TransferLease and have verified closed clients. This
//! module is deliberately not a Runtime/QML route: it cannot survive MAX exit
//! on its own, and a UI-provided boolean is not proof of closed clients.
//! Existing active tokens and stale metadata are preserved. Credentials have
//! cooperative expected-value checks, not an atomic cross-process CAS.
use crate::{
    login_credentials::{CredentialError, LoginCredentials},
    mdata_login::{self as login, LoginTokens},
};
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};
use zeroize::Zeroizing;

pub type AuthFiles = BTreeMap<String, Zeroizing<Vec<u8>>>;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ImportError {
    InvalidRequest,
    InvalidPayload,
    InvalidProfile,
    AccountLimit,
    NoAccounts,
    UnsafePath,
    ProfileCollision,
    ProfileChanged,
    IoError,
    CredentialIo,
    DestinationNotEmpty,
    RollbackFailed,
}
impl ImportError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidPayload => "invalid_payload",
            Self::InvalidProfile => "invalid_profile",
            Self::AccountLimit => "account_limit",
            Self::NoAccounts => "no_accounts",
            Self::UnsafePath => "unsafe_path",
            Self::ProfileCollision => "profile_collision",
            Self::ProfileChanged => "profile_changed",
            Self::IoError => "io_error",
            Self::CredentialIo => "credential_io",
            Self::DestinationNotEmpty => "destination_not_empty",
            Self::RollbackFailed => "rollback_failed",
        }
    }
    fn storage(code: &str) -> Self {
        match code {
            "invalid_request" => Self::InvalidRequest,
            "invalid_payload" => Self::InvalidPayload,
            "invalid_profile" => Self::InvalidProfile,
            "unsafe_path" => Self::UnsafePath,
            "profile_collision" => Self::ProfileCollision,
            "profile_changed" => Self::ProfileChanged,
            "rollback_failed" => Self::RollbackFailed,
            _ => Self::IoError,
        }
    }
}
impl fmt::Display for ImportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for ImportError {}
pub type Result<T> = std::result::Result<T, ImportError>;
fn credential(error: CredentialError) -> ImportError {
    match error {
        CredentialError::InvalidPayload => ImportError::InvalidProfile,
        CredentialError::CredentialIo => ImportError::CredentialIo,
        CredentialError::DestinationNotEmpty => ImportError::DestinationNotEmpty,
    }
}

/// A preflight owns only direct user/auth entries. Drop releases handles; it
/// must not delete metadata while a committed token may still refer to it.
pub trait ImportStorage {
    fn prepare(&mut self, ids: &[String]) -> Result<()>;
    fn ensure_auth(&mut self, id: &str, incoming: Option<&[u8]>) -> Result<()>;
    fn revalidate(&mut self, id: &str) -> Result<()>;
    fn rollback(&mut self) -> Result<()>;
    fn commit(&mut self);
}
impl ImportStorage for crate::mdata_login_storage::LoginStorage {
    fn prepare(&mut self, ids: &[String]) -> Result<()> {
        Self::prepare(self, ids).map_err(|e| ImportError::storage(e.code()))
    }
    fn ensure_auth(&mut self, id: &str, incoming: Option<&[u8]>) -> Result<()> {
        Self::ensure_auth(self, id, incoming).map_err(|e| ImportError::storage(e.code()))
    }
    fn revalidate(&mut self, id: &str) -> Result<()> {
        Self::revalidate(self, id).map_err(|e| ImportError::storage(e.code()))
    }
    fn rollback(&mut self) -> Result<()> {
        Self::rollback(self).map_err(|e| ImportError::storage(e.code()))
    }
    fn commit(&mut self) {
        Self::commit(self)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ImportResult {
    pub schema: u8,
    pub result: &'static str,
    pub profiles: usize,
}
struct Current {
    active: LoginTokens,
    empty: BTreeSet<String>,
}
fn inventory(credentials: &dyn LoginCredentials) -> Result<Current> {
    let names = credentials.owned_token_names().map_err(credential)?;
    // The production adapter also enforces this before returning names. Retain
    // the check for alternate adapters and validate the entire list first.
    if names.len() > 256 {
        return Err(ImportError::InvalidProfile);
    }
    let mut own = BTreeSet::new();
    for name in names {
        if login::token_id(&name).is_some() {
            if !own.insert(name) {
                return Err(ImportError::InvalidProfile);
            }
        } else {
            if name
                .get(..13)
                .is_some_and(|p| p.eq_ignore_ascii_case("access_token_"))
                && name
                    .get(name.len().saturating_sub(6)..)
                    .is_some_and(|p| p.eq_ignore_ascii_case("@MXMOD"))
            {
                return Err(ImportError::InvalidProfile);
            }
        }
    }
    let mut current = Current {
        active: LoginTokens::new(),
        empty: BTreeSet::new(),
    };
    for name in own {
        match credentials.read(&name).map_err(credential)? {
            None => {}
            Some(blob) if blob.is_empty() => {
                current.empty.insert(name);
            }
            Some(blob) => {
                if blob.len() > login::MAX_BLOB {
                    return Err(ImportError::InvalidProfile);
                }
                current.active.insert(name, blob);
                if current.active.len() > login::MAX_ACCOUNTS {
                    return Err(ImportError::AccountLimit);
                }
            }
        }
    }
    Ok(current)
}
struct TokenWrite<'a> {
    name: &'a str,
    value: &'a [u8],
    previous_empty: bool,
}
fn rollback_tokens(credentials: &dyn LoginCredentials, ledger: &[TokenWrite<'_>]) -> bool {
    let mut failed = false;
    for entry in ledger.iter().rev() {
        let result = if entry.previous_empty {
            match credentials.read(entry.name) {
                Ok(Some(value)) if value.is_empty() => Ok(()),
                Ok(_) => credentials.set_owned(entry.name, &[], Some(entry.value)),
                Err(error) => Err(error),
            }
        } else {
            credentials.delete_owned(entry.name, entry.value)
        };
        failed |= result.is_err();
    }
    !failed
}

/// Apply an already authenticated compact snapshot. All semantic validation
/// still occurs before credential inventory or filesystem mutations. On an
/// ordinary error, roll back tokens before deleting any newly created auth.
/// A failed token rollback retains auth files so an active partial login is
/// not left without its metadata. The owner must not auto-restart on failure.
pub fn apply(
    manifest: &Value,
    tokens: &LoginTokens,
    auth: &AuthFiles,
    credentials: &dyn LoginCredentials,
    storage: &mut dyn ImportStorage,
) -> Result<ImportResult> {
    let validated = login::validate(manifest, tokens).map_err(|error| match error {
        login::LoginError::AccountLimit => ImportError::AccountLimit,
        login::LoginError::NoAccounts => ImportError::NoAccounts,
        _ => ImportError::InvalidPayload,
    })?;
    if auth.len() != validated.files.len()
        || validated.files.iter().any(|file| {
            !auth
                .get(&file.path)
                .is_some_and(|bytes| bytes.len() == file.size)
        })
    {
        return Err(ImportError::InvalidPayload);
    }
    let current = inventory(credentials)?;
    let new_ids: Vec<_> = validated
        .account_ids
        .iter()
        .filter(|id| {
            !current
                .active
                .contains_key(&format!("access_token_{id}@MXMOD"))
        })
        .cloned()
        .collect();
    if new_ids.is_empty() {
        return Ok(ImportResult {
            schema: 1,
            result: "already_present",
            profiles: 0,
        });
    }
    if current.active.len() + new_ids.len() > login::MAX_ACCOUNTS {
        return Err(ImportError::AccountLimit);
    }
    storage.prepare(&new_ids)?;
    let mut ledger = Vec::new();
    let result = (|| {
        for id in &new_ids {
            let name = format!("auth_{id}");
            storage.ensure_auth(id, auth.get(&name).map(|bytes| bytes.as_slice()))?;
            storage.revalidate(id)?;
            let target = format!("access_token_{id}@MXMOD");
            let (target, value) = tokens
                .get_key_value(&target)
                .ok_or(ImportError::InvalidPayload)?;
            let previous_empty = current.empty.contains(target);
            // Include a possible committed-but-error WinAPI call in rollback.
            ledger.push(TokenWrite {
                name: target,
                value,
                previous_empty,
            });
            if let Err(error) =
                credentials.set_owned(target, value, previous_empty.then_some(&[][..]))
            {
                if error == CredentialError::DestinationNotEmpty {
                    // Contract: this finite error reports pre-write conflict.
                    // Even byte-identical foreign values are not our writes.
                    ledger.pop();
                }
                return Err(credential(error));
            }
        }
        Ok(())
    })();
    if let Err(error) = result {
        if !rollback_tokens(credentials, &ledger) {
            return Err(ImportError::RollbackFailed);
        }
        if storage.rollback().is_err() {
            return Err(ImportError::RollbackFailed);
        }
        return Err(error);
    }
    storage.commit();
    Ok(ImportResult {
        schema: 1,
        result: "added",
        profiles: new_ids.len(),
    })
}

#[cfg(test)]
#[path = "mdata_login_import_tests.rs"]
mod tests;
