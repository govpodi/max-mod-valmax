//! Legacy bool preference with an explicit native-host enable gate. A saved
//! intent alone never claims capture or durable message availability.
use crate::settings_io::{GuardedStore, SafeStorePath, StoreError, sha256};
use serde_json::{Value, json};
use std::path::Path;

pub const FILENAME: &str = "message-retention-settings.json";
pub const MAX_STORE_BYTES: usize = 4096;
type Result<T> = std::result::Result<T, &'static str>;

pub fn canonical(enabled: bool) -> &'static [u8] {
    if enabled { b"{\"enabled\":true,\"schema\":1}\n" }
    else { b"{\"enabled\":false,\"schema\":1}\n" }
}

pub fn revision(enabled: bool) -> String { sha256(canonical(enabled)) }

pub fn parse(data: &[u8]) -> Result<bool> {
    if data.len() > MAX_STORE_BYTES { return Err("invalid_store"); }
    let value = crate::session_archive::parse_json(data, "invalid_payload")
        .map_err(|_| "invalid_store")?;
    let map = value.as_object().ok_or("invalid_store")?;
    if map.len() != 2 || map.get("schema").and_then(Value::as_u64) != Some(1) {
        return Err("invalid_store");
    }
    map.get("enabled").and_then(Value::as_bool).ok_or("invalid_store")
}

fn read(guard: &GuardedStore) -> Result<bool> {
    guard.read_bounded(MAX_STORE_BYTES).map_err(StoreError::code)?
        .map_or(Ok(false), |bytes| parse(&bytes))
}

fn status(enabled: bool) -> Value {
    json!({
        "schema": 2, "revision": revision(enabled), "enabled": enabled,
        "capabilities": {"capture": false, "lookup": false, "enable": false, "disable": true},
        "runtime": {"ready": false, "applied": false, "enabled": false,
            "historySyncVerified": false, "error": "retention_not_integrated",
            "retainedEvents": null, "retainedMessages": null}
    })
}

pub struct RetentionPreferencesStore { path: SafeStorePath }
impl RetentionPreferencesStore {
    pub fn new(path: impl AsRef<Path>) -> Result<Self> {
        Ok(Self { path: SafeStorePath::new(path).map_err(StoreError::code)? })
    }
    fn desired(&self) -> Result<bool> {
        self.path.begin(false).map_err(StoreError::code)?
            .map_or(Ok(false), |guard| read(&guard))
    }
    pub fn snapshot(&self) -> Result<Value> { self.desired().map(status) }
    pub fn replace(&self, expected_revision: &str, value: &Value) -> Result<Value> {
        self.replace_with_host(expected_revision, value, false)
    }
    pub fn replace_with_host(&self, expected_revision: &str, value: &Value,
                             allow_enable: bool) -> Result<Value> {
        if expected_revision.len() != 64 || !expected_revision.bytes().all(|byte|
            byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
            return Err("invalid_revision");
        }
        let enabled = value.as_bool().ok_or("invalid_settings")?;
        // The runtime may pass true only from a fresh, typed native host ACK.
        // The saved bool is still intent; runtime status separately proves
        // that the host applied it. An unavailable host touches no store.
        if enabled && !allow_enable { return Err("retention_unavailable"); }
        if revision(self.desired()?) != expected_revision { return Err("revision_conflict"); }
        let guard = self.path.begin(true).map_err(StoreError::code)?.ok_or("io_error")?;
        let _lock = guard.writer_lock().map_err(StoreError::code)?;
        let validate = || -> crate::settings_io::Result<()> {
            let current = guard.read_bounded(MAX_STORE_BYTES)?
                .map_or(Ok(false), |bytes| parse(&bytes).map_err(|_| StoreError::InvalidStore))?;
            if revision(current) == expected_revision { Ok(()) } else { Err(StoreError::RevisionConflict) }
        };
        validate().map_err(StoreError::code)?;
        guard.atomic_replace(canonical(enabled), validate).map_err(StoreError::code)?;
        Ok(status(enabled))
    }
}

#[cfg(all(test, windows))]
#[path = "retention_preferences_tests.rs"]
mod tests;
