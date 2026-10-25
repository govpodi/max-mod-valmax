//! Exact-name Windows Generic Credentials for explicit full-profile MData recovery.
//! Construction performs no I/O. Enumeration retains only policy-approved MXMOD
//! names and never dereferences any blob; value reads are separate exact calls.
//!
//! Windows Credential Manager has no compare-and-swap operation. `set_owned`
//! and `delete_owned` compare a snapshot before mutation, not atomically with
//! it. Mutation and rollback require the same session-transfer mutex and closed
//! participating clients. Read-only live capture is permitted with repeated
//! bounded snapshot checks by the caller. The mutex serializes cooperating
//! callers only; an unrelated writer can still race the WinAPI call.
//! No method logs targets or bytes. Secret results have no adapter-specific Debug.

use crate::{mdata_login::MAX_BLOB, mdata_profile::allowed_name};
use std::{collections::BTreeSet, ffi::c_void, fmt, ptr};
use subtle::ConstantTimeEq;
use zeroize::{Zeroize, Zeroizing};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum CredentialError {
    InvalidPayload,
    CredentialIo,
    DestinationNotEmpty,
}
impl CredentialError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidPayload => "invalid_payload",
            Self::CredentialIo => "credential_io",
            Self::DestinationNotEmpty => "destination_not_empty",
        }
    }
}
impl fmt::Display for CredentialError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for CredentialError {}
pub type Result<T> = std::result::Result<T, CredentialError>;

pub trait RecoveryCredentials {
    /// Explicit-job metadata inventory, sorted and bounded to 256 own names.
    /// No blob is read here. The caller must bound the eventual whole snapshot
    /// and repeat inventory before a full-profile mutation; individual writes
    /// deliberately do not enumerate the user's credential store.
    fn owned_names(&self) -> Result<Vec<String>>;
    fn read(&self, name: &str) -> Result<Option<Zeroizing<Vec<u8>>>>;
    /// `None` requires an absent target; `Some(&[])` requires an existing empty
    /// value. Empty replacement is permitted to restore that previous state.
    fn set_owned(&self, name: &str, blob: &[u8], expected: Option<&[u8]>) -> Result<()>;
    /// Missing or mismatched current value is DestinationNotEmpty before any
    /// native delete attempt. Unlike compact-login rollback, full recovery must
    /// not classify an already-absent record as its own successful deletion:
    /// a later rollback could otherwise recreate a value removed by a foreign
    /// writer. Native failure after the matched read is CredentialIo and needs
    /// the coordinator's reread/rollback handling. Requires the guard above.
    fn delete_owned(&self, name: &str, expected: &[u8]) -> Result<()>;
}

/// Stateless exact-name adapter. No credential API is called by `new/default`.
/// Mutation callers must satisfy the transfer/closed-client preconditions above.
#[derive(Default)]
pub struct WindowsCredentials;
impl WindowsCredentials {
    pub const fn new() -> Self {
        Self
    }
}
impl fmt::Debug for WindowsCredentials {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("<MXMOD recovery credentials>")
    }
}
impl RecoveryCredentials for WindowsCredentials {
    fn owned_names(&self) -> Result<Vec<String>> {
        Adapter(SystemApi).owned_names()
    }
    fn read(&self, name: &str) -> Result<Option<Zeroizing<Vec<u8>>>> {
        Adapter(SystemApi).read(name)
    }
    fn set_owned(&self, name: &str, blob: &[u8], expected: Option<&[u8]>) -> Result<()> {
        Adapter(SystemApi).set_owned(name, blob, expected)
    }
    fn delete_owned(&self, name: &str, expected: &[u8]) -> Result<()> {
        Adapter(SystemApi).delete_owned(name, expected)
    }
}

const GENERIC: u32 = 1;
const LOCAL_MACHINE: u32 = 2; // This user's local machine; no roaming added.
const NOT_FOUND: u32 = 1168;
const MAX_ENUM_ENTRIES: usize = 100_000;
const MAX_OWN_NAMES: usize = 256;
const MAX_TARGET_UNITS: usize = 32_767;
const MAX_ENUM_NAME_UNITS: usize = 1_048_576;

#[repr(C)]
#[derive(Default)]
struct FileTime {
    low: u32,
    high: u32,
}
#[repr(C)]
struct RawCredential {
    flags: u32,
    kind: u32,
    target_name: *mut u16,
    comment: *mut u16,
    last_written: FileTime,
    blob_size: u32,
    blob: *mut u8,
    persist: u32,
    attribute_count: u32,
    attributes: *mut c_void,
    target_alias: *mut u16,
    username: *mut u16,
}
#[cfg(target_pointer_width = "64")]
const _: () = {
    assert!(std::mem::size_of::<RawCredential>() == 80);
    assert!(std::mem::align_of::<RawCredential>() == 8);
    assert!(std::mem::offset_of!(RawCredential, target_name) == 8);
    assert!(std::mem::offset_of!(RawCredential, blob_size) == 32);
    assert!(std::mem::offset_of!(RawCredential, blob) == 40);
    assert!(std::mem::offset_of!(RawCredential, username) == 72);
};

/// Private trusted boundary: successful calls return valid Windows-style owned
/// allocations, including NUL-terminated strings and arrays of `count` entries.
/// Allocations remain valid until exactly one free. Test fakes obey that memory
/// contract while injecting invalid metadata; no pointer comes from JSON/QML.
unsafe trait WinApi {
    unsafe fn enumerate(
        &self,
        filter: *const u16,
        count: *mut u32,
        result: *mut *mut *mut RawCredential,
    ) -> u32;
    unsafe fn read(&self, name: *const u16, result: *mut *mut RawCredential) -> u32;
    unsafe fn write(&self, entry: *const RawCredential) -> u32;
    unsafe fn delete(&self, name: *const u16) -> u32;
    unsafe fn free(&self, pointer: *mut c_void);
    #[cfg(test)]
    fn write_copy_wiped(&self, _bytes: &[u8]) {}
}
struct SystemApi;
#[cfg(windows)]
mod native {
    use super::*;
    #[allow(clashing_extern_declarations)]
    #[link(name = "advapi32")]
    unsafe extern "system" {
        pub(super) fn CredEnumerateW(
            filter: *const u16,
            flags: u32,
            count: *mut u32,
            result: *mut *mut *mut RawCredential,
        ) -> i32;
        pub(super) fn CredReadW(
            name: *const u16,
            kind: u32,
            flags: u32,
            result: *mut *mut RawCredential,
        ) -> i32;
        pub(super) fn CredWriteW(entry: *const RawCredential, flags: u32) -> i32;
        pub(super) fn CredDeleteW(name: *const u16, kind: u32, flags: u32) -> i32;
        pub(super) fn CredFree(pointer: *mut c_void);
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetLastError() -> u32;
    }
    pub(super) fn status(success: i32) -> u32 {
        if success != 0 {
            0
        } else {
            let error = unsafe { GetLastError() };
            if error == 0 {
                1
            } else {
                error
            }
        }
    }
}
#[cfg(windows)]
unsafe impl WinApi for SystemApi {
    unsafe fn enumerate(
        &self,
        filter: *const u16,
        count: *mut u32,
        result: *mut *mut *mut RawCredential,
    ) -> u32 {
        native::status(unsafe { native::CredEnumerateW(filter, 0, count, result) })
    }
    unsafe fn read(&self, name: *const u16, result: *mut *mut RawCredential) -> u32 {
        native::status(unsafe { native::CredReadW(name, GENERIC, 0, result) })
    }
    unsafe fn write(&self, entry: *const RawCredential) -> u32 {
        native::status(unsafe { native::CredWriteW(entry, 0) })
    }
    unsafe fn delete(&self, name: *const u16) -> u32 {
        native::status(unsafe { native::CredDeleteW(name, GENERIC, 0) })
    }
    unsafe fn free(&self, pointer: *mut c_void) {
        unsafe { native::CredFree(pointer) };
    }
}
#[cfg(not(windows))]
unsafe impl WinApi for SystemApi {
    unsafe fn enumerate(&self, _: *const u16, _: *mut u32, _: *mut *mut *mut RawCredential) -> u32 {
        1
    }
    unsafe fn read(&self, _: *const u16, _: *mut *mut RawCredential) -> u32 {
        1
    }
    unsafe fn write(&self, _: *const RawCredential) -> u32 {
        1
    }
    unsafe fn delete(&self, _: *const u16) -> u32 {
        1
    }
    unsafe fn free(&self, _: *mut c_void) {}
}

fn wide_name(name: &str) -> Result<Vec<u16>> {
    if !allowed_name(name) {
        return Err(CredentialError::InvalidPayload);
    }
    Ok(name.encode_utf16().chain(Some(0)).collect())
}
fn bounded_blob(blob: &[u8]) -> Result<()> {
    if blob.len() > MAX_BLOB {
        Err(CredentialError::InvalidPayload)
    } else {
        Ok(())
    }
}
unsafe fn target(pointer: *const u16) -> Result<String> {
    if pointer.is_null() {
        return Err(CredentialError::CredentialIo);
    }
    let mut length = 0;
    while length <= MAX_TARGET_UNITS {
        if unsafe { *pointer.add(length) } == 0 {
            return String::from_utf16(unsafe { std::slice::from_raw_parts(pointer, length) })
                .map_err(|_| CredentialError::CredentialIo);
        }
        length += 1;
    }
    Err(CredentialError::CredentialIo)
}

struct Allocation<'a, A: WinApi> {
    api: &'a A,
    pointer: *mut c_void,
    read: bool,
}
impl<A: WinApi> Drop for Allocation<'_, A> {
    fn drop(&mut self) {
        if self.pointer.is_null() {
            return;
        }
        unsafe {
            if self.read {
                let entry = &*self.pointer.cast::<RawCredential>();
                // Metadata corruption must not authorize an unbounded wipe.
                // Genuine WinCred blob sizes cannot exceed this API maximum.
                if !entry.blob.is_null() && entry.blob_size as usize <= MAX_BLOB {
                    std::slice::from_raw_parts_mut(entry.blob, entry.blob_size as usize).zeroize();
                }
            }
            self.api.free(self.pointer);
        }
    }
}
struct Adapter<A: WinApi>(A);
impl<A: WinApi> RecoveryCredentials for Adapter<A> {
    fn owned_names(&self) -> Result<Vec<String>> {
        let mut count = 0u32;
        let mut entries = ptr::null_mut();
        let status = unsafe { self.0.enumerate(ptr::null(), &mut count, &mut entries) };
        if status != 0 {
            return if status == NOT_FOUND {
                Ok(Vec::new())
            } else {
                Err(CredentialError::CredentialIo)
            };
        }
        let _allocation = Allocation {
            api: &self.0,
            pointer: entries.cast(),
            read: false,
        };
        if count as usize > MAX_ENUM_ENTRIES || count != 0 && entries.is_null() {
            return Err(CredentialError::CredentialIo);
        }
        let mut result = BTreeSet::new();
        let mut folded = BTreeSet::new();
        let mut name_units = 0usize;
        for index in 0..count as usize {
            let pointer = unsafe { *entries.add(index) };
            if pointer.is_null() {
                return Err(CredentialError::CredentialIo);
            }
            let entry = unsafe { &*pointer };
            if entry.kind != GENERIC {
                continue;
            }
            let name = unsafe { target(entry.target_name) }?;
            name_units = name_units
                .checked_add(name.encode_utf16().count())
                .ok_or(CredentialError::CredentialIo)?;
            if name_units > MAX_ENUM_NAME_UNITS {
                return Err(CredentialError::CredentialIo);
            }
            // Match the legacy inventory's exact suffix; unrelated namespaces
            // (including lowercase suffixes) are not selected for recovery.
            // WinCred itself is case-insensitive: read() must still verify its
            // returned target byte-for-byte before any set/delete mutation.
            if !name.ends_with("@MXMOD") {
                continue;
            }
            // All accepted names are ASCII, so this equals Python casefold.
            // Web-prefix case variants are individually valid but cannot both
            // appear in a coherent Windows credential inventory.
            if !allowed_name(&name)
                || !folded.insert(name.to_ascii_lowercase())
                || !result.insert(name)
                || result.len() > MAX_OWN_NAMES
            {
                return Err(CredentialError::InvalidPayload);
            }
        }
        Ok(result.into_iter().collect())
    }
    fn read(&self, name: &str) -> Result<Option<Zeroizing<Vec<u8>>>> {
        let wide = wide_name(name)?;
        let mut pointer = ptr::null_mut();
        let status = unsafe { self.0.read(wide.as_ptr(), &mut pointer) };
        if status != 0 {
            return if status == NOT_FOUND {
                Ok(None)
            } else {
                Err(CredentialError::CredentialIo)
            };
        }
        let _allocation = Allocation {
            api: &self.0,
            pointer: pointer.cast(),
            read: true,
        };
        if pointer.is_null() {
            return Err(CredentialError::CredentialIo);
        }
        let entry = unsafe { &*pointer };
        if entry.kind != GENERIC
            || unsafe { target(entry.target_name) }? != name
            || entry.blob_size as usize > MAX_BLOB
            || entry.blob_size != 0 && entry.blob.is_null()
        {
            return Err(CredentialError::CredentialIo);
        }
        let bytes = if entry.blob_size == 0 {
            Zeroizing::new(Vec::new())
        } else {
            Zeroizing::new(
                unsafe { std::slice::from_raw_parts(entry.blob, entry.blob_size as usize) }
                    .to_vec(),
            )
        };
        Ok(Some(bytes))
    }
    fn set_owned(&self, name: &str, blob: &[u8], expected: Option<&[u8]>) -> Result<()> {
        let mut wide = wide_name(name)?;
        bounded_blob(blob)?;
        if let Some(expected) = expected {
            bounded_blob(expected)?;
        }
        let current = self.read(name)?;
        let matches = match (current.as_ref(), expected) {
            (None, None) => true,
            (Some(current), Some(expected)) => bool::from(current.as_slice().ct_eq(expected)),
            _ => false,
        };
        if !matches {
            return Err(CredentialError::DestinationNotEmpty);
        }
        let mut copy = Zeroizing::new(blob.to_vec());
        let mut username = [0u16];
        let entry = RawCredential {
            flags: 0,
            kind: GENERIC,
            target_name: wide.as_mut_ptr(),
            comment: ptr::null_mut(),
            last_written: FileTime::default(),
            blob_size: copy.len() as u32,
            blob: if copy.is_empty() {
                ptr::null_mut()
            } else {
                copy.as_mut_ptr()
            },
            persist: LOCAL_MACHINE,
            attribute_count: 0,
            attributes: ptr::null_mut(),
            target_alias: ptr::null_mut(),
            username: username.as_mut_ptr(),
        };
        let status = unsafe { self.0.write(&entry) };
        // Explicitly zero while the allocation is still live; Drop also guards
        // every early/unwind path. Input bytes remain caller-owned and unchanged.
        copy.as_mut_slice().zeroize();
        #[cfg(test)]
        self.0.write_copy_wiped(copy.as_slice());
        if status == 0 {
            Ok(())
        } else {
            Err(CredentialError::CredentialIo)
        }
    }
    fn delete_owned(&self, name: &str, expected: &[u8]) -> Result<()> {
        let wide = wide_name(name)?;
        bounded_blob(expected)?;
        let Some(current) = self.read(name)? else {
            return Err(CredentialError::DestinationNotEmpty);
        };
        if !bool::from(current.as_slice().ct_eq(expected)) {
            return Err(CredentialError::DestinationNotEmpty);
        }
        let status = unsafe { self.0.delete(wide.as_ptr()) };
        if status == 0 {
            Ok(())
        } else {
            Err(CredentialError::CredentialIo)
        }
    }
}

#[cfg(test)]
#[path = "mdata_credentials_tests.rs"]
mod tests;
