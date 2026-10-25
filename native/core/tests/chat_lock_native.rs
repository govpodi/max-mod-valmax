//! Synthetic stores only. Test PINs are fixtures, never user data.
use serde_json::{Value, json};
use std::{
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
};
use valmax_core::{chat_lock::*, notification_policy::Policy};
use zeroize::Zeroizing;
static SERIAL: Mutex<()> = Mutex::new(());
struct TestClock(AtomicU64);
impl Clock for TestClock {
    fn unix_ms(&self) -> Result<u64, LockError> {
        Ok(self.0.load(Ordering::SeqCst))
    }
    fn monotonic_ms(&self) -> u64 {
        self.0.load(Ordering::SeqCst)
    }
}
struct FakeProtector;
impl Protector for FakeProtector {
    fn protect(&self, data: &[u8]) -> Result<Zeroizing<Vec<u8>>, LockError> {
        let mut out = b"OWN-TEST-PROTECTED:".to_vec();
        out.extend(data);
        Ok(Zeroizing::new(out))
    }
    fn unprotect(&self, data: &[u8]) -> Result<Zeroizing<Vec<u8>>, LockError> {
        data.strip_prefix(b"OWN-TEST-PROTECTED:")
            .map(|v| Zeroizing::new(v.to_vec()))
            .ok_or(LockError::ProtectorUnavailable)
    }
}
struct NotificationGuard<'a>(&'a Notify, Option<std::fs::File>);
impl ProtectionLease for NotificationGuard<'_> {}
impl Drop for NotificationGuard<'_> {
    fn drop(&mut self) {
        self.1.take();
        self.0.held.store(false, Ordering::SeqCst);
    }
}
struct Notify {
    allowed: AtomicBool,
    held: AtomicBool,
    block_rename: AtomicBool,
    catalog: Mutex<Option<Value>>,
    path: PathBuf,
}
impl NotificationProtector for Notify {
    fn prepare<'a>(&'a self, catalog: &Value) -> Result<Box<dyn ProtectionLease + 'a>, LockError> {
        if !self.allowed.load(Ordering::SeqCst) {
            return Err(LockError::NotificationUnavailable);
        }
        Policy::from_catalog(catalog).expect("only exact scope IDs reach protection");
        if self.path.exists() {
            let prior: Value = serde_json::from_slice(&std::fs::read(&self.path).unwrap()).unwrap();
            assert!(
                prior["locks"].as_object().unwrap().len()
                    < catalog["protected"].as_array().unwrap().len()
            );
        }
        *self.catalog.lock().unwrap() = Some(catalog.clone());
        self.held.store(true, Ordering::SeqCst);
        let block = if self.block_rename.load(Ordering::SeqCst) {
            Some(pinned_file(&self.path))
        } else {
            None
        };
        Ok(Box::new(NotificationGuard(self, block)))
    }
}
struct Fixture {
    directory: PathBuf,
    clock: Arc<TestClock>,
    notify: Arc<Notify>,
}
impl Fixture {
    fn new() -> Self {
        let mut random = [0u8; 12];
        getrandom::getrandom(&mut random).unwrap();
        let suffix: String = random.iter().map(|b| format!("{b:02x}")).collect();
        let directory =
            PathBuf::from("C:/ValMAXBuild/fixtures").join(format!("pin-native-{suffix}"));
        Self {
            clock: Arc::new(TestClock(AtomicU64::new(1_000_000))),
            notify: Arc::new(Notify {
                allowed: AtomicBool::new(true),
                held: AtomicBool::new(false),
                block_rename: AtomicBool::new(false),
                catalog: Mutex::new(None),
                path: directory.join("profile/chat-locks.json"),
            }),
            directory,
        }
    }
    fn service(&self) -> ChatLockService {
        ChatLockService::with_adapters(
            self.directory.join("profile"),
            Arc::new(FakeProtector),
            self.clock.clone(),
            self.notify.clone(),
        )
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if self.directory.exists() {
            let canonical = self.directory.canonicalize().unwrap();
            let base = PathBuf::from("C:/ValMAXBuild/fixtures")
                .canonicalize()
                .unwrap();
            assert!(
                canonical.starts_with(&base)
                    && canonical != base
                    && self
                        .directory
                        .file_name()
                        .unwrap()
                        .to_str()
                        .unwrap()
                        .starts_with("pin-native-")
            );
            std::fs::remove_dir_all(canonical).unwrap();
        }
    }
}
fn request(action: &str, owner: &str, chat: &str, pin: &str) -> Value {
    let mut value = json!({"schema":1,"accountId":owner,"chatId":chat});
    if matches!(action, "set" | "unlock" | "remove") {
        value["pin"] = json!(pin);
    }
    if action == "set" {
        value["confirmPin"] = json!(pin);
    }
    value
}
fn call(
    service: &ChatLockService,
    action: &str,
    owner: &str,
    chat: &str,
    pin: &str,
) -> Result<Value, LockError> {
    service.execute(action, &request(action, owner, chat, pin))
}
fn pinned_file(path: &std::path::Path) -> std::fs::File {
    use std::os::windows::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .read(true)
        .share_mode(3)
        .open(path)
        .unwrap()
}

#[test]
fn scope_grants_restart_and_durable_rate_limit() {
    let _serial = SERIAL.lock().unwrap();
    let fixture = Fixture::new();
    let service = fixture.service();
    assert_eq!(
        service.catalog().unwrap(),
        json!({"schema":1,"ready":true,"protected":[]})
    );
    assert!(!fixture.directory.exists());
    let result = call(
        &service,
        "set",
        "9007199254740993",
        "-9223372036854775808",
        "681439",
    )
    .unwrap();
    assert_eq!(result["protected"], true);
    assert_eq!(result["locked"], false);
    assert!(!fixture.notify.held.load(Ordering::SeqCst));
    let bytes = std::fs::read(&fixture.notify.path).unwrap();
    assert!(!String::from_utf8(bytes).unwrap().contains("681439"));
    assert_eq!(
        call(&service, "status", "9007199254740993", "0", "").unwrap()["protected"],
        false
    );
    assert_eq!(
        call(&service, "status", "42", "-9223372036854775808", "").unwrap()["protected"],
        false
    );
    fixture.clock.0.fetch_add(UNLOCK_MS, Ordering::SeqCst);
    assert_eq!(
        call(
            &service,
            "status",
            "9007199254740993",
            "-9223372036854775808",
            ""
        )
        .unwrap()["locked"],
        true
    );
    assert_eq!(
        call(
            &service,
            "unlock",
            "9007199254740993",
            "-9223372036854775808",
            "681439"
        )
        .unwrap()["locked"],
        false
    );
    let restarted = fixture.service();
    assert_eq!(
        call(
            &restarted,
            "status",
            "9007199254740993",
            "-9223372036854775808",
            ""
        )
        .unwrap()["locked"],
        true
    );
    for _ in 0..5 {
        assert_eq!(
            call(
                &restarted,
                "unlock",
                "9007199254740993",
                "-9223372036854775808",
                "000000"
            ),
            Err(LockError::WrongPin)
        );
    }
    let restarted = fixture.service();
    assert_eq!(
        call(
            &restarted,
            "unlock",
            "9007199254740993",
            "-9223372036854775808",
            "681439"
        ),
        Err(LockError::RateLimited)
    );
    fixture.clock.0.fetch_add(30_000, Ordering::SeqCst);
    assert_eq!(
        call(
            &restarted,
            "unlock",
            "9007199254740993",
            "-9223372036854775808",
            "681439"
        )
        .unwrap()["locked"],
        false
    );
    assert_eq!(
        call(
            &restarted,
            "remove",
            "9007199254740993",
            "-9223372036854775808",
            "681439"
        )
        .unwrap()["protected"],
        false
    );
}
#[test]
fn protection_ack_precedes_commit_and_failure_preserves_store() {
    let _serial = SERIAL.lock().unwrap();
    let fixture = Fixture::new();
    let service = fixture.service();
    fixture.notify.allowed.store(false, Ordering::SeqCst);
    assert_eq!(
        call(&service, "set", "42", "77", "681439"),
        Err(LockError::NotificationUnavailable)
    );
    assert!(!fixture.notify.path.exists());
    fixture.notify.allowed.store(true, Ordering::SeqCst);
    call(&service, "set", "42", "77", "681439").unwrap();
    let before = std::fs::read(&fixture.notify.path).unwrap();
    fixture.notify.allowed.store(false, Ordering::SeqCst);
    assert_eq!(
        call(&service, "set", "43", "77", "123456"),
        Err(LockError::NotificationUnavailable)
    );
    assert_eq!(before, std::fs::read(&fixture.notify.path).unwrap());
    assert_eq!(
        call(&service, "set", "42", "77", "123456"),
        Err(LockError::AlreadyProtected)
    );
    assert_eq!(
        call(&service, "status", "42", "0", "").unwrap()["protected"],
        false
    );
    fixture.notify.allowed.store(true, Ordering::SeqCst);
    call(&service, "set", "42", "0", "123456").unwrap();
    let catalog = service.execute("lock-all", &json!({"schema":1})).unwrap();
    assert!(
        catalog["protected"]
            .as_array()
            .unwrap()
            .iter()
            .all(|v| v["locked"] == true)
    );
    assert_eq!(
        call(&service, "unlock", "42", "0", "123456").unwrap()["locked"],
        false
    );
    assert_eq!(
        call(&service, "status", "42", "77", "").unwrap()["locked"],
        true
    );
}
#[test]
fn malformed_inputs_do_not_write_and_corrupt_store_is_preserved() {
    let _serial = SERIAL.lock().unwrap();
    let fixture = Fixture::new();
    let service = fixture.service();
    for (owner, chat) in [
        ("0", "1"),
        ("042", "1"),
        ("9223372036854775808", "1"),
        ("42", "-0"),
        ("42", "01"),
        ("42", "-9223372036854775809"),
    ] {
        assert_eq!(
            call(&service, "status", owner, chat, ""),
            Err(LockError::InvalidRequest)
        );
    }
    for value in [
        json!(true),
        json!(123456),
        json!("１２３４５６"),
        json!("12345"),
        json!("1234567890123"),
    ] {
        let mut input = request("set", "42", "1", "681439");
        input["pin"] = value.clone();
        input["confirmPin"] = value;
        assert_eq!(service.execute("set", &input), Err(LockError::InvalidPin));
    }
    let mut input = request("set", "42", "1", "681439");
    input["schema"] = json!(true);
    assert_eq!(
        service.execute("set", &input),
        Err(LockError::InvalidRequest)
    );
    assert!(!fixture.directory.exists());
    std::fs::create_dir_all(fixture.notify.path.parent().unwrap()).unwrap();
    for malformed in [br#"{"schema":1,"schema":1,"locks":{}}"#.as_slice(),br#"{"schema":1,"locks":{"42:1":{"salt":"ffffffffffffffffffffffffffffffff","protected":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=","failures":true,"blockedUntil":0}}}"#] {
        std::fs::write(&fixture.notify.path,malformed).unwrap();assert_eq!(service.catalog(),Err(LockError::InvalidStore));assert_eq!(malformed,std::fs::read(&fixture.notify.path).unwrap());}
}
#[test]
fn owned_dpapi_roundtrip_and_corruption() {
    let protector = WindowsProtector;
    let data = [0x39u8; 32];
    let cipher = protector.protect(&data).unwrap();
    assert_ne!(&*cipher, &data);
    assert_eq!(&*protector.unprotect(&cipher).unwrap(), &data);
    assert!(matches!(
        protector.unprotect(b"owned synthetic corrupt verifier"),
        Err(LockError::ProtectorUnavailable)
    ));
}
#[test]
fn replaced_verifier_does_not_inherit_old_grant() {
    let _serial = SERIAL.lock().unwrap();
    let fixture = Fixture::new();
    let service = fixture.service();
    call(&service, "set", "42", "77", "681439").unwrap();
    let other = fixture.service();
    call(&other, "remove", "42", "77", "681439").unwrap();
    call(&other, "set", "42", "77", "123456").unwrap();
    assert_eq!(
        call(&service, "status", "42", "77", "").unwrap()["locked"],
        true
    );
}
#[test]
fn failed_commit_after_ack_or_unlock_never_grants_access() {
    let _serial = SERIAL.lock().unwrap();
    let fixture = Fixture::new();
    let service = fixture.service();
    call(&service, "set", "42", "77", "681439").unwrap();
    let before = std::fs::read(&fixture.notify.path).unwrap();
    fixture.notify.block_rename.store(true, Ordering::SeqCst);
    assert!(matches!(
        call(&service, "set", "42", "88", "123456"),
        Err(LockError::StoreBusy | LockError::IoError)
    ));
    assert!(!fixture.notify.held.load(Ordering::SeqCst));
    assert_eq!(before, std::fs::read(&fixture.notify.path).unwrap());
    assert_eq!(
        call(&service, "status", "42", "88", "").unwrap()["protected"],
        false
    );
    call(&service, "lock", "42", "77", "").unwrap();
    let held = pinned_file(&fixture.notify.path);
    assert!(matches!(
        call(&service, "unlock", "42", "77", "681439"),
        Err(LockError::StoreBusy | LockError::IoError)
    ));
    assert_eq!(
        call(&service, "status", "42", "77", "").unwrap()["locked"],
        true
    );
    assert_eq!(before, std::fs::read(&fixture.notify.path).unwrap());
    drop(held);
    assert_eq!(
        std::fs::read_dir(fixture.notify.path.parent().unwrap())
            .unwrap()
            .count(),
        1
    );
    assert_eq!(
        call(&service, "unlock", "42", "77", "681439").unwrap()["locked"],
        false
    );
}
