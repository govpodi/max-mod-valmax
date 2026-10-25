//! Pure validation of the compact, login-only MData manifest.
//! No credential store, profile discovery, file reads, token parsing or import.
//! Tokens are opaque caller-owned buffers; results contain only IDs/file sizes.

use crate::session_archive::{self as session, OwnedValue};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

pub const MAX_AUTH_BYTES: usize = 256 * 1024;
pub const MAX_ACCOUNTS: usize = 3;
pub const MAX_BLOB: usize = 2560;
pub const MAX_MANIFEST_JSON_BYTES: usize = 32 * 1024;
pub type LoginTokens = BTreeMap<String, Zeroizing<Vec<u8>>>;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LoginError {
    InvalidRequest,
    InvalidPayload,
    AccountLimit,
    NoAccounts,
    TooLarge,
}
impl LoginError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidPayload => "invalid_payload",
            Self::AccountLimit => "account_limit",
            Self::NoAccounts => "no_accounts",
            Self::TooLarge => "too_large",
        }
    }
}
impl fmt::Display for LoginError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for LoginError {}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AuthFile {
    pub path: String,
    pub size: usize,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ValidatedLogin {
    pub account_ids: Vec<String>,
    pub files: Vec<AuthFile>,
}

pub fn account_id(value: &str) -> bool {
    (1..=19).contains(&value.len())
        && !value.starts_with('0')
        && value.bytes().all(|byte| byte.is_ascii_digit())
        && value
            .parse::<u64>()
            .is_ok_and(|number| number <= i64::MAX as u64)
}
pub fn auth_id(value: &str) -> Option<&str> {
    value.strip_prefix("auth_").filter(|id| account_id(id))
}
pub fn token_id(value: &str) -> Option<&str> {
    value
        .strip_prefix("access_token_")
        .and_then(|id| id.strip_suffix("@MXMOD"))
        .filter(|id| account_id(id))
}
fn exact(value: &Value, keys: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|map| map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key)))
}
fn sorted_ids<'a>(ids: impl IntoIterator<Item = &'a str>) -> Vec<String> {
    let mut values: Vec<_> = ids.into_iter().map(str::to_owned).collect();
    // All callers first check account_id. Decimal length then byte ordering is
    // exact numeric order without any floating-point or signed conversion.
    values.sort_by(|a, b| a.len().cmp(&b.len()).then_with(|| a.cmp(b)));
    values
}
/// Explicit nonempty selection, validated before any future native reads.
pub fn selected_account_ids(value: &Value) -> Result<Vec<String>, LoginError> {
    let list = value.as_array().ok_or(LoginError::InvalidRequest)?;
    if !(1..=MAX_ACCOUNTS).contains(&list.len()) {
        return Err(LoginError::InvalidRequest);
    }
    let mut ids = BTreeSet::new();
    for item in list {
        let id = item
            .as_str()
            .filter(|id| account_id(id))
            .ok_or(LoginError::InvalidRequest)?;
        if !ids.insert(id) {
            return Err(LoginError::InvalidRequest);
        }
    }
    Ok(sorted_ids(ids))
}
/// Raw JSON entry point rejects duplicate keys, invalid UTF-8, non-finite
/// numbers and depth >64. The compact format has no legitimate need for a
/// larger manifest; the byte limit also bounds whitespace and unknown input.
pub fn validate_json(json: &[u8], tokens: &LoginTokens) -> Result<ValidatedLogin, LoginError> {
    if json.len() > MAX_MANIFEST_JSON_BYTES {
        return Err(LoginError::TooLarge);
    }
    let parsed = OwnedValue(
        session::parse_json(json, "invalid_payload").map_err(|_| LoginError::InvalidPayload)?,
    );
    validate(&parsed.0, tokens)
}
pub fn validate(manifest: &Value, tokens: &LoginTokens) -> Result<ValidatedLogin, LoginError> {
    if !exact(
        manifest,
        &[
            "schema",
            "sourceClient",
            "sourceVersion",
            "files",
            "credentials",
        ],
    ) || manifest["schema"].as_u64() != Some(1)
        || manifest["sourceClient"].as_str() != Some(session::SOURCE_CLIENT)
        || manifest["sourceVersion"].as_str() != Some(session::SOURCE_VERSION)
        || !manifest["files"].is_array()
        || !manifest["credentials"].is_array()
    {
        return Err(LoginError::InvalidPayload);
    }
    let credentials = manifest["credentials"].as_array().unwrap();
    if tokens.len() > MAX_ACCOUNTS || credentials.len() > MAX_ACCOUNTS {
        return Err(LoginError::AccountLimit);
    }
    if tokens.is_empty() {
        return Err(LoginError::NoAccounts);
    }
    let mut ids = BTreeSet::new();
    for (name, blob) in tokens {
        ids.insert(token_id(name).ok_or(LoginError::InvalidPayload)?);
        if !(1..=MAX_BLOB).contains(&blob.len()) {
            return Err(LoginError::InvalidPayload);
        }
    }
    let mut seen_tokens = BTreeSet::new();
    for record in credentials {
        if !exact(record, &["name", "blob"]) {
            return Err(LoginError::InvalidPayload);
        }
        let name = record["name"]
            .as_str()
            .filter(|name| token_id(name).is_some())
            .ok_or(LoginError::InvalidPayload)?;
        if !seen_tokens.insert(name) {
            return Err(LoginError::InvalidPayload);
        }
        let encoded = record["blob"].as_str().ok_or(LoginError::InvalidPayload)?;
        if encoded.len() > 4 * MAX_BLOB.div_ceil(3) || !encoded.is_ascii() {
            return Err(LoginError::InvalidPayload);
        }
        let decoded = Zeroizing::new(
            STANDARD
                .decode(encoded)
                .map_err(|_| LoginError::InvalidPayload)?,
        );
        if !(1..=MAX_BLOB).contains(&decoded.len()) {
            return Err(LoginError::InvalidPayload);
        }
        let canonical = Zeroizing::new(STANDARD.encode(&decoded));
        if canonical.as_str() != encoded {
            return Err(LoginError::InvalidPayload);
        }
        let original = tokens.get(name).ok_or(LoginError::InvalidPayload)?;
        if !bool::from(decoded.as_slice().ct_eq(original.as_slice())) {
            return Err(LoginError::InvalidPayload);
        }
    }
    if seen_tokens.len() != tokens.len() {
        return Err(LoginError::InvalidPayload);
    }
    let files = manifest["files"].as_array().unwrap();
    if files.len() > MAX_ACCOUNTS {
        return Err(LoginError::InvalidPayload);
    }
    let mut seen_files = BTreeSet::new();
    let mut approved = Vec::new();
    for file in files {
        if !exact(file, &["path", "size"]) {
            return Err(LoginError::InvalidPayload);
        }
        let path = file["path"].as_str().ok_or(LoginError::InvalidPayload)?;
        let id = auth_id(path).ok_or(LoginError::InvalidPayload)?;
        let size = file["size"]
            .as_u64()
            .filter(|size| (1..=MAX_AUTH_BYTES as u64).contains(size))
            .ok_or(LoginError::InvalidPayload)?;
        if !ids.contains(id) || !seen_files.insert(path) {
            return Err(LoginError::InvalidPayload);
        }
        approved.push(AuthFile {
            path: path.into(),
            size: size as usize,
        });
    }
    Ok(ValidatedLogin {
        account_ids: sorted_ids(ids),
        files: approved,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn fixture() -> (Value, LoginTokens) {
        let tokens = [(
            "access_token_2@MXMOD".into(),
            Zeroizing::new(b"synthetic".to_vec()),
        )]
        .into();
        (
            json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
            "files":[{"path":"auth_2","size":1}],"credentials":[{"name":"access_token_2@MXMOD","blob":STANDARD.encode(b"synthetic")}]}),
            tokens,
        )
    }
    #[test]
    fn selection_exact_numeric_order_and_types() {
        assert_eq!(
            selected_account_ids(&json!(["9223372036854775807", "2", "9007199254740993"])).unwrap(),
            vec!["2", "9007199254740993", "9223372036854775807"]
        );
        for value in [
            json!([]),
            json!(null),
            json!({}),
            json!("2"),
            json!([2]),
            json!([true]),
            json!(["2", "2"]),
            json!(["1", "2", "3", "4"]),
        ] {
            assert_eq!(
                selected_account_ids(&value),
                Err(LoginError::InvalidRequest)
            );
        }
        for id in [
            "",
            "0",
            "01",
            "+1",
            "-1",
            " 1",
            "1 ",
            "1.0",
            "1e2",
            "１",
            "9223372036854775808",
            "12345678901234567890",
        ] {
            assert!(!account_id(id));
            assert_eq!(
                selected_account_ids(&json!([id])),
                Err(LoginError::InvalidRequest)
            );
        }
    }
    #[test]
    fn valid_optional_files_and_opaque_tokens() {
        let (mut m, t) = fixture();
        assert_eq!(validate(&m, &t).unwrap().account_ids, vec!["2"]);
        m["files"] = json!([]);
        assert!(validate(&m, &t).is_ok());
        m["files"] = json!([{"path":"auth_2","size":MAX_AUTH_BYTES}]);
        assert!(validate(&m, &t).is_ok());
        assert_eq!(
            validate_json(&serde_json::to_vec(&m).unwrap(), &t),
            validate(&m, &t)
        );
    }
    #[test]
    fn root_shape_and_strict_integer_types() {
        let (m, t) = fixture();
        for (field, value) in [
            ("schema", json!(true)),
            ("schema", json!(1.0)),
            ("schema", json!("1")),
            ("sourceClient", json!(1)),
            ("sourceVersion", json!("other")),
            ("files", json!({})),
            ("credentials", json!(null)),
        ] {
            let mut changed = m.clone();
            changed[field] = value;
            assert_eq!(validate(&changed, &t), Err(LoginError::InvalidPayload));
        }
        let mut extra = m.clone();
        extra["unknown"] = json!(0);
        assert_eq!(validate(&extra, &t), Err(LoginError::InvalidPayload));
        let mut missing = m;
        missing.as_object_mut().unwrap().remove("files");
        assert_eq!(validate(&missing, &t), Err(LoginError::InvalidPayload));
    }
    #[test]
    fn account_limits_and_empty_order() {
        let (mut m, t) = fixture();
        assert_eq!(
            validate(&m, &LoginTokens::new()),
            Err(LoginError::NoAccounts)
        );
        m["credentials"] = json!([{}, {}, {}, {}]);
        assert_eq!(
            validate(&m, &LoginTokens::new()),
            Err(LoginError::AccountLimit)
        );
        let (_, mut many) = fixture();
        for id in [3, 4, 5] {
            many.insert(format!("access_token_{id}@MXMOD"), Zeroizing::new(vec![1]));
        }
        assert_eq!(validate(&fixture().0, &many), Err(LoginError::AccountLimit));
        m["credentials"] = json!([]);
        assert_eq!(validate(&m, &t), Err(LoginError::InvalidPayload));
    }
    #[test]
    fn credential_canonical_base64_and_binding() {
        let (m, t) = fixture();
        for blob in [
            json!(""),
            json!("YQ==\n"),
            json!("YR=="),
            json!("YQ"),
            json!("☃"),
            json!(7),
            json!(STANDARD.encode(vec![1; MAX_BLOB + 1])),
        ] {
            let mut changed = m.clone();
            changed["credentials"][0]["blob"] = blob;
            assert_eq!(validate(&changed, &t), Err(LoginError::InvalidPayload));
        }
        let mut wrong = m.clone();
        wrong["credentials"][0]["blob"] = json!(STANDARD.encode(b"other"));
        assert_eq!(validate(&wrong, &t), Err(LoginError::InvalidPayload));
        let mut duplicate = m.clone();
        duplicate["credentials"]
            .as_array_mut()
            .unwrap()
            .push(m["credentials"][0].clone());
        assert_eq!(validate(&duplicate, &t), Err(LoginError::InvalidPayload));
        for name in [
            "access_token_2@ONEME",
            "access_token_02@MXMOD",
            "history_storage_key_2@MXMOD",
            "access_token_9223372036854775808@MXMOD",
        ] {
            let mut changed = m.clone();
            changed["credentials"][0]["name"] = json!(name);
            assert_eq!(validate(&changed, &t), Err(LoginError::InvalidPayload));
        }
        for length in [0, MAX_BLOB + 1] {
            let bad = [(
                "access_token_2@MXMOD".into(),
                Zeroizing::new(vec![1; length]),
            )]
            .into();
            assert_eq!(validate(&m, &bad), Err(LoginError::InvalidPayload));
        }
        let mut max = m;
        let max_tokens = [(
            "access_token_2@MXMOD".into(),
            Zeroizing::new(vec![0; MAX_BLOB]),
        )]
        .into();
        max["credentials"][0]["blob"] = json!(STANDARD.encode(vec![0; MAX_BLOB]));
        assert!(validate(&max, &max_tokens).is_ok());
    }
    #[test]
    fn auth_file_scope_size_and_duplicates() {
        let (m, t) = fixture();
        for file in [
            json!({"path":"auth_3","size":1}),
            json!({"path":"auth_2/x","size":1}),
            json!({"path":"auth_02","size":1}),
            json!({"path":"auth_2","size":true}),
            json!({"path":"auth_2","size":1.0}),
            json!({"path":"auth_2","size":0}),
            json!({"path":"auth_2","size":MAX_AUTH_BYTES+1}),
            json!({"path":"auth_2","size":1,"extra":0}),
        ] {
            let mut changed = m.clone();
            changed["files"] = json!([file]);
            assert_eq!(validate(&changed, &t), Err(LoginError::InvalidPayload));
        }
        let mut duplicate = m;
        let file = duplicate["files"][0].clone();
        duplicate["files"].as_array_mut().unwrap().push(file);
        assert_eq!(validate(&duplicate, &t), Err(LoginError::InvalidPayload));
    }
    #[test]
    fn duplicate_json_utf8_trailing_and_bound() {
        let (m, t) = fixture();
        let raw = serde_json::to_string(&m).unwrap();
        let duplicate = raw.replacen("\"schema\":1", "\"schema\":1,\"schema\":1", 1);
        assert_eq!(
            validate_json(duplicate.as_bytes(), &t),
            Err(LoginError::InvalidPayload)
        );
        assert_eq!(
            validate_json(format!("{raw} null").as_bytes(), &t),
            Err(LoginError::InvalidPayload)
        );
        assert_eq!(validate_json(&[0xff], &t), Err(LoginError::InvalidPayload));
        assert_eq!(
            validate_json(&vec![b' '; MAX_MANIFEST_JSON_BYTES + 1], &t),
            Err(LoginError::TooLarge)
        );
    }
}
