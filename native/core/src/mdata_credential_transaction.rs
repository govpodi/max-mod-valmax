//! Credential part of explicit full-profile recovery. No profile discovery,
//! filesystem changes, network access, or credential mutations on Drop.
//! The owner must hold TransferLease, verify closed clients, and retain its
//! recovery marker until both credentials and profile publication succeed.
//! Expected-value checks are cooperative; WinCred offers no atomic CAS.
use crate::{
    mdata_credentials::{CredentialError, RecoveryCredentials},
    mdata_profile,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

const MAX_KEYS: usize = 256;
const MAX_BLOB: usize = crate::mdata_login::MAX_BLOB;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TransactionError {
    InvalidRequest,
    InvalidPayload,
    CredentialIo,
    DestinationNotEmpty,
    ProfileChanged,
    RollbackFailed,
}
impl TransactionError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidPayload => "invalid_payload",
            Self::CredentialIo => "credential_io",
            Self::DestinationNotEmpty => "destination_not_empty",
            Self::ProfileChanged => "profile_changed",
            Self::RollbackFailed => "rollback_failed",
        }
    }
}
impl fmt::Display for TransactionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for TransactionError {}
impl From<CredentialError> for TransactionError {
    fn from(error: CredentialError) -> Self {
        match error {
            CredentialError::InvalidPayload => Self::InvalidPayload,
            CredentialError::CredentialIo => Self::CredentialIo,
            CredentialError::DestinationNotEmpty => Self::DestinationNotEmpty,
        }
    }
}
pub type Result<T> = std::result::Result<T, TransactionError>;

/// No Debug/Clone/Serialize: values may contain session keys. None and an
/// explicitly stored empty blob are distinct throughout apply and rollback.
pub struct RecoveryKeys(BTreeMap<String, Zeroizing<Vec<u8>>>);
impl RecoveryKeys {
    pub fn new(values: BTreeMap<String, Zeroizing<Vec<u8>>>) -> Result<Self> {
        validate_names(values.keys().map(String::as_str))?;
        if values.values().any(|value| value.len() > MAX_BLOB) {
            return Err(TransactionError::InvalidPayload);
        }
        Ok(Self(values))
    }
    pub fn len(&self) -> usize {
        self.0.len()
    }
    pub fn is_empty(&self) -> bool {
        self.0.is_empty()
    }
    fn get(&self, name: &str) -> Option<&[u8]> {
        self.0.get(name).map(|value| value.as_slice())
    }
}
fn validate_names<'a>(names: impl IntoIterator<Item = &'a str>) -> Result<()> {
    let mut seen = BTreeSet::new();
    for name in names {
        // Every allowed name is ASCII, so this also catches Windows aliases.
        if !mdata_profile::allowed_name(name)
            || !seen.insert(name.to_ascii_lowercase())
            || seen.len() > MAX_KEYS
        {
            return Err(TransactionError::InvalidPayload);
        }
    }
    Ok(())
}
fn equal(left: Option<&[u8]>, right: Option<&[u8]>) -> bool {
    match (left, right) {
        (None, None) => true,
        (Some(left), Some(right)) => bool::from(left.ct_eq(right)),
        _ => false,
    }
}
fn same_snapshot(left: &RecoveryKeys, right: &RecoveryKeys) -> bool {
    left.len() == right.len()
        && left
            .0
            .iter()
            .all(|(name, value)| equal(Some(value), right.get(name)))
}
fn snapshot(store: &dyn RecoveryCredentials) -> Result<RecoveryKeys> {
    let names = store.owned_names()?;
    // Validate the entire metadata inventory before the first value read.
    validate_names(names.iter().map(String::as_str))?;
    let mut values = BTreeMap::new();
    for name in names {
        let value = store.read(&name)?.ok_or(TransactionError::ProfileChanged)?;
        if value.len() > MAX_BLOB {
            return Err(TransactionError::InvalidPayload);
        }
        values.insert(name, value);
    }
    RecoveryKeys::new(values)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TransactionState {
    Prepared,
    Applied,
    RolledBack,
    RollbackFailed,
    Committed,
}

/// Holds both snapshots until dropped; only a bounded count/state is exposed.
/// `apply` rolls back on any error. After a successful apply the profile owner
/// must explicitly call rollback or commit; Drop never writes to WinCred.
pub struct CredentialTransaction<'a> {
    store: &'a dyn RecoveryCredentials,
    before: RecoveryKeys,
    after: RecoveryKeys,
    names: Vec<String>,
    attempted: Vec<usize>,
    state: TransactionState,
}
impl<'a> CredentialTransaction<'a> {
    pub fn prepare(store: &'a dyn RecoveryCredentials, after: RecoveryKeys) -> Result<Self> {
        let before = snapshot(store)?;
        let union = before
            .0
            .keys()
            .chain(after.0.keys())
            .collect::<BTreeSet<_>>();
        let mut folded = BTreeSet::new();
        if union
            .iter()
            .any(|name| !folded.insert(name.to_ascii_lowercase()))
        {
            // WinCred is case-insensitive. Recovery does not rename keys whose
            // exact spelling differs between the saved and current snapshots.
            return Err(TransactionError::InvalidPayload);
        }
        let names: Vec<_> = union
            .into_iter()
            .filter(|name| !equal(before.get(name), after.get(name)))
            .cloned()
            .collect();
        // At most 512 changed names (disjoint bounded before/after snapshots).
        let attempted = Vec::with_capacity(names.len());
        Ok(Self {
            store,
            before,
            after,
            names,
            attempted,
            state: TransactionState::Prepared,
        })
    }
    pub fn state(&self) -> TransactionState {
        self.state
    }
    pub fn change_count(&self) -> usize {
        self.names.len()
    }
    pub fn apply(&mut self) -> Result<()> {
        if self.state != TransactionState::Prepared {
            return Err(TransactionError::InvalidRequest);
        }
        let current = snapshot(self.store)?;
        if !same_snapshot(&current, &self.before) {
            return Err(TransactionError::ProfileChanged);
        }
        for index in 0..self.names.len() {
            let name = &self.names[index];
            let old = self.before.get(name);
            let new = self.after.get(name);
            // A low-level failure can follow a committed write. Record the
            // attempt first, then distinguish a proven pre-write conflict.
            self.attempted.push(index);
            let result = match new {
                Some(value) => self.store.set_owned(name, value, old),
                None => self
                    .store
                    .delete_owned(name, old.expect("changed removal has an old value")),
            };
            if let Err(error) = result {
                if error == CredentialError::DestinationNotEmpty {
                    self.attempted.pop();
                }
                self.state = TransactionState::Applied;
                self.rollback()?;
                return Err(error.into());
            }
        }
        self.state = TransactionState::Applied;
        Ok(())
    }
    pub fn rollback(&mut self) -> Result<()> {
        if self.state == TransactionState::RolledBack {
            return Ok(());
        }
        if !matches!(
            self.state,
            TransactionState::Applied | TransactionState::RollbackFailed
        ) {
            return Err(TransactionError::InvalidRequest);
        }
        let mut failed = false;
        // Retain only unresolved attempts for an explicit retry. A completed
        // restoration must never become ours again if another writer later
        // stores byte-identical incoming content under the same name.
        for position in (0..self.attempted.len()).rev() {
            let index = self.attempted[position];
            let name = &self.names[index];
            let old = self.before.get(name);
            let new = self.after.get(name);
            let current = match self.store.read(name) {
                Ok(value) => value,
                Err(_) => {
                    failed = true;
                    continue;
                }
            };
            let current = current.as_ref().map(|value| value.as_slice());
            if equal(current, old) {
                self.attempted.remove(position);
                continue;
            }
            if !equal(current, new) {
                failed = true;
                continue;
            }
            let result = match old {
                Some(value) => self.store.set_owned(name, value, new),
                None => self
                    .store
                    .delete_owned(name, new.expect("changed addition has a new value")),
            };
            // Do not hide a reported failure even if it might have applied;
            // a deliberate retry can observe the restored value safely.
            if result.is_ok() {
                self.attempted.remove(position);
            } else {
                failed = true;
            }
        }
        self.state = if failed {
            TransactionState::RollbackFailed
        } else {
            TransactionState::RolledBack
        };
        if failed {
            Err(TransactionError::RollbackFailed)
        } else {
            Ok(())
        }
    }
    /// Call only after the prepared profile was published and verified.
    /// The owner then clears the exact recovery marker separately. A failure
    /// clearing that marker must not roll back an already committed profile.
    pub fn commit(&mut self) -> Result<()> {
        if self.state != TransactionState::Applied {
            return Err(TransactionError::InvalidRequest);
        }
        self.state = TransactionState::Committed;
        Ok(())
    }
}

#[cfg(test)]
#[path = "mdata_credential_transaction_tests.rs"]
mod tests;
