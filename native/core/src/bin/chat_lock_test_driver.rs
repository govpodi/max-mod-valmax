//! Synthetic compatibility fixture runner, gated out of product builds.
use serde_json::{Value, json};
use std::{
    io::{self, Read, Write},
    sync::{
        Arc,
        atomic::{AtomicU64, Ordering},
    },
};
use valmax_core::{chat_lock::*, notification_policy::Policy};
use zeroize::{Zeroize, Zeroizing};
struct FixtureClock(AtomicU64);
impl Clock for FixtureClock {
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
        out.extend_from_slice(data);
        Ok(Zeroizing::new(out))
    }
    fn unprotect(&self, data: &[u8]) -> Result<Zeroizing<Vec<u8>>, LockError> {
        data.strip_prefix(b"OWN-TEST-PROTECTED:")
            .map(|v| Zeroizing::new(v.to_vec()))
            .ok_or(LockError::ProtectorUnavailable)
    }
}
struct FixtureLease;
impl ProtectionLease for FixtureLease {}
struct FixtureNotifications;
impl NotificationProtector for FixtureNotifications {
    fn prepare<'a>(&'a self, catalog: &Value) -> Result<Box<dyn ProtectionLease + 'a>, LockError> {
        Policy::from_catalog(catalog).map_err(|_| LockError::NotificationUnavailable)?;
        Ok(Box::new(FixtureLease))
    }
}
fn run(value: &Value) -> Result<Value, LockError> {
    let directory = value["directory"]
        .as_str()
        .ok_or(LockError::InvalidRequest)?;
    let now = value["nowMs"].as_u64().ok_or(LockError::InvalidRequest)?;
    let dpapi = value["dpapi"].as_bool().ok_or(LockError::InvalidRequest)?;
    let clock = Arc::new(FixtureClock(AtomicU64::new(now)));
    let protector: Arc<dyn Protector> = if dpapi {
        Arc::new(WindowsProtector)
    } else {
        Arc::new(FakeProtector)
    };
    let service = ChatLockService::with_adapters(
        directory,
        protector,
        clock.clone(),
        Arc::new(FixtureNotifications),
    )?;
    let mut responses = Vec::new();
    for request in value["requests"]
        .as_array()
        .filter(|a| a.len() <= 100)
        .ok_or(LockError::InvalidRequest)?
    {
        if let Some(time) = request.get("nowMs").and_then(Value::as_u64) {
            clock.0.store(time, Ordering::SeqCst);
        }
        let action = request["action"]
            .as_str()
            .ok_or(LockError::InvalidRequest)?;
        let result = if action == "catalog" {
            service.catalog()
        } else if action == "policy" {
            service.catalog().and_then(|v| {
                Policy::from_catalog(&v)
                    .map(|v| v.wire())
                    .map_err(|_| LockError::NotificationUnavailable)
            })
        } else {
            service.execute(action, &request["value"])
        };
        responses.push(match result {
            Ok(data) => json!({"ok":true,"value":data}),
            Err(error) => json!({"ok":false,"error":error.code()}),
        });
    }
    Ok(json!({"responses":responses}))
}
fn wipe(value: &mut Value) {
    match value {
        Value::String(text) => text.zeroize(),
        Value::Array(items) => items.iter_mut().for_each(wipe),
        Value::Object(items) => items.values_mut().for_each(wipe),
        _ => {}
    }
}
fn main() {
    let mut input = Zeroizing::new(Vec::new());
    if io::stdin().take(1_048_577).read_to_end(&mut input).is_err() || input.len() > 1_048_576 {
        std::process::exit(2);
    }
    let mut value = match valmax_core::session_archive::parse_json(&input, "invalid_payload") {
        Ok(v) => v,
        Err(_) => std::process::exit(2),
    };
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| run(&value)));
    wipe(&mut value);
    let output = match result {
        Ok(Ok(data)) => data,
        Ok(Err(error)) => json!({"error":error.code()}),
        Err(_) => json!({"error":"io_error"}),
    };
    if io::stdout()
        .write_all(&serde_json::to_vec(&output).unwrap())
        .is_err()
    {
        std::process::exit(3);
    }
}
