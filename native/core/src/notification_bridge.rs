//! Trusted in-process host callbacks, registered outside the QML dispatch API.
//! Registry lock outlives the native transaction lease, preventing detach/free
//! until the same worker releases it after the durable PIN operation.
use crate::{
    chat_lock::{LockError, NotificationProtector, ProtectionLease},
    notification_policy::Policy,
};
use serde_json::Value;
use std::{
    ffi::c_void,
    marker::PhantomData,
    rc::Rc,
    sync::{Mutex, MutexGuard},
};

#[repr(C)]
#[derive(Clone, Copy)]
pub struct NativeCallbacks {
    pub size: u32,
    pub version: u32,
    pub user_data: *mut c_void,
    pub prepare: Option<unsafe extern "C" fn(*mut c_void, *const u8, usize, *mut u64) -> i32>,
    pub release: Option<unsafe extern "C" fn(*mut c_void, u64)>,
}
// These pointers come only from the native host. Registration's safety contract
// requires thread-safe callbacks and user_data lifetime through detach/close.
unsafe impl Send for NativeCallbacks {}
unsafe impl Sync for NativeCallbacks {}
#[derive(Default)]
pub struct NativeNotificationProtector {
    callbacks: Mutex<Option<NativeCallbacks>>,
}
impl NativeNotificationProtector {
    /// # Safety
    /// All function pointers and user_data must be valid through replacement,
    /// detach or runtime close. Callbacks must not unwind or reenter the runtime.
    pub unsafe fn register(&self, callbacks: Option<NativeCallbacks>) -> bool {
        if callbacks.is_some_and(|c| {
            c.size as usize != std::mem::size_of::<NativeCallbacks>()
                || c.version != 1
                || c.user_data.is_null()
                || c.prepare.is_none()
                || c.release.is_none()
        }) {
            return false;
        }
        let Ok(mut slot) = self.callbacks.lock() else {
            return false;
        };
        *slot = callbacks;
        true
    }
}
struct NativeLease<'a> {
    _registered: MutexGuard<'a, Option<NativeCallbacks>>,
    callbacks: NativeCallbacks,
    lease: u64,
    _same_thread: PhantomData<Rc<()>>,
}
impl ProtectionLease for NativeLease<'_> {}
impl Drop for NativeLease<'_> {
    fn drop(&mut self) {
        if let Some(release) = self.callbacks.release {
            unsafe {
                release(self.callbacks.user_data, self.lease);
            }
        }
    }
}
impl NotificationProtector for NativeNotificationProtector {
    fn prepare<'a>(&'a self, catalog: &Value) -> Result<Box<dyn ProtectionLease + 'a>, LockError> {
        let policy =
            Policy::from_catalog(catalog).map_err(|_| LockError::NotificationUnavailable)?;
        let payload =
            serde_json::to_vec(&policy.wire()).map_err(|_| LockError::NotificationUnavailable)?;
        let registered = self
            .callbacks
            .lock()
            .map_err(|_| LockError::NotificationUnavailable)?;
        let callbacks = registered
            .as_ref()
            .copied()
            .ok_or(LockError::NotificationUnavailable)?;
        let prepare = callbacks
            .prepare
            .ok_or(LockError::NotificationUnavailable)?;
        let mut lease = 0u64;
        let result = unsafe {
            prepare(
                callbacks.user_data,
                payload.as_ptr(),
                payload.len(),
                &mut lease,
            )
        };
        if result != 0 || lease == 0 {
            // An erroneous host returning a lease with failure must still release
            // its lock. Successful implementations return zero lease on failure.
            if lease != 0 {
                if let Some(release) = callbacks.release {
                    unsafe {
                        release(callbacks.user_data, lease);
                    }
                }
            }
            return Err(LockError::NotificationUnavailable);
        }
        Ok(Box::new(NativeLease {
            _registered: registered,
            callbacks,
            lease,
            _same_thread: PhantomData,
        }))
    }
}
