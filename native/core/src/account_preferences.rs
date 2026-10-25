//! Labels and schedules for IDs supplied by the native account model.
//! Reserved per-account ghost fields are preserved, not interpreted as active hooks.
use std::{collections::HashSet, path::Path};
use serde_json::{Value, json};
use crate::settings_io::{SafeStorePath, GuardedStore, StoreError, sha256};

pub const MAX_STORE_BYTES: usize = 64 * 1024;
pub const MAX_ACCOUNTS: usize = 32;
pub const COLORS: [&str; 8] = ["", "#0088ff", "#8b5cf6", "#22a06b", "#e58b17", "#dc5260", "#d35ca5", "#149ca1"];
pub const GHOST_FIELDS: [&str; 5] = ["hideRead", "hideStoryRead", "hideTyping", "hideOnline", "autoOffline"];
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error { InvalidAccounts, Store(StoreError) }
impl Error { pub fn code(self) -> &'static str { match self { Self::InvalidAccounts => "invalid_accounts", Self::Store(e) => e.code() } } }
impl From<StoreError> for Error { fn from(value: StoreError) -> Self { Self::Store(value) } }
pub type Result<T> = std::result::Result<T, Error>;
fn invalid() -> Error { Error::InvalidAccounts }
fn exact(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|map| map.len() == keys.len() && keys.iter().all(|key|map.contains_key(*key)))
}
pub fn valid_account_id(value: &str) -> bool {
    !value.is_empty() && value.len() <= 19 && !value.starts_with('0')
        && value.bytes().all(|c|c.is_ascii_digit()) && value.parse::<i64>().is_ok_and(|n|n > 0)
}
pub fn defaults(id: &str) -> Result<Value> {
    if !valid_account_id(id) { return Err(invalid()); }
    Ok(json!({"accountId":id,"displayName":"","color":"","workHours":{
        "enabled":false,"days":[1,2,3,4,5],"start":"09:00","end":"18:00"},"ghost":{
        "hideRead":false,"hideStoryRead":false,"hideTyping":false,"hideOnline":false,"autoOffline":false}}))
}
fn display_name(text: &str) -> bool {
    text.chars().count() <= 64 && text.chars().all(|c| {
        let n = u32::from(c);
        n >= 32 && !(127..160).contains(&n) && !matches!(n,0x2028..=0x202e|0x2066..=0x2069)
    })
}
fn minutes(value: &str) -> Option<u16> {
    let b = value.as_bytes();
    if b.len() != 5 || b[2] != b':' || ![b[0],b[1],b[3],b[4]].iter().all(u8::is_ascii_digit) { return None; }
    let h = (b[0]-b'0')*10+b[1]-b'0'; let m = (b[3]-b'0')*10+b[4]-b'0';
    (h < 24 && m < 60).then_some(u16::from(h)*60+u16::from(m))
}
pub fn validate_hours(value: &Value) -> Result<Value> {
    if !exact(value,&["enabled","days","start","end"]) { return Err(invalid()); }
    let enabled = value["enabled"].as_bool().ok_or_else(invalid)?;
    let days = value["days"].as_array().ok_or_else(invalid)?;
    let start = value["start"].as_str().ok_or_else(invalid)?;
    let end = value["end"].as_str().ok_or_else(invalid)?;
    if days.len() > 7 || enabled && days.is_empty() || start == end || minutes(start).is_none() || minutes(end).is_none() { return Err(invalid()); }
    let mut clean = Vec::new();
    for day in days {
        let day = day.as_u64().filter(|d|(1..=7).contains(d)).ok_or_else(invalid)?;
        if clean.contains(&day) { return Err(invalid()); } clean.push(day);
    }
    clean.sort_unstable();
    Ok(json!({"enabled":enabled,"days":clean,"start":start,"end":end}))
}
pub fn validate_accounts(value: &Value) -> Result<Value> {
    let accounts = value.as_array().filter(|v|v.len() <= MAX_ACCOUNTS).ok_or_else(invalid)?;
    let mut seen = HashSet::new(); let mut clean = Vec::new();
    for row in accounts {
        if !exact(row,&["accountId","displayName","color","workHours","ghost"]) { return Err(invalid()); }
        let id = row["accountId"].as_str().ok_or_else(invalid)?;
        let name = row["displayName"].as_str().ok_or_else(invalid)?;
        let color = row["color"].as_str().ok_or_else(invalid)?;
        if !valid_account_id(id) || !seen.insert(id) || !display_name(name) || !COLORS.contains(&color)
            || !exact(&row["ghost"],&GHOST_FIELDS) || GHOST_FIELDS.iter().any(|k|!row["ghost"][*k].is_boolean()) { return Err(invalid()); }
        clean.push(json!({"accountId":id,"displayName":name,"color":color,
            "workHours":validate_hours(&row["workHours"])?,"ghost":row["ghost"].clone()}));
    }
    clean.sort_by_key(|row|row["accountId"].as_str().and_then(|id|id.parse::<i64>().ok()).unwrap_or(0));
    let result = Value::Array(clean); canonical(&result)?; Ok(result)
}
pub fn canonical(clean: &Value) -> Result<Vec<u8>> {
    let bytes = serde_json::to_vec(&json!({"schema":1,"accounts":clean})).map_err(|_|invalid())?;
    if bytes.len() > MAX_STORE_BYTES { return Err(invalid()); } Ok(bytes)
}
pub fn snapshot_value(clean: &Value) -> Result<Value> {
    Ok(json!({"schema":1,"revision":sha256(&canonical(clean)?),"accounts":clean}))
}
pub fn parse(data: &[u8]) -> Result<Value> {
    let invalid_store = ||Error::Store(StoreError::InvalidStore);
    if data.len() > MAX_STORE_BYTES { return Err(invalid_store()); }
    let value = crate::session_archive::parse_json(data,"invalid_payload").map_err(|_|invalid_store())?;
    if !exact(&value,&["schema","accounts"]) || value["schema"].as_u64() != Some(1) { return Err(invalid_store()); }
    validate_accounts(&value["accounts"]).map_err(|_|invalid_store())
}
fn read(guard: &GuardedStore) -> Result<Value> {
    guard.read_bounded(MAX_STORE_BYTES)?.map_or(Ok(json!([])),|data|parse(&data))
}
pub struct AccountPreferencesStore { path: SafeStorePath }
impl AccountPreferencesStore {
    pub fn new(path: impl AsRef<Path>) -> Result<Self> { Ok(Self { path: SafeStorePath::new(path)? }) }
    pub fn snapshot(&self) -> Result<Value> {
        snapshot_value(&self.path.begin(false)?.map_or(Ok(json!([])),|guard|read(&guard))?)
    }
    pub fn replace(&self, revision: &str, value: &Value) -> Result<Value> {
        if revision.len()!=64 || !revision.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) { return Err(StoreError::InvalidRevision.into()); }
        let clean = validate_accounts(value)?;
        let payload = canonical(&clean)?;
        let guard = self.path.begin(true)?.ok_or(StoreError::IoError)?;
        let _lock = guard.writer_lock()?;
        let check = || -> crate::settings_io::Result<()> {
            let current = read(&guard).map_err(|e|match e { Error::Store(e)=>e, _=>StoreError::InvalidStore })?;
            let bytes = canonical(&current).map_err(|_|StoreError::InvalidStore)?;
            if sha256(&bytes) != revision { Err(StoreError::RevisionConflict) } else { Ok(()) }
        };
        check()?; guard.atomic_replace(&payload,check)?; snapshot_value(&clean)
    }
}
/// The caller supplies the computer's local ISO weekday and minute of day.
/// An overnight interval belongs to its starting day, including Sunday→Monday.
pub fn is_work_time(hours: &Value, weekday: u8, minute: u16) -> Result<bool> {
    if !(1..=7).contains(&weekday) || minute >= 1440 { return Err(invalid()); }
    let clean = validate_hours(hours)?;
    if clean["enabled"].as_bool() == Some(false) { return Ok(true); }
    let days = clean["days"].as_array().ok_or_else(invalid)?;
    let contains = |d:u8|days.contains(&json!(d));
    let start = minutes(clean["start"].as_str().ok_or_else(invalid)?).ok_or_else(invalid)?;
    let end = minutes(clean["end"].as_str().ok_or_else(invalid)?).ok_or_else(invalid)?;
    Ok(if start < end { contains(weekday) && start <= minute && minute < end }
       else { contains(weekday) && minute >= start || contains(if weekday==1 {7}else{weekday-1}) && minute < end })
}
