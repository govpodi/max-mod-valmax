//! One-shot native transfer host boundary. Never a Runtime/QML operation.
//! Passwords remain in borrowed caller memory and a short-lived erased JSON
//! value. No password, archive path or token is written to a job file/log.
use crate::{
    ffi::CoreBuffer,
    login_credentials::WindowsCredentials,
    mdata_credentials::{RecoveryCredentials, WindowsCredentials as RecoveryWindowsCredentials},
    mdata_import::{self, HostError, ImportHost},
    mdata_preview_file, mdata_recovery_apply,
    mdata_recovery_state::{self, RecoveryMarker},
    session_archive::{self, OwnedValue},
};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    ffi::c_void,
    panic::{AssertUnwindSafe, catch_unwind},
    path::Path,
    ptr, slice,
};
use subtle::ConstantTimeEq;

const MAX_INPUT: usize = 128 * 1024;
const MAX_LOCAL: usize = 32000;
pub type HostCallback = unsafe extern "C" fn(*mut c_void) -> u32;
/// Windows x64 ABI1, size40. Callbacks execute synchronously on the importing
/// worker thread while Rust holds the transfer mutex. No callback may unwind,
/// reenter import, destroy its context or unload this DLL.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct TransferHost {
    pub size: u32,
    pub version: u32,
    pub context: *mut c_void,
    pub wait_closed: Option<HostCallback>,
    pub check_closed: Option<HostCallback>,
    pub cancelled: Option<HostCallback>,
}
impl TransferHost {
    fn valid(&self) -> bool {
        self.size as usize == std::mem::size_of::<Self>()
            && self.version == 1
            && self.wait_closed.is_some()
            && self.check_closed.is_some()
            && self.cancelled.is_some()
    }
    fn closed(&self, callback: Option<HostCallback>) -> Result<(), HostError> {
        let code = unsafe { callback.expect("validated host callback")(self.context) };
        match code {
            0 => Ok(()),
            1 => Err(HostError::ClientStillRunning),
            3 => Err(HostError::Cancelled),
            _ => Err(HostError::ProcessScanFailed),
        }
    }
}
impl ImportHost for TransferHost {
    fn wait_closed(&mut self) -> Result<(), HostError> {
        self.closed(self.wait_closed)
    }
    fn check_closed(&mut self) -> Result<(), HostError> {
        self.closed(self.check_closed)
    }
    fn cancelled(&mut self) -> bool {
        unsafe { self.cancelled.expect("validated cancellation callback")(self.context) != 0 }
    }
}
fn error(code: &str) -> Value {
    json!({"schema":1,"ok":false,"error":code})
}
fn request(input: &[u8], importing: bool) -> Result<OwnedValue, ()> {
    if input.is_empty() || input.len() > MAX_INPUT {
        return Err(());
    }
    let owned = OwnedValue(session_archive::parse_json(input, "invalid_request").map_err(|_| ())?);
    let fields: &[&str] = if importing {
        &["file", "password", "fingerprint"]
    } else {
        &["file", "password"]
    };
    let Some(map) = owned.0.as_object() else {
        return Err(());
    };
    if map.len() != fields.len()
        || fields
            .iter()
            .any(|field| !map.get(*field).is_some_and(Value::is_string))
    {
        return Err(());
    }
    Ok(owned)
}
fn preview(input: &[u8]) -> Value {
    let Ok(request) = request(input, false) else {
        return error("invalid_request");
    };
    match mdata_preview_file::inspect(
        request.0["file"].as_str().unwrap(),
        request.0["password"].as_str().unwrap(),
    ) {
        Ok(preview) => json!({"schema":1,"ok":true,"result":preview}),
        Err(failure) => error(failure.code()),
    }
}
fn import(local: &[u8], input: &[u8], host: &mut TransferHost) -> Value {
    let Ok(local) = std::str::from_utf8(local) else {
        return error("invalid_request");
    };
    if local.is_empty()
        || local.len() > MAX_LOCAL
        || crate::launcher_config::config_path(Path::new(local)).is_err()
    {
        return error("invalid_request");
    }
    let Ok(request) = request(input, true) else {
        return error("invalid_request");
    };
    match mdata_import::execute(
        Path::new(local),
        request.0["file"].as_str().unwrap(),
        request.0["password"].as_str().unwrap(),
        request.0["fingerprint"].as_str().unwrap(),
        &WindowsCredentials::new(),
        host,
    ) {
        Ok(result) => json!({"schema":1,"ok":true,"result":result}),
        Err(failure) => error(failure.code()),
    }
}
fn recovery_root(local: &[u8]) -> Result<std::path::PathBuf, &'static str> {
    let local = std::str::from_utf8(local).map_err(|_| "invalid_request")?;
    if local.is_empty()
        || local.len() > MAX_LOCAL
        || crate::launcher_config::config_path(Path::new(local)).is_err()
    {
        return Err("invalid_request");
    }
    mdata_recovery_state::root_candidate(Path::new(local)).map_err(|e| e.code())
}
fn marker_fingerprint(marker: &RecoveryMarker) -> Result<String, &'static str> {
    // Only the digest leaves this boundary; marker paths are never UI output.
    let bytes = serde_json::to_vec(marker).map_err(|_| "internal_error")?;
    Ok(format!("{:x}", Sha256::digest(&bytes)))
}
fn recovery_status(local: &[u8]) -> Value {
    let root = match recovery_root(local) {
        Ok(root) => root,
        Err(code) => return error(code),
    };
    match mdata_recovery_state::read(&root) {
        Ok(None) => json!({"schema":1,"ok":true,"result":{"required":false,"fingerprint":""}}),
        Ok(Some(marker)) => match marker_fingerprint(&marker) {
            Ok(fingerprint) => {
                json!({"schema":1,"ok":true,"result":{"required":true,"fingerprint":fingerprint}})
            }
            Err(code) => error(code),
        },
        Err(failure) => error(failure.code()),
    }
}
fn recover(
    local: &[u8],
    input: &[u8],
    host: &mut TransferHost,
    credentials: &dyn RecoveryCredentials,
) -> Value {
    if input.is_empty() || input.len() > MAX_INPUT {
        return error("invalid_request");
    }
    let Ok(request) = session_archive::parse_json(input, "invalid_request").map(OwnedValue) else {
        return error("invalid_request");
    };
    let Some(map) = request.0.as_object() else {
        return error("invalid_request");
    };
    if map.len() != 2
        || !map.get("password").is_some_and(Value::is_string)
        || !map.get("fingerprint").is_some_and(Value::is_string)
    {
        return error("invalid_request");
    }
    let password = request.0["password"].as_str().unwrap();
    let fingerprint = request.0["fingerprint"].as_str().unwrap();
    if password.len() < 12 || password.len() > 1024 || password.contains('\0') {
        return error("invalid_password");
    }
    if fingerprint.len() != 64
        || !fingerprint
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return error("invalid_request");
    }
    let root = match recovery_root(local) {
        Ok(root) => root,
        Err(code) => return error(code),
    };
    let marker = match mdata_recovery_state::read(&root) {
        Ok(Some(marker)) => marker,
        Ok(None) => return error("recovery_not_required"),
        Err(failure) => return error(failure.code()),
    };
    let actual = match marker_fingerprint(&marker) {
        Ok(value) => value,
        Err(code) => return error(code),
    };
    if !bool::from(actual.as_bytes().ct_eq(fingerprint.as_bytes())) {
        return error("source_changed");
    }
    match mdata_recovery_apply::execute(&root, &marker, password, credentials, host) {
        Ok(result) => json!({"schema":1,"ok":true,"result":result}),
        Err(failure) => error(failure.code()),
    }
}
unsafe fn clear(output: *mut CoreBuffer) {
    unsafe {
        output.write(CoreBuffer {
            data: ptr::null_mut(),
            len: 0,
            capacity: 0,
        });
    }
}
unsafe fn finish(output: *mut CoreBuffer, work: impl FnOnce() -> Value) {
    let mut bytes = catch_unwind(AssertUnwindSafe(|| serde_json::to_vec(&work())))
        .ok()
        .and_then(Result::ok)
        .unwrap_or_else(|| b"{\"schema\":1,\"ok\":false,\"error\":\"internal_error\"}".to_vec());
    let buffer = CoreBuffer {
        data: bytes.as_mut_ptr(),
        len: bytes.len(),
        capacity: bytes.capacity(),
    };
    std::mem::forget(bytes);
    unsafe {
        output.write(buffer);
    }
}

/// # Safety
/// Borrowed bounded UTF-8 JSON {file,password}; output is an empty writable
/// nonaliasing CoreBuffer. Worker only, no profile/credential IO. The caller
/// erases its input and frees the result with valmax_core_buffer_free.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_transfer_preview(
    input: *const u8,
    input_len: usize,
    output: *mut CoreBuffer,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        clear(output);
    }
    if input.is_null() || input_len == 0 || input_len > MAX_INPUT {
        return 1;
    }
    let input = unsafe { slice::from_raw_parts(input, input_len) };
    unsafe {
        finish(output, || preview(input));
    }
    0
}
/// # Safety
/// local is trusted explicit LocalAppData, input is exact JSON
/// {file,password,fingerprint}. All slices/structs are valid, aligned, disjoint
/// and alive until return. Host owns callback context for the full call. UI
/// confirmation must precede calling. No user-supplied closed=true field exists.
/// Output is empty on entry; free only through valmax_core_buffer_free.
/// Cancellation after apply starts cannot interrupt commit/rollback. A failure
/// may leave a prepared config or valid partial accounts (rollback_failed).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_transfer_import(
    local: *const u8,
    local_len: usize,
    input: *const u8,
    input_len: usize,
    host: *const TransferHost,
    output: *mut CoreBuffer,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        clear(output);
    }
    if local.is_null()
        || local_len == 0
        || local_len > MAX_LOCAL
        || input.is_null()
        || input_len == 0
        || input_len > MAX_INPUT
        || host.is_null()
    {
        return 1;
    }
    let mut host = unsafe { *host };
    if !host.valid() {
        return 1;
    }
    let local = unsafe { slice::from_raw_parts(local, local_len) };
    let input = unsafe { slice::from_raw_parts(input, input_len) };
    unsafe {
        finish(output, || import(local, input, &mut host));
    }
    0
}

/// # Safety
/// Host-only worker call with trusted explicit LocalAppData. Reads the recovery
/// marker only; returns required/fingerprint, never paths or credentials.
/// All buffers follow the ownership rules of valmax_transfer_import.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_transfer_recovery_status(
    local: *const u8,
    local_len: usize,
    output: *mut CoreBuffer,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        clear(output);
    }
    if local.is_null() || local_len == 0 || local_len > MAX_LOCAL {
        return 1;
    }
    let local = unsafe { slice::from_raw_parts(local, local_len) };
    unsafe {
        finish(output, || recovery_status(local));
    }
    0
}
/// # Safety
/// Explicit confirmed recovery of the current exact marker only. Input is
/// {password,fingerprint}, the fingerprint comes from recovery_status. No file,
/// profile, closed-client boolean or marker may be supplied by QML. Context and
/// DLL remain alive through the final commit/rollback; cancellation cannot
/// interrupt mutation. recovery_required/rollback_failed must remain visible.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_transfer_recover(
    local: *const u8,
    local_len: usize,
    input: *const u8,
    input_len: usize,
    host: *const TransferHost,
    output: *mut CoreBuffer,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        clear(output);
    }
    if local.is_null()
        || local_len == 0
        || local_len > MAX_LOCAL
        || input.is_null()
        || input_len == 0
        || input_len > MAX_INPUT
        || host.is_null()
    {
        return 1;
    }
    let mut host = unsafe { *host };
    if !host.valid() {
        return 1;
    }
    let local = unsafe { slice::from_raw_parts(local, local_len) };
    let input = unsafe { slice::from_raw_parts(input, input_len) };
    unsafe {
        finish(output, || {
            recover(local, input, &mut host, &RecoveryWindowsCredentials::new())
        });
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::{offset_of, size_of};
    use std::sync::atomic::{AtomicUsize, Ordering};
    static UNEXPECTED_CALLBACKS: AtomicUsize = AtomicUsize::new(0);
    unsafe extern "C" fn forbidden(_: *mut c_void) -> u32 {
        UNEXPECTED_CALLBACKS.fetch_add(1, Ordering::Relaxed);
        2
    }
    unsafe extern "C" fn code(context: *mut c_void) -> u32 {
        unsafe { *(context as *const u32) }
    }
    fn host() -> TransferHost {
        TransferHost {
            size: 40,
            version: 1,
            context: ptr::null_mut(),
            wait_closed: Some(forbidden),
            check_closed: Some(forbidden),
            cancelled: Some(forbidden),
        }
    }
    fn buffer() -> CoreBuffer {
        CoreBuffer {
            data: ptr::null_mut(),
            len: 0,
            capacity: 0,
        }
    }
    unsafe fn take(out: &mut CoreBuffer) -> Value {
        let value =
            serde_json::from_slice(unsafe { slice::from_raw_parts(out.data, out.len) }).unwrap();
        unsafe {
            crate::ffi::valmax_core_buffer_free(out);
        }
        value
    }
    #[test]
    fn host_layout_and_invalid_abi_rejected_without_io() {
        assert_eq!(size_of::<TransferHost>(), 40);
        assert_eq!(
            [
                offset_of!(TransferHost, size),
                offset_of!(TransferHost, context),
                offset_of!(TransferHost, wait_closed),
                offset_of!(TransferHost, check_closed),
                offset_of!(TransferHost, cancelled)
            ],
            [0, 8, 16, 24, 32]
        );
        let mut out = buffer();
        let valid = host();
        unsafe {
            assert_eq!(valmax_transfer_preview(ptr::null(), 1, &mut out), 1);
            assert_eq!(
                valmax_transfer_preview(b"x".as_ptr(), MAX_INPUT + 1, &mut out),
                1
            );
            assert_eq!(
                valmax_transfer_import(ptr::null(), 1, b"x".as_ptr(), 1, &valid, &mut out),
                1
            );
            for bad in [
                TransferHost { size: 39, ..valid },
                TransferHost {
                    version: 2,
                    ..valid
                },
                TransferHost {
                    check_closed: None,
                    ..valid
                },
            ] {
                assert_eq!(
                    valmax_transfer_import(b"C:/x".as_ptr(), 4, b"{}".as_ptr(), 2, &bad, &mut out),
                    1
                );
                assert!(out.data.is_null());
            }
        }
        assert_eq!(UNEXPECTED_CALLBACKS.load(Ordering::Relaxed), 0);
    }
    #[test]
    fn exact_request_schema_and_local_path_fail_before_callbacks_or_credentials() {
        let mut out = buffer();
        let host = host();
        for input in [
            b"{}".as_slice(),
            br#"{"file":"x","password":"y","password":"z"}"#,
            br#"{"file":"x","password":"y","fingerprint":"z","closed":true}"#,
            b"\xff",
        ] {
            unsafe {
                assert_eq!(
                    valmax_transfer_import(
                        b"C:/owned".as_ptr(),
                        8,
                        input.as_ptr(),
                        input.len(),
                        &host,
                        &mut out
                    ),
                    0
                );
                assert_eq!(take(&mut out), error("invalid_request"));
            }
        }
        unsafe {
            assert_eq!(
                valmax_transfer_import(b"relative".as_ptr(), 8, b"{}".as_ptr(), 2, &host, &mut out),
                0
            );
            assert_eq!(take(&mut out), error("invalid_request"));
            assert_eq!(valmax_transfer_preview(b"{}".as_ptr(), 2, &mut out), 0);
            assert_eq!(take(&mut out), error("invalid_request"));
        }
        assert_eq!(UNEXPECTED_CALLBACKS.load(Ordering::Relaxed), 0);
    }
    #[test]
    fn unknown_callback_codes_fail_closed_and_cancellation_is_conservative() {
        for (value, expected) in [
            (0u32, Ok(())),
            (1, Err(HostError::ClientStillRunning)),
            (2, Err(HostError::ProcessScanFailed)),
            (3, Err(HostError::Cancelled)),
            (99, Err(HostError::ProcessScanFailed)),
        ] {
            let mut host = TransferHost {
                context: &value as *const u32 as *mut c_void,
                wait_closed: Some(code),
                check_closed: Some(code),
                cancelled: Some(code),
                ..host()
            };
            assert_eq!(host.wait_closed(), expected);
            assert_eq!(host.check_closed(), expected);
            assert_eq!(host.cancelled(), value != 0);
        }
    }
    #[test]
    fn unwind_boundary_returns_only_finite_error() {
        let mut out = buffer();
        unsafe {
            finish(&mut out, || panic!("synthetic transfer panic"));
        }
        assert_eq!(unsafe { take(&mut out) }, error("internal_error"));
    }
}

#[cfg(all(test, windows))]
#[path = "transfer_recovery_ffi_tests.rs"]
mod recovery_tests;
