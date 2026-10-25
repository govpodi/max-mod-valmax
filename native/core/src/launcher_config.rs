//! Pure, pinned launcher-configuration data contract. This module does not read
//! the filesystem or environment, discover profiles, start processes, access
//! credentials, register hooks, or communicate over a network.
//! The template is embedded at compile time, not loaded from an installed client.

use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    borrow::Cow,
    fmt,
    path::{Path, PathBuf},
};

pub const MAX_CONFIG_BYTES: usize = 64 * 1024;
pub const TEMPLATE_FIELD_COUNT: usize = 74;
pub const TEMPLATE_SHA256: &str =
    "0a822dc4316554f9e2210091263337c41a3ba0b4888d519dcfcedf249330955a";
const TEMPLATE: &[u8] = include_bytes!("../../../modkit/config.defaults.json");

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ConfigError {
    TemplateMismatch,
    InvalidConfig,
    TooLarge,
    UnsafePath,
}
impl ConfigError {
    pub fn code(self) -> &'static str {
        match self {
            Self::TemplateMismatch => "template_mismatch",
            Self::InvalidConfig => "invalid_config",
            Self::TooLarge => "too_large",
            Self::UnsafePath => "unsafe_path",
        }
    }
}
impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for ConfigError {}
pub type Result<T> = std::result::Result<T, ConfigError>;

fn overrides() -> Value {
    json!({"client_friendly_name":"MAX Mod", "app_folder_name":"MXMOD",
        "single_instance_id":"mxmod_instance", "local_server_pipe_name":"mxmod_local_pipe",
        "auto_update_enabled":false, "rooms_auto_update_enabled":false,
        "need_mask_logs":true, "send_error_logs_to_crash_server":false})
}

/// serde_json::Value has bounded integer variants. Reject integer tokens that
/// would otherwise be silently rounded to f64; strings are not number tokens.
/// Python permits arbitrary-width integers, which this native contract does not.
fn exact_integer_tokens(data: &[u8]) -> Option<Cow<'_, [u8]>> {
    let mut normalized = Cow::Borrowed(data);
    let mut position = 0;
    let mut quoted = false;
    while position < data.len() {
        match data[position] {
            b'\\' if quoted => {
                position = position.saturating_add(2);
                continue;
            }
            b'"' => {
                quoted = !quoted;
                position += 1;
            }
            b'-' | b'0'..=b'9' if !quoted => {
                let start = position;
                while position < data.len()
                    && matches!(
                        data[position],
                        b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E'
                    )
                {
                    position += 1;
                }
                let token = &data[start..position];
                if !token.iter().any(|byte| matches!(byte, b'.' | b'e' | b'E')) {
                    let token = std::str::from_utf8(token).ok()?;
                    if if token.starts_with('-') {
                        token.parse::<i64>().is_err()
                    } else {
                        token.parse::<u64>().is_err()
                    } {
                        return None;
                    }
                    // Python parses the integer token -0 as int(0), while serde
                    // preserves its sign in f64(-0.0). Replace only this exact
                    // unquoted token with space+0; float -0.0 stays a float.
                    if token == "-0" {
                        normalized.to_mut()[start] = b' ';
                    }
                }
            }
            _ => position += 1,
        }
    }
    Some(normalized)
}

fn parse(data: &[u8]) -> Result<Value> {
    if data.len() > MAX_CONFIG_BYTES {
        return Err(ConfigError::TooLarge);
    }
    // Match Python utf-8-sig: a single leading UTF-8 BOM is optional and counts
    // against the byte limit. Embedded/doubled BOMs are not removed.
    let data = data.strip_prefix(b"\xef\xbb\xbf").unwrap_or(data);
    let data = exact_integer_tokens(data).ok_or(ConfigError::InvalidConfig)?;
    let value = crate::session_archive::parse_json(&data, "invalid_payload")
        .map_err(|_| ConfigError::InvalidConfig)?;
    if !value.is_object() {
        return Err(ConfigError::InvalidConfig);
    }
    Ok(value)
}

fn template() -> Result<Value> {
    if format!("{:x}", Sha256::digest(TEMPLATE)) != TEMPLATE_SHA256 {
        return Err(ConfigError::TemplateMismatch);
    }
    let value = parse(TEMPLATE).map_err(|_| ConfigError::TemplateMismatch)?;
    let fields = value.as_object().ok_or(ConfigError::TemplateMismatch)?;
    if fields.len() != TEMPLATE_FIELD_COUNT
        || overrides()
            .as_object()
            .unwrap()
            .keys()
            .any(|key| !fields.contains_key(key))
    {
        return Err(ConfigError::TemplateMismatch);
    }
    Ok(value)
}

/// A new independent, full 74-field document. Exactly eight critical overrides
/// match launch_mod.py; all other defaults remain byte-template-derived values.
pub fn make_config() -> Result<Value> {
    let mut value = template()?;
    let fields = value.as_object_mut().ok_or(ConfigError::TemplateMismatch)?;
    fields.extend(overrides().as_object().unwrap().clone());
    Ok(value)
}

fn same_type(value: &Value, default: &Value) -> bool {
    match default {
        Value::Null => value.is_null(),
        Value::Bool(_) => value.is_boolean(),
        Value::String(_) => value.is_string(),
        Value::Array(_) => value
            .as_array()
            .is_some_and(|items| items.iter().all(Value::is_string)),
        Value::Object(_) => value.is_object(),
        Value::Number(number) if number.is_i64() || number.is_u64() => {
            value.is_i64() || value.is_u64()
        }
        Value::Number(_) => value.is_f64(),
    }
}

/// Validate without writing, repairing, normalizing, or discarding extra keys.
/// Required fields have their template types; only the eight critical fields
/// must have the pinned values. Numeric booleans and floats are not integers.
/// Native JSON additionally bounds nesting to 64, rejects invalid Unicode and
/// non-finite numbers, and rejects integers outside i64/u64 instead of rounding.
pub fn validate_config(data: &[u8]) -> Result<Value> {
    let value = parse(data)?;
    let defaults = template()?;
    let fields = value.as_object().ok_or(ConfigError::InvalidConfig)?;
    for (key, default) in defaults.as_object().unwrap() {
        if !fields.get(key).is_some_and(|item| same_type(item, default)) {
            return Err(ConfigError::InvalidConfig);
        }
    }
    for (key, expected) in overrides().as_object().unwrap() {
        if !fields
            .get(key)
            .is_some_and(|item| same_type(item, expected) && item == expected)
        {
            return Err(ConfigError::InvalidConfig);
        }
    }
    Ok(value)
}

fn safe_component(value: &str) -> bool {
    let stem = value.split('.').next().unwrap_or("").to_ascii_uppercase();
    !value.is_empty()
        && !matches!(value, "." | "..")
        && !value.ends_with(['.', ' '])
        && !value.eq_ignore_ascii_case("ONEME")
        && !value
            .chars()
            .any(|c| c < ' ' || c == '\u{7f}' || "<>:\"/\\|?*".contains(c))
        && !matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        && !(stem.len() == 4
            && (stem.starts_with("COM") || stem.starts_with("LPT"))
            && (b'1'..=b'9').contains(&stem.as_bytes()[3]))
}

/// Return a lexical private config path from an explicit local-root argument.
/// This is not a filesystem safety check: callers must separately validate any
/// actual I/O. No environment lookup, existence check or directory creation.
pub fn config_path(explicit_local_root: impl AsRef<Path>) -> Result<PathBuf> {
    let root = explicit_local_root
        .as_ref()
        .to_str()
        .ok_or(ConfigError::UnsafePath)?;
    let bytes = root.as_bytes();
    if bytes.len() < 3
        || bytes.len() > 32000
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1] != b':'
        || !matches!(bytes[2], b'/' | b'\\')
    {
        return Err(ConfigError::UnsafePath);
    }
    let normalized = root.replace('/', "\\");
    let suffix = &normalized[3..];
    let suffix = suffix.strip_suffix('\\').unwrap_or(suffix);
    if !suffix.is_empty() && suffix.split('\\').any(|part| !safe_component(part)) {
        return Err(ConfigError::UnsafePath);
    }
    // Repeated separators must not turn an invalid root into an accepted alias.
    if normalized.len() > 3 && suffix.is_empty() {
        return Err(ConfigError::UnsafePath);
    }
    let base = if suffix.is_empty() {
        normalized[..3].to_owned()
    } else {
        format!("{}{}\\", &normalized[..3], suffix)
    };
    Ok(PathBuf::from(format!("{base}MXMOD\\config.json")))
}
