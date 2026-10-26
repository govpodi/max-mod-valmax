//! Explicit profile-scoped native services. No implicit profile discovery.
//! The C++ owner opens one context before use and closes it after workers stop.
use crate::{
    account_preferences::AccountPreferencesStore, ghost_store::GhostSettingsStore,
    optimization_store::OptimizationSettingsStore, settings_io::SafeStorePath,
};
use serde_json::{Value, json};
use std::path::{Path, PathBuf};

const RETENTION_LOOKUP_CACHE_ENTRIES: usize = 16;
struct RetentionLookupCacheEntry {
    owner: i64,
    chat: i64,
    ids: Vec<i64>,
    marker_revision: String,
    content_revision: String,
    deleted_ids: Vec<i64>,
}

fn retention_id(value: &Value, positive: bool) -> Result<i64> {
    let raw = value.as_str().ok_or("invalid_request")?;
    let id = raw.parse::<i64>().map_err(|_| "invalid_request")?;
    if id.to_string() != raw || id == 0 || positive && id < 0 {
        return Err("invalid_request");
    }
    Ok(id)
}

fn retention_selected_ids(value: &Value) -> Result<(i64, i64, Vec<i64>)> {
    if !exact(value, &["schema", "accountId", "chatId", "messageIds"])
        || value["schema"].as_u64() != Some(1) { return Err("invalid_request"); }
    let owner = retention_id(&value["accountId"], true)?;
    let chat = retention_id(&value["chatId"], false)?;
    let values = value["messageIds"].as_array().ok_or("invalid_request")?;
    if values.is_empty() || values.len() > crate::retention_store::MAX_LOOKUP_IDS {
        return Err("invalid_request");
    }
    let mut ids = Vec::with_capacity(values.len());
    let mut unique = std::collections::HashSet::with_capacity(values.len());
    for value in values {
        let id = retention_id(value, true)?;
        if !unique.insert(id) { return Err("invalid_request"); }
        ids.push(id);
    }
    Ok((owner, chat, ids))
}

fn retention_selected_list(value: &Value, inline: bool) -> Result<(i64, i64, Option<&str>, usize, bool)> {
    let keys: &[&str] = if inline {
        &["schema", "accountId", "chatId", "cursor", "limit", "legacyOnly"]
    } else {
        &["schema", "accountId", "chatId", "cursor", "limit"]
    };
    if !exact(value, keys)
        || value["schema"].as_u64() != Some(1) { return Err("invalid_request"); }
    let owner = retention_id(&value["accountId"], true)?;
    let chat = retention_id(&value["chatId"], false)?;
    let cursor = if value["cursor"].is_null() { None } else {
        Some(value["cursor"].as_str().filter(|raw| !raw.is_empty() && raw.len() <= 256)
            .ok_or("invalid_request")?)
    };
    let limit = value["limit"].as_u64().filter(|limit| (1..=100).contains(limit))
        .ok_or("invalid_request")? as usize;
    let legacy_only = if inline { value["legacyOnly"].as_bool().ok_or("invalid_request")? }
        else { false };
    Ok((owner, chat, cursor, limit, legacy_only))
}

fn retention_selected_range(value: &Value) -> Result<(i64, i64, i64, i64, Option<&str>, usize)> {
    if !exact(value, &["schema", "accountId", "chatId", "fromOrderKey",
        "toOrderKey", "cursor", "limit"]) || value["schema"].as_u64() != Some(1) {
        return Err("invalid_request");
    }
    let owner = retention_id(&value["accountId"], true)?;
    let chat = retention_id(&value["chatId"], false)?;
    let from = retention_id(&value["fromOrderKey"], true)?;
    let to = retention_id(&value["toOrderKey"], true)?;
    if to < from { return Err("invalid_request"); }
    let cursor = if value["cursor"].is_null() { None } else {
        Some(value["cursor"].as_str().filter(|raw| !raw.is_empty() && raw.len() <= 256)
            .ok_or("invalid_request")?)
    };
    let limit = value["limit"].as_u64().filter(|limit| (1..=100).contains(limit))
        .ok_or("invalid_request")? as usize;
    Ok((owner, chat, from, to, cursor, limit))
}

fn retention_selected_marker_summary(value: &Value) -> Result<(i64, i64, &str)> {
    if !exact(value, &["schema", "accountId", "chatId", "revision"])
        || value["schema"].as_u64() != Some(1) { return Err("invalid_request"); }
    let owner = retention_id(&value["accountId"], true)?;
    let chat = retention_id(&value["chatId"], false)?;
    let revision = value["revision"].as_str().ok_or("invalid_request")?;
    if revision.len() != 64 || !revision.bytes().all(|byte|
        byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)) {
        return Err("invalid_request");
    }
    Ok((owner, chat, revision))
}

pub struct Runtime {
    profile: PathBuf,
    optimization: OptimizationSettingsStore,
    ghost: GhostSettingsStore,
    accounts: AccountPreferencesStore,
    quick_replies: crate::quick_reply_store::QuickReplyStore,
    retention_preferences: crate::retention_preferences::RetentionPreferencesStore,
    retention_ack: crate::retention_ack::RetentionAck,
    retention_lookup_cache: std::sync::Mutex<std::collections::VecDeque<RetentionLookupCacheEntry>>,
    chat_lock: crate::chat_lock::ChatLockService,
    notifications: std::sync::Arc<crate::notification_bridge::NativeNotificationProtector>,
    ghost_ack: crate::ghost_ack::GhostAck,
    exports: crate::export_service::ExportService,
    api2_preview_last: std::sync::Mutex<Option<std::time::Instant>>,
    api2_preview_cancelled: std::sync::atomic::AtomicBool,
    mdata_export: crate::mdata_export::MDataExportService,
}
type Result<T> = std::result::Result<T, &'static str>;
fn exact(value: &Value, keys: &[&str]) -> bool {
    value
        .as_object()
        .is_some_and(|map| map.len() == keys.len() && keys.iter().all(|key| map.contains_key(*key)))
}
fn selected_history_preview_scope(
    value: &Value,
) -> Result<crate::api2_history_contract::SelectedScope> {
    if !exact(value, &["schema", "ownId", "chatId"]) || value["schema"].as_u64() != Some(1) {
        return Err("invalid_request");
    }
    let own_id = value["ownId"].as_str().ok_or("invalid_request")?;
    let chat_id = value["chatId"].as_str().ok_or("invalid_request")?;
    crate::api2_history_contract::SelectedScope::parse(own_id, chat_id)
        .map_err(|_| "invalid_request")
}
#[cfg(test)]
mod api2_history_preview_tests {
    use super::selected_history_preview_scope;
    use serde_json::json;

    #[test]
    fn preview_scope_requires_exact_string_ids_and_no_caller_supplied_host_or_limit() {
        assert!(
            selected_history_preview_scope(&json!({
                "schema": 1, "ownId": "9007199254740993", "chatId": "-9007199254740995"
            }))
            .is_ok()
        );
        for value in [
            json!({"schema": 1, "ownId": 42, "chatId": "-99"}),
            json!({"schema": 1, "ownId": "42", "chatId": -99}),
            json!({"schema": 1, "ownId": "042", "chatId": "-99"}),
            json!({"schema": 1, "ownId": "42", "chatId": "-99", "host": "other.example"}),
            json!({"schema": 1, "ownId": "42", "chatId": "-99", "backward": 200}),
            json!({"schema": true, "ownId": "42", "chatId": "-99"}),
        ] {
            assert!(selected_history_preview_scope(&value).is_err());
        }
    }
}
impl Runtime {
    fn retention_lookup(&self, value: &Value) -> Result<Value> {
        let (owner, chat, ids) = retention_selected_ids(value)?;
        let store = crate::retention_store::RetentionStore::new(&self.profile)
            .map_err(|error| error.code())?;
        // Both revisions cover the two durable writes in the deletion gate.
        // A marker from the old ID-only implementation is not enough to claim
        // the original text was saved; every reported ID must also decrypt.
        let marker_revision = store.marker_revision().map_err(|error| error.code())?;
        let content_revision = store.content_revision().map_err(|error| error.code())?;
        let cached = {
            let mut cache = self.retention_lookup_cache.lock().map_err(|_| "store_busy")?;
            let match_index = cache.iter().position(|entry| entry.owner == owner
                && entry.chat == chat && entry.ids == ids
                && entry.marker_revision == marker_revision
                && entry.content_revision == content_revision);
            match_index.and_then(|index| cache.remove(index)).map(|entry| {
                let deleted = entry.deleted_ids.clone();
                cache.push_back(entry);
                deleted
            })
        };
        let deleted = if let Some(value) = cached { value } else {
            let marker = store.lookup(owner, chat, &ids).map_err(|error| error.code())?;
            if marker.revision != marker_revision { return Err("store_busy"); }
            let matching = if marker.deleted_ids.is_empty() { Vec::new() } else {
                store.lookup_text(owner, chat, &marker.deleted_ids)
                    .map_err(|error| error.code())?
                    .into_iter().map(|row| row.id).collect()
            };
            if store.content_revision().map_err(|error| error.code())? != content_revision
                || store.marker_revision().map_err(|error| error.code())? != marker_revision {
                return Err("store_busy");
            }
            let mut cache = self.retention_lookup_cache.lock().map_err(|_| "store_busy")?;
            cache.retain(|entry| !(entry.owner == owner && entry.chat == chat && entry.ids == ids));
            if cache.len() >= RETENTION_LOOKUP_CACHE_ENTRIES { cache.pop_front(); }
            cache.push_back(RetentionLookupCacheEntry {
                owner, chat, ids: ids.clone(), marker_revision: marker_revision.clone(),
                content_revision: content_revision.clone(), deleted_ids: matching.clone(),
            });
            matching
        };
        // A damaged toggle must never claim live capture, but it must not
        // hide already durable, decryptable text in this selected route.
        let enabled = self.retention_status().ok()
            .and_then(|snapshot| snapshot["runtime"]["enabled"].as_bool())
            .unwrap_or(false);
        let revision = crate::settings_io::sha256(format!(
            "valmax-retention-visible-v1:{content_revision}:{marker_revision}"
        ).as_bytes());
        Ok(json!({"schema":1,"accountId":owner.to_string(),"chatId":chat.to_string(),
            "deletedIds":deleted.into_iter().map(|id| id.to_string()).collect::<Vec<_>>(),
            "enabled":enabled,"ready":true,"revision":revision}))
    }

    fn retention_list(&self, value: &Value) -> Result<Value> {
        let (owner, chat, cursor, limit, _) = retention_selected_list(value, false)?;
        let store = crate::retention_store::RetentionStore::new(&self.profile)
            .map_err(|error| error.code())?;
        let page = store.list_deleted_text(owner, chat, cursor, limit)
            .map_err(|error| error.code())?;
        let items = page.items.into_iter().map(|row| json!({
            "receivedAtMs": row.received_at_ms,
            "deletedAtMs": row.deleted_at_ms,
            "text": row.text.as_str(),
        })).collect::<Vec<_>>();
        Ok(json!({"schema":1,"accountId":owner.to_string(),"chatId":chat.to_string(),
            "items":items,"nextCursor":page.next_cursor,"revision":page.revision}))
    }

    fn retention_inline(&self, value: &Value) -> Result<Value> {
        let (owner, chat, cursor, limit, legacy_only) = retention_selected_list(value, true)?;
        let store = crate::retention_store::RetentionStore::new(&self.profile)
            .map_err(|error| error.code())?;
        let page = if legacy_only {
            store.list_deleted_text_legacy(owner, chat, cursor, limit)
        } else {
            store.list_deleted_text(owner, chat, cursor, limit)
        }
            .map_err(|error| error.code())?;
        let items = page.items.into_iter().map(|row| json!({
            "messageId": row.id.to_string(),
            "orderKey": row.order_key.map(|value| value.to_string()),
            // The persisted p11 receipt time is the last local capture time,
            // not the server's original message time. Never mislabel it.
            "sentAtMs": null,
            "receivedAtMs": row.received_at_ms,
            "deletedAtMs": row.deleted_at_ms,
            "text": row.text.as_str(),
        })).collect::<Vec<_>>();
        Ok(json!({"schema":1,"accountId":owner.to_string(),"chatId":chat.to_string(),
            "items":items,"nextCursor":page.next_cursor,"revision":page.revision}))
    }

    fn retention_range(&self, value: &Value) -> Result<Value> {
        let (owner, chat, from, to, cursor, limit) = retention_selected_range(value)?;
        let store = crate::retention_store::RetentionStore::new(&self.profile)
            .map_err(|error| error.code())?;
        let page = store.list_deleted_text_range(owner, chat, from, to, cursor, limit)
            .map_err(|error| error.code())?;
        let items = page.items.into_iter().map(|row| json!({
            "messageId": row.id.to_string(),
            "orderKey": row.order_key.map(|key| key.to_string()),
            "sentAtMs": null,
            "receivedAtMs": row.received_at_ms,
            "deletedAtMs": row.deleted_at_ms,
            "text": row.text.as_str(),
        })).collect::<Vec<_>>();
        Ok(json!({"schema":1,"accountId":owner.to_string(),"chatId":chat.to_string(),
            "fromOrderKey":from.to_string(),"toOrderKey":to.to_string(),
            "items":items,"nextCursor":page.next_cursor,"revision":page.revision}))
    }

    fn retention_marker_summary(&self, value: &Value) -> Result<Value> {
        let (owner, chat, expected_revision) = retention_selected_marker_summary(value)?;
        let store = crate::retention_store::RetentionStore::new(&self.profile)
            .map_err(|error| error.code())?;
        if store.visibility_revision().map_err(|error| error.code())? != expected_revision {
            return Err("stale_cursor");
        }
        let marker_only_count = store.marker_only_count(owner, chat)
            .map_err(|error| error.code())?;
        if store.visibility_revision().map_err(|error| error.code())? != expected_revision {
            return Err("stale_cursor");
        }
        Ok(json!({"schema":1,"accountId":owner.to_string(),"chatId":chat.to_string(),
            "revision":expected_revision,"markerOnlyCount":marker_only_count}))
    }
    fn retention_status(&self) -> Result<Value> {
        self.retention_preferences.snapshot().map(|saved| self.retention_ack.status(saved))
    }
    fn retention_set(&self, revision: &str, value: &Value) -> Result<Value> {
        let saved = self.retention_preferences.replace_with_host(
            revision, value, self.retention_ack.infrastructure_ready())?;
        self.retention_ack.preference_saved();
        Ok(self.retention_ack.status(saved))
    }

    pub(crate) fn export_download_open(
        &self,
        job: &str,
        token: &str,
        message: &str,
        attachment: &str,
    ) -> std::result::Result<
        crate::export_service::DownloadSink,
        crate::export_service::ExportServiceError,
    > {
        self.exports.open_download(job, token, message, attachment)
    }
    pub fn open(config: &Value) -> Result<Self> {
        if !exact(
            config,
            &["profileDirectory", "runtimeDirectory", "existingClient"],
        ) {
            return Err("invalid_request");
        }
        let profile = config["profileDirectory"]
            .as_str()
            .ok_or("invalid_request")?;
        let directory = config["runtimeDirectory"]
            .as_str()
            .ok_or("invalid_request")?;
        let existing = config["existingClient"]
            .as_bool()
            .ok_or("invalid_request")?;
        // Validate both explicit absolute roots even if one file is still absent.
        // Path safety is checked again with pinned handles on every operation.
        SafeStorePath::new(Path::new(profile).join("config.json")).map_err(|e| e.code())?;
        SafeStorePath::new(Path::new(directory).join("optimization-active.json"))
            .map_err(|e| e.code())?;
        let optimization =
            OptimizationSettingsStore::new(Path::new(profile).join("optimization-settings.json"))
                .map_err(|e| e.code())?;
        let ghost = GhostSettingsStore::new(Path::new(profile).join("ghost-settings.json"))
            .map_err(|e| e.code())?;
        let accounts =
            AccountPreferencesStore::new(Path::new(profile).join("account-settings.json"))
                .map_err(|e| e.code())?;
        let quick_replies = crate::quick_reply_store::QuickReplyStore::new(
            Path::new(profile).join("quick-replies.json"),
        )
        .map_err(|e| e.code())?;
        let retention_preferences = crate::retention_preferences::RetentionPreferencesStore::new(
            Path::new(profile).join(crate::retention_preferences::FILENAME),
        )?;
        let notifications =
            std::sync::Arc::new(crate::notification_bridge::NativeNotificationProtector::default());
        let chat_lock = crate::chat_lock::ChatLockService::with_adapters(
            Path::new(profile),
            std::sync::Arc::new(crate::chat_lock::WindowsProtector),
            std::sync::Arc::new(crate::chat_lock::SystemClock::default()),
            notifications.clone(),
        )
        .map_err(|e| e.code())?;
        optimization
            .activate(directory, existing)
            .map_err(|e| e.code())?;
        Ok(Self {
            profile: PathBuf::from(profile),
            optimization,
            ghost,
            accounts,
            quick_replies,
            retention_preferences,
            retention_ack: Default::default(),
            retention_lookup_cache: std::sync::Mutex::new(std::collections::VecDeque::new()),
            chat_lock,
            notifications,
            ghost_ack: Default::default(),
            exports: crate::export_service::ExportService::with_retention_profile(Path::new(profile)),
            api2_preview_last: std::sync::Mutex::new(None),
            api2_preview_cancelled: std::sync::atomic::AtomicBool::new(false),
            mdata_export: Default::default(),
        })
    }
    pub fn call(&self, request: &Value) -> Result<Value> {
        let operation = request
            .get("operation")
            .and_then(Value::as_str)
            .ok_or("invalid_request")?;
        let keys: &[&str] = match operation {
            "optimization.get" | "ghost.get" | "accounts.get" | "quick_replies.get"
            | "chat_lock.catalog" | "retention.get" => &["operation"],
            "optimization.set" | "ghost.set" => &["operation", "revision", "settings"],
            "accounts.set" => &["operation", "revision", "accounts"],
            "quick_replies.set" => &["operation", "revision", "replies"],
            "retention.set" => &["operation", "revision", "enabled"],
            "retention.lookup" | "retention.list" | "retention.inline" | "retention.range"
            | "retention.marker-summary" => &["operation", "value"],
            "quick_replies.for_account" => &["operation", "accountId", "prefix"],
            "chat_lock.execute" => &["operation", "action", "value"],
            "support.preview" => &["operation", "context"],
            "support.export" => &["operation", "context", "file"],
            "mdata.inspect" => &["operation", "file", "password"],
            "mdata.export" => &["operation", "file", "password"],
            "mdata.export-selected" => &["operation", "file", "password", "accounts"],
            "api2.history.preview" => &["operation", "value"],
            "export.start" | "export.page" | "export.media-start" | "export.media-chunk"
            | "export.media" | "export.finish" | "export.status" | "export.cancel"
            | "export.history" | "export.api2-next" => &["operation", "value"],
            _ => return Err("unsupported_operation"),
        };
        if !exact(request, keys) {
            return Err("invalid_request");
        }
        let revision = if operation.ends_with(".set") {
            request["revision"].as_str().ok_or("invalid_revision")?
        } else {
            ""
        };
        match operation {
            "retention.get" => self.retention_status(),
            "retention.set" => self.retention_set(revision, &request["enabled"]),
            "retention.lookup" => self.retention_lookup(&request["value"]),
            "retention.list" => self.retention_list(&request["value"]),
            "retention.inline" => self.retention_inline(&request["value"]),
            "retention.range" => self.retention_range(&request["value"]),
            "retention.marker-summary" => self.retention_marker_summary(&request["value"]),
            "mdata.export" | "mdata.export-selected" => {
                let result = self
                    .mdata_export
                    .execute(
                        &self.profile,
                        request["file"].as_str().ok_or("invalid_request")?,
                        request["password"].as_str().ok_or("invalid_request")?,
                        request.get("accounts"),
                        &crate::login_credentials::WindowsCredentials::new(),
                    )
                    .map_err(|e| e.code())?;
                serde_json::to_value(result).map_err(|_| "internal_error")
            }
            "mdata.inspect" => {
                let preview = crate::mdata_preview_file::inspect(
                    request["file"].as_str().ok_or("invalid_request")?,
                    request["password"].as_str().ok_or("invalid_request")?,
                )
                .map_err(|e| e.code())?;
                serde_json::to_value(preview).map_err(|_| "internal_error")
            }
            "api2.history.preview" => {
                let selected = selected_history_preview_scope(&request["value"])?;
                if self
                    .api2_preview_cancelled
                    .load(std::sync::atomic::Ordering::Acquire)
                {
                    return Err("api2_connect_cancelled");
                }
                // One short read-only preview at a time, with a local retry
                // interval. Hold only this dedicated lock across the request.
                let mut last = self.api2_preview_last.try_lock().map_err(|_| "busy")?;
                if last
                    .as_ref()
                    .is_some_and(|start| start.elapsed() < std::time::Duration::from_secs(10))
                {
                    return Err("busy");
                }
                *last = Some(std::time::Instant::now());
                let evidence = crate::api2_history_transport::preview_selected(
                    selected,
                    &crate::login_credentials::WindowsCredentials::new(),
                    &self.api2_preview_cancelled,
                )
                .map_err(|error| error.code())?;
                Ok(serde_json::json!({
                    "schema": 1,
                    "pages": evidence.pages,
                    "firstCount": evidence.first_count,
                    "secondCount": evidence.second_count,
                    "boundaryOverlapCount": evidence.boundary_overlap_count,
                    "firstAdvanced": evidence.first_advanced_in_time,
                    "secondAdvanced": evidence.second_advanced_in_time,
                    "emptyFollowupWindow": evidence.empty_followup_window,
                    "cursorFieldSeen": evidence.cursor_field_seen,
                    "hasMoreFieldSeen": evidence.has_more_field_seen,
                    "totalFieldSeen": evidence.total_field_seen,
                    "complete": false
                }))
            }
            "export.start" | "export.page" | "export.media-start" | "export.media-chunk"
            | "export.media" | "export.finish" | "export.status" | "export.cancel"
            | "export.history" => self
                .exports
                .execute(&operation[7..], &request["value"])
                .map_err(|e| e.code()),
            "export.api2-next" => self
                .exports
                .api2_next(
                    &request["value"],
                    &crate::login_credentials::WindowsCredentials::new(),
                )
                .map_err(|e| e.code()),
            "optimization.get" => self
                .optimization
                .snapshot()
                .map(|v| v.value())
                .map_err(|e| e.code()),
            "optimization.set" => self
                .optimization
                .replace(revision, &request["settings"])
                .map(|v| v.value())
                .map_err(|e| e.code()),
            "accounts.get" => self.accounts.snapshot().map_err(|e| e.code()),
            "accounts.set" => self
                .accounts
                .replace(revision, &request["accounts"])
                .map_err(|e| e.code()),
            "chat_lock.catalog" => self.chat_lock.catalog().map_err(|e| e.code()),
            "chat_lock.execute" => self
                .chat_lock
                .execute(
                    request["action"].as_str().ok_or("invalid_request")?,
                    &request["value"],
                )
                .map_err(|e| e.code()),
            "quick_replies.get" => self.quick_replies.snapshot().map_err(|e| e.code()),
            "quick_replies.set" => self
                .quick_replies
                .replace(revision, &request["replies"])
                .map_err(|e| e.code()),
            "quick_replies.for_account" => {
                let account = request["accountId"].as_str().ok_or("invalid_request")?;
                let prefix = request["prefix"].as_str().ok_or("invalid_request")?;
                let mut snapshot = self.quick_replies.snapshot().map_err(|e| e.code())?;
                snapshot["replies"] = crate::quick_reply_store::composer_replies(
                    &snapshot["replies"],
                    account,
                    prefix,
                )
                .map_err(|e| e.code())?;
                Ok(snapshot)
            }
            "ghost.get" => self
                .ghost
                .snapshot()
                .map(|s| self.ghost_status(s))
                .map_err(|e| e.code()),
            "ghost.set" => self
                .ghost
                .replace(revision, &request["settings"])
                .map(|s| self.ghost_status(s))
                .map_err(|e| e.code()),
            "support.preview" | "support.export" => {
                let context = &request["context"];
                if !exact(context, &["versions", "startup", "privacy"]) {
                    return Err("invalid_request");
                }
                // Optional unavailable snapshots remain null. Only the two
                // settings files owned by these services are consulted; no
                // account, credential, message or raw-log discovery occurs.
                let ghost = self.ghost.snapshot().unwrap_or(Value::Null);
                let optimization = self
                    .optimization
                    .snapshot()
                    .map(|s| s.value())
                    .unwrap_or(Value::Null);
                let report = crate::support_diagnostics::build_report(
                    &context["versions"],
                    &context["startup"],
                    &context["privacy"],
                    &ghost,
                    &optimization,
                );
                crate::support_diagnostics::serialize(&report).map_err(|e| e.code())?;
                if operation == "support.export" {
                    crate::support_diagnostics::save_report(
                        request["file"].as_str().ok_or("invalid_request")?,
                        &report,
                    )
                    .map_err(|e| e.code())
                } else {
                    Ok(serde_json::json!({"schema":1,"report":report}))
                }
            }
            _ => Err("unsupported_operation"),
        }
    }
    fn ghost_status(&self, mut settings: Value) -> Value {
        settings["runtime"] = self.ghost_ack.status(&settings);
        settings
    }
    pub(crate) fn export_history_bind(&self, job: &str, token: &str, source: u64) -> Result<()> {
        self.exports
            .bind_history(job, token, source)
            .map_err(|e| e.code())
    }
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn export_history_observe(
        &self,
        job: &str,
        token: &str,
        source: u64,
        direction: &str,
        count: u64,
        pending: bool,
        backward: bool,
        forward: bool,
    ) -> Result<()> {
        self.exports
            .observe_history(
                job, token, source, direction, count, pending, backward, forward,
            )
            .map_err(|e| e.code())
    }
    pub(crate) fn export_history_invalidate(
        &self,
        job: &str,
        token: &str,
        source: u64,
    ) -> Result<()> {
        self.exports
            .invalidate_history(job, token, source)
            .map_err(|e| e.code())
    }
    /// Native owner shutdown: stop export admission and signal active work.
    /// Does not wait for I/O or acknowledge that cleanup has completed.
    pub fn cancel_pending(&self) {
        self.api2_preview_cancelled
            .store(true, std::sync::atomic::Ordering::Release);
        self.exports.cancel_pending();
        self.mdata_export.cancel_pending();
    }
    pub fn acknowledge_ghost(&self, status: crate::ghost_ack::NativeStatus) -> bool {
        self.ghost_ack.update(status)
    }
    pub fn invalidate_ghost(&self) {
        self.ghost_ack.invalidate();
    }
    pub fn acknowledge_retention(&self, status: crate::retention_ack::NativeStatus) -> bool {
        self.retention_ack.update(status)
    }
    pub fn invalidate_retention(&self) {
        self.retention_ack.invalidate();
    }
    /// # Safety
    /// Native callbacks obey the host lifetime/threading contract in valmax_core.h.
    pub unsafe fn notifications_attach(
        &self,
        callbacks: Option<crate::notification_bridge::NativeCallbacks>,
    ) -> bool {
        unsafe { self.notifications.register(callbacks) }
    }
    pub fn notifications_refresh(&self) -> std::result::Result<(), crate::chat_lock::LockError> {
        self.chat_lock.refresh_notifications()
    }
}
