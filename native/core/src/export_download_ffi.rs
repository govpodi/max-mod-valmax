//! An authorized attachment lease as an opaque native stream. The network host
//! supplies bytes; no filesystem path, URL, header or credential crosses here.
use crate::{
    export_job, export_render,
    export_service::{DownloadSink, ExportServiceError},
    runtime::Runtime,
};
use serde::Deserialize;
use std::{
    mem,
    panic::{AssertUnwindSafe, catch_unwind},
    ptr, slice,
};
use zeroize::Zeroize;
pub struct NativeDownload {
    sink: DownloadSink,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields, rename_all = "camelCase")]
struct Request {
    job_id: String,
    job_token: String,
    message_id: String,
    attachment_id: String,
}
impl Drop for Request {
    fn drop(&mut self) {
        self.job_id.zeroize();
        self.job_token.zeroize();
        self.message_id.zeroize();
        self.attachment_id.zeroize();
    }
}
fn aligned<T>(value: *const T) -> bool {
    !value.is_null() && (value as usize).is_multiple_of(mem::align_of::<T>())
}
fn service_error(error: ExportServiceError) -> u32 {
    match error {
        ExportServiceError::Busy => 3,
        ExportServiceError::Job(export_job::Error::InvalidRequest) => 1,
        ExportServiceError::Job(export_job::Error::Cancelled) => 4,
        ExportServiceError::Job(export_job::Error::IoError) => 5,
        ExportServiceError::Job(export_job::Error::LimitReached) => 6,
        ExportServiceError::Job(export_job::Error::InsufficientSpace) => 7,
        _ => 2,
    }
}
fn stream_error(error: export_render::Error) -> u32 {
    match error {
        export_render::Error::Cancelled => 4,
        export_render::Error::AssetTooLarge | export_render::Error::LimitExceeded => 6,
        export_render::Error::NoSpace => 7,
        _ => 5,
    }
}
/// # Safety
/// Live Runtime for the call. Output is an aligned NULL slot. Input owns1..1024
/// readable bytes until return. Sink ownership is unique and never concurrently
/// accessed; close it before host shutdown finishes. An Arc holds the selected
/// job/lease alive; runtime shutdown signals its cancellation immediately.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_export_download_open(
    runtime: *const Runtime,
    input: *const u8,
    length: usize,
    out: *mut *mut NativeDownload,
) -> u32 {
    if !aligned(runtime) || !aligned(out) || input.is_null() || length == 0 || length > 1024 {
        return 1;
    }
    if !unsafe { *out }.is_null() {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let bytes = unsafe { slice::from_raw_parts(input, length) };
        let request: Request = match serde_json::from_slice(bytes) {
            Ok(value) => value,
            Err(_) => return 1,
        };
        match unsafe { &*runtime }.export_download_open(
            &request.job_id,
            &request.job_token,
            &request.message_id,
            &request.attachment_id,
        ) {
            Ok(sink) => {
                unsafe {
                    out.write(Box::into_raw(Box::new(NativeDownload { sink })));
                }
                0
            }
            Err(error) => service_error(error),
        }
    }))
    .unwrap_or(2)
}
/// # Safety
/// Unique live sink, readable1..1MiB bytes; no concurrent write/close. Does not
/// retain input. Returns0ok,1invalidABI,4cancelled,5io,6limit,7diskspace.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_export_download_write(
    sink: *mut NativeDownload,
    input: *const u8,
    length: usize,
) -> u32 {
    if !aligned(sink) || input.is_null() || length == 0 || length > 1024 * 1024 {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        match unsafe { &mut *sink }
            .sink
            .write(unsafe { slice::from_raw_parts(input, length) })
        {
            Ok(()) => 0,
            Err(error) => stream_error(error),
        }
    }))
    .unwrap_or(5)
}
/// # Safety
/// Unique owning aligned slot, same slot reusable after close. Closes the writer
/// even on flush error, sets NULL first; it does not mark the asset ready or ACK
/// lease cleanup. The host sends the existing media terminal command afterward.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_export_download_close(slot: *mut *mut NativeDownload) -> u32 {
    if !aligned(slot) {
        return 1;
    }
    let sink = unsafe { *slot };
    if sink.is_null() {
        return 0;
    }
    if !aligned(sink) {
        return 1;
    }
    unsafe {
        slot.write(ptr::null_mut());
    }
    catch_unwind(AssertUnwindSafe(|| {
        let mut sink = unsafe { Box::from_raw(sink) };
        match sink.sink.close() {
            Ok(()) => 0,
            Err(error) => stream_error(error),
        }
    }))
    .unwrap_or(5)
}
#[cfg(all(test, windows))]
#[path = "export_download_ffi_tests.rs"]
mod tests;
