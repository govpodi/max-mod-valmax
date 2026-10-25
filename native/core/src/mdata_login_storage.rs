//! Filesystem ledger for compact login import into one explicit MXMOD root.
//! The caller holds the transfer mutex and proves participating clients closed.
//! This module never discovers profiles, traverses user directories or calls
//! WinCred. Tokens must be committed only after ensure_auth + revalidate.
//!
//! On failure the caller rolls back tokens FIRST, then calls rollback here.
//! Drop only releases leases: implicit deletion after a token commit/panic could
//! leave that token without its auth file. A crash can retain a valid subset;
//! this is not a multi-file/credential atomic transaction.
use crate::{
    mdata_login as login,
    settings_io::{GuardedStore, SafeStorePath, StoreError},
};
use std::{
    fmt,
    fs::File,
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StorageError {
    InvalidRequest,
    InvalidPayload,
    UnsafePath,
    InvalidProfile,
    ProfileCollision,
    ProfileChanged,
    IoError,
    RollbackFailed,
}
impl StorageError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidPayload => "invalid_payload",
            Self::UnsafePath => "unsafe_path",
            Self::InvalidProfile => "invalid_profile",
            Self::ProfileCollision => "profile_collision",
            Self::ProfileChanged => "profile_changed",
            Self::IoError => "io_error",
            Self::RollbackFailed => "rollback_failed",
        }
    }
}
impl fmt::Display for StorageError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for StorageError {}
pub type Result<T> = std::result::Result<T, StorageError>;
fn store_error(error: StoreError) -> StorageError {
    match error {
        StoreError::UnsafePath => StorageError::UnsafePath,
        _ => StorageError::IoError,
    }
}

#[cfg(windows)]
#[path = "mdata_login_storage/win.rs"]
mod win;
#[derive(Clone, Copy, PartialEq, Eq)]
struct Identity {
    volume: u64,
    id: [u8; 16],
}
struct Pin {
    file: File,
    identity: Identity,
}
struct Auth {
    pin: Pin,
    bytes: Zeroizing<Vec<u8>>,
    created: bool,
    // A failed exclusive write has never been handed to a token writer. Its
    // partial bytes are owned, but must not be mistaken for a completed auth.
    complete: bool,
}
struct AccountSlot {
    id: String,
    user: Option<Pin>,
    auth: Option<Auth>,
    ensured: bool,
}
struct Profile {
    root: PathBuf,
    #[cfg(windows)]
    directory: win::Directory,
    _guard: GuardedStore,
}

/// Bounded, worker-owned filesystem state. No Debug implementation for bytes.
pub struct LoginStorage {
    profile: Option<Profile>,
    slots: Vec<AccountSlot>,
    created: Vec<usize>,
    prepared: bool,
    failed: bool,
    terminal: bool,
    rollback_failed: bool,
    #[cfg(test)]
    fault: Fault,
}
impl LoginStorage {
    /// Opens existing ancestors/root only; config.json itself is not read.
    pub fn open(root: &Path) -> Result<Self> {
        let safe = SafeStorePath::new(root.join("config.json")).map_err(store_error)?;
        let root = safe
            .path()
            .parent()
            .ok_or(StorageError::UnsafePath)?
            .to_owned();
        if root.file_name().is_none_or(|name| name != "MXMOD")
            || root
                .components()
                .any(|p| p.as_os_str().eq_ignore_ascii_case("ONEME"))
        {
            return Err(StorageError::UnsafePath);
        }
        let guard = safe
            .begin(false)
            .map_err(store_error)?
            .ok_or(StorageError::InvalidProfile)?;
        #[cfg(windows)]
        {
            let directory = win::Directory::open(&root)?;
            Ok(Self {
                profile: Some(Profile {
                    root,
                    directory,
                    _guard: guard,
                }),
                slots: Vec::new(),
                created: Vec::new(),
                prepared: false,
                failed: false,
                terminal: false,
                rollback_failed: false,
                #[cfg(test)]
                fault: Fault::None,
            })
        }
        #[cfg(not(windows))]
        {
            let _ = (root, guard);
            Err(StorageError::IoError)
        }
    }
    /// Every ID is checked before any write. Existing auth may be empty or
    /// arbitrary non-JSON bytes; existing user contents are never enumerated.
    pub fn prepare(&mut self, ids: &[String]) -> Result<()> {
        if self.prepared
            || self.terminal
            || !(1..=login::MAX_ACCOUNTS).contains(&ids.len())
            || ids.iter().any(|id| !login::account_id(id))
        {
            return Err(StorageError::InvalidRequest);
        }
        let mut sorted = ids.to_vec();
        sorted.sort();
        sorted.dedup();
        if sorted.len() != ids.len() {
            return Err(StorageError::InvalidRequest);
        }
        #[cfg(windows)]
        {
            let profile = self.profile.as_ref().ok_or(StorageError::InvalidRequest)?;
            let mut slots = Vec::with_capacity(ids.len());
            for id in sorted {
                let user = profile
                    .directory
                    .user(&profile.root, &format!("user{id}"))?;
                let auth = match profile
                    .directory
                    .auth(&profile.root, &format!("auth_{id}"))?
                {
                    Some(mut pin) => {
                        let bytes = read_auth(&mut pin.file)?;
                        if win::verify(&pin.file, &profile.root.join(format!("auth_{id}")), false)?
                            != pin.identity
                        {
                            return Err(StorageError::ProfileChanged);
                        }
                        Some(Auth {
                            pin,
                            bytes,
                            created: false,
                            complete: true,
                        })
                    }
                    None => None,
                };
                slots.push(AccountSlot {
                    id,
                    user,
                    auth,
                    ensured: false,
                });
            }
            self.slots = slots;
            self.prepared = true;
            Ok(())
        }
        #[cfg(not(windows))]
        {
            let _ = sorted;
            Err(StorageError::IoError)
        }
    }
    /// Preserve an existing auth verbatim. Otherwise create only an explicitly
    /// supplied bounded auth, exclusively, and retain its handle through commit.
    pub fn ensure_auth(&mut self, id: &str, incoming: Option<&[u8]>) -> Result<()> {
        if incoming.is_some_and(|bytes| bytes.is_empty() || bytes.len() > login::MAX_AUTH_BYTES) {
            return Err(StorageError::InvalidPayload);
        }
        let index = self.slot_index(id)?;
        if self.slots[index].ensured {
            return Err(StorageError::InvalidRequest);
        }
        self.slots[index].ensured = true;
        if self.slots[index].auth.is_some() || incoming.is_none() {
            return Ok(());
        }
        let bytes = incoming.unwrap();
        self.failed = true;
        #[cfg(windows)]
        {
            let profile = self.profile.as_ref().ok_or(StorageError::InvalidRequest)?;
            let path = profile.root.join(format!("auth_{id}"));
            let pin = profile
                .directory
                .create_auth(&profile.root, &format!("auth_{id}"))?;
            // Register ownership before the first potentially partial write.
            self.slots[index].auth = Some(Auth {
                pin,
                bytes: Zeroizing::new(bytes.to_vec()),
                created: true,
                complete: false,
            });
            self.created.push(index);
            let auth = self.slots[index].auth.as_mut().unwrap();
            let result = (|| {
                #[cfg(test)]
                if matches!(self.fault, Fault::Write | Fault::Cleanup) {
                    auth.pin
                        .file
                        .write_all(&bytes[..bytes.len().min(2)])
                        .map_err(|_| StorageError::IoError)?;
                    return Err(StorageError::IoError);
                }
                auth.pin
                    .file
                    .write_all(bytes)
                    .map_err(|_| StorageError::IoError)?;
                #[cfg(test)]
                if self.fault == Fault::Flush {
                    return Err(StorageError::IoError);
                }
                auth.pin
                    .file
                    .sync_all()
                    .map_err(|_| StorageError::IoError)?;
                verify_auth(auth, &path).map_err(|_| StorageError::ProfileChanged)?;
                auth.complete = true;
                Ok(())
            })();
            if let Err(error) = result {
                #[cfg(test)]
                let cleanup = if self.fault == Fault::Cleanup {
                    Err(StorageError::RollbackFailed)
                } else {
                    remove_created(auth, &path)
                };
                #[cfg(not(test))]
                let cleanup = remove_created(auth, &path);
                if cleanup.is_err() {
                    return Err(StorageError::RollbackFailed);
                }
                self.slots[index].auth = None;
                self.created.pop();
                return Err(error);
            }
            self.failed = false;
            Ok(())
        }
        #[cfg(not(windows))]
        {
            let _ = bytes;
            Err(StorageError::IoError)
        }
    }
    /// Revalidate this account immediately before its credential write. Missing
    /// entries are observations, not reservations: a non-cooperating process can
    /// still create one afterwards; closed clients + transfer mutex are required.
    pub fn revalidate(&mut self, id: &str) -> Result<()> {
        let index = self.slot_index(id)?;
        if !self.slots[index].ensured {
            return Err(StorageError::InvalidRequest);
        }
        #[cfg(windows)]
        {
            let profile = self.profile.as_ref().ok_or(StorageError::InvalidRequest)?;
            let slot = &mut self.slots[index];
            let user_name = format!("user{id}");
            let auth_name = format!("auth_{id}");
            if let Some(user) = &slot.user {
                if win::verify(&user.file, &profile.root.join(&user_name), true)
                    .map_err(|_| StorageError::ProfileChanged)?
                    != user.identity
                {
                    return Err(StorageError::ProfileChanged);
                }
            } else if profile
                .directory
                .exists(&user_name)
                .map_err(|_| StorageError::ProfileChanged)?
            {
                return Err(StorageError::ProfileChanged);
            }
            if let Some(auth) = &mut slot.auth {
                if !auth.complete {
                    return Err(StorageError::ProfileChanged);
                }
                verify_auth(auth, &profile.root.join(auth_name))
                    .map_err(|_| StorageError::ProfileChanged)?;
            } else if profile
                .directory
                .exists(&auth_name)
                .map_err(|_| StorageError::ProfileChanged)?
            {
                return Err(StorageError::ProfileChanged);
            }
            Ok(())
        }
        #[cfg(not(windows))]
        {
            Err(StorageError::IoError)
        }
    }
    /// Explicit rollback, after the caller has rolled back its token journal.
    /// Foreign replacements/changed bytes are preserved and reported as failure.
    pub fn rollback(&mut self) -> Result<()> {
        if self.terminal {
            return if self.rollback_failed {
                Err(StorageError::RollbackFailed)
            } else {
                Ok(())
            };
        }
        #[cfg(windows)]
        if let Some(profile) = &self.profile {
            for &index in self.created.iter().rev() {
                let slot = &mut self.slots[index];
                if let Some(auth) = &mut slot.auth {
                    if auth.created
                        && remove_created(auth, &profile.root.join(format!("auth_{}", slot.id)))
                            .is_err()
                    {
                        self.rollback_failed = true;
                    }
                }
            }
        }
        self.slots.clear();
        self.created.clear();
        self.profile = None;
        self.terminal = true;
        if self.rollback_failed {
            Err(StorageError::RollbackFailed)
        } else {
            Ok(())
        }
    }
    /// No I/O and no fallible publication. The caller already committed tokens.
    pub fn commit(&mut self) {
        self.slots.clear();
        self.created.clear();
        self.profile = None;
        self.terminal = true;
    }
    fn slot_index(&self, id: &str) -> Result<usize> {
        if !self.prepared || self.failed || self.terminal {
            return Err(StorageError::InvalidRequest);
        }
        self.slots
            .iter()
            .position(|slot| slot.id == id)
            .ok_or(StorageError::InvalidRequest)
    }
}
// No Drop rollback: File/Zeroizing destructors only release owned resources.
fn read_auth(file: &mut File) -> Result<Zeroizing<Vec<u8>>> {
    file.seek(SeekFrom::Start(0))
        .map_err(|_| StorageError::IoError)?;
    // Reserve the entire bounded read so realloc cannot leave older auth-byte
    // copies in freed allocations. Zeroizing erases the final allocation.
    let mut bytes = Zeroizing::new(Vec::with_capacity(login::MAX_AUTH_BYTES + 1));
    file.take((login::MAX_AUTH_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| StorageError::IoError)?;
    if bytes.len() > login::MAX_AUTH_BYTES {
        return Err(StorageError::InvalidProfile);
    }
    Ok(bytes)
}
#[cfg(windows)]
fn verify_auth(auth: &mut Auth, path: &Path) -> Result<()> {
    if win::verify(&auth.pin.file, path, false)? != auth.pin.identity {
        return Err(StorageError::ProfileChanged);
    }
    let observed = read_auth(&mut auth.pin.file)?;
    if !bool::from(observed.as_slice().ct_eq(auth.bytes.as_slice())) {
        return Err(StorageError::ProfileChanged);
    }
    if win::verify(&auth.pin.file, path, false)? != auth.pin.identity {
        return Err(StorageError::ProfileChanged);
    }
    Ok(())
}
#[cfg(windows)]
fn remove_created(auth: &mut Auth, path: &Path) -> Result<()> {
    if !auth.created {
        return Err(StorageError::RollbackFailed);
    }
    if auth.complete {
        verify_auth(auth, path).map_err(|_| StorageError::RollbackFailed)?;
    } else if win::verify(&auth.pin.file, path, false).map_err(|_| StorageError::RollbackFailed)?
        != auth.pin.identity
    {
        return Err(StorageError::RollbackFailed);
    }
    win::delete(&auth.pin.file).map_err(|_| StorageError::RollbackFailed)
}

#[cfg(test)]
#[derive(Clone, Copy, PartialEq, Eq)]
enum Fault {
    None,
    Write,
    Flush,
    Cleanup,
}
#[cfg(all(test, windows))]
#[path = "mdata_login_storage/tests.rs"]
mod tests;
