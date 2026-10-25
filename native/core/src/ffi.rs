//! Versioned C boundary. All operations are explicit and bounded.
use crate::session_archive::{self, ArchivePolicy};
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    panic::{AssertUnwindSafe, catch_unwind},
    ptr, slice,
};
use zeroize::Zeroize;

pub const MAX_REQUEST_BYTES: usize = 16 * 1024 * 1024;

enum Request<'a> {
    Version,
    Encrypt {
        payload: &'a Value,
        password: &'a str,
        policy: Policy,
    },
    Decrypt {
        archive: &'a str,
        password: &'a str,
        policy: Policy,
    },
    Validate {
        payload: &'a Value,
        policy: Policy,
    },
}

fn wipe_json(value: &mut Value) {
    match value {
        Value::String(text) => text.zeroize(),
        Value::Array(items) => items.iter_mut().for_each(wipe_json),
        Value::Object(items) => {
            for (mut key, mut item) in std::mem::take(items) {
                key.zeroize();
                wipe_json(&mut item);
            }
        }
        _ => {}
    }
}

struct OwnedJson(Value);
impl Drop for OwnedJson {
    fn drop(&mut self) {
        wipe_json(&mut self.0);
    }
}

struct Policy(ArchivePolicy);
impl Drop for Policy {
    fn drop(&mut self) {
        for items in [
            &mut self.0.credential_names,
            &mut self.0.credential_prefixes,
            &mut self.0.allowed_file_paths,
            &mut self.0.credential_name_patterns,
            &mut self.0.file_path_patterns,
        ] {
            items.zeroize();
        }
    }
}

impl Policy {
    fn parse(value: &Value) -> Option<Self> {
        let object = value.as_object()?;
        const KEYS: [&str; 5] = [
            "credential_names",
            "credential_prefixes",
            "allowed_file_paths",
            "credential_name_patterns",
            "file_path_patterns",
        ];
        // Complete schema validation precedes allocation. No partially deserialized
        // password/payload clones are abandoned if a later field is malformed.
        if object.iter().any(|(key, value)| {
            !KEYS.contains(&key.as_str())
                || !value
                    .as_array()
                    .is_some_and(|items| items.iter().all(Value::is_string))
        }) {
            return None;
        }
        let mut result = Self(ArchivePolicy::default());
        for (key, target) in [
            (KEYS[0], &mut result.0.credential_names),
            (KEYS[1], &mut result.0.credential_prefixes),
            (KEYS[2], &mut result.0.allowed_file_paths),
            (KEYS[3], &mut result.0.credential_name_patterns),
            (KEYS[4], &mut result.0.file_path_patterns),
        ] {
            if let Some(items) = object.get(key).and_then(Value::as_array) {
                for item in items {
                    target.push(item.as_str()?.to_owned());
                }
            }
        }
        Some(result)
    }
}

impl<'a> Request<'a> {
    fn parse(value: &'a Value) -> Option<Self> {
        let object = value.as_object()?;
        let operation = object.get("operation")?.as_str()?;
        let keys: &[&str] = match operation {
            "version" => &["operation"],
            "session.encrypt" => &["operation", "payload", "password", "policy"],
            "session.decrypt" => &["operation", "archive", "password", "policy"],
            "session.validate" => &["operation", "payload", "policy"],
            _ => return None,
        };
        if object.len() != keys.len() || keys.iter().any(|key| !object.contains_key(*key)) {
            return None;
        }
        match operation {
            "version" => Some(Self::Version),
            "session.encrypt" => Some(Self::Encrypt {
                payload: &object["payload"],
                password: object["password"].as_str()?,
                policy: Policy::parse(&object["policy"])?,
            }),
            "session.decrypt" => Some(Self::Decrypt {
                archive: object["archive"].as_str()?,
                password: object["password"].as_str()?,
                policy: Policy::parse(&object["policy"])?,
            }),
            "session.validate" => Some(Self::Validate {
                payload: &object["payload"],
                policy: Policy::parse(&object["policy"])?,
            }),
            _ => None,
        }
    }
}

fn failure(code: &str) -> Value {
    json!({"schema":1,"ok":false,"error":code})
}

/// The caller owns and erases its input. Only synthetic inputs belong in diagnostics.
pub fn dispatch(input: &[u8]) -> Vec<u8> {
    let result = catch_unwind(AssertUnwindSafe(|| dispatch_inner(input)));
    let output = OwnedJson(result.unwrap_or_else(|_| failure("internal_error")));
    serde_json::to_vec(&output.0)
        .unwrap_or_else(|_| b"{\"schema\":1,\"ok\":false,\"error\":\"internal_error\"}".to_vec())
}

fn dispatch_inner(input: &[u8]) -> Value {
    if input.is_empty() || input.len() > MAX_REQUEST_BYTES {
        return failure("invalid_request");
    }
    let strict = match session_archive::parse_json(input, "invalid_payload") {
        Ok(value) => OwnedJson(value),
        Err(_) => return failure("invalid_request"),
    };
    // Passwords and payloads borrow the guarded tree, including every error path.
    let request = match Request::parse(&strict.0) {
        Some(value) => value,
        None => return failure("invalid_request"),
    };
    match &request {
        Request::Version => {
            json!({"schema":1,"ok":true,"result":{"abi":1,"runtime":"rust","features":["session_archive"]}})
        }
        Request::Encrypt {
            payload,
            password,
            policy,
        } => match session_archive::encrypt(payload, password, &policy.0) {
            Ok(mut data) => {
                let encoded = STANDARD.encode(&data);
                data.zeroize();
                json!({"schema":1,"ok":true,"result":{"archive":encoded}})
            }
            Err(error) => failure(error.code()),
        },
        Request::Decrypt {
            archive,
            password,
            policy,
        } => {
            let data = match STANDARD.decode(archive) {
                Ok(data) => zeroize::Zeroizing::new(data),
                _ => return failure("invalid_archive"),
            };
            if STANDARD.encode(&data) != *archive {
                return failure("invalid_archive");
            }
            let result = session_archive::decrypt(&data, password, &policy.0);
            match result {
                Ok(payload) => json!({"schema":1,"ok":true,"result":{"payload":payload}}),
                Err(error) => failure(error.code()),
            }
        }
        Request::Validate { payload, policy } => {
            match session_archive::validate_payload(payload, &policy.0) {
                Ok(payload) => json!({"schema":1,"ok":true,"result":{"payload":payload}}),
                Err(error) => failure(error.code()),
            }
        }
    }
}

/// Owned result. Free exactly once with valmax_core_buffer_free; never use a C allocator.
#[repr(C)]
pub struct CoreBuffer {
    pub data: *mut u8,
    pub len: usize,
    pub capacity: usize,
}

#[unsafe(no_mangle)]
pub extern "C" fn valmax_core_abi_version() -> u32 {
    1
}

/// # Safety
/// input must point to input_len readable bytes; output must be a writable CoreBuffer.
/// Neither may alias. This ABI is for the trusted C++ bridge, not a network endpoint.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_dispatch(
    input: *const u8,
    input_len: usize,
    output: *mut CoreBuffer,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        output.write(CoreBuffer {
            data: ptr::null_mut(),
            len: 0,
            capacity: 0,
        });
    }
    if input.is_null() || input_len == 0 || input_len > MAX_REQUEST_BYTES {
        return 1;
    }
    let bytes = unsafe { slice::from_raw_parts(input, input_len) };
    let mut result = dispatch(bytes);
    let buffer = CoreBuffer {
        data: result.as_mut_ptr(),
        len: result.len(),
        capacity: result.capacity(),
    };
    std::mem::forget(result);
    unsafe {
        output.write(buffer);
    }
    0
}

/// # Safety
/// buffer must be a writable CoreBuffer returned by valmax_core_dispatch, unmodified.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_buffer_free(buffer: *mut CoreBuffer) {
    if buffer.is_null() {
        return;
    }
    let item = unsafe { &mut *buffer };
    if !item.data.is_null() {
        let mut bytes = unsafe { Vec::from_raw_parts(item.data, item.len, item.capacity) };
        bytes.zeroize();
    }
    item.data = ptr::null_mut();
    item.len = 0;
    item.capacity = 0;
}

fn owned_buffer(mut bytes: Vec<u8>) -> CoreBuffer {
    let result = CoreBuffer {
        data: bytes.as_mut_ptr(),
        len: bytes.len(),
        capacity: bytes.capacity(),
    };
    std::mem::forget(bytes);
    result
}
fn encoded_value(value: Value) -> Vec<u8> {
    let guarded = OwnedJson(value);
    serde_json::to_vec(&guarded.0)
        .unwrap_or_else(|_| b"{\"schema\":1,\"ok\":false,\"error\":\"internal_error\"}".to_vec())
}

/// # Safety
/// Same input/output rules as dispatch. runtime must point to an empty writable
/// handle slot. On success its ownership moves to the caller; close after all
/// in-flight dispatches have finished. Paths are explicit, trusted host input.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_open(
    input: *const u8,
    input_len: usize,
    runtime: *mut *mut crate::runtime::Runtime,
    output: *mut CoreBuffer,
) -> i32 {
    if runtime.is_null() || output.is_null() {
        return 1;
    }
    unsafe {
        runtime.write(ptr::null_mut());
        output.write(CoreBuffer {
            data: ptr::null_mut(),
            len: 0,
            capacity: 0,
        });
    }
    if input.is_null() || input_len == 0 || input_len > 128 * 1024 {
        return 1;
    }
    let bytes = unsafe { slice::from_raw_parts(input, input_len) };
    let result = catch_unwind(AssertUnwindSafe(
        || -> Result<crate::runtime::Runtime, &'static str> {
            let config = OwnedJson(
                session_archive::parse_json(bytes, "invalid_payload")
                    .map_err(|_| "invalid_request")?,
            );
            crate::runtime::Runtime::open(&config.0)
        },
    ));
    let value = match result {
        Ok(Ok(instance)) => {
            unsafe {
                runtime.write(Box::into_raw(Box::new(instance)));
            }
            json!({"schema":1,"ok":true,"result":{"abi":1,"runtime":"rust","features":["session_archive","optimization","ghost_settings","account_preferences","quick_replies","chat_lock","support_diagnostics","mdata_preview","mdata_export","chat_export_coordinator"]}})
        }
        Ok(Err(code)) => failure(code),
        Err(_) => failure("internal_error"),
    };
    unsafe {
        output.write(owned_buffer(encoded_value(value)));
    }
    0
}

/// # Safety
/// runtime is a live handle returned by open, and remains live until this call
/// returns. Concurrent dispatches are supported; concurrent close is forbidden.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_dispatch(
    runtime: *const crate::runtime::Runtime,
    input: *const u8,
    input_len: usize,
    output: *mut CoreBuffer,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        output.write(CoreBuffer {
            data: ptr::null_mut(),
            len: 0,
            capacity: 0,
        });
    }
    if runtime.is_null() || input.is_null() || input_len == 0 || input_len > MAX_REQUEST_BYTES {
        return 1;
    }
    let bytes = unsafe { slice::from_raw_parts(input, input_len) };
    let value = catch_unwind(AssertUnwindSafe(|| {
        let parsed = match session_archive::parse_json(bytes,"invalid_payload") {
            Ok(value)=>OwnedJson(value),Err(_)=>return failure("invalid_request"),
        };
        let operation = parsed.0.get("operation").and_then(Value::as_str).unwrap_or("");
        if operation == "version" {
            if parsed.0.as_object().is_some_and(|v|v.len()==1) {
                return json!({"schema":1,"ok":true,"result":{"abi":1,"runtime":"rust","features":["session_archive","optimization","ghost_settings","account_preferences","quick_replies","chat_lock","support_diagnostics","mdata_preview","mdata_export","chat_export_coordinator"]}});
            }
            return failure("invalid_request");
        }
        if operation.starts_with("session.") { return dispatch_inner(bytes); }
        match unsafe { &*runtime }.call(&parsed.0) {
            Ok(value)=>json!({"schema":1,"ok":true,"result":value}),Err(code)=>failure(code),
        }
    })).unwrap_or_else(|_|failure("internal_error"));
    unsafe {
        output.write(owned_buffer(encoded_value(value)));
    }
    0
}

/// # Safety
/// Signal before joining workers. Runtime must remain alive until all calls
/// return; no concurrent close. This does not wait for filesystem cleanup.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_cancel_pending(
    runtime: *const crate::runtime::Runtime,
) -> i32 {
    if runtime.is_null() {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        unsafe { &*runtime }.cancel_pending();
        0
    }))
    .unwrap_or(1)
}

/// # Safety
/// Close once after every worker using this handle has finished. The slot is
/// cleared, making repeat close on that same slot a no-op. Never clone ownership.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_close(runtime: *mut *mut crate::runtime::Runtime) {
    if runtime.is_null() {
        return;
    }
    let pointer = unsafe { *runtime };
    if !pointer.is_null() {
        unsafe {
            runtime.write(ptr::null_mut());
            drop(Box::from_raw(pointer));
        }
    }
}

/// # Safety
/// Trusted native host only. status points to the exact C++ status struct from
/// valmax_ghost_status, not a UI request. NULL revokes the current ACK.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_ghost_ack(
    runtime: *const crate::runtime::Runtime,
    status: *const crate::ghost_ack::NativeStatus,
) -> i32 {
    if runtime.is_null() {
        return 1;
    }
    let context = unsafe { &*runtime };
    if status.is_null() {
        context.invalidate_ghost();
        return 0;
    }
    if context.acknowledge_ghost(unsafe { *status }) {
        0
    } else {
        1
    }
}

/// # Safety
/// Trusted native host only. `status` points to the versioned 184-byte
/// ValmaxRetentionNativeStatus in valmax_core.h; NULL revokes its lease.
/// The caller keeps the Runtime alive until this call returns. UI input must
/// never be reinterpreted as a host acknowledgement.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_retention_ack(
    runtime: *const crate::runtime::Runtime,
    status: *const crate::retention_ack::NativeStatus,
) -> i32 {
    if runtime.is_null() { return 1; }
    catch_unwind(AssertUnwindSafe(|| {
        let context = unsafe { &*runtime };
        if status.is_null() {
            context.invalidate_retention();
            0
        } else if context.acknowledge_retention(unsafe { *status }) {
            0
        } else {
            1
        }
    })).unwrap_or(1)
}

/// # Safety
/// runtime and callback pointers obey valmax_core.h. No callback or user_data
/// may be freed before detach returns. Never call with arbitrary UI pointers.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_notifications_attach(
    runtime: *const crate::runtime::Runtime,
    callbacks: *const crate::notification_bridge::NativeCallbacks,
) -> i32 {
    if runtime.is_null() {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        let selected = if callbacks.is_null() {
            None
        } else {
            Some(unsafe { *callbacks })
        };
        if unsafe { (&*runtime).notifications_attach(selected) } {
            0
        } else {
            1
        }
    }))
    .unwrap_or(1)
}
/// # Safety
/// Live host-owned runtime; no concurrent close. The caller holds no host
/// transaction lock. All callback leases are released before this returns.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_runtime_notifications_refresh(
    runtime: *const crate::runtime::Runtime,
) -> i32 {
    if runtime.is_null() {
        return 1;
    }
    catch_unwind(AssertUnwindSafe(|| {
        match unsafe { &*runtime }.notifications_refresh() {
            Ok(()) => 0,
            Err(crate::chat_lock::LockError::NotificationUnavailable) => 2,
            Err(_) => 3,
        }
    }))
    .unwrap_or(3)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn strict_boundary_and_roundtrip_ownership() {
        for input in [
            b"{}".as_slice(),
            b"{\"operation\":\"version\",\"extra\":1}",
            b"{\"operation\":\"version\",\"operation\":\"version\"}",
        ] {
            assert_eq!(
                serde_json::from_slice::<Value>(&dispatch(input)).unwrap()["error"],
                "invalid_request"
            );
        }
        let input = br#"{"operation":"version"}"#;
        let mut out = CoreBuffer {
            data: ptr::null_mut(),
            len: 0,
            capacity: 0,
        };
        unsafe {
            assert_eq!(
                valmax_core_dispatch(input.as_ptr(), input.len(), &mut out),
                0
            );
            assert_eq!(
                serde_json::from_slice::<Value>(slice::from_raw_parts(out.data, out.len)).unwrap()
                    ["result"]["abi"],
                1
            );
            valmax_core_buffer_free(&mut out);
            assert!(out.data.is_null());
            assert_eq!(out.len, 0);
            valmax_core_buffer_free(&mut out);
            assert_eq!(valmax_core_dispatch(ptr::null(), 0, &mut out), 1);
        }
    }
}
