//! Host-only C ABI for one selected public-media file. Opaque integer tokens
//! never become pointers. The registry owns bounded Arc jobs; an in-flight stage
//! keeps its job and quota slot alive even after another thread revokes its token.
//! No Runtime/QML dispatch route, profile lookup, network or callback is added.
use crate::{
    ffi::CoreBuffer,
    media_staging::{self, Cancellation, MediaSpec, PreparedMedia, StagingRoot},
};
use std::{
    collections::BTreeMap,
    mem,
    panic::{AssertUnwindSafe, catch_unwind},
    ptr, slice,
    sync::{Arc, Mutex, MutexGuard, OnceLock},
};

pub const ABI_VERSION: u32 = 1;
pub const MAX_ROOT_BYTES: usize = 31_000;
pub const MAX_JOBS: usize = 64;
pub const OK: u32 = 0;
pub const INVALID_REQUEST: u32 = 1;
pub const INVALID_HANDLE: u32 = 12;
pub const INVALID_STATE: u32 = 13;
pub const HANDLE_LIMIT: u32 = 14;
pub const INTERNAL_ERROR: u32 = 15;
pub const CANCELLED: u32 = 9;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct StageRequest {
    pub size: u32,
    pub version: u32,
    pub mime: u32,
    pub reserved: u32,
    pub expected_bytes: u64,
    pub now_unix_seconds: u64,
}
#[repr(C)]
pub struct StageResult {
    pub size: u32,
    pub version: u32,
    pub byte_length: u64,
    pub file_url: CoreBuffer,
}
impl StageResult {
    pub fn empty() -> Self {
        Self {
            size: 40,
            version: ABI_VERSION,
            byte_length: 0,
            file_url: CoreBuffer {
                data: ptr::null_mut(),
                len: 0,
                capacity: 0,
            },
        }
    }
}
fn error(error: media_staging::Error) -> u32 {
    use media_staging::Error::*;
    match error {
        InvalidInput => 1,
        UnsupportedMedia => 2,
        FileTooLarge => 3,
        CacheFull => 4,
        Busy => 5,
        UnsafePath => 6,
        Unavailable => 7,
        NoSpace => 8,
        Cancelled => 9,
        CleanupFailed => 10,
        UnsupportedPlatform => 11,
    }
}
fn guarded(operation: impl FnOnce() -> u32) -> u32 {
    catch_unwind(AssertUnwindSafe(operation)).unwrap_or(INTERNAL_ERROR)
}
fn mime(value: u32) -> Option<&'static str> {
    match value {
        1 => Some("image/jpeg"),
        2 => Some("image/png"),
        3 => Some("image/gif"),
        4 => Some("image/webp"),
        5 => Some("video/webm"),
        6 => Some("video/mp4"),
        _ => None,
    }
}
enum Phase {
    Ready(StagingRoot),
    Running,
    Prepared(PreparedMedia),
    Retained,
    Cancelled,
    Failed,
    Closed,
}
struct Job {
    state: Mutex<Phase>,
    cancel: Cancellation,
    _permit: Permit,
}
struct Registry {
    next: u64,
    live: usize,
    jobs: BTreeMap<u64, Arc<Job>>,
}
static REGISTRY: OnceLock<Mutex<Registry>> = OnceLock::new();
fn registry() -> &'static Mutex<Registry> {
    REGISTRY.get_or_init(|| {
        Mutex::new(Registry {
            next: 1,
            live: 0,
            jobs: BTreeMap::new(),
        })
    })
}
fn lock_registry() -> Result<MutexGuard<'static, Registry>, u32> {
    registry().lock().map_err(|_| INTERNAL_ERROR)
}
fn state(job: &Job) -> Result<MutexGuard<'_, Phase>, u32> {
    job.state.lock().map_err(|_| INTERNAL_ERROR)
}
fn lookup(handle: u64) -> Result<Arc<Job>, u32> {
    lock_registry()?
        .jobs
        .get(&handle)
        .cloned()
        .ok_or(INVALID_HANDLE)
}

// The slot follows the Arc job, not just the registry token. Thus repeatedly
// releasing blocked jobs cannot exceed MAX_JOBS outstanding operations.
struct Permit;
impl Drop for Permit {
    fn drop(&mut self) {
        if let Ok(mut registry) = registry().lock() {
            registry.live = registry.live.saturating_sub(1);
        }
    }
}
struct Reservation {
    handle: u64,
    permit: Option<Permit>,
}
impl Reservation {
    fn new() -> Result<Self, u32> {
        let mut registry = lock_registry()?;
        if registry.live >= MAX_JOBS {
            return Err(HANDLE_LIMIT);
        }
        let next = registry.next.checked_add(1).ok_or(HANDLE_LIMIT)?;
        let handle = registry.next;
        registry.next = next;
        registry.live += 1;
        Ok(Self {
            handle,
            permit: Some(Permit),
        })
    }
    fn commit(mut self, root: StagingRoot) -> Result<u64, u32> {
        let job = Arc::new(Job {
            state: Mutex::new(Phase::Ready(root)),
            cancel: Cancellation::default(),
            _permit: self.permit.take().ok_or(INTERNAL_ERROR)?,
        });
        let mut registry = lock_registry()?;
        registry.jobs.insert(self.handle, job);
        Ok(self.handle)
    }
}
fn begin(root: &[u8]) -> Result<u64, u32> {
    if root.is_empty() || root.len() > MAX_ROOT_BYTES || root.contains(&0) {
        return Err(INVALID_REQUEST);
    }
    let path = std::str::from_utf8(root).map_err(|_| INVALID_REQUEST)?;
    let reservation = Reservation::new()?;
    // No registry mutex remains held across filesystem work.
    let root = StagingRoot::open(path).map_err(error)?;
    reservation.commit(root)
}

// Restores a finite terminal phase if Rust unwinds out of a running operation.
struct RunningGuard(Arc<Job>);
impl Drop for RunningGuard {
    fn drop(&mut self) {
        if let Ok(mut state) = self.0.state.lock() {
            if matches!(*state, Phase::Running) {
                *state = Phase::Failed;
            }
        }
    }
}

/// Validated buffers are borrowed until this synchronous function completes.
/// `work` is private for deterministic race tests; production always uses stage.
unsafe fn run_stage(
    handle: u64,
    request: StageRequest,
    data: &[u8],
    output: *mut StageResult,
    work: impl FnOnce(
        &mut StagingRoot,
        &[u8],
        MediaSpec<'_>,
        u64,
        &Cancellation,
    ) -> media_staging::Result<PreparedMedia>,
) -> u32 {
    let Some(media_mime) = mime(request.mime) else {
        return INVALID_REQUEST;
    };
    if request.size != 32
        || request.version != ABI_VERSION
        || request.reserved != 0
        || data.is_empty()
        || request.expected_bytes != data.len() as u64
    {
        return INVALID_REQUEST;
    }
    if request.expected_bytes > media_staging::MAX_FILE_BYTES {
        return 3;
    }
    let job = match lookup(handle) {
        Ok(job) => job,
        Err(code) => return code,
    };
    let mut root = {
        let mut phase = match state(&job) {
            Ok(phase) => phase,
            Err(code) => return code,
        };
        if job.cancel.check().is_err() {
            return CANCELLED;
        }
        if !matches!(*phase, Phase::Ready(_)) {
            return INVALID_STATE;
        }
        let Phase::Ready(root) = mem::replace(&mut *phase, Phase::Running) else {
            unreachable!()
        };
        root
    };
    let _running = RunningGuard(job.clone());
    let staged = work(
        &mut root,
        data,
        MediaSpec {
            mime: media_mime,
            expected_bytes: request.expected_bytes,
        },
        request.now_unix_seconds,
        &job.cancel,
    );
    // Discard the root's Arc outside every registry/state lock. PreparedMedia
    // holds its own pinned root while waiting for native draft acceptance.
    drop(root);
    let prepared = match staged {
        Ok(prepared) => prepared,
        Err(err) => {
            let mut phase = match state(&job) {
                Ok(phase) => phase,
                Err(code) => return code,
            };
            if matches!(*phase, Phase::Running) {
                *phase = Phase::Failed;
            }
            // Preserve cleanup failure even when cancellation caused the failure.
            return if err == media_staging::Error::CleanupFailed {
                error(err)
            } else if job.cancel.check().is_err() {
                CANCELLED
            } else {
                error(err)
            };
        }
    };
    let mut url = prepared.file_url().into_bytes();
    let byte_length = prepared.byte_length();
    let mut prepared = Some(prepared);
    // Publication is a single ordered step against release. No filesystem work
    // or caller callbacks occurs while these locks are held.
    let publish = (|| -> Result<(), u32> {
        let registry = lock_registry()?;
        if !registry
            .jobs
            .get(&handle)
            .is_some_and(|current| Arc::ptr_eq(current, &job))
        {
            return Err(CANCELLED);
        }
        let mut phase = state(&job)?;
        if job.cancel.check().is_err() || !matches!(*phase, Phase::Running) {
            return Err(CANCELLED);
        }
        *phase = Phase::Prepared(prepared.take().ok_or(INTERNAL_ERROR)?);
        let file_url = CoreBuffer {
            data: url.as_mut_ptr(),
            len: url.len(),
            capacity: url.capacity(),
        };
        mem::forget(mem::take(&mut url));
        unsafe {
            output.write(StageResult {
                size: 40,
                version: ABI_VERSION,
                byte_length,
                file_url,
            });
        }
        Ok(())
    })();
    // Explicit cleanup is outside both locks, and a failed cleanup is not
    // disguised as successful cancellation.
    match publish {
        Ok(()) => OK,
        Err(code) => match prepared {
            Some(prepared) => prepared.cancel().map_or_else(error, |_| code),
            None => INTERNAL_ERROR,
        },
    }
}

fn signal_cancel(handle: u64) -> u32 {
    // Borrow under the short registry lock instead of cloning an Arc: this call
    // must never become the last owner responsible for filesystem cleanup.
    let registry = match lock_registry() {
        Ok(registry) => registry,
        Err(code) => return code,
    };
    match registry.jobs.get(&handle) {
        Some(job) => {
            job.cancel.cancel();
            OK
        }
        None => INVALID_HANDLE,
    }
}
fn finish_phase(old: Phase) -> u32 {
    match old {
        Phase::Prepared(prepared) => prepared.cancel().map_or_else(error, |_| OK),
        _ => OK,
    }
}
fn cancel(handle: u64) -> u32 {
    let job = match lookup(handle) {
        Ok(job) => job,
        Err(code) => return code,
    };
    job.cancel.cancel();
    let old = {
        let mut phase = match state(&job) {
            Ok(phase) => phase,
            Err(code) => return code,
        };
        if matches!(*phase, Phase::Retained) {
            return OK;
        }
        mem::replace(&mut *phase, Phase::Cancelled)
    };
    finish_phase(old)
}
fn retain(handle: u64) -> u32 {
    let job = match lookup(handle) {
        Ok(job) => job,
        Err(code) => return code,
    };
    let prepared = {
        let registry = match lock_registry() {
            Ok(registry) => registry,
            Err(code) => return code,
        };
        if !registry
            .jobs
            .get(&handle)
            .is_some_and(|current| Arc::ptr_eq(current, &job))
        {
            return CANCELLED;
        }
        let mut phase = match state(&job) {
            Ok(phase) => phase,
            Err(code) => return code,
        };
        if matches!(*phase, Phase::Retained) {
            return OK;
        }
        if job.cancel.check().is_err() {
            return CANCELLED;
        }
        if !matches!(*phase, Phase::Prepared(_)) {
            return INVALID_STATE;
        }
        let Phase::Prepared(prepared) = mem::replace(&mut *phase, Phase::Retained) else {
            unreachable!()
        };
        prepared
    };
    prepared.retain_for_draft();
    OK
}
fn release(handle: u64) -> u32 {
    let job = {
        let mut registry = match lock_registry() {
            Ok(registry) => registry,
            Err(code) => return code,
        };
        let job = registry.jobs.remove(&handle);
        if let Some(job) = &job {
            job.cancel.cancel();
        }
        job
    };
    let Some(job) = job else {
        return OK;
    };
    let old = {
        let mut phase = match state(&job) {
            Ok(phase) => phase,
            Err(code) => return code,
        };
        mem::replace(&mut *phase, Phase::Closed)
    };
    finish_phase(old)
}

#[unsafe(no_mangle)]
pub extern "C" fn valmax_media_staging_abi_version() -> u32 {
    ABI_VERSION
}

/// # Safety
/// Trusted host pointers only: root is readable for root_len, handle writable,
/// aligned and disjoint. Caller retains root bytes. Handle output starts empty.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_media_staging_begin(
    root: *const u8,
    root_len: usize,
    handle: *mut u64,
) -> u32 {
    if handle.is_null() {
        return INVALID_REQUEST;
    }
    unsafe {
        handle.write(0);
    }
    if root.is_null() || root_len == 0 || root_len > MAX_ROOT_BYTES {
        return INVALID_REQUEST;
    }
    guarded(
        || match begin(unsafe { slice::from_raw_parts(root, root_len) }) {
            Ok(value) => {
                unsafe { handle.write(value) };
                OK
            }
            Err(code) => code,
        },
    )
}
/// # Safety
/// request and result are complete aligned disjoint structs; data is immutable
/// and readable until return. Input/result may not alias. Prior URL must be freed.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_media_staging_stage(
    handle: u64,
    request: *const StageRequest,
    data: *const u8,
    data_len: usize,
    result: *mut StageResult,
) -> u32 {
    if result.is_null() {
        return INVALID_REQUEST;
    }
    unsafe {
        result.write(StageResult::empty());
    }
    if request.is_null() || data.is_null() || data_len == 0 {
        return INVALID_REQUEST;
    }
    if data_len as u64 > media_staging::MAX_FILE_BYTES {
        return 3;
    }
    let request = unsafe { request.read() };
    let data = unsafe { slice::from_raw_parts(data, data_len) };
    guarded(|| unsafe {
        run_stage(
            handle,
            request,
            data,
            result,
            |root, data, spec, now, cancel| root.stage(data, spec, now, cancel),
        )
    })
}
#[unsafe(no_mangle)]
pub extern "C" fn valmax_media_staging_signal_cancel(handle: u64) -> u32 {
    guarded(|| signal_cancel(handle))
}
#[unsafe(no_mangle)]
pub extern "C" fn valmax_media_staging_cancel(handle: u64) -> u32 {
    guarded(|| cancel(handle))
}
#[unsafe(no_mangle)]
pub extern "C" fn valmax_media_staging_retain(handle: u64) -> u32 {
    guarded(|| retain(handle))
}
#[unsafe(no_mangle)]
pub extern "C" fn valmax_media_staging_release(handle: u64) -> u32 {
    guarded(|| release(handle))
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;
    use std::{
        fs,
        path::{Path, PathBuf},
        sync::mpsc,
        time::Duration,
    };
    static SERIAL: Mutex<()> = Mutex::new(());
    const PNG: &[u8] = b"\x89PNG\r\n\x1a\nSYNTHETIC-ABI-RACE";
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let mut id = [0u8; 16];
            getrandom::getrandom(&mut id).unwrap();
            let path = Path::new("C:/ValMAXBuild/fixtures/media-staging-ffi-unit")
                .join(id.iter().map(|b| format!("{b:02x}")).collect::<String>());
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
        fn begin(&self) -> u64 {
            begin(self.0.to_str().unwrap().as_bytes()).unwrap()
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let base = fs::canonicalize("C:/ValMAXBuild/fixtures/media-staging-ffi-unit").unwrap();
            let root = fs::canonicalize(&self.0).unwrap();
            assert!(root.starts_with(&base) && root != base);
            fs::remove_dir_all(root).unwrap();
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
    fn paused_stage(
        handle: u64,
    ) -> (
        mpsc::Receiver<PathBuf>,
        mpsc::Sender<()>,
        std::thread::JoinHandle<(u32, usize)>,
    ) {
        let (ready_tx, ready_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            let mut out = StageResult::empty();
            let code = guarded(|| unsafe {
                run_stage(
                    handle,
                    request(),
                    PNG,
                    &mut out,
                    |root, data, spec, now, cancel| {
                        let file = root.stage(data, spec, now, cancel)?;
                        ready_tx.send(file.path().to_owned()).unwrap();
                        resume_rx.recv_timeout(Duration::from_secs(10)).unwrap();
                        Ok(file)
                    },
                )
            });
            let length = out.file_url.len;
            unsafe { crate::ffi::valmax_core_buffer_free(&mut out.file_url) };
            (code, length)
        });
        (ready_rx, resume_tx, worker)
    }
    #[test]
    fn staging_ffi_cancel_and_release_before_publication_never_return_stale_url() {
        let _serial = SERIAL.lock().unwrap();
        for action in [0, 1, 2] {
            let fixture = Fixture::new();
            let handle = fixture.begin();
            let (ready, resume, worker) = paused_stage(handle);
            let path = ready.recv_timeout(Duration::from_secs(10)).unwrap();
            assert!(path.exists());
            assert_eq!(
                match action {
                    0 => signal_cancel(handle),
                    1 => cancel(handle),
                    _ => release(handle),
                },
                OK
            );
            resume.send(()).unwrap();
            assert_eq!(worker.join().unwrap(), (CANCELLED, 0));
            assert!(!path.exists());
            assert_eq!(release(handle), OK);
        }
        assert_eq!(lock_registry().unwrap().live, 0);
    }
    #[test]
    fn staging_ffi_revoked_running_job_keeps_capacity_until_it_finishes() {
        let _serial = SERIAL.lock().unwrap();
        let fixture = Fixture::new();
        let handle = fixture.begin();
        let (ready, resume, worker) = paused_stage(handle);
        ready.recv_timeout(Duration::from_secs(10)).unwrap();
        assert_eq!(release(handle), OK);
        let mut handles = Vec::new();
        for _ in 0..MAX_JOBS - 1 {
            handles.push(fixture.begin());
        }
        assert_eq!(
            begin(fixture.0.to_str().unwrap().as_bytes()).unwrap_err(),
            HANDLE_LIMIT
        );
        resume.send(()).unwrap();
        assert_eq!(worker.join().unwrap(), (CANCELLED, 0));
        let replacement = fixture.begin();
        assert!(replacement > handle);
        assert_eq!(signal_cancel(handle), INVALID_HANDLE);
        assert_eq!(release(replacement), OK);
        for handle in handles {
            assert_eq!(release(handle), OK);
        }
        assert_eq!(lock_registry().unwrap().live, 0);
    }
    #[test]
    fn staging_ffi_reservations_and_failed_begin_restore_capacity() {
        let _serial = SERIAL.lock().unwrap();
        let fixture = Fixture::new();
        let reservations: Vec<_> = (0..MAX_JOBS).map(|_| Reservation::new().unwrap()).collect();
        assert!(matches!(Reservation::new(), Err(HANDLE_LIMIT)));
        drop(reservations);
        assert_eq!(lock_registry().unwrap().live, 0);
        for _ in 0..MAX_JOBS + 1 {
            assert!(begin(fixture.0.join("missing").to_str().unwrap().as_bytes()).is_err());
        }
        assert_eq!(lock_registry().unwrap().live, 0);
        let handle = fixture.begin();
        assert_eq!(release(handle), OK);
    }
    #[test]
    fn staging_ffi_panic_guard_leaves_a_terminal_releasable_job() {
        let _serial = SERIAL.lock().unwrap();
        let fixture = Fixture::new();
        let handle = fixture.begin();
        let mut out = StageResult::empty();
        let code = guarded(|| unsafe {
            run_stage(handle, request(), PNG, &mut out, |_, _, _, _, _| {
                panic!("synthetic stage panic")
            })
        });
        assert_eq!(code, INTERNAL_ERROR);
        assert!(out.file_url.data.is_null());
        assert!(matches!(
            *state(&lookup(handle).unwrap()).unwrap(),
            Phase::Failed
        ));
        assert_eq!(release(handle), OK);
        assert_eq!(lock_registry().unwrap().live, 0);
    }
}
