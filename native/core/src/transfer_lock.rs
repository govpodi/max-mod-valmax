//! Shared legacy-compatible lock for explicit account transfer operations.
//! Acquiring it never reads profiles, credentials, or process state.
use std::{fmt, marker::PhantomData, rc::Rc};

const NAME: &str = "Local\\MAXModSessionTransferV1";
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LockError {
    Busy,
    IoError,
}
impl LockError {
    pub fn code(self) -> &'static str {
        match self {
            Self::Busy => "busy",
            Self::IoError => "io_error",
        }
    }
}
impl fmt::Display for LockError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for LockError {}

/// Windows mutex ownership belongs to one OS thread. This guard is !Send/!Sync.
/// An abandoned lock is acquired but does NOT clear any recovery marker.
pub struct TransferLease {
    #[cfg(windows)]
    handle: *mut std::ffi::c_void,
    _thread: PhantomData<Rc<()>>,
}
impl TransferLease {
    pub fn acquire() -> Result<Self, LockError> {
        Self::named(NAME)
    }
    fn named(name: &str) -> Result<Self, LockError> {
        #[cfg(windows)]
        {
            let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
            let handle = unsafe { win::CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
            if handle.is_null() {
                return Err(LockError::IoError);
            }
            match unsafe { win::WaitForSingleObject(handle, 0) } {
                0 | 0x80 => Ok(Self {
                    handle,
                    _thread: PhantomData,
                }),
                status => {
                    unsafe {
                        win::CloseHandle(handle);
                    }
                    Err(if status == 0x102 {
                        LockError::Busy
                    } else {
                        LockError::IoError
                    })
                }
            }
        }
        #[cfg(not(windows))]
        {
            let _ = name;
            Err(LockError::IoError)
        }
    }
}
#[cfg(windows)]
impl Drop for TransferLease {
    fn drop(&mut self) {
        unsafe {
            win::ReleaseMutex(self.handle);
            win::CloseHandle(self.handle);
        }
    }
}
#[cfg(windows)]
mod win {
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        pub(super) fn CreateMutexW(
            security: *const c_void,
            owned: i32,
            name: *const u16,
        ) -> *mut c_void;
        pub(super) fn WaitForSingleObject(handle: *mut c_void, timeout: u32) -> u32;
        pub(super) fn ReleaseMutex(handle: *mut c_void) -> i32;
        pub(super) fn CloseHandle(handle: *mut c_void) -> i32;
    }
}
#[cfg(all(test, windows))]
mod tests {
    use super::*;
    #[test]
    fn recursive_owner_other_thread_busy_and_release() {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let name = format!("Local\\ValMAXTransferTest-{:x}", u128::from_le_bytes(nonce));
        let first = TransferLease::named(&name).unwrap();
        let second = TransferLease::named(&name).unwrap();
        let contender = name.clone();
        assert!(
            std::thread::spawn(move || matches!(
                TransferLease::named(&contender),
                Err(LockError::Busy)
            ))
            .join()
            .unwrap()
        );
        drop(second);
        drop(first);
        assert!(
            std::thread::spawn(move || TransferLease::named(&name).is_ok())
                .join()
                .unwrap()
        );
    }
}
