//! Compatible per-chat UI PINs. No messenger DB, network or credential enumeration.
//! The host must confirm notification protection before a new verifier is committed.
use crate::settings_io::{GuardedStore, SafeStorePath, StoreError, sha256};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use sha2::Sha256;
use std::{
    collections::BTreeMap,
    path::Path,
    sync::{
        Arc, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
    time::{Instant, SystemTime, UNIX_EPOCH},
};
use subtle::ConstantTimeEq;
use zeroize::{Zeroize, Zeroizing};

pub const MAX_BYTES: usize = 512 * 1024;
pub const MAX_LOCKS: usize = 256;
pub const ITERATIONS: u32 = 210_000;
pub const UNLOCK_MS: u64 = 120_000;
type Result<T> = std::result::Result<T, LockError>;
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LockError {
    InvalidRequest,
    InvalidPin,
    WrongPin,
    RateLimited,
    NotProtected,
    AlreadyProtected,
    LimitReached,
    UnsafePath,
    StoreBusy,
    IoError,
    InvalidStore,
    ProtectorUnavailable,
    NotificationUnavailable,
}
impl LockError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidPin => "invalid_pin",
            Self::WrongPin => "wrong_pin",
            Self::RateLimited => "rate_limited",
            Self::NotProtected => "not_protected",
            Self::AlreadyProtected => "already_protected",
            Self::LimitReached => "limit_reached",
            Self::UnsafePath => "unsafe_path",
            Self::StoreBusy => "store_busy",
            Self::IoError => "io_error",
            Self::InvalidStore => "invalid_store",
            Self::ProtectorUnavailable => "protector_unavailable",
            Self::NotificationUnavailable => "notification_unavailable",
        }
    }
}
impl std::fmt::Display for LockError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for LockError {}
impl From<StoreError> for LockError {
    fn from(error: StoreError) -> Self {
        match error {
            StoreError::UnsafePath => Self::UnsafePath,
            StoreError::StoreBusy => Self::StoreBusy,
            StoreError::InvalidStore => Self::InvalidStore,
            _ => Self::IoError,
        }
    }
}
pub(crate) fn exact(value: &Value, keys: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|m| m.len() == keys.len() && keys.iter().all(|k| m.contains_key(*k)))
}
pub fn scope(account: &str, chat: &str) -> Result<String> {
    fn canonical(s: &str, zero: bool, signed: bool) -> bool {
        if zero && s == "0" {
            return true;
        }
        let digits = if signed {
            s.strip_prefix('-').unwrap_or(s)
        } else {
            s
        };
        !digits.is_empty()
            && digits.len() <= 19
            && digits.as_bytes()[0] != b'0'
            && digits.bytes().all(|b| b.is_ascii_digit())
            && s.parse::<i64>().is_ok_and(|n| signed || n > 0)
    }
    if !canonical(account, false, false) || !canonical(chat, true, true) {
        return Err(LockError::InvalidRequest);
    }
    Ok(format!("{account}:{chat}"))
}
fn pin(value: &Value) -> Result<&str> {
    value
        .as_str()
        .filter(|s| (6..=12).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit()))
        .ok_or(LockError::InvalidPin)
}
#[derive(Clone)]
struct Record {
    salt: String,
    protected: String,
    failures: u32,
    blocked_until: u64,
}
impl Record {
    fn value(&self) -> Value {
        json!({"salt":self.salt,"protected":self.protected,"failures":self.failures,"blockedUntil":self.blocked_until})
    }
    fn identity(&self) -> String {
        sha256(format!("{}:{}", self.salt, self.protected).as_bytes())
    }
}
type State = BTreeMap<String, Record>;
fn canonical(state: &State) -> Vec<u8> {
    let records: serde_json::Map<String, Value> = state
        .iter()
        .map(|(key, record)| (key.clone(), record.value()))
        .collect();
    serde_json::to_vec(&json!({"schema":1,"locks":records})).expect("bounded string schema")
}
fn parse(raw: &[u8]) -> Result<State> {
    if raw.len() > MAX_BYTES {
        return Err(LockError::InvalidStore);
    }
    let value = crate::session_archive::parse_json(raw, "invalid_payload")
        .map_err(|_| LockError::InvalidStore)?;
    if !exact(&value, &["schema", "locks"]) || value["schema"].as_u64() != Some(1) {
        return Err(LockError::InvalidStore);
    }
    let records = value["locks"]
        .as_object()
        .filter(|m| m.len() <= MAX_LOCKS)
        .ok_or(LockError::InvalidStore)?;
    let mut state = State::new();
    for (key, item) in records {
        let (owner, chat) = key.split_once(':').ok_or(LockError::InvalidStore)?;
        if scope(owner, chat).as_deref() != Ok(key)
            || !exact(item, &["salt", "protected", "failures", "blockedUntil"])
        {
            return Err(LockError::InvalidStore);
        }
        let salt = item["salt"]
            .as_str()
            .filter(|s| {
                s.len() == 32
                    && s.bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
            .ok_or(LockError::InvalidStore)?;
        let protected = item["protected"]
            .as_str()
            .filter(|s| !s.is_empty() && s.len() <= 16384)
            .ok_or(LockError::InvalidStore)?;
        let decoded = Zeroizing::new(
            STANDARD
                .decode(protected)
                .map_err(|_| LockError::InvalidStore)?,
        );
        if !(16..=12288).contains(&decoded.len()) {
            return Err(LockError::InvalidStore);
        }
        let failures = item["failures"]
            .as_u64()
            .filter(|n| *n <= 1_000_000)
            .ok_or(LockError::InvalidStore)? as u32;
        let blocked_until = item["blockedUntil"]
            .as_u64()
            .filter(|n| *n < (1u64 << 53))
            .ok_or(LockError::InvalidStore)?;
        state.insert(
            key.clone(),
            Record {
                salt: salt.to_owned(),
                protected: protected.to_owned(),
                failures,
                blocked_until,
            },
        );
    }
    Ok(state)
}
fn read(guard: Option<&GuardedStore>) -> Result<State> {
    match guard
        .map(|g| g.read_bounded(MAX_BYTES))
        .transpose()?
        .flatten()
    {
        None => Ok(State::new()),
        Some(raw) => parse(&Zeroizing::new(raw)),
    }
}
fn persist(guard: &GuardedStore, before: &State, after: &State) -> Result<()> {
    let raw = Zeroizing::new(canonical(after));
    if raw.len() > MAX_BYTES {
        return Err(LockError::LimitReached);
    }
    let expected = sha256(&canonical(before));
    guard.atomic_replace(&raw, || {
        let current = read(Some(guard)).map_err(|e| match e {
            LockError::UnsafePath => StoreError::UnsafePath,
            LockError::StoreBusy => StoreError::StoreBusy,
            _ => StoreError::InvalidStore,
        })?;
        if sha256(&canonical(&current)) == expected {
            Ok(())
        } else {
            Err(StoreError::RevisionConflict)
        }
    })?;
    Ok(())
}

/// Implementations must only protect this feature's verifier, never access WinCred.
pub trait Protector: Send + Sync {
    fn protect(&self, data: &[u8]) -> Result<Zeroizing<Vec<u8>>>;
    fn unprotect(&self, data: &[u8]) -> Result<Zeroizing<Vec<u8>>>;
}
pub struct WindowsProtector;
impl Protector for WindowsProtector {
    fn protect(&self, data: &[u8]) -> Result<Zeroizing<Vec<u8>>> {
        dpapi(data, true)
    }
    fn unprotect(&self, data: &[u8]) -> Result<Zeroizing<Vec<u8>>> {
        dpapi(data, false)
    }
}
#[cfg(not(windows))]
fn dpapi(_: &[u8], _: bool) -> Result<Zeroizing<Vec<u8>>> {
    Err(LockError::ProtectorUnavailable)
}
#[cfg(windows)]
fn dpapi(data: &[u8], protect: bool) -> Result<Zeroizing<Vec<u8>>> {
    use std::{ffi::c_void, ptr};
    #[repr(C)]
    struct Blob {
        length: u32,
        data: *mut u8,
    }
    #[link(name = "crypt32")]
    unsafe extern "system" {
        fn CryptProtectData(
            input: *const Blob,
            description: *const u16,
            entropy: *const Blob,
            reserved: *const c_void,
            prompt: *const c_void,
            flags: u32,
            output: *mut Blob,
        ) -> i32;
        fn CryptUnprotectData(
            input: *const Blob,
            description: *mut *mut u16,
            entropy: *const Blob,
            reserved: *const c_void,
            prompt: *const c_void,
            flags: u32,
            output: *mut Blob,
        ) -> i32;
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn LocalFree(pointer: *mut c_void) -> *mut c_void;
    }
    if data.is_empty() || data.len() > 12288 {
        return Err(LockError::ProtectorUnavailable);
    }
    let mut source = Zeroizing::new(data.to_vec());
    let input = Blob {
        length: source.len() as u32,
        data: source.as_mut_ptr(),
    };
    let entropy = b"ValMAX.ChatOpeningPIN.v1";
    let entropy = Blob {
        length: entropy.len() as u32,
        data: entropy.as_ptr().cast_mut(),
    };
    let mut output = Blob {
        length: 0,
        data: ptr::null_mut(),
    };
    let result = unsafe {
        if protect {
            CryptProtectData(
                &input,
                ptr::null(),
                &entropy,
                ptr::null(),
                ptr::null(),
                1,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                ptr::null_mut(),
                &entropy,
                ptr::null(),
                ptr::null(),
                1,
                &mut output,
            )
        }
    };
    struct Output(Blob);
    impl Drop for Output {
        fn drop(&mut self) {
            if !self.0.data.is_null() {
                unsafe {
                    if self.0.length > 0 {
                        std::slice::from_raw_parts_mut(self.0.data, self.0.length as usize)
                            .zeroize();
                    }
                    LocalFree(self.0.data.cast());
                }
            }
        }
    }
    let output = Output(output);
    if result == 0 || output.0.data.is_null() || !(1..=12288).contains(&output.0.length) {
        return Err(LockError::ProtectorUnavailable);
    }
    Ok(Zeroizing::new(
        unsafe { std::slice::from_raw_parts(output.0.data, output.0.length as usize) }.to_vec(),
    ))
}

pub trait Clock: Send + Sync {
    fn unix_ms(&self) -> Result<u64>;
    fn monotonic_ms(&self) -> u64;
}
pub struct SystemClock {
    start: Instant,
}
impl Default for SystemClock {
    fn default() -> Self {
        Self {
            start: Instant::now(),
        }
    }
}
impl Clock for SystemClock {
    fn unix_ms(&self) -> Result<u64> {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .ok()
            .map(|d| d.as_millis())
            .filter(|n| *n < (1u128 << 53) - 300_000)
            .map(|n| n as u64)
            .ok_or(LockError::IoError)
    }
    fn monotonic_ms(&self) -> u64 {
        self.start.elapsed().as_millis().min(u64::MAX as u128) as u64
    }
}
/// Lease holds the native policy refresh lock through durable commit and grant.
/// Drop must release it. It must not revert a successful staged policy to an old one.
pub trait ProtectionLease {}
pub trait NotificationProtector: Send + Sync {
    fn prepare<'a>(&'a self, catalog: &Value) -> Result<Box<dyn ProtectionLease + 'a>>;
}
pub struct UnavailableNotifications;
impl NotificationProtector for UnavailableNotifications {
    fn prepare<'a>(&'a self, _: &Value) -> Result<Box<dyn ProtectionLease + 'a>> {
        Err(LockError::NotificationUnavailable)
    }
}
static KDF_ACTIVE: AtomicUsize = AtomicUsize::new(0);
fn derive(value: &str, salt: &str) -> Result<Zeroizing<[u8; 32]>> {
    if KDF_ACTIVE
        .try_update(Ordering::AcqRel, Ordering::Acquire, |n| {
            (n < 2).then_some(n + 1)
        })
        .is_err()
    {
        return Err(LockError::StoreBusy);
    }
    struct Slot;
    impl Drop for Slot {
        fn drop(&mut self) {
            KDF_ACTIVE.fetch_sub(1, Ordering::Release);
        }
    }
    let _slot = Slot;
    let mut decoded = [0u8; 16];
    for (i, out) in decoded.iter_mut().enumerate() {
        *out =
            u8::from_str_radix(&salt[i * 2..i * 2 + 2], 16).map_err(|_| LockError::InvalidStore)?;
    }
    let mut result = Zeroizing::new([0u8; 32]);
    pbkdf2::pbkdf2_hmac::<Sha256>(value.as_bytes(), &decoded, ITERATIONS, &mut *result);
    Ok(result)
}
#[derive(Default)]
struct Grants {
    items: BTreeMap<String, (u64, String)>,
}
impl Grants {
    fn prune(&mut self, state: &State, now: u64) {
        self.items
            .retain(|key, (until, _)| now < *until && state.contains_key(key));
    }
    fn locked(&mut self, key: &str, record: &Record, now: u64) -> bool {
        if self
            .items
            .get(key)
            .is_some_and(|(until, id)| now < *until && *id == record.identity())
        {
            false
        } else {
            self.items.remove(key);
            true
        }
    }
}
pub struct ChatLockService {
    path: SafeStorePath,
    protector: Arc<dyn Protector>,
    clock: Arc<dyn Clock>,
    notifications: Arc<dyn NotificationProtector>,
    grants: Mutex<Grants>,
}
impl ChatLockService {
    pub fn new(directory: impl AsRef<Path>) -> Result<Self> {
        Self::with_adapters(
            directory,
            Arc::new(WindowsProtector),
            Arc::new(SystemClock::default()),
            Arc::new(UnavailableNotifications),
        )
    }
    /// Explicit trusted host adapters; unavailable notifications deny creation.
    pub fn with_adapters(
        directory: impl AsRef<Path>,
        protector: Arc<dyn Protector>,
        clock: Arc<dyn Clock>,
        notifications: Arc<dyn NotificationProtector>,
    ) -> Result<Self> {
        Ok(Self {
            path: SafeStorePath::new(directory.as_ref().join("chat-locks.json"))?,
            protector,
            clock,
            notifications,
            grants: Mutex::new(Grants::default()),
        })
    }
    fn catalog_value(&self, state: &State, grants: &mut Grants) -> Value {
        let now = self.clock.monotonic_ms();
        grants.prune(state, now);
        let entries: Vec<_> = state
            .iter()
            .map(|(key, record)| {
                let (owner, chat) = key.split_once(':').expect("validated scope");
                json!({"accountId":owner,"chatId":chat,"locked":grants.locked(key,record,now)})
            })
            .collect();
        json!({"schema":1,"ready":true,"protected":entries})
    }
    pub fn catalog(&self) -> Result<Value> {
        let mut grants = self.grants.lock().map_err(|_| LockError::StoreBusy)?;
        let guard = self.path.begin(false)?;
        let _writer = guard.as_ref().map(GuardedStore::writer_lock).transpose()?;
        Ok(self.catalog_value(&read(guard.as_ref())?, &mut grants))
    }
    /// Host-only refresh uses the same Rust -> native lock order as PIN set.
    /// The host must not pre-lock its transaction mutex or publish a stale
    /// catalogue independently after reading it from this service.
    pub fn refresh_notifications(&self) -> Result<()> {
        let mut grants = self.grants.lock().map_err(|_| LockError::StoreBusy)?;
        let guard = self.path.begin(false)?;
        let _writer = guard.as_ref().map(GuardedStore::writer_lock).transpose()?;
        let catalog = self.catalog_value(&read(guard.as_ref())?, &mut grants);
        let _lease = self.notifications.prepare(&catalog)?;
        Ok(())
    }
    pub fn execute(&self, action: &str, value: &Value) -> Result<Value> {
        let keys: &[&str] = match action {
            "status" | "lock" => &["schema", "accountId", "chatId"],
            "set" => &["schema", "accountId", "chatId", "pin", "confirmPin"],
            "unlock" | "remove" => &["schema", "accountId", "chatId", "pin"],
            "lock-all" => &["schema"],
            _ => return Err(LockError::InvalidRequest),
        };
        if !exact(value, keys) || value["schema"].as_u64() != Some(1) {
            return Err(LockError::InvalidRequest);
        }
        let key = if action == "lock-all" {
            String::new()
        } else {
            scope(
                value["accountId"]
                    .as_str()
                    .ok_or(LockError::InvalidRequest)?,
                value["chatId"].as_str().ok_or(LockError::InvalidRequest)?,
            )?
        };
        let supplied = if matches!(action, "set" | "unlock" | "remove") {
            Some(pin(&value["pin"])?)
        } else {
            None
        };
        if action == "set"
            && !bool::from(
                supplied
                    .unwrap()
                    .as_bytes()
                    .ct_eq(pin(&value["confirmPin"])?.as_bytes()),
            )
        {
            return Err(LockError::InvalidPin);
        }
        let mut grants = self.grants.lock().map_err(|_| LockError::StoreBusy)?;
        let guard = self.path.begin(action == "set")?;
        let _writer = guard.as_ref().map(GuardedStore::writer_lock).transpose()?;
        let before = read(guard.as_ref())?;
        // Other processes may remove records while this context holds a grant.
        // Every operation keeps the in-memory table bounded by the live store.
        grants.prune(&before, self.clock.monotonic_ms());
        let mut state = before.clone();
        if action == "lock-all" {
            grants.items.clear();
            return Ok(self.catalog_value(&state, &mut grants));
        }
        match action {
            "set" => {
                if state.contains_key(&key) {
                    return Err(LockError::AlreadyProtected);
                }
                if state.len() >= MAX_LOCKS {
                    return Err(LockError::LimitReached);
                }
                let mut random = [0; 16];
                getrandom::getrandom(&mut random).map_err(|_| LockError::IoError)?;
                let salt: String = random.iter().map(|b| format!("{b:02x}")).collect();
                let digest = derive(supplied.unwrap(), &salt)?;
                let protected = self.protector.protect(&*digest)?;
                if !(16..=12288).contains(&protected.len()) {
                    return Err(LockError::ProtectorUnavailable);
                }
                state.insert(
                    key.clone(),
                    Record {
                        salt,
                        protected: STANDARD.encode(&*protected),
                        failures: 0,
                        blocked_until: 0,
                    },
                );
                let staged = self.catalog_value(&state, &mut Grants::default());
                let _lease = self
                    .notifications
                    .prepare(&staged)
                    .map_err(|_| LockError::NotificationUnavailable)?;
                persist(guard.as_ref().ok_or(LockError::IoError)?, &before, &state)?;
                grants.items.insert(
                    key.clone(),
                    (
                        self.clock.monotonic_ms().saturating_add(UNLOCK_MS),
                        state[&key].identity(),
                    ),
                );
            }
            "unlock" | "remove" => {
                let record = state.get_mut(&key).ok_or(LockError::NotProtected)?;
                let now = self.clock.unix_ms()?;
                if now >= (1u64 << 53) - 300_000 {
                    return Err(LockError::IoError);
                }
                if record.blocked_until > now {
                    return Err(LockError::RateLimited);
                }
                let encrypted = Zeroizing::new(
                    STANDARD
                        .decode(&record.protected)
                        .map_err(|_| LockError::InvalidStore)?,
                );
                let expected = self.protector.unprotect(&encrypted)?;
                if expected.len() != 32 {
                    return Err(LockError::InvalidStore);
                }
                let actual = derive(supplied.unwrap(), &record.salt)?;
                if !bool::from(actual.as_slice().ct_eq(&expected)) {
                    grants.items.remove(&key);
                    record.failures = (record.failures + 1).min(1_000_000);
                    let delay = if record.failures < 5 {
                        0
                    } else {
                        (30_000u64 << ((record.failures - 5).min(4))).min(300_000)
                    };
                    record.blocked_until = if delay == 0 { 0 } else { now + delay };
                    persist(guard.as_ref().ok_or(LockError::IoError)?, &before, &state)?;
                    return Err(LockError::WrongPin);
                }
                record.failures = 0;
                record.blocked_until = 0;
                if action == "remove" {
                    state.remove(&key);
                }
                persist(guard.as_ref().ok_or(LockError::IoError)?, &before, &state)?;
                if action == "remove" {
                    grants.items.remove(&key);
                } else {
                    grants.items.insert(
                        key.clone(),
                        (
                            self.clock.monotonic_ms().saturating_add(UNLOCK_MS),
                            state[&key].identity(),
                        ),
                    );
                }
            }
            "lock" => {
                grants.items.remove(&key);
            }
            _ => {}
        }
        let exists = state.contains_key(&key);
        let locked = state
            .get(&key)
            .is_some_and(|record| grants.locked(&key, record, self.clock.monotonic_ms()));
        Ok(
            json!({"schema":1,"ready":true,"accountId":value["accountId"],"chatId":value["chatId"],"protected":exists,"locked":locked}),
        )
    }
}
