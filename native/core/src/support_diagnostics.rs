//! Explicit, local support reports built from a fixed scalar allowlist.
//! This module never discovers profiles, processes, network endpoints or logs.
//! Saving creates one user-selected file; no report is uploaded automatically.
use crate::settings_io::{SafeStorePath, StoreError};
use serde_json::{Value, json};
use std::{fmt, path::PathBuf};

pub const MAX_BYTES: usize = 16_384;
pub const MAX_COUNT: u64 = (1u64 << 53) - 1;
const VERSIONS_V1: &[&str] = &["valmax", "max", "windows", "python"];
const VERSIONS_V2: &[&str] = &["valmax", "max", "windows"];
const GHOST: &[&str] = &[
    "enabled",
    "hideTyping",
    "hideRead",
    "hideOnline",
    "hideStories",
];
const OPTIMIZATION: &[&str] = &[
    "enabled",
    "smoothWheel",
    "listScheduling",
    "listPrefetch",
    "readyCache",
    "historyCache",
    "historyBookkeeping",
    "photoDecodeResize",
    "windowPosition",
    "hiddenPageWork",
    "profileRows",
];
const OBSERVATION: &[&str] = &[
    "seconds",
    "blockedCalls",
    "tracerHooks",
    "transportHooks",
    "processes",
];
const DASHBOARD: &[&str] = &[
    "observedPackets",
    "observedBytes",
    "parsedPackets",
    "headerOnlyPackets",
    "excludedPackets",
    "excludedBytes",
    "requestTypes",
    "excludedRequestTypes",
];
const ERRORS: &[&str] = &[
    "attach_failed",
    "hook_failed",
    "metadata_rejected",
    "process_exited",
    "runtime_snapshot_timeout",
    "runtime_snapshot_unavailable",
    "runtime_session_lost",
    "runtime_transport_failed",
    "runtime_retention_ack_failed",
    "runtime_ghost_ack_failed",
    "runtime_notification_ack_failed",
    "runtime_schema_failed",
    "runtime_worker_failed",
];
pub(crate) const ROLES: &[&str] = &["launcher", "observe", "recover", "mdata"];
pub(crate) const STAGES: &[&str] = &[
    "bootstrap",
    "import",
    "verify",
    "desktop",
    "profile",
    "observer",
    "optimization",
    "spawn",
    "protection",
    "recovery",
    "complete",
    "frida_initialize",
    "setup_stores",
    "bind",
    "native_spawn",
    "protection_attach",
    "ready",
];
pub(crate) const STATES: &[&str] = &["starting", "ready", "error"];
pub(crate) const STARTUP_CODES: &[&str] = &[
    "startup_failed",
    "invalid_command",
    "unsupported_platform",
    "import_failed",
    "payload_invalid",
    "desktop_unavailable",
    "desktop_bootstrap_failed",
    "desktop_bootstrap_timeout",
    "profile_guard_failed",
    "recovery_required",
    "transfer_busy",
    "port_unavailable",
    "port_busy",
    "port_other_runtime",
    "observer_idle",
    "observer_start_failed",
    "observer_timeout",
    "observer_worker_stopped",
    "observer_identity_invalid",
    "observer_unavailable",
    "optimization_prepare_failed",
    "native_spawn_failed",
    "window_activation_failed",
    "protection_ack_failed",
    "ghost_ack_failed",
    "retention_ack_failed",
    "notification_ack_failed",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReportError {
    InvalidReport,
    UnsafePath,
    FileExists,
    IoError,
}
impl ReportError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidReport => "invalid_report",
            Self::UnsafePath => "unsafe_path",
            Self::FileExists => "file_exists",
            Self::IoError => "io_error",
        }
    }
}
impl fmt::Display for ReportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for ReportError {}
type Result<T> = std::result::Result<T, ReportError>;

fn version(value: &Value) -> Value {
    let Some(text) = value.as_str() else {
        return Value::Null;
    };
    if !(3..=23).contains(&text.len()) {
        return Value::Null;
    }
    let parts: Vec<_> = text.split('.').collect();
    if !(2..=4).contains(&parts.len())
        || parts
            .iter()
            .any(|part| !(1..=5).contains(&part.len()) || !part.bytes().all(|b| b.is_ascii_digit()))
    {
        return Value::Null;
    }
    Value::String(text.to_owned())
}
fn boolean(value: &Value) -> Value {
    value.as_bool().map(Value::Bool).unwrap_or(Value::Null)
}
fn count(value: &Value) -> Value {
    value
        .as_u64()
        .filter(|v| *v <= MAX_COUNT)
        .map(Value::from)
        .unwrap_or(Value::Null)
}
fn fields(value: &Value, keys: &[&str], convert: fn(&Value) -> Value) -> Value {
    Value::Object(
        keys.iter()
            .map(|key| ((*key).to_owned(), convert(&value[*key])))
            .collect(),
    )
}
fn member(value: &Value, choices: &[&str]) -> bool {
    value.as_str().is_some_and(|s| choices.contains(&s))
}
fn startup(value: &Value) -> Value {
    if !member(&value["role"], ROLES)
        || !member(&value["stage"], STAGES)
        || !member(&value["state"], STATES)
        || !(if value["state"] == "error" {
            member(&value["code"], STARTUP_CODES)
        } else {
            value["code"] == "none"
        })
    {
        return Value::Null;
    }
    Value::Object(
        ["role", "stage", "state", "code"]
            .iter()
            .map(|key| ((*key).to_owned(), value[*key].clone()))
            .collect(),
    )
}

/// Schema 2 replaces the old Python version with an explicit native runtime.
/// Missing/unavailable observations remain null, never fabricated zero/success.
pub fn build_report(
    versions: &Value,
    start: &Value,
    privacy: &Value,
    ghost: &Value,
    optimization: &Value,
) -> Value {
    let observation = &privacy["observation"];
    let dashboard = &privacy["dashboard"];
    let errors = Value::Object(
        ERRORS
            .iter()
            .map(|key| {
                let value = match privacy["errors"].as_object() {
                    Some(map) => map.get(*key).map(count).unwrap_or(json!(0)),
                    None => Value::Null,
                };
                ((*key).to_owned(), value)
            })
            .collect(),
    );
    json!({"schema": 2,
        "versions": fields(versions, VERSIONS_V2, version),
        "runtime": {"kind": "native", "core": env!("CARGO_PKG_VERSION"), "qt": version(&versions["qt"])},
        "startup": startup(start),
        "flags": {"verified": boolean(&privacy["verified"]),
            "observationActive": boolean(&observation["active"]),
            "requestTypesComplete": boolean(&dashboard["requestTypesComplete"]),
            "ghost": fields(&ghost["settings"], GHOST, boolean),
            "optimization": fields(&optimization["settings"], OPTIMIZATION, boolean)},
        "counters": {"observation": fields(observation, OBSERVATION, count),
            "dashboard": fields(dashboard, DASHBOARD, count)},
        "errors": errors})
}
fn exact(value: &Value, keys: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|map| map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key)))
}
fn nullable_fields(value: &Value, keys: &[&str], convert: fn(&Value) -> Value) -> bool {
    exact(value, keys)
        && value
            .as_object()
            .unwrap()
            .values()
            .all(|v| v.is_null() || !convert(v).is_null())
}

/// Accept old schema-1 reports for compatibility, but reject extra fields at
/// every level. This never serializes a raw broader source snapshot.
pub fn serialize(report: &Value) -> Result<Vec<u8>> {
    let schema = report["schema"]
        .as_u64()
        .ok_or(ReportError::InvalidReport)?;
    let base = [
        "schema", "versions", "startup", "flags", "counters", "errors",
    ];
    let (keys, versions) = if schema == 1 {
        (base.to_vec(), VERSIONS_V1)
    } else if schema == 2 {
        (
            base.iter().copied().chain(Some("runtime")).collect(),
            VERSIONS_V2,
        )
    } else {
        return Err(ReportError::InvalidReport);
    };
    let flags = &report["flags"];
    let counters = &report["counters"];
    if !exact(report, &keys)
        || !nullable_fields(&report["versions"], versions, version)
        || (!report["startup"].is_null()
            && (!exact(&report["startup"], &["role", "stage", "state", "code"])
                || startup(&report["startup"]) != report["startup"]))
        || !exact(
            flags,
            &[
                "verified",
                "observationActive",
                "requestTypesComplete",
                "ghost",
                "optimization",
            ],
        )
        || ["verified", "observationActive", "requestTypesComplete"]
            .iter()
            .any(|k| !flags[*k].is_null() && !flags[*k].is_boolean())
        || !nullable_fields(&flags["ghost"], GHOST, boolean)
        || !nullable_fields(&flags["optimization"], OPTIMIZATION, boolean)
        || !exact(counters, &["observation", "dashboard"])
        || !nullable_fields(&counters["observation"], OBSERVATION, count)
        || !nullable_fields(&counters["dashboard"], DASHBOARD, count)
        || !nullable_fields(&report["errors"], ERRORS, count)
    {
        return Err(ReportError::InvalidReport);
    }
    if schema == 2 {
        let native = &report["runtime"];
        if !exact(native, &["kind", "core", "qt"])
            || native["kind"] != "native"
            || version(&native["core"]).is_null()
            || (!native["qt"].is_null() && version(&native["qt"]).is_null())
        {
            return Err(ReportError::InvalidReport);
        }
    }
    // All accepted strings are ASCII numeric versions or fixed enum constants.
    let mut bytes = serde_json::to_vec_pretty(report).map_err(|_| ReportError::InvalidReport)?;
    bytes.push(b'\n');
    if bytes.len() > MAX_BYTES || !bytes.is_ascii() {
        return Err(ReportError::InvalidReport);
    }
    Ok(bytes)
}

/// Strict QML SaveFile URL, decoded exactly once. No UNC, authority, query,
/// fragment, device path, traversal, NUL, ADS or non-JSON destination.
pub fn selected_file(url: &str) -> Result<PathBuf> {
    if url.len() > 32_768 || url.contains(['?', '#', '\0']) {
        return Err(ReportError::UnsafePath);
    }
    let source = url
        .strip_prefix("file:///")
        .ok_or(ReportError::UnsafePath)?
        .as_bytes();
    let mut decoded = Vec::with_capacity(source.len());
    let mut i = 0;
    while i < source.len() {
        if source[i] == b'%' {
            let nibble = |b: u8| -> Option<u8> { (b as char).to_digit(16).map(|v| v as u8) };
            if i + 2 >= source.len() {
                return Err(ReportError::UnsafePath);
            }
            decoded.push(
                nibble(source[i + 1]).ok_or(ReportError::UnsafePath)? * 16
                    + nibble(source[i + 2]).ok_or(ReportError::UnsafePath)?,
            );
            i += 3;
        } else {
            decoded.push(source[i]);
            i += 1;
        }
    }
    let text = String::from_utf8(decoded).map_err(|_| ReportError::UnsafePath)?;
    let safe = SafeStorePath::new(&text).map_err(|_| ReportError::UnsafePath)?;
    if !safe
        .path()
        .extension()
        .is_some_and(|s| s.eq_ignore_ascii_case("json"))
    {
        return Err(ReportError::UnsafePath);
    }
    Ok(safe.path().to_owned())
}
fn io_error(error: StoreError) -> ReportError {
    match error {
        StoreError::UnsafePath => ReportError::UnsafePath,
        StoreError::FileExists => ReportError::FileExists,
        _ => ReportError::IoError,
    }
}
pub fn save_report(file_url: &str, report: &Value) -> Result<Value> {
    let bytes = serialize(report)?;
    let path = SafeStorePath::new(selected_file(file_url)?).map_err(io_error)?;
    let guard = path
        .begin(false)
        .map_err(io_error)?
        .ok_or(ReportError::UnsafePath)?;
    guard.atomic_create(&bytes).map_err(io_error)?;
    Ok(json!({"schema": 1, "saved": true}))
}
