#![cfg(windows)]
use std::{
    fs,
    path::{Path, PathBuf},
    ptr,
};
use valmax_core::{ffi::valmax_core_buffer_free, media_staging_ffi::*};
const PNG: &[u8] = b"\x89PNG\r\n\x1a\nSYNTHETIC-PUBLIC-ABI";
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut id = [0u8; 16];
        getrandom::getrandom(&mut id).unwrap();
        let path = Path::new("C:/ValMAXBuild/fixtures/media-staging-ffi")
            .join(id.iter().map(|b| format!("{b:02x}")).collect::<String>());
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn begin(&self) -> Owned {
        let root = self.0.to_str().unwrap().as_bytes();
        let mut handle = 0;
        assert_eq!(
            unsafe { valmax_media_staging_begin(root.as_ptr(), root.len(), &mut handle) },
            OK
        );
        assert_ne!(handle, 0);
        Owned(handle)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize("C:/ValMAXBuild/fixtures/media-staging-ffi").unwrap();
        let root = fs::canonicalize(&self.0).unwrap();
        assert!(root.starts_with(&base) && root != base);
        fs::remove_dir_all(root).unwrap();
    }
}
struct Owned(u64);
impl Drop for Owned {
    fn drop(&mut self) {
        valmax_media_staging_release(self.0);
    }
}
fn request() -> StageRequest {
    StageRequest {
        size: 32,
        version: 1,
        mime: 2,
        reserved: 0,
        expected_bytes: PNG.len() as u64,
        now_unix_seconds: 0,
    }
}
fn stage(handle: u64, req: &StageRequest) -> (u32, StageResult) {
    let mut out = StageResult::empty();
    let code =
        unsafe { valmax_media_staging_stage(handle, req, PNG.as_ptr(), PNG.len(), &mut out) };
    (code, out)
}
fn take_url(out: &mut StageResult) -> String {
    let url = unsafe {
        std::str::from_utf8(std::slice::from_raw_parts(
            out.file_url.data,
            out.file_url.len,
        ))
        .unwrap()
        .to_owned()
    };
    unsafe { valmax_core_buffer_free(&mut out.file_url) };
    assert!(out.file_url.data.is_null() && out.file_url.len == 0 && out.file_url.capacity == 0);
    url
}
fn empty(out: &StageResult) {
    assert_eq!((out.size, out.version, out.byte_length), (40, 1, 0));
    assert!(out.file_url.data.is_null() && out.file_url.len == 0 && out.file_url.capacity == 0);
}

#[test]
fn staging_ffi_layout_null_and_finite_codes() {
    assert_eq!(valmax_media_staging_abi_version(), 1);
    assert_eq!(std::mem::size_of::<StageRequest>(), 32);
    assert_eq!(std::mem::size_of::<StageResult>(), 40);
    assert_eq!(std::mem::offset_of!(StageRequest, expected_bytes), 16);
    assert_eq!(std::mem::offset_of!(StageResult, file_url), 16);
    let mut handle = 99;
    assert_eq!(
        unsafe { valmax_media_staging_begin(ptr::null(), 1, &mut handle) },
        INVALID_REQUEST
    );
    assert_eq!(handle, 0);
    assert_eq!(
        unsafe { valmax_media_staging_begin(b"x".as_ptr(), 1, ptr::null_mut()) },
        INVALID_REQUEST
    );
    assert_eq!(
        unsafe { valmax_media_staging_begin(b"x".as_ptr(), MAX_ROOT_BYTES + 1, &mut handle) },
        INVALID_REQUEST
    );
    let mut out = StageResult::empty();
    assert_eq!(
        unsafe { valmax_media_staging_stage(0, ptr::null(), PNG.as_ptr(), PNG.len(), &mut out) },
        INVALID_REQUEST
    );
    empty(&out);
    assert_eq!(
        unsafe { valmax_media_staging_stage(0, &request(), ptr::null(), 1, &mut out) },
        INVALID_REQUEST
    );
    empty(&out);
    assert_eq!(
        unsafe {
            valmax_media_staging_stage(0, &request(), PNG.as_ptr(), 25 * 1024 * 1024 + 1, &mut out)
        },
        3
    );
    empty(&out);
    assert_eq!(stage(u64::MAX, &request()).0, INVALID_HANDLE);
    assert_eq!(valmax_media_staging_cancel(0), INVALID_HANDLE);
    assert_eq!(valmax_media_staging_signal_cancel(u64::MAX), INVALID_HANDLE);
    assert_eq!(valmax_media_staging_retain(u64::MAX), INVALID_HANDLE);
    assert_eq!(valmax_media_staging_release(0), OK);
    assert_eq!(valmax_media_staging_release(u64::MAX), OK);
}
#[test]
fn staging_ffi_malformed_input_does_not_create_files_or_consume_ready_job() {
    let f = Fixture::new();
    let job = f.begin();
    for change in 0..5 {
        let mut req = request();
        match change {
            0 => req.size = 31,
            1 => req.version = 2,
            2 => req.mime = 7,
            3 => req.reserved = 1,
            _ => req.expected_bytes += 1,
        };
        let (code, out) = stage(job.0, &req);
        assert_eq!(code, INVALID_REQUEST);
        empty(&out);
    }
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
    let mut out = StageResult::empty();
    assert_eq!(
        unsafe { valmax_media_staging_stage(job.0, &request(), PNG.as_ptr(), PNG.len(), &mut out) },
        OK
    );
    take_url(&mut out);
    assert_eq!(valmax_media_staging_cancel(job.0), OK);
}
#[test]
fn staging_ffi_manual_handoff_retains_file_and_caller_owns_url() {
    let f = Fixture::new();
    let job = f.begin();
    assert_eq!(valmax_media_staging_retain(job.0), INVALID_STATE);
    let (code, mut out) = stage(job.0, &request());
    assert_eq!(code, OK);
    assert_eq!(out.byte_length, PNG.len() as u64);
    assert_eq!(stage(job.0, &request()).0, INVALID_STATE);
    let url = take_url(&mut out);
    let path = PathBuf::from(url.strip_prefix("file:///").unwrap());
    assert_eq!(fs::read(&path).unwrap(), PNG);
    assert_eq!(valmax_media_staging_retain(job.0), OK);
    assert_eq!(valmax_media_staging_retain(job.0), OK);
    assert_eq!(valmax_media_staging_signal_cancel(job.0), OK);
    assert_eq!(valmax_media_staging_cancel(job.0), OK);
    assert_eq!(valmax_media_staging_release(job.0), OK);
    assert_eq!(valmax_media_staging_release(job.0), OK);
    assert_eq!(fs::read(path).unwrap(), PNG);
}
#[test]
fn staging_ffi_signal_cancel_prepared_has_no_cleanup_then_cancel_is_terminal() {
    let f = Fixture::new();
    let job = f.begin();
    let (code, mut out) = stage(job.0, &request());
    assert_eq!(code, OK);
    let path = PathBuf::from(take_url(&mut out).strip_prefix("file:///").unwrap());
    assert_eq!(valmax_media_staging_signal_cancel(job.0), OK);
    assert!(path.exists());
    assert_eq!(valmax_media_staging_retain(job.0), CANCELLED);
    assert_eq!(valmax_media_staging_cancel(job.0), OK);
    assert!(!path.exists());
    assert_eq!(valmax_media_staging_cancel(job.0), OK);
    assert_eq!(valmax_media_staging_retain(job.0), CANCELLED);
}
#[test]
fn staging_ffi_release_and_cancel_report_cleanup_failure_without_freeing_url() {
    for use_release in [false, true] {
        let f = Fixture::new();
        let job = f.begin();
        let (code, mut out) = stage(job.0, &request());
        assert_eq!(code, OK);
        let url = unsafe {
            std::str::from_utf8(std::slice::from_raw_parts(
                out.file_url.data,
                out.file_url.len,
            ))
            .unwrap()
            .to_owned()
        };
        let path = PathBuf::from(url.strip_prefix("file:///").unwrap());
        let reader = fs::File::open(&path).unwrap();
        assert_eq!(
            if use_release {
                valmax_media_staging_release(job.0)
            } else {
                valmax_media_staging_cancel(job.0)
            },
            10
        );
        assert_eq!(take_url(&mut out), url);
        assert!(path.exists());
        drop(reader);
    }
}
#[test]
fn staging_ffi_cancel_ready_and_failed_stage_are_one_shot() {
    let f = Fixture::new();
    let job = f.begin();
    assert_eq!(valmax_media_staging_signal_cancel(job.0), OK);
    let (code, out) = stage(job.0, &request());
    assert_eq!(code, CANCELLED);
    empty(&out);
    let second = f.begin();
    let mut req = request();
    req.mime = 1;
    let (code, out) = stage(second.0, &req);
    assert_eq!(code, 2);
    empty(&out);
    assert_eq!(stage(second.0, &request()).0, INVALID_STATE);
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
}
