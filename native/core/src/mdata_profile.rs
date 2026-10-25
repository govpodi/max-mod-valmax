//! Pure policy for full-profile MData archives and Recovery.
//!
//! This does not read a profile, acquire credentials, create staging files or
//! authorize applying an archive. Callers must first authenticate and validate
//! the full container with `mdata_archive`, then validate the relationships
//! below. The borrowed credential values are inspected only for emptiness.
use crate::{mdata_archive, mdata_login, session_archive::ArchivePolicy};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
};
use zeroize::Zeroizing;

pub const ACCOUNT_KEYS: [&str; 4] = [
    "access_token",
    "history_storage_key",
    "calls_token",
    "calls_session",
];
pub const COMMON_NAMES: [&str; 2] = [
    "app_data_storage_key_Common@MXMOD",
    "multi_state_enabled_Common@MXMOD",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProfileError {
    InvalidPayload,
    TooLarge,
}
impl ProfileError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidPayload => "invalid_payload",
            Self::TooLarge => "too_large",
        }
    }
}
impl fmt::Display for ProfileError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}
impl std::error::Error for ProfileError {}
pub type Result<T> = std::result::Result<T, ProfileError>;

/// The existing Python inclusion filter is deliberately distinct from lexical
/// path validation. Top-level helper names/prefixes are case-sensitive; the
/// logs/crashdumps components and archive extensions are case-insensitive.
pub fn included(relative: &str) -> bool {
    let mut parts = relative.split('/');
    let first = parts.next().unwrap_or("");
    if std::iter::once(first).chain(parts).any(|part| {
        matches!(
            mdata_archive::path_comparison_key(part).as_str(),
            "logs" | "crashdumps"
        )
    }) {
        return false;
    }
    !matches!(first, "mdata-result.json" | "session-transfer-result.json")
        && ![".mxsession-", ".session-result-", ".mdata-"]
            .iter()
            .any(|prefix| first.starts_with(*prefix))
        && ![".mdata", ".maxsession"]
            .iter()
            .any(|suffix| first.to_ascii_lowercase().ends_with(*suffix))
}

pub fn safe_relative(relative: &str) -> bool {
    mdata_archive::valid_file_path(relative) && included(relative)
}

/// Requires a structurally validated, bounded manifest file list. Malformed
/// input still produces a finite error rather than indexing/panicking.
pub fn profile_ids(files: &[Value]) -> Result<BTreeSet<String>> {
    let mut result = BTreeSet::new();
    for file in files {
        let path = file
            .get("path")
            .and_then(Value::as_str)
            .ok_or(ProfileError::InvalidPayload)?;
        let first = mdata_archive::path_comparison_key(path.split('/').next().unwrap_or(""));
        if let Some(id) = first
            .strip_prefix("auth_")
            .or_else(|| first.strip_prefix("user"))
        {
            if mdata_login::account_id(id) {
                result.insert(id.to_owned());
            }
        }
    }
    if result.len() > 63 {
        Err(ProfileError::TooLarge)
    } else {
        Ok(result)
    }
}

fn account_key_id(name: &str) -> Option<&str> {
    let body = name.strip_suffix("@MXMOD")?;
    ACCOUNT_KEYS.iter().find_map(|key| {
        body.strip_prefix(*key)?
            .strip_prefix('_')
            .filter(|id| mdata_login::account_id(id))
    })
}

fn web_owner(name: &str) -> Option<String> {
    if !name.is_ascii() {
        return None;
    }
    let body = name.strip_suffix("@MXMOD")?;
    // Python's greedy .{1,192} chooses the final '-' that can precede the
    // numeric suffix. Neither numeric group permits another dash/underscore.
    let (prefix, suffix) = body.rsplit_once('-')?;
    if !(1..=192).contains(&prefix.len()) || !prefix.bytes().all(|byte| (32..=126).contains(&byte))
    {
        return None;
    }
    let (left, right) = suffix.split_once('_')?;
    if !mdata_login::account_id(left)
        || !(1..=19).contains(&right.len())
        || !right.bytes().all(|byte| byte.is_ascii_digit())
        || (right != "0" && right.starts_with('0'))
    {
        return None;
    }
    let owner = left.parse::<u64>().ok()? ^ right.parse::<u64>().ok()?;
    if owner == 0 || owner > i64::MAX as u64 {
        None
    } else {
        Some(owner.to_string())
    }
}

pub fn allowed_name(name: &str) -> bool {
    COMMON_NAMES.contains(&name) || account_key_id(name).is_some() || web_owner(name).is_some()
}

pub fn owner_id(name: &str) -> Result<Option<String>> {
    if COMMON_NAMES.contains(&name) {
        return Ok(None);
    }
    if let Some(id) = account_key_id(name) {
        return Ok(Some(id.to_owned()));
    }
    web_owner(name)
        .map(Some)
        .ok_or(ProfileError::InvalidPayload)
}

/// Preserve Python lexical ordering and duplicate input IDs. This lists exact
/// names only; it does not enumerate or read the Windows credential store.
pub fn targets(ids: &[String]) -> Result<Vec<String>> {
    if ids.len() > 63 || ids.iter().any(|id| !mdata_login::account_id(id)) {
        return Err(ProfileError::InvalidPayload);
    }
    let mut sorted: Vec<_> = ids.iter().collect();
    sorted.sort();
    let mut result: Vec<_> = COMMON_NAMES.iter().map(|name| (*name).to_owned()).collect();
    for id in sorted {
        for key in ACCOUNT_KEYS {
            result.push(format!("{key}_{id}@MXMOD"));
        }
    }
    Ok(result)
}

/// Web names are approved separately by `MDataPolicy::credential_name_validator`
/// using `allowed_name`; they must not be represented by a broad name prefix.
pub fn credential_policy() -> ArchivePolicy {
    ArchivePolicy {
        credential_names: COMMON_NAMES.iter().map(|name| (*name).to_owned()).collect(),
        credential_name_patterns: ACCOUNT_KEYS
            .iter()
            .map(|key| format!("{key}_[1-9][0-9]{{0,18}}@MXMOD"))
            .collect(),
        ..Default::default()
    }
}

fn history_owner(path: &str) -> Option<&str> {
    let rest = path.strip_prefix("user")?;
    let (id, suffix) = rest.split_once("/history/")?;
    // Legacy storage validation's regex accepts 1..19 positive decimal digits,
    // even above i64::MAX. Such a path cannot have an allowed key and fails.
    if !(1..=19).contains(&id.len())
        || id.starts_with('0')
        || !id.bytes().all(|b| b.is_ascii_digit())
        || !suffix.ends_with(".db")
        || suffix.len() <= 3
        || suffix.contains('\n')
    {
        return None;
    }
    Some(id)
}

/// Relationship validation after the full archive's structural/authentication
/// checks. Zero active accounts is valid for a backup of an empty profile.
/// Account limits for an *import action* belong to its coordinator, not here.
pub fn validate_storage(
    manifest: &Value,
    credentials: &BTreeMap<String, Zeroizing<Vec<u8>>>,
) -> Result<()> {
    let files = manifest
        .get("files")
        .and_then(Value::as_array)
        .ok_or(ProfileError::InvalidPayload)?;
    let mut known = profile_ids(files)?;
    for name in credentials.keys() {
        if let Some(id) = account_key_id(name) {
            known.insert(id.to_owned());
        }
    }
    for name in credentials.keys() {
        if owner_id(name)?.is_some_and(|id| !known.contains(&id)) {
            return Err(ProfileError::InvalidPayload);
        }
    }
    for file in files {
        let path = mdata_archive::path_comparison_key(
            file.get("path")
                .and_then(Value::as_str)
                .ok_or(ProfileError::InvalidPayload)?,
        );
        if let Some(id) = history_owner(&path) {
            if !credentials
                .get(&format!("history_storage_key_{id}@MXMOD"))
                .is_some_and(|bytes| !bytes.is_empty())
            {
                return Err(ProfileError::InvalidPayload);
            }
        }
        if path.starts_with("app/")
            && path.ends_with(".db")
            && !credentials
                .get(COMMON_NAMES[0])
                .is_some_and(|bytes| !bytes.is_empty())
        {
            return Err(ProfileError::InvalidPayload);
        }
    }
    Ok(())
}

#[cfg(test)]
#[path = "mdata_profile_tests.rs"]
mod tests;
