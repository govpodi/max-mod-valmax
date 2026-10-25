//! Host-only selected native view observations. No message data, raw addresses,
//! server URLs or JSON command can assert completeness through this boundary.
use crate::runtime::Runtime;
use std::{
    mem,
    panic::{AssertUnwindSafe, catch_unwind},
};

#[repr(C)]
pub struct HistoryBinding {
    pub size: u32,
    pub version: u32,
    pub source: u64,
    pub job_id: [u8; 32],
    pub job_token: [u8; 64],
}
#[repr(C)]
pub struct HistoryObservation {
    pub binding: HistoryBinding,
    pub direction: u32,
    pub count: u32,
    pub pending_done: u32,
    pub more_backward: u32,
    pub more_forward: u32,
    pub reserved: u32,
}
const _: () = assert!(mem::size_of::<HistoryBinding>() == 112);
const _: () = assert!(mem::size_of::<HistoryObservation>() == 136);
fn binding(value: &HistoryBinding) -> Option<(&str, &str)> {
    if value.size != 112 || value.version != 1 || value.source == 0 {
        return None;
    }
    if !value
        .job_id
        .iter()
        .chain(value.job_token.iter())
        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(b))
    {
        return None;
    }
    Some((
        std::str::from_utf8(&value.job_id).ok()?,
        std::str::from_utf8(&value.job_token).ok()?,
    ))
}
fn aligned<T>(value: *const T) -> bool {
    !value.is_null() && (value as usize).is_multiple_of(mem::align_of::<T>())
}

/// # Safety
/// Live runtime, no concurrent close. The host owns a readable aligned binding
/// through return; source is its opaque view generation, never a raw address.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_export_history_bind(
    runtime: *const Runtime,
    value: *const HistoryBinding,
) -> i32 {
    if !aligned(runtime) || !aligned(value) {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let value = unsafe { &*value };
        let Some((job, token)) = binding(value) else {
            return 1;
        };
        match unsafe { &*runtime }.export_history_bind(job, token, value.source) {
            Ok(()) => 0,
            Err("busy") => 3,
            Err(_) => 2,
        }
    }))
    .unwrap_or(2)
}
/// # Safety
/// Same ownership as bind. Observation booleans are exactly0/1, reserved0. Only
/// a pinned native server-terminal callback may supply pending_done=1.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_export_history_observe(
    runtime: *const Runtime,
    value: *const HistoryObservation,
) -> i32 {
    if !aligned(runtime) || !aligned(value) {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let value = unsafe { &*value };
        let Some((job, token)) = binding(&value.binding) else {
            return 1;
        };
        if value.direction > 2
            || value.count > 100000
            || value.pending_done > 1
            || value.more_backward > 1
            || value.more_forward > 1
            || value.reserved != 0
        {
            return 1;
        }
        let direction = ["backward", "center", "forward"][value.direction as usize];
        match unsafe { &*runtime }.export_history_observe(
            job,
            token,
            value.binding.source,
            direction,
            value.count.into(),
            value.pending_done == 1,
            value.more_backward == 1,
            value.more_forward == 1,
        ) {
            Ok(()) => 0,
            Err("busy") => 3,
            Err(_) => 2,
        }
    }))
    .unwrap_or(2)
}
/// # Safety
/// Same ownership as bind. Revocation is a cancellation SIGNAL with no I/O; the
/// host keeps the runtime alive until workers observe it and complete cleanup.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_export_history_invalidate(
    runtime: *const Runtime,
    value: *const HistoryBinding,
) -> i32 {
    if !aligned(runtime) || !aligned(value) {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let value = unsafe { &*value };
        let Some((job, token)) = binding(value) else {
            return 1;
        };
        match unsafe { &*runtime }.export_history_invalidate(job, token, value.source) {
            Ok(()) => 0,
            Err(_) => 2,
        }
    }))
    .unwrap_or(2)
}

#[cfg(all(test, windows))]
#[path = "export_history_ffi_tests.rs"]
mod tests;
