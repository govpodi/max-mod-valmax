//! Compatible MXMODSA v1 envelope for explicitly supplied records.
//! No profile, credential, filesystem, process, or network access.

use aes_gcm::{Aes256Gcm, KeyInit, Nonce, aead::{Aead, Payload}};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize, de::{self, DeserializeSeed, MapAccess, SeqAccess, Visitor}};
use serde_json::{Map, Number, Value, json};
use std::{collections::HashSet, fmt};
use zeroize::{Zeroize, Zeroizing};

pub const MAGIC: &[u8; 8] = b"MXMODSA\x01";
pub const SOURCE_CLIENT: &str = "MAX Desktop";
pub const SOURCE_VERSION: &str = "26.35.1";
pub const MAX_SECRET_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_PLAINTEXT_BYTES: usize = 6 * 1024 * 1024;
pub const MAX_HEADER_BYTES: usize = 1024;
pub const MAX_ARCHIVE_BYTES: usize = MAGIC.len() + 2 + MAX_HEADER_BYTES + MAX_PLAINTEXT_BYTES + 16;
pub const MAX_CREDENTIALS: usize = 256;
pub const MAX_FILES: usize = 128;
const MAX_JSON_DEPTH: usize = 64;
const MAX_JSON_BYTES: usize = 16 * 1024 * 1024;

pub(crate) struct OwnedValue(pub Value);
impl Drop for OwnedValue {
    fn drop(&mut self) {
        fn erase(value: &mut Value) {
            match value {
                Value::String(text) => text.zeroize(),
                Value::Array(values) => values.iter_mut().for_each(erase),
                Value::Object(values) => {
                    for (mut key, mut value) in std::mem::take(values) {
                        key.zeroize();
                        erase(&mut value);
                    }
                }
                _ => {},
            }
        }
        erase(&mut self.0);
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArchiveError {
    InvalidPassword, AuthenticationFailed, InvalidArchive, UnsupportedVersion,
    InvalidPayload, PolicyViolation, TooLarge, CryptoUnavailable,
}

impl ArchiveError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidPassword => "invalid_password",
            Self::AuthenticationFailed => "authentication_failed",
            Self::InvalidArchive => "invalid_archive",
            Self::UnsupportedVersion => "unsupported_version",
            Self::InvalidPayload => "invalid_payload",
            Self::PolicyViolation => "policy_violation",
            Self::TooLarge => "too_large",
            Self::CryptoUnavailable => "crypto_unavailable",
        }
    }
    fn from_code(code: &str) -> Self {
        match code {
            "invalid_password" => Self::InvalidPassword,
            "authentication_failed" => Self::AuthenticationFailed,
            "invalid_archive" => Self::InvalidArchive,
            "unsupported_version" => Self::UnsupportedVersion,
            "policy_violation" => Self::PolicyViolation,
            "too_large" => Self::TooLarge,
            "crypto_unavailable" => Self::CryptoUnavailable,
            _ => Self::InvalidPayload,
        }
    }
}
impl fmt::Display for ArchiveError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result { formatter.write_str(self.code()) }
}
impl std::error::Error for ArchiveError {}

/// Policy is independent of the archive, denies all records by default, and is
/// revalidated on every operation (including values made directly in Rust).
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct ArchivePolicy {
    pub credential_names: Vec<String>,
    pub credential_prefixes: Vec<String>,
    pub allowed_file_paths: Vec<String>,
    pub credential_name_patterns: Vec<String>,
    pub file_path_patterns: Vec<String>,
}

fn valid_name(value: &str) -> bool {
    (1..=256).contains(&value.len()) && value.bytes().all(|b| (32..=126).contains(&b))
}

pub(crate) fn valid_path(value: &str) -> bool {
    if !(1..=240).contains(&value.len()) || !value.bytes().all(|b| (32..=126).contains(&b))
        || value.bytes().any(|b| b"\\:%<>|?*\"".contains(&b)) { return false; }
    value.split('/').all(|part| {
        let stem = part.split('.').next().unwrap_or("").to_ascii_uppercase();
        let device = matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
            || (stem.len() == 4 && (stem.starts_with("COM") || stem.starts_with("LPT"))
                && (b'1'..=b'9').contains(&stem.as_bytes()[3]));
        !part.is_empty() && !matches!(part, "." | "..") && !part.starts_with(' ')
            && !part.ends_with(['.', ' ']) && !device
    })
}

fn valid_prefix(value: &str) -> bool {
    let b = value.as_bytes();
    (4..=128).contains(&b.len()) && b[0].is_ascii_alphabetic()
        && b":/_-".contains(&b[b.len() - 1])
        && b[1..b.len() - 1].iter().all(|c| c.is_ascii_alphanumeric() || b"_.-".contains(c))
}

struct Pattern { before: String, after: String, max_digits: usize }

fn literal_pattern(value: &str) -> Result<String, ArchiveError> {
    let mut result = String::new();
    let mut chars = value.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            if chars.next() != Some('.') { return Err(ArchiveError::PolicyViolation); }
            result.push('.');
        } else if c.is_ascii_alphanumeric() || "_@/ -".contains(c) { result.push(c); }
        else { return Err(ArchiveError::PolicyViolation); }
    }
    Ok(result)
}

fn pattern_parts(value: &str) -> Result<Pattern, ArchiveError> {
    if !(1..=512).contains(&value.len()) { return Err(ArchiveError::PolicyViolation); }
    let mut found = None;
    for (token, max_digits) in [("[1-9][0-9]{0,18}", 19), ("[1-9][0-9]{0,19}", 20)] {
        if value.match_indices(token).count() == 1 {
            if found.is_some() { return Err(ArchiveError::PolicyViolation); }
            let (before, after) = value.split_once(token).ok_or(ArchiveError::PolicyViolation)?;
            found = Some(Pattern { before: literal_pattern(before)?, after: literal_pattern(after)?, max_digits });
        }
    }
    let pattern = found.ok_or(ArchiveError::PolicyViolation)?;
    if pattern.before.is_empty() && pattern.after.is_empty() { return Err(ArchiveError::PolicyViolation); }
    Ok(pattern)
}

fn matches_pattern(value: &str, patterns: &[String]) -> Result<bool, ArchiveError> {
    for raw in patterns {
        let pattern = pattern_parts(raw)?;
        if let Some(middle) = value.strip_prefix(&pattern.before).and_then(|s| s.strip_suffix(&pattern.after)) {
            if !middle.is_empty() && middle.len() <= pattern.max_digits && !middle.starts_with('0')
                && middle.bytes().all(|b| b.is_ascii_digit())
                && middle.parse::<u64>().is_ok_and(|number| number > 0) { return Ok(true); }
        }
    }
    Ok(false)
}

impl ArchivePolicy {
    pub fn validate(&self) -> Result<(), ArchiveError> {
        for (values, maximum, validator) in [
            (&self.credential_names, MAX_CREDENTIALS, valid_name as fn(&str) -> bool),
            (&self.credential_prefixes, 32, valid_prefix as fn(&str) -> bool),
            (&self.allowed_file_paths, MAX_FILES, valid_path as fn(&str) -> bool),
        ] {
            let mut seen = HashSet::new();
            if values.len() > maximum || values.iter().any(|s| !validator(s) || !seen.insert(s.to_ascii_lowercase())) {
                return Err(ArchiveError::PolicyViolation);
            }
        }
        for (patterns, validator) in [
            (&self.credential_name_patterns, valid_name as fn(&str) -> bool),
            (&self.file_path_patterns, valid_path as fn(&str) -> bool),
        ] {
            if patterns.len() > 32 { return Err(ArchiveError::PolicyViolation); }
            let mut seen = HashSet::new();
            for raw in patterns {
                let p = pattern_parts(raw)?;
                if !seen.insert(raw) || !validator(&format!("{}1{}", p.before, p.after)) {
                    return Err(ArchiveError::PolicyViolation);
                }
            }
        }
        Ok(())
    }
}

struct StrictValue { depth: usize }
impl<'de> DeserializeSeed<'de> for StrictValue {
    type Value = Value;
    fn deserialize<D: serde::Deserializer<'de>>(self, deserializer: D) -> Result<Value, D::Error> {
        if self.depth > MAX_JSON_DEPTH { return Err(de::Error::custom("depth_limit")); }
        deserializer.deserialize_any(self)
    }
}
impl<'de> Visitor<'de> for StrictValue {
    type Value = Value;
    fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result { formatter.write_str("bounded JSON value") }
    fn visit_bool<E: de::Error>(self, value: bool) -> Result<Value, E> { Ok(Value::Bool(value)) }
    fn visit_i64<E: de::Error>(self, value: i64) -> Result<Value, E> { Ok(Value::Number(value.into())) }
    fn visit_u64<E: de::Error>(self, value: u64) -> Result<Value, E> { Ok(Value::Number(value.into())) }
    fn visit_f64<E: de::Error>(self, value: f64) -> Result<Value, E> {
        Number::from_f64(value).map(Value::Number).ok_or_else(|| de::Error::custom("non_finite"))
    }
    fn visit_str<E: de::Error>(self, value: &str) -> Result<Value, E> { Ok(Value::String(value.to_owned())) }
    fn visit_string<E: de::Error>(self, value: String) -> Result<Value, E> { Ok(Value::String(value)) }
    fn visit_unit<E: de::Error>(self) -> Result<Value, E> { Ok(Value::Null) }
    fn visit_seq<A: SeqAccess<'de>>(self, mut seq: A) -> Result<Value, A::Error> {
        let mut values = OwnedValue(Value::Array(Vec::new()));
        while let Some(value) = seq.next_element_seed(StrictValue { depth: self.depth + 1 })? {
            if let Value::Array(items) = &mut values.0 { items.push(value); }
        }
        Ok(std::mem::take(&mut values.0))
    }
    fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Value, A::Error> {
        let mut values = OwnedValue(Value::Object(Map::new()));
        while let Some(key) = map.next_key::<String>()? {
            let mut key = Zeroizing::new(key);
            if values.0.as_object().is_some_and(|items| items.contains_key(key.as_str())) { return Err(de::Error::custom("duplicate_key")); }
            let value = map.next_value_seed(StrictValue { depth: self.depth + 1 })?;
            if let Value::Object(items) = &mut values.0 { items.insert(std::mem::take(&mut *key), value); }
        }
        Ok(std::mem::take(&mut values.0))
    }
}

/// Duplicate keys, invalid UTF-8, non-finite numbers, trailing data and nesting
/// deeper than 64 levels are rejected. Unknown caller error strings never escape.
pub fn parse_json(data: &[u8], code: &'static str) -> Result<Value, ArchiveError> {
    // Generic bridge JSON can wrap a 6 MiB archive in base64. Archive/header
    // callers enforce their tighter limits before reaching this parser.
    if data.len() > MAX_JSON_BYTES { return Err(ArchiveError::TooLarge); }
    let error = ArchiveError::from_code(code);
    let mut decoder = serde_json::Deserializer::from_slice(data);
    let mut value = OwnedValue(StrictValue { depth: 0 }.deserialize(&mut decoder).map_err(|_| error)?);
    decoder.end().map_err(|_| error)?;
    Ok(std::mem::take(&mut value.0))
}

pub(crate) fn canonical(value: &Value) -> Result<Vec<u8>, ArchiveError> {
    fn write(value: &Value, output: &mut Vec<u8>, depth: usize) -> Result<(), ArchiveError> {
        if depth > MAX_JSON_DEPTH { return Err(ArchiveError::InvalidPayload); }
        match value {
            Value::Object(map) => {
                output.push(b'{');
                let mut keys: Vec<_> = map.keys().collect(); keys.sort();
                for (index, key) in keys.into_iter().enumerate() {
                    if index > 0 { output.push(b','); }
                    serde_json::to_writer(&mut *output, key).map_err(|_| ArchiveError::InvalidPayload)?;
                    output.push(b':'); write(&map[key], output, depth + 1)?;
                }
                output.push(b'}');
            }
            Value::Array(values) => {
                output.push(b'[');
                for (index, item) in values.iter().enumerate() {
                    if index > 0 { output.push(b','); } write(item, output, depth + 1)?;
                }
                output.push(b']');
            }
            _ => serde_json::to_writer(&mut *output, value).map_err(|_| ArchiveError::InvalidPayload)?,
        }
        Ok(())
    }
    let mut result = Zeroizing::new(Vec::new()); write(value, &mut result, 0)?; Ok(std::mem::take(&mut *result))
}

fn exact(value: &Value, keys: &[&str]) -> bool {
    value.as_object().is_some_and(|map| map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key)))
}

pub(crate) fn decode_base64(value: &Value, maximum: usize, code: ArchiveError) -> Result<Zeroizing<Vec<u8>>, ArchiveError> {
    let text = value.as_str().ok_or(code)?;
    if text.len() > 4 * maximum.div_ceil(3) { return Err(ArchiveError::TooLarge); }
    let data = Zeroizing::new(STANDARD.decode(text).map_err(|_| code)?);
    if data.len() > maximum { return Err(ArchiveError::TooLarge); }
    if Zeroizing::new(STANDARD.encode(&*data)).as_str() != text { return Err(code); }
    Ok(data)
}

pub(crate) fn password_bytes(value: &str) -> Result<&[u8], ArchiveError> {
    if value.contains('\0') || !(12..=1024).contains(&value.len()) { return Err(ArchiveError::InvalidPassword); }
    Ok(value.as_bytes())
}

fn validate_named_records(records: &[Value], name_key: &str, data_key: &str,
    validator: fn(&str) -> bool, explicit: &[String], patterns: &[String], prefixes: &[String],
    total: &mut usize) -> Result<(), ArchiveError> {
    let mut seen = HashSet::new();
    for record in records {
        if !exact(record, &[name_key, data_key]) { return Err(ArchiveError::InvalidPayload); }
        let name = record[name_key].as_str().ok_or(ArchiveError::InvalidPayload)?;
        if !validator(name) || !seen.insert(name.to_ascii_lowercase()) { return Err(ArchiveError::InvalidPayload); }
        if !explicit.iter().any(|item| item == name)
            && !prefixes.iter().any(|prefix| name.starts_with(prefix) && name.len() > prefix.len())
            && !matches_pattern(name, patterns)? { return Err(ArchiveError::PolicyViolation); }
        *total += decode_base64(&record[data_key], MAX_SECRET_BYTES - *total, ArchiveError::InvalidPayload)?.len();
    }
    Ok(())
}

/// Borrowed precheck for the streaming container: limit/validate credential
/// records before constructing any owned clean manifest or cloning blobs.
pub(crate) fn validate_credential_records(records: &[Value], policy: &ArchivePolicy) -> Result<(), ArchiveError> {
    policy.validate()?;
    if records.len() > MAX_CREDENTIALS { return Err(ArchiveError::TooLarge); }
    let mut total = 0;
    validate_named_records(records, "name", "blob", valid_name, &policy.credential_names,
        &policy.credential_name_patterns, &policy.credential_prefixes, &mut total)
}

pub fn validate_payload(payload: &Value, policy: &ArchivePolicy) -> Result<Value, ArchiveError> {
    policy.validate()?;
    if !exact(payload, &["schema", "sourceClient", "sourceVersion", "credentials", "files"])
        || payload["schema"].as_u64() != Some(1) || !payload["sourceClient"].is_string()
        || !payload["sourceVersion"].is_string() { return Err(ArchiveError::InvalidPayload); }
    if payload["sourceClient"] != SOURCE_CLIENT || payload["sourceVersion"] != SOURCE_VERSION { return Err(ArchiveError::UnsupportedVersion); }
    let credentials = payload["credentials"].as_array().ok_or(ArchiveError::InvalidPayload)?;
    let files = payload["files"].as_array().ok_or(ArchiveError::InvalidPayload)?;
    if credentials.len() > MAX_CREDENTIALS || files.len() > MAX_FILES { return Err(ArchiveError::TooLarge); }
    let mut total = 0;
    for (records, name_key, data_key, validator, explicit, patterns, prefixes) in [
        (credentials, "name", "blob", valid_name as fn(&str) -> bool, &policy.credential_names, &policy.credential_name_patterns, policy.credential_prefixes.as_slice()),
        (files, "path", "data", valid_path as fn(&str) -> bool, &policy.allowed_file_paths, &policy.file_path_patterns, &[][..]),
    ] {
        validate_named_records(records, name_key, data_key, validator, explicit, patterns, prefixes, &mut total)?;
    }
    // All keys/record shapes are exact, so the input already is the clean shape.
    let encoded = Zeroizing::new(canonical(payload)?);
    if encoded.len() > MAX_PLAINTEXT_BYTES { return Err(ArchiveError::TooLarge); }
    Ok(payload.clone())
}

pub(crate) fn derive(password: &[u8], salt: &[u8]) -> Result<Zeroizing<[u8; 32]>, ArchiveError> {
    let params = scrypt::Params::new(15, 8, 1, 32).map_err(|_| ArchiveError::CryptoUnavailable)?;
    let mut key = Zeroizing::new([0u8; 32]);
    scrypt::scrypt(password, salt, &params, key.as_mut()).map_err(|_| ArchiveError::CryptoUnavailable)?;
    Ok(key)
}

pub fn encrypt(payload: &Value, password: &str, policy: &ArchivePolicy) -> Result<Vec<u8>, ArchiveError> {
    let password = password_bytes(password)?;
    let clean = OwnedValue(validate_payload(payload, policy)?);
    let plaintext = Zeroizing::new(canonical(&clean.0)?);
    let mut salt = [0u8; 16]; let mut nonce = [0u8; 12];
    getrandom::getrandom(&mut salt).map_err(|_| ArchiveError::CryptoUnavailable)?;
    getrandom::getrandom(&mut nonce).map_err(|_| ArchiveError::CryptoUnavailable)?;
    let header = json!({"schema":1,"sourceClient":SOURCE_CLIENT,"sourceVersion":SOURCE_VERSION,
        "cipher":"AES-256-GCM","plaintextBytes":plaintext.len(),
        "kdf":{"name":"scrypt","n":32768,"r":8,"p":1,"salt":STANDARD.encode(salt)},
        "nonce":STANDARD.encode(nonce)});
    let encoded = canonical(&header)?;
    let mut aad = Vec::with_capacity(MAGIC.len() + 2 + encoded.len());
    aad.extend_from_slice(MAGIC); aad.extend_from_slice(&(encoded.len() as u16).to_be_bytes()); aad.extend_from_slice(&encoded);
    let key = derive(password, &salt)?;
    let cipher = Aes256Gcm::new_from_slice(key.as_ref()).map_err(|_| ArchiveError::CryptoUnavailable)?;
    let encrypted = cipher.encrypt(Nonce::from_slice(&nonce), Payload { msg: &plaintext, aad: &aad })
        .map_err(|_| ArchiveError::CryptoUnavailable)?;
    aad.extend_from_slice(&encrypted);
    Ok(aad)
}

struct Header<'a> { salt: Zeroizing<Vec<u8>>, nonce: Zeroizing<Vec<u8>>, aad: &'a [u8], ciphertext: &'a [u8] }
fn header(archive: &[u8]) -> Result<Header<'_>, ArchiveError> {
    if archive.len() > MAX_ARCHIVE_BYTES { return Err(ArchiveError::TooLarge); }
    let prefix = MAGIC.len() + 2;
    if archive.len() < prefix + 2 + 16 || !archive.starts_with(MAGIC) { return Err(ArchiveError::InvalidArchive); }
    let size = u16::from_be_bytes([archive[MAGIC.len()], archive[MAGIC.len() + 1]]) as usize;
    if !(2..=MAX_HEADER_BYTES).contains(&size) || prefix + size + 16 > archive.len() { return Err(ArchiveError::InvalidArchive); }
    let raw = &archive[prefix..prefix + size];
    let value = parse_json(raw, "invalid_archive")?;
    if !exact(&value, &["schema","sourceClient","sourceVersion","cipher","plaintextBytes","kdf","nonce"])
        || value["schema"].as_u64() != Some(1) || !value["sourceClient"].is_string()
        || !value["sourceVersion"].is_string() || value["cipher"] != "AES-256-GCM"
        || !value["plaintextBytes"].as_u64().is_some_and(|n| (1..=MAX_PLAINTEXT_BYTES as u64).contains(&n)) {
        return Err(ArchiveError::InvalidArchive);
    }
    if value["sourceClient"] != SOURCE_CLIENT || value["sourceVersion"] != SOURCE_VERSION { return Err(ArchiveError::UnsupportedVersion); }
    let kdf = &value["kdf"];
    if !exact(kdf, &["name","n","r","p","salt"]) || kdf["name"] != "scrypt"
        || kdf["n"].as_u64() != Some(32768) || kdf["r"].as_u64() != Some(8) || kdf["p"].as_u64() != Some(1) { return Err(ArchiveError::InvalidArchive); }
    let salt = decode_base64(&kdf["salt"], 16, ArchiveError::InvalidArchive)?;
    let nonce = decode_base64(&value["nonce"], 12, ArchiveError::InvalidArchive)?;
    if salt.len() != 16 || nonce.len() != 12 || canonical(&value)? != raw
        || archive.len() - prefix - size != value["plaintextBytes"].as_u64().ok_or(ArchiveError::InvalidArchive)? as usize + 16 {
        return Err(ArchiveError::InvalidArchive);
    }
    Ok(Header { salt, nonce, aad: &archive[..prefix + size], ciphertext: &archive[prefix + size..] })
}

pub fn decrypt(archive: &[u8], password: &str, policy: &ArchivePolicy) -> Result<Value, ArchiveError> {
    policy.validate()?;
    let password = password_bytes(password)?;
    let frame = header(archive)?;
    let key = derive(password, &frame.salt)?;
    let cipher = Aes256Gcm::new_from_slice(key.as_ref()).map_err(|_| ArchiveError::CryptoUnavailable)?;
    let plaintext = Zeroizing::new(cipher.decrypt(Nonce::from_slice(&frame.nonce), Payload { msg: frame.ciphertext, aad: frame.aad })
        .map_err(|_| ArchiveError::AuthenticationFailed)?);
    let value = OwnedValue(parse_json(&plaintext, "invalid_payload")?);
    validate_payload(&value.0, policy)
}
