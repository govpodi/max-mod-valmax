//! Requested ghost settings only. A durable write does not acknowledge hooks.
use std::path::Path;
use serde_json::{Value, json};
use crate::settings_io::{GuardedStore, Result, SafeStorePath, StoreError, sha256};

pub const KEYS: [&str; 5] = ["enabled", "hideTyping", "hideRead", "hideOnline", "hideStories"];
pub const MAX_STORE_BYTES: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Settings { pub enabled: bool, pub hide_typing: bool, pub hide_read: bool,
    pub hide_online: bool, pub hide_stories: bool }
impl Default for Settings {
    fn default() -> Self { Self { enabled: false, hide_typing: true, hide_read: true,
        hide_online: false, hide_stories: false } }
}
impl Settings {
    pub fn from_value(value: &Value) -> Result<Self> { Self::validate(value, StoreError::InvalidSettings) }
    fn validate(value: &Value, error: StoreError) -> Result<Self> {
        let map = value.as_object().ok_or(error)?;
        let keys = if map.len() == 3 { &KEYS[..3] } else { &KEYS[..] };
        if map.len() != keys.len() || keys.iter().any(|k| !map.get(*k).is_some_and(Value::is_boolean)) { return Err(error); }
        Ok(Self { enabled: map["enabled"].as_bool().ok_or(error)?,
            hide_typing: map["hideTyping"].as_bool().ok_or(error)?,
            hide_read: map["hideRead"].as_bool().ok_or(error)?,
            hide_online: map.get("hideOnline").and_then(Value::as_bool).unwrap_or(false),
            hide_stories: map.get("hideStories").and_then(Value::as_bool).unwrap_or(false) })
    }
    pub fn value(self) -> Value { json!({"enabled":self.enabled,"hideTyping":self.hide_typing,
        "hideRead":self.hide_read,"hideOnline":self.hide_online,"hideStories":self.hide_stories}) }
    pub fn canonical(self) -> Vec<u8> { serde_json::to_vec(&json!({"schema":1,"settings":self.value()})).expect("bool-only schema") }
    pub fn revision(self) -> String { sha256(&self.canonical()) }
    pub fn snapshot(self) -> Value { json!({"schema":1,"revision":self.revision(),"settings":self.value()}) }
}
pub fn parse(data: &[u8]) -> Result<Settings> {
    if data.len() > MAX_STORE_BYTES { return Err(StoreError::InvalidStore); }
    let value = crate::session_archive::parse_json(data,"invalid_payload").map_err(|_|StoreError::InvalidStore)?;
    let map = value.as_object().ok_or(StoreError::InvalidStore)?;
    if map.len() != 2 || map.get("schema").and_then(Value::as_u64) != Some(1) { return Err(StoreError::InvalidStore); }
    Settings::validate(map.get("settings").ok_or(StoreError::InvalidStore)?, StoreError::InvalidStore)
}
fn read(guard: &GuardedStore) -> Result<Settings> {
    guard.read_bounded(MAX_STORE_BYTES)?.map_or(Ok(Settings::default()), |bytes|parse(&bytes))
}
pub struct GhostSettingsStore { path: SafeStorePath }
impl GhostSettingsStore {
    pub fn new(path: impl AsRef<Path>) -> Result<Self> { Ok(Self { path: SafeStorePath::new(path)? }) }
    pub fn snapshot(&self) -> Result<Value> {
        Ok(self.path.begin(false)?.map_or(Ok(Settings::default()), |guard|read(&guard))?.snapshot())
    }
    pub fn replace(&self, revision: &str, value: &Value) -> Result<Value> {
        if revision.len() != 64 || !revision.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) { return Err(StoreError::InvalidRevision); }
        let clean = Settings::from_value(value)?;
        let guard = self.path.begin(true)?.ok_or(StoreError::IoError)?;
        let _lock = guard.writer_lock()?;
        let validate = || if read(&guard)?.revision() == revision { Ok(()) } else { Err(StoreError::RevisionConflict) };
        validate()?;
        guard.atomic_replace(&clean.canonical(), validate)?;
        Ok(clean.snapshot())
    }
}
