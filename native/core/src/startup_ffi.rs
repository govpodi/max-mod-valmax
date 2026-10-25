//! Host-only startup receipts. No runtime/QML route, file discovery or UI.
//! Numeric tables are ABI v1 and must only grow by adding a new ABI version.
use crate::{
    startup_status::{self, StartupStatus},
    support_diagnostics::{ROLES, STAGES, STARTUP_CODES, STATES},
};
use serde_json::Value;
use std::{
    panic::{AssertUnwindSafe, catch_unwind},
    path::Path,
    slice,
};

pub const MAX_HELPER_PATH_BYTES: usize = 31_000;
/// All fields are value types. action:1 read,2 write; role/stage/state are
/// 1-based indices of the fixed v1 tables. code:0 none,1..27 finite failures.
/// Read requires stage/state/code/claim=0. operation_id is 32 lowercase hex.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StartupRequest {
    pub size: u32,
    pub version: u32,
    pub action: u32,
    pub role: u32,
    pub stage: u32,
    pub state: u32,
    pub code: u32,
    pub claim: u32,
    pub operation_id: [u8; 32],
}
/// error:0 ok,1 invalid_request,2 invalid_receipt,3 unsafe_path,4 store_busy,
/// 5 io_error,6 internal_error. outcome:0 not written/absent,1 written,2 receipt.
/// Receipt fields are all zero unless outcome=2. No paths/strings allocated.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StartupResult {
    pub size: u32,
    pub version: u32,
    pub error: u32,
    pub outcome: u32,
    pub role: u32,
    pub stage: u32,
    pub state: u32,
    pub code: u32,
    pub operation_id: [u8; 32],
}
impl StartupResult {
    fn empty(error: u32) -> Self {
        Self {
            size: 64,
            version: 1,
            error,
            outcome: 0,
            role: 0,
            stage: 0,
            state: 0,
            code: 0,
            operation_id: [0; 32],
        }
    }
}
fn error_code(error: startup_status::Error) -> u32 {
    match error {
        startup_status::Error::InvalidReceipt => 2,
        startup_status::Error::UnsafePath => 3,
        startup_status::Error::StoreBusy => 4,
        startup_status::Error::IoError => 5,
    }
}
fn at(table: &'static [&'static str], value: u32) -> Option<&'static str> {
    value
        .checked_sub(1)
        .and_then(|n| table.get(n as usize).copied())
}
fn index(table: &[&str], value: &Value) -> Option<u32> {
    let value = value.as_str()?;
    table.iter().position(|v| *v == value).map(|n| n as u32 + 1)
}
struct Decoded<'a> {
    role: &'static str,
    operation: &'a str,
    write: Option<(&'static str, &'static str, &'static str, bool)>,
}
fn decode(request: &StartupRequest) -> Option<Decoded<'_>> {
    if request.size != 64 || request.version != 1 {
        return None;
    }
    let role = at(ROLES, request.role)?;
    let operation = std::str::from_utf8(&request.operation_id).ok()?;
    if !startup_status::valid_operation_id(operation) {
        return None;
    }
    let write = match request.action {
        1 if [request.stage, request.state, request.code, request.claim] == [0; 4] => None,
        2 if request.claim <= 1 => {
            let stage = at(STAGES, request.stage)?;
            let state = at(STATES, request.state)?;
            let code = if request.code == 0 {
                "none"
            } else {
                at(STARTUP_CODES, request.code)?
            };
            if (state == "error") != (request.code != 0)
                || (request.claim != 0 && (role == "observe" || state != "starting"))
            {
                return None;
            }
            Some((stage, state, code, request.claim != 0))
        }
        _ => return None,
    };
    Some(Decoded {
        role,
        operation,
        write,
    })
}
fn receipt_result(value: Value) -> Option<StartupResult> {
    let mut result = StartupResult::empty(0);
    result.outcome = 2;
    result.role = index(ROLES, &value["role"])?;
    result.stage = index(STAGES, &value["stage"])?;
    result.state = index(STATES, &value["state"])?;
    result.code = if value["code"] == "none" {
        0
    } else {
        index(STARTUP_CODES, &value["code"])?
    };
    let operation = value["operationId"].as_str()?;
    if !startup_status::valid_operation_id(operation) {
        return None;
    }
    result.operation_id.copy_from_slice(operation.as_bytes());
    Some(result)
}
fn call_with(
    input: &[u8],
    request: &StartupRequest,
    operation: impl FnOnce(&Path, Decoded<'_>) -> Result<StartupResult, startup_status::Error>,
) -> StartupResult {
    if input.is_empty() || input.len() > MAX_HELPER_PATH_BYTES {
        return StartupResult::empty(1);
    }
    let Some(decoded) = decode(request) else {
        return StartupResult::empty(1);
    };
    let Ok(path) = std::str::from_utf8(input) else {
        return StartupResult::empty(1);
    };
    // Constructor performs lexical validation only. Reject every malformed
    // request/path before the injected production operation can perform IO.
    let path = Path::new(path);
    if let Err(error) = StartupStatus::new(path, decoded.role, Some(decoded.operation)) {
        return StartupResult::empty(error_code(error));
    }
    match operation(path, decoded) {
        Ok(result) => result,
        Err(error) => StartupResult::empty(error_code(error)),
    }
}
fn call(input: &[u8], request: &StartupRequest) -> StartupResult {
    call_with(input, request, |path, decoded| {
        let mut status = StartupStatus::new(path, decoded.role, Some(decoded.operation))?;
        if let Some((stage, state, code, claim)) = decoded.write {
            let mut result = StartupResult::empty(0);
            result.outcome = u32::from(status.write(stage, state, code, claim)?);
            Ok(result)
        } else {
            match status.read(None)? {
                Some(value) => receipt_result(value).ok_or(startup_status::Error::InvalidReceipt),
                None => Ok(StartupResult::empty(0)),
            }
        }
    })
}
/// # Safety
/// helper is explicit trusted host input, 1..31000 UTF-8 bytes. request/output
/// are aligned, readable/writable complete 64-byte v1 structs. All buffers must
/// be disjoint and valid for this call. Caller retains all memory. No allocations
/// are returned. Return1 means invalid ABI arguments; non-null output is reset.
/// Return0 gives a finite result, including filesystem/validation failures.
/// Persistence is diagnostic ONLY: failure must not replace the launcher's
/// original failure, authorize launch, or clear a recovery/protection gate.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_startup_status(
    helper: *const u8,
    helper_len: usize,
    request: *const StartupRequest,
    output: *mut StartupResult,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        output.write(StartupResult::empty(1));
    }
    if helper.is_null()
        || helper_len == 0
        || helper_len > MAX_HELPER_PATH_BYTES
        || request.is_null()
    {
        return 1;
    }
    let bytes = unsafe { slice::from_raw_parts(helper, helper_len) };
    let request = unsafe { request.read() };
    let result = catch_unwind(AssertUnwindSafe(|| call(bytes, &request)))
        .unwrap_or_else(|_| StartupResult::empty(6));
    unsafe {
        output.write(result);
    }
    0
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        mem::{offset_of, size_of},
        ptr,
    };
    const OP: [u8; 32] = *b"abcdef0123456789abcdef0123456789";
    fn read_request() -> StartupRequest {
        StartupRequest {
            size: 64,
            version: 1,
            action: 1,
            role: 1,
            stage: 0,
            state: 0,
            code: 0,
            claim: 0,
            operation_id: OP,
        }
    }
    fn write_request() -> StartupRequest {
        StartupRequest {
            action: 2,
            stage: 1,
            state: 1,
            claim: 1,
            ..read_request()
        }
    }
    #[test]
    fn abi_layout_and_null_arguments_do_not_touch_files() {
        assert_eq!(
            (size_of::<StartupRequest>(), size_of::<StartupResult>()),
            (64, 64)
        );
        assert_eq!(offset_of!(StartupRequest, operation_id), 32);
        assert_eq!(offset_of!(StartupResult, operation_id), 32);
        assert_eq!(offset_of!(StartupRequest, claim), 28);
        assert_eq!(offset_of!(StartupResult, code), 28);
        let req = read_request();
        let mut out = StartupResult::empty(99);
        unsafe {
            assert_eq!(valmax_startup_status(ptr::null(), 1, &req, &mut out), 1);
            assert_eq!(out, StartupResult::empty(1));
            assert_eq!(valmax_startup_status(b"x".as_ptr(), 0, &req, &mut out), 1);
            assert_eq!(
                valmax_startup_status(b"x".as_ptr(), MAX_HELPER_PATH_BYTES + 1, &req, &mut out),
                1
            );
            assert_eq!(
                valmax_startup_status(b"x".as_ptr(), 1, ptr::null(), &mut out),
                1
            );
            assert_eq!(
                valmax_startup_status(b"x".as_ptr(), 1, &req, ptr::null_mut()),
                1
            );
        }
    }
    #[test]
    fn v1_numeric_tables_are_pinned() {
        assert_eq!(ROLES, ["launcher", "observe", "recover", "mdata"]);
        assert_eq!(
            STAGES,
            [
                "bootstrap",
                "import",
                "verify",
                "desktop",
                "profile",
                "observer",
                "optimization",
                "spawn",
                "protection",
                "recovery",
                "complete",
                "frida_initialize",
                "setup_stores",
                "bind",
                "native_spawn",
                "protection_attach",
                "ready"
            ]
        );
        assert_eq!(STATES, ["starting", "ready", "error"]);
        assert_eq!(
            STARTUP_CODES,
            [
                "startup_failed",
                "invalid_command",
                "unsupported_platform",
                "import_failed",
                "payload_invalid",
                "desktop_unavailable",
                "desktop_bootstrap_failed",
                "desktop_bootstrap_timeout",
                "profile_guard_failed",
                "recovery_required",
                "transfer_busy",
                "port_unavailable",
                "port_busy",
                "port_other_runtime",
                "observer_idle",
                "observer_start_failed",
                "observer_timeout",
                "observer_worker_stopped",
                "observer_identity_invalid",
                "observer_unavailable",
                "optimization_prepare_failed",
                "native_spawn_failed",
                "window_activation_failed",
                "protection_ack_failed",
                "ghost_ack_failed",
                "retention_ack_failed",
                "notification_ack_failed"
            ]
        );
    }
    #[test]
    fn complete_enum_validation_precedes_any_filesystem_call() {
        let path = b"C:/ValMAXBuild/fixtures/startup-abi/.mxmod-helper-temp";
        let base = read_request();
        let mut variants = Vec::new();
        for field in 0..8 {
            for value in [0, 1, 2, 3, 5, 28, 64, u32::MAX] {
                let mut r = base;
                let fields = [
                    &mut r.size,
                    &mut r.version,
                    &mut r.action,
                    &mut r.role,
                    &mut r.stage,
                    &mut r.state,
                    &mut r.code,
                    &mut r.claim,
                ];
                *fields.into_iter().nth(field).unwrap() = value;
                if decode(&r).is_none() {
                    variants.push(r);
                }
            }
        }
        let mut invalid_id = base;
        invalid_id.operation_id[0] = b'A';
        variants.push(invalid_id);
        let mut invalid_utf8 = base;
        invalid_utf8.operation_id[0] = 255;
        variants.push(invalid_utf8);
        for req in variants {
            assert_eq!(
                call_with(path, &req, |_, _| panic!("invalid must not perform IO")),
                StartupResult::empty(1)
            );
        }
        for role in 1..=4 {
            for stage in 1..=17 {
                for state in 1..=3 {
                    for code in 0..=27 {
                        for claim in 0..=1 {
                            let req = StartupRequest {
                                role,
                                stage,
                                state,
                                code,
                                claim,
                                ..write_request()
                            };
                            let valid = (state == 3) == (code != 0)
                                && (claim == 0 || (role != 2 && state == 1));
                            assert_eq!(decode(&req).is_some(), valid);
                        }
                    }
                }
            }
        }
        for path in [
            b"relative".as_slice(),
            b"C:/wrong",
            b"C:/x/../.mxmod-helper-temp",
            b"C:/x\0/.mxmod-helper-temp",
            b"\\\\server\\share\\.mxmod-helper-temp",
            b"\xff",
        ] {
            let result = call_with(path, &base, |_, _| {
                panic!("invalid path must not perform IO")
            });
            assert!(matches!(result.error, 1 | 3));
        }
    }
    #[test]
    fn finite_receipt_fields_preserve_all_error_codes() {
        for (number, code) in STARTUP_CODES.iter().enumerate() {
            let value = serde_json::json!({"schema":1,"operationId":std::str::from_utf8(&OP).unwrap(),
                "role":"observe","stage":"protection","state":"error","code":code});
            let result = receipt_result(value).unwrap();
            assert_eq!(
                (
                    result.outcome,
                    result.role,
                    result.stage,
                    result.state,
                    result.code
                ),
                (2, 2, 9, 3, number as u32 + 1)
            );
            assert_eq!(result.operation_id, OP);
        }
        for (error, expected) in [
            (startup_status::Error::InvalidReceipt, 2),
            (startup_status::Error::UnsafePath, 3),
            (startup_status::Error::StoreBusy, 4),
            (startup_status::Error::IoError, 5),
        ] {
            assert_eq!(
                call_with(
                    b"C:/ValMAXBuild/fixtures/x/.mxmod-helper-temp",
                    &read_request(),
                    |_, _| Err(error)
                ),
                StartupResult::empty(expected)
            );
        }
    }
    #[cfg(windows)]
    #[test]
    fn real_c_boundary_claim_read_sticky_failure_and_stale_child() {
        let base = Path::new("C:/ValMAXBuild/fixtures/startup-ffi");
        std::fs::create_dir_all(base).unwrap();
        let root = base.join(startup_status::new_operation_id().unwrap());
        std::fs::create_dir(&root).unwrap();
        let helper = root.join(".mxmod-helper-temp");
        let input = helper.to_str().unwrap().as_bytes();
        let invoke = |req: StartupRequest| {
            let mut out = StartupResult::empty(99);
            assert_eq!(
                unsafe { valmax_startup_status(input.as_ptr(), input.len(), &req, &mut out) },
                0
            );
            out
        };
        assert_eq!(invoke(read_request()), StartupResult::empty(0));
        assert!(!helper.exists());
        assert_eq!(invoke(write_request()).outcome, 1);
        let read = invoke(read_request());
        assert_eq!(
            (read.outcome, read.stage, read.state, read.operation_id),
            (2, 1, 1, OP)
        );
        let fail = StartupRequest {
            role: 2,
            stage: 9,
            state: 3,
            code: 27,
            claim: 0,
            ..write_request()
        };
        assert_eq!(invoke(fail).outcome, 1);
        let path = helper.join(startup_status::FILENAME);
        let precise = std::fs::read(&path).unwrap();
        assert_eq!(invoke(StartupRequest { code: 1, ..fail }).outcome, 0);
        assert_eq!(std::fs::read(&path).unwrap(), precise);
        assert_eq!(invoke(read_request()).code, 27);
        let newer = *b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
        assert_eq!(
            invoke(StartupRequest {
                operation_id: newer,
                ..write_request()
            })
            .outcome,
            1
        );
        assert_eq!(invoke(fail).outcome, 0);
        assert_eq!(invoke(read_request()), StartupResult::empty(0));
        assert_eq!(
            invoke(StartupRequest {
                operation_id: newer,
                ..read_request()
            })
            .operation_id,
            newer
        );
        std::fs::write(&path, b"synthetic-invalid").unwrap();
        assert_eq!(invoke(write_request()), StartupResult::empty(2));
        assert_eq!(std::fs::read(&path).unwrap(), b"synthetic-invalid");
        // Recursive removal is limited to the exact synthetic directory created above.
        let root = root.canonicalize().unwrap();
        let base = base.canonicalize().unwrap();
        assert!(root.starts_with(&base) && root != base);
        std::fs::remove_dir_all(root).unwrap();
    }
}
