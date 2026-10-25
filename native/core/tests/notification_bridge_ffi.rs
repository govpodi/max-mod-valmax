#![cfg(windows)]
//! Owned callback fixture, synthetic PIN only. No MAX process or notifications.
use serde_json::{Value, json};
use std::{
    ffi::c_void,
    path::PathBuf,
    ptr,
    sync::{
        Arc, Condvar, Mutex,
        atomic::{AtomicBool, AtomicUsize, Ordering},
    },
    time::Duration,
};
use valmax_core::{ffi::*, notification_bridge::NativeCallbacks, runtime::Runtime};
struct Host {
    entered: AtomicBool,
    hold: AtomicBool,
    gate: Mutex<()>,
    resume: Condvar,
    thread: Mutex<Option<std::thread::ThreadId>>,
    prepares: AtomicUsize,
    releases: AtomicUsize,
    fail: AtomicBool,
    malformed: AtomicBool,
    lease: AtomicUsize,
}
impl Default for Host {
    fn default() -> Self {
        Self {
            entered: AtomicBool::new(false),
            hold: AtomicBool::new(false),
            gate: Mutex::new(()),
            resume: Condvar::new(),
            thread: Mutex::new(None),
            prepares: AtomicUsize::new(0),
            releases: AtomicUsize::new(0),
            fail: AtomicBool::new(false),
            malformed: AtomicBool::new(false),
            lease: AtomicUsize::new(0),
        }
    }
}
unsafe extern "C" fn prepare(
    context: *mut c_void,
    input: *const u8,
    len: usize,
    lease: *mut u64,
) -> i32 {
    let host = unsafe { &*(context as *const Host) };
    let result = std::panic::catch_unwind(|| {
        assert!(len <= 65536);
        let bytes = unsafe { std::slice::from_raw_parts(input, len) };
        let value: Value = serde_json::from_slice(bytes).unwrap();
        assert_eq!(value.as_object().unwrap().len(), 5);
        assert_eq!(value["schema"], 1);
        assert_eq!(value["ready"], true);
        assert_eq!(value["leaseMs"], 5000);
        let records = value["protected"].as_array().unwrap();
        assert!(records.len() <= 256);
        for row in records {
            assert_eq!(row.as_object().unwrap().len(), 2);
            assert!(row["ownerId"].is_string() && row["chatId"].is_string());
        }
        assert!(!String::from_utf8_lossy(bytes).contains("681439"));
        assert!(value.get("pin").is_none());
        let expected = valmax_core::settings_io::sha256(
            &serde_json::to_vec(&json!({"schema":1,"ready":true,"protected":records})).unwrap(),
        );
        assert_eq!(value["revision"], expected);
        *host.thread.lock().unwrap() = Some(std::thread::current().id());
        host.prepares.fetch_add(1, Ordering::SeqCst);
        host.entered.store(true, Ordering::SeqCst);
        let mut guard = host.gate.lock().unwrap();
        while host.hold.load(Ordering::SeqCst) {
            guard = host
                .resume
                .wait_timeout(guard, Duration::from_secs(2))
                .unwrap()
                .0;
        }
        if host.malformed.load(Ordering::SeqCst) {
            return 0;
        }
        let id = host.lease.fetch_add(1, Ordering::SeqCst) + 1;
        unsafe { *lease = id as u64 };
        if host.fail.load(Ordering::SeqCst) {
            2
        } else {
            0
        }
    });
    result.unwrap_or(9)
}
unsafe extern "C" fn release(context: *mut c_void, lease: u64) {
    let host = unsafe { &*(context as *const Host) };
    if *host.thread.lock().unwrap() != Some(std::thread::current().id()) || lease == 0 {
        host.malformed.store(true, Ordering::SeqCst);
    }
    host.releases.fetch_add(1, Ordering::SeqCst);
}
fn callbacks(host: &Host) -> NativeCallbacks {
    NativeCallbacks {
        size: 32,
        version: 1,
        user_data: host as *const _ as *mut c_void,
        prepare: Some(prepare),
        release: Some(release),
    }
}
struct Fixture {
    root: PathBuf,
    context: *mut Runtime,
}
impl Fixture {
    fn new() -> Self {
        let mut random = [0; 8];
        getrandom::getrandom(&mut random).unwrap();
        let root = PathBuf::from("C:/ValMAXBuild/fixtures")
            .join(format!("pin-ffi-{:x}", u64::from_le_bytes(random)));
        let config=serde_json::to_vec(&json!({"profileDirectory":root.join("profile"),"runtimeDirectory":root.join("runtime"),"existingClient":true})).unwrap();
        let mut context = ptr::null_mut();
        let mut out = empty();
        unsafe {
            assert_eq!(
                valmax_runtime_open(config.as_ptr(), config.len(), &mut context, &mut out),
                0
            );
            assert_eq!(take(&mut out)["ok"], true);
        }
        Self { root, context }
    }
    fn call(&self, value: Value) -> Value {
        let bytes = serde_json::to_vec(&value).unwrap();
        let mut out = empty();
        unsafe {
            assert_eq!(
                valmax_runtime_dispatch(self.context, bytes.as_ptr(), bytes.len(), &mut out),
                0
            );
            take(&mut out)
        }
    }
}
fn empty() -> CoreBuffer {
    CoreBuffer {
        data: ptr::null_mut(),
        len: 0,
        capacity: 0,
    }
}
unsafe fn take(output: &mut CoreBuffer) -> Value {
    let value =
        serde_json::from_slice(unsafe { std::slice::from_raw_parts(output.data, output.len) })
            .unwrap();
    unsafe { valmax_core_buffer_free(output) };
    value
}
impl Drop for Fixture {
    fn drop(&mut self) {
        unsafe { valmax_runtime_close(&mut self.context) };
        if self.root.exists() {
            let base = PathBuf::from("C:/ValMAXBuild/fixtures")
                .canonicalize()
                .unwrap();
            let target = self.root.canonicalize().unwrap();
            assert!(
                target.starts_with(&base)
                    && target != base
                    && self
                        .root
                        .file_name()
                        .unwrap()
                        .to_str()
                        .unwrap()
                        .starts_with("pin-ffi-")
            );
            std::fs::remove_dir_all(target).unwrap();
        }
    }
}
fn set_request() -> Value {
    json!({"operation":"chat_lock.execute","action":"set","value":{"schema":1,"accountId":"42","chatId":"77","pin":"681439","confirmPin":"681439"}})
}

#[test]
fn host_registration_is_separate_from_ui_and_protection_precedes_pin_commit() {
    assert_eq!(std::mem::size_of::<NativeCallbacks>(), 32);
    let host = Host::default();
    let fixture = Fixture::new();
    assert_eq!(
        fixture.call(set_request())["error"],
        "notification_unavailable"
    );
    assert!(!fixture.root.join("profile/chat-locks.json").exists());
    unsafe {
        assert_eq!(valmax_runtime_notifications_refresh(fixture.context), 2);
        assert_eq!(
            valmax_runtime_notifications_attach(fixture.context, &callbacks(&host)),
            0
        );
    }
    assert_eq!(
        fixture.call(json!({"operation":"notifications.attach","pointer":1}))["error"],
        "unsupported_operation"
    );
    assert_eq!(fixture.call(set_request())["ok"], true);
    assert!(fixture.root.join("profile/chat-locks.json").exists());
    assert_eq!(host.prepares.load(Ordering::SeqCst), 1);
    assert_eq!(host.releases.load(Ordering::SeqCst), 1);
    assert!(!host.malformed.load(Ordering::SeqCst));
    let mut invalid = callbacks(&host);
    invalid.version = 2;
    unsafe {
        assert_eq!(
            valmax_runtime_notifications_attach(fixture.context, &invalid),
            1
        );
        assert_eq!(valmax_runtime_notifications_refresh(fixture.context), 0);
        assert_eq!(
            valmax_runtime_notifications_attach(fixture.context, ptr::null()),
            0
        );
        assert_eq!(valmax_runtime_notifications_refresh(fixture.context), 2);
    }
    assert_eq!(host.prepares.load(Ordering::SeqCst), 2);
    assert_eq!(host.releases.load(Ordering::SeqCst), 2);
}
#[test]
fn detach_waits_until_same_thread_lease_release_and_failed_host_never_commits() {
    let host = Arc::new(Host::default());
    let fixture = Fixture::new();
    host.hold.store(true, Ordering::SeqCst);
    unsafe {
        assert_eq!(
            valmax_runtime_notifications_attach(fixture.context, &callbacks(&host)),
            0
        );
    }
    let context = fixture.context as usize;
    let refresh = std::thread::spawn(move || unsafe {
        valmax_runtime_notifications_refresh(context as *const Runtime)
    });
    for _ in 0..100 {
        if host.entered.load(Ordering::SeqCst) {
            break;
        }
        std::thread::sleep(Duration::from_millis(5));
    }
    assert!(host.entered.load(Ordering::SeqCst));
    let (sent, received) = std::sync::mpsc::channel();
    let detach = std::thread::spawn(move || {
        let code =
            unsafe { valmax_runtime_notifications_attach(context as *const Runtime, ptr::null()) };
        sent.send(code).unwrap();
    });
    assert!(received.recv_timeout(Duration::from_millis(30)).is_err());
    {
        let _guard = host.gate.lock().unwrap();
        host.hold.store(false, Ordering::SeqCst);
        host.resume.notify_all();
    }
    assert_eq!(refresh.join().unwrap(), 0);
    assert_eq!(received.recv_timeout(Duration::from_secs(1)).unwrap(), 0);
    detach.join().unwrap();
    assert_eq!(host.releases.load(Ordering::SeqCst), 1);
    assert!(!host.malformed.load(Ordering::SeqCst));
    host.fail.store(true, Ordering::SeqCst);
    unsafe {
        assert_eq!(
            valmax_runtime_notifications_attach(fixture.context, &callbacks(&host)),
            0
        );
    }
    assert_eq!(
        fixture.call(set_request())["error"],
        "notification_unavailable"
    );
    assert!(!fixture.root.join("profile/chat-locks.json").exists());
    assert_eq!(host.prepares.load(Ordering::SeqCst), 2);
    assert_eq!(host.releases.load(Ordering::SeqCst), 2);
    host.fail.store(false, Ordering::SeqCst);
    host.malformed.store(true, Ordering::SeqCst);
    assert_eq!(
        fixture.call(set_request())["error"],
        "notification_unavailable"
    );
    assert!(!fixture.root.join("profile/chat-locks.json").exists());
    unsafe {
        assert_eq!(
            valmax_runtime_notifications_attach(fixture.context, ptr::null()),
            0
        );
    }
}
