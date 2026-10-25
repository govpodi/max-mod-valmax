//! PIN notification scopes and acknowledgements. No names, previews or PIN data.
//! Unlocking a UI chat never changes whether its notification must be generic.
use crate::{
    chat_lock::{MAX_LOCKS, exact, scope},
    settings_io::sha256,
};
use serde_json::{Value, json};
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Mutex,
    time::{Duration, Instant},
};
pub const LEASE_MS: u64 = 5_000;
const MAX_CLIENTS: usize = 64;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PolicyError;
impl std::fmt::Display for PolicyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("notification_unavailable")
    }
}
impl std::error::Error for PolicyError {}
type Result<T> = std::result::Result<T, PolicyError>;
#[derive(Clone)]
pub struct Policy {
    ready: bool,
    protected: Vec<(String, String)>,
    revision: String,
}
impl Policy {
    fn new(ready: bool, protected: Vec<(String, String)>) -> Self {
        let rows: Vec<_> = protected
            .iter()
            .map(|(owner, chat)| json!({"ownerId":owner,"chatId":chat}))
            .collect();
        let bytes = serde_json::to_vec(&json!({"schema":1,"ready":ready,"protected":rows}))
            .expect("bounded schema");
        Self {
            ready,
            protected,
            revision: sha256(&bytes),
        }
    }
    pub fn unavailable() -> Self {
        Self::new(false, Vec::new())
    }
    pub fn from_catalog(catalog: &Value) -> Result<Self> {
        if !exact(catalog, &["schema", "ready", "protected"])
            || catalog["schema"].as_u64() != Some(1)
            || catalog["ready"] != true
        {
            return Err(PolicyError);
        }
        let items = catalog["protected"]
            .as_array()
            .filter(|v| v.len() <= MAX_LOCKS)
            .ok_or(PolicyError)?;
        let mut scopes = BTreeSet::new();
        for item in items {
            if !exact(item, &["accountId", "chatId", "locked"]) || !item["locked"].is_boolean() {
                return Err(PolicyError);
            }
            let owner = item["accountId"].as_str().ok_or(PolicyError)?;
            let chat = item["chatId"].as_str().ok_or(PolicyError)?;
            scope(owner, chat).map_err(|_| PolicyError)?;
            if !scopes.insert((owner.to_owned(), chat.to_owned())) {
                return Err(PolicyError);
            }
        }
        Ok(Self::new(true, scopes.into_iter().collect()))
    }
    pub fn revision(&self) -> &str {
        &self.revision
    }
    pub fn ready(&self) -> bool {
        self.ready
    }
    pub fn wire(&self) -> Value {
        let rows: Vec<_> = self
            .protected
            .iter()
            .map(|(owner, chat)| json!({"ownerId":owner,"chatId":chat}))
            .collect();
        json!({"schema":1,"ready":self.ready,"protected":rows,"revision":self.revision,"leaseMs":LEASE_MS})
    }
}
#[derive(Clone)]
pub struct NativeSnapshot {
    ready: bool,
    applied: bool,
    revision: String,
    masked: u32,
    scope_unknown: u32,
}
impl NativeSnapshot {
    pub fn from_value(value: &Value) -> Result<Self> {
        const ERRORS: &[&str] = &[
            "waiting_for_modules",
            "module_mismatch",
            "hook_failed",
            "invalid_policy",
            "lease_expired",
            "memory_guard",
            "scope_guard",
            "sanitization_failed",
            "mapping_unavailable",
            "module_unloaded",
            "unsupported_platform",
            "invalid_specification",
            "clock_unavailable",
            "catalog_unavailable",
            "identity_unavailable",
            "clone_failed",
            "cleanup_failed",
            "invalid_data",
            "invalid_asset",
            "bridge_unavailable",
        ];
        if !exact(
            value,
            &[
                "ready",
                "applied",
                "revision",
                "error",
                "masked",
                "scopeUnknown",
            ],
        ) {
            return Err(PolicyError);
        }
        let ready = value["ready"].as_bool().ok_or(PolicyError)?;
        let applied = value["applied"].as_bool().ok_or(PolicyError)?;
        let revision = value["revision"]
            .as_str()
            .filter(|s| {
                s.len() == 64
                    && s.bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
            .ok_or(PolicyError)?;
        if !value["error"].is_null()
            && !value["error"].as_str().is_some_and(|s| ERRORS.contains(&s))
        {
            return Err(PolicyError);
        }
        if applied && (!ready || !value["error"].is_null()) {
            return Err(PolicyError);
        }
        let count = |key| {
            value[key]
                .as_u64()
                .filter(|n| *n <= 1_000_000)
                .map(|n| n as u32)
                .ok_or(PolicyError)
        };
        Ok(Self {
            ready,
            applied,
            revision: revision.to_owned(),
            masked: count("masked")?,
            scope_unknown: count("scopeUnknown")?,
        })
    }
}
#[derive(Default)]
pub struct NotificationRuntime {
    clients: Mutex<BTreeMap<u32, Option<(Instant, NativeSnapshot)>>>,
}
impl NotificationRuntime {
    pub fn sync(&self, pids: &[u32]) -> Result<()> {
        if pids.len() > MAX_CLIENTS
            || pids.contains(&0)
            || pids.iter().collect::<BTreeSet<_>>().len() != pids.len()
        {
            return Err(PolicyError);
        }
        let mut entries = self.clients.lock().map_err(|_| PolicyError)?;
        entries.retain(|pid, _| pids.contains(pid));
        for pid in pids {
            entries.entry(*pid).or_insert(None);
        }
        Ok(())
    }
    pub fn register(&self, pid: u32) -> Result<()> {
        if pid == 0 {
            return Err(PolicyError);
        }
        let mut entries = self.clients.lock().map_err(|_| PolicyError)?;
        if !entries.contains_key(&pid) && entries.len() == MAX_CLIENTS {
            return Err(PolicyError);
        }
        entries.insert(pid, None);
        Ok(())
    }
    pub fn remove(&self, pid: u32) {
        if let Ok(mut entries) = self.clients.lock() {
            entries.remove(&pid);
        }
    }
    pub fn invalidate(&self, pid: u32) {
        if let Ok(mut entries) = self.clients.lock() {
            if let Some(entry) = entries.get_mut(&pid) {
                *entry = None;
            }
        }
    }
    pub fn update(&self, pid: u32, value: &Value) -> Result<()> {
        self.update_at(pid, value, Instant::now())
    }
    fn update_at(&self, pid: u32, value: &Value, now: Instant) -> Result<()> {
        let parsed = NativeSnapshot::from_value(value);
        let mut entries = self.clients.lock().map_err(|_| PolicyError)?;
        if let Some(entry) = entries.get_mut(&pid) {
            *entry = parsed.as_ref().ok().map(|v| (now, v.clone()));
        }
        parsed.map(|_| ())
    }
    pub fn status(&self, policy: &Policy) -> Value {
        self.at(policy, Instant::now())
    }
    fn at(&self, policy: &Policy, now: Instant) -> Value {
        let entries = self.clients.lock();
        let mut ready = false;
        let mut applied = false;
        let mut masked = 0u32;
        let mut unknown = 0u32;
        if let Ok(entries) = entries {
            ready = !entries.is_empty();
            applied = ready && policy.ready;
            for entry in entries.values() {
                let fresh = entry.as_ref().filter(|(seen, _)| {
                    now.checked_duration_since(*seen)
                        .is_some_and(|age| age < Duration::from_millis(LEASE_MS))
                });
                if let Some((_, value)) = fresh {
                    ready &= value.ready;
                    applied &= value.ready && value.applied && value.revision == policy.revision;
                    masked = masked.saturating_add(value.masked).min(1_000_000);
                    unknown = unknown.saturating_add(value.scope_unknown).min(1_000_000);
                } else {
                    ready = false;
                    applied = false;
                }
            }
        }
        json!({"ready":ready,"applied":applied,"masked":masked,"scopeUnknown":unknown})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn catalog(locked: bool) -> Value {
        json!({"schema":1,"ready":true,"protected":[{"accountId":"9007199254740993","chatId":"-9223372036854775808","locked":locked},{"accountId":"42","chatId":"0","locked":false}]})
    }
    fn snapshot(policy: &Policy) -> Value {
        json!({"ready":true,"applied":true,"revision":policy.revision(),"error":null,"masked":600000,"scopeUnknown":3})
    }
    #[test]
    fn exact_scopes_and_unlock_independence() {
        let a = Policy::from_catalog(&catalog(true)).unwrap();
        let b = Policy::from_catalog(&catalog(false)).unwrap();
        assert_eq!(a.wire(), b.wire());
        assert_eq!(
            a.wire()["protected"][0],
            json!({"ownerId":"42","chatId":"0"})
        );
        assert_ne!(
            Policy::unavailable().revision(),
            Policy::from_catalog(&json!({"schema":1,"ready":true,"protected":[]}))
                .unwrap()
                .revision()
        );
        for (field, value) in [
            ("chatId", json!("-0")),
            ("accountId", json!("0")),
            ("locked", json!(1)),
            ("chatId", json!("9223372036854775808")),
        ] {
            let mut data = catalog(true);
            data["protected"][0][field] = value;
            assert!(Policy::from_catalog(&data).is_err());
        }
        let mut data = catalog(true);
        data["protected"][1] = data["protected"][0].clone();
        assert!(Policy::from_catalog(&data).is_err());
    }
    #[test]
    fn acknowledgements_require_all_clients_exact_revision_and_fresh_lease() {
        let runtime = NotificationRuntime::default();
        let policy = Policy::from_catalog(&catalog(true)).unwrap();
        let value = snapshot(&policy);
        let now = Instant::now();
        runtime.update_at(100, &value, now).unwrap();
        assert_eq!(runtime.at(&policy, now)["ready"], false);
        runtime.sync(&[100, 200]).unwrap();
        runtime.update_at(100, &value, now).unwrap();
        assert_eq!(runtime.at(&policy, now)["applied"], false);
        runtime.update_at(200, &value, now).unwrap();
        let status = runtime.at(&policy, now);
        assert_eq!(status["applied"], true);
        assert_eq!(status["masked"], 1_000_000);
        assert_eq!(runtime.at(&Policy::unavailable(), now)["applied"], false);
        assert_eq!(
            runtime.at(&policy, now + Duration::from_millis(4999))["ready"],
            true
        );
        assert_eq!(
            runtime.at(&policy, now + Duration::from_millis(5000))["ready"],
            false
        );
        assert_eq!(
            runtime.at(&policy, now - Duration::from_millis(1))["ready"],
            false
        );
        runtime.remove(200);
        runtime.update_at(200, &value, now).unwrap();
        assert_eq!(runtime.at(&policy, now)["masked"], 600000);
        runtime.register(100).unwrap();
        assert_eq!(runtime.at(&policy, now)["ready"], false);
        let unavailable = Policy::unavailable();
        runtime
            .update_at(100, &snapshot(&unavailable), now)
            .unwrap();
        assert_eq!(runtime.at(&unavailable, now)["applied"], false);
        runtime.update_at(100, &value, now).unwrap();
        let mut invalid = value.clone();
        invalid["masked"] = json!(true);
        assert!(runtime.update_at(100, &invalid, now).is_err());
        assert_eq!(runtime.at(&policy, now)["ready"], false);
    }
}
