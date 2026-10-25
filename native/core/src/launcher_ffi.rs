//! Typed, host-only configuration boundary for the native Windows launcher.
//! This is not exposed through Runtime::call, QML or a network endpoint.
use crate::launcher_profile::{self, ConfigState, PreparedConfig};
use std::{
    panic::{AssertUnwindSafe, catch_unwind},
    path::Path,
    slice,
};

pub const MAX_LOCAL_PATH_BYTES: usize = 32000;
pub const CODE_OK: u32 = 0;
pub const CODE_INVALID_REQUEST: u32 = 1;
pub const CODE_PROFILE_GUARD_FAILED: u32 = 2;
pub const CODE_TRANSFER_BUSY: u32 = 3;
pub const CODE_RECOVERY_REQUIRED: u32 = 4;
pub const CODE_INTERNAL_ERROR: u32 = 5;

/// ABI v1, 16 bytes on every supported platform. State: 0=unavailable,
/// 1=absent (check-only), 2=existing, 3=created. No borrowed buffers are returned.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LauncherProfileResult {
    pub size: u32,
    pub version: u32,
    pub code: u32,
    pub state: u32,
}
impl LauncherProfileResult {
    fn failure(code: u32) -> Self {
        Self {
            size: 16,
            version: 1,
            code,
            state: 0,
        }
    }
}

fn prepare_with(
    input: &[u8],
    check_only: u32,
    prepare: impl FnOnce(&Path, bool) -> Result<PreparedConfig, launcher_profile::Error>,
) -> LauncherProfileResult {
    if input.is_empty() || input.len() > MAX_LOCAL_PATH_BYTES || check_only > 1 {
        return LauncherProfileResult::failure(CODE_INVALID_REQUEST);
    }
    let Ok(text) = std::str::from_utf8(input) else {
        return LauncherProfileResult::failure(CODE_INVALID_REQUEST);
    };
    let path = Path::new(text);
    // Validate the complete path before invoking even a lock or filesystem API.
    if crate::launcher_config::config_path(path).is_err() {
        return LauncherProfileResult::failure(CODE_INVALID_REQUEST);
    }
    match catch_unwind(AssertUnwindSafe(|| prepare(path, check_only == 1))) {
        Ok(Ok(result)) => LauncherProfileResult {
            size: 16,
            version: 1,
            code: CODE_OK,
            state: match result.state {
                ConfigState::Absent => 1,
                ConfigState::Existing => 2,
                ConfigState::Created => 3,
            },
        },
        Ok(Err(error)) => LauncherProfileResult::failure(match error {
            launcher_profile::Error::ProfileGuardFailed => CODE_PROFILE_GUARD_FAILED,
            launcher_profile::Error::TransferBusy => CODE_TRANSFER_BUSY,
            launcher_profile::Error::RecoveryRequired => CODE_RECOVERY_REQUIRED,
        }),
        Err(_) => LauncherProfileResult::failure(CODE_INTERNAL_ERROR),
    }
}

/// # Safety
/// local must point to local_len readable UTF-8 bytes; output must point to one
/// writable, correctly aligned result. They must not overlap. No arbitrary
/// caller pointers are accepted from QML. Call on the SAME OS THREAD retaining
/// the launcher's outer TransferGuard. This function only guards its own IO;
/// successful preparation does not authorize spawning or clear recovery state.
/// A check-only call can report Absent and does not inspect a recovery marker.
/// Returns 1 for invalid ABI arguments, 0 for a finite result in output.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_launcher_prepare(
    local: *const u8,
    local_len: usize,
    check_only: u32,
    output: *mut LauncherProfileResult,
) -> i32 {
    if output.is_null() {
        return 1;
    }
    unsafe {
        output.write(LauncherProfileResult::failure(CODE_INVALID_REQUEST));
    }
    if local.is_null() || local_len == 0 || local_len > MAX_LOCAL_PATH_BYTES || check_only > 1 {
        return 1;
    }
    let input = unsafe { slice::from_raw_parts(local, local_len) };
    // Include path validation/allocation in the unwind boundary too.
    let result = catch_unwind(AssertUnwindSafe(|| {
        prepare_with(input, check_only, launcher_profile::prepare)
    }))
    .unwrap_or_else(|_| LauncherProfileResult::failure(CODE_INTERNAL_ERROR));
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
    #[test]
    fn typed_layout_and_invalid_abi_arguments_are_finite_without_io() {
        assert_eq!(size_of::<LauncherProfileResult>(), 16);
        assert_eq!(
            [
                offset_of!(LauncherProfileResult, size),
                offset_of!(LauncherProfileResult, version),
                offset_of!(LauncherProfileResult, code),
                offset_of!(LauncherProfileResult, state)
            ],
            [0, 4, 8, 12]
        );
        let invalid = LauncherProfileResult::failure(CODE_INVALID_REQUEST);
        let mut result = LauncherProfileResult {
            size: 99,
            version: 99,
            code: 99,
            state: 99,
        };
        unsafe {
            assert_eq!(valmax_launcher_prepare(ptr::null(), 1, 0, &mut result), 1);
            assert_eq!(result, invalid);
            assert_eq!(valmax_launcher_prepare(b"x".as_ptr(), 0, 0, &mut result), 1);
            assert_eq!(
                valmax_launcher_prepare(b"x".as_ptr(), MAX_LOCAL_PATH_BYTES + 1, 0, &mut result),
                1
            );
            assert_eq!(valmax_launcher_prepare(b"x".as_ptr(), 1, 2, &mut result), 1);
            assert_eq!(
                valmax_launcher_prepare(b"x".as_ptr(), 1, 0, ptr::null_mut()),
                1
            );
        }
        assert_eq!(result, invalid);
    }
    #[test]
    fn malformed_path_never_reaches_prepare() {
        for data in [
            b"".as_slice(),
            b"\xff",
            b"relative",
            b"C:/tmp/ONEME",
            b"C:/tmp/../other",
            b"C:/tmp\0ignored",
            b"\\\\server\\share",
            b"C:/tmp:stream",
        ] {
            assert_eq!(
                prepare_with(data, 0, |_, _| panic!("must not perform IO")).code,
                CODE_INVALID_REQUEST
            );
        }
        let data = b"C:/ValMAXBuild/test-data/launcher-boundary";
        assert_eq!(
            prepare_with(data, 2, |_, _| panic!("must not perform IO")).code,
            CODE_INVALID_REQUEST
        );
        let mut result = LauncherProfileResult::failure(CODE_OK);
        unsafe {
            assert_eq!(
                valmax_launcher_prepare(b"relative".as_ptr(), 8, 0, &mut result),
                0
            );
        }
        assert_eq!(result, LauncherProfileResult::failure(CODE_INVALID_REQUEST));
    }
    #[test]
    fn states_and_errors_cross_the_boundary_without_paths_or_allocated_buffers() {
        let input = "C:/ValMAXBuild/fixtures/локальный-профиль".as_bytes();
        for (state, expected) in [
            (ConfigState::Absent, 1),
            (ConfigState::Existing, 2),
            (ConfigState::Created, 3),
        ] {
            let result = prepare_with(input, 1, |path, check| {
                assert!(check);
                assert_eq!(path, Path::new(std::str::from_utf8(input).unwrap()));
                Ok(PreparedConfig {
                    path: path.join("MXMOD/config.json"),
                    state,
                })
            });
            assert_eq!(
                result,
                LauncherProfileResult {
                    size: 16,
                    version: 1,
                    code: CODE_OK,
                    state: expected
                }
            );
        }
        for (error, expected) in [
            (
                launcher_profile::Error::ProfileGuardFailed,
                CODE_PROFILE_GUARD_FAILED,
            ),
            (launcher_profile::Error::TransferBusy, CODE_TRANSFER_BUSY),
            (
                launcher_profile::Error::RecoveryRequired,
                CODE_RECOVERY_REQUIRED,
            ),
        ] {
            assert_eq!(
                prepare_with(input, 0, |_, check| {
                    assert!(!check);
                    Err(error)
                }),
                LauncherProfileResult::failure(expected)
            );
        }
        assert_eq!(
            prepare_with(input, 0, |_, _| panic!("synthetic failure")).code,
            CODE_INTERNAL_ERROR
        );
    }
}
