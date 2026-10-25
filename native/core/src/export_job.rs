//! Worker-owned selected-chat export. No account discovery, network or HTTP.
//! The host supplies pages from one explicitly selected chat, and runs blocking
//! spool/hash/render operations off the GUI thread. Cancellation is thread-safe.
use crate::{
    export_index::ExportIndex,
    export_render::{self, RenderSink},
    export_storage::{Cancellation, ExportWorkspace, ExternalMediaLease},
};
use rusqlite::{OptionalExtension, params};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
};

pub const MAX_PAGES: u32 = 20_000;
pub const MAX_PAGE_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_CHUNK_BYTES: usize = 49_152;
pub const MAX_CHUNKED_ASSET_BYTES: u64 = 64 * 1024 * 1024;
const MEDIA_REASONS: &[&str] = &[
    "asset_too_large",
    "not_available",
    "not_downloaded",
    "permission_denied",
    "download_failed",
    "timeout",
    "unsupported",
    "cancelled",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error {
    InvalidRequest,
    InvalidState,
    SequenceConflict,
    LimitReached,
    Cancelled,
    UnsafePath,
    IoError,
    InsufficientSpace,
    CleanupFailed,
}
impl Error {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidRequest => "invalid_request",
            Self::InvalidState => "invalid_state",
            Self::SequenceConflict => "sequence_conflict",
            Self::LimitReached => "limit_reached",
            Self::Cancelled => "cancelled",
            Self::UnsafePath => "unsafe_path",
            Self::IoError => "io_error",
            Self::InsufficientSpace => "insufficient_space",
            Self::CleanupFailed => "cleanup_failed",
        }
    }
}
type Result<T> = std::result::Result<T, Error>;
impl From<rusqlite::Error> for Error {
    fn from(e: rusqlite::Error) -> Self {
        match e.sqlite_error_code() {
            Some(rusqlite::ErrorCode::OperationInterrupted) => Self::Cancelled,
            Some(rusqlite::ErrorCode::DiskFull) => Self::InsufficientSpace,
            _ => Self::IoError,
        }
    }
}
impl From<export_render::Error> for Error {
    fn from(e: export_render::Error) -> Self {
        use export_render::Error as E;
        match e {
            E::Cancelled => Self::Cancelled,
            E::NoSpace => Self::InsufficientSpace,
            E::CleanupFailed => Self::CleanupFailed,
            E::UnsafePath | E::InvalidPath | E::Exists | E::SourceChanged => Self::UnsafePath,
            E::LimitExceeded | E::AssetTooLarge => Self::LimitReached,
            _ => Self::IoError,
        }
    }
}

fn bounded(v: &Value, n: usize) -> Result<()> {
    if v.as_str()
        .is_some_and(|s| s.chars().count() <= n && !s.contains('\0'))
    {
        Ok(())
    } else {
        Err(Error::InvalidRequest)
    }
}
fn identifier(s: &str) -> Result<()> {
    if !s.is_empty() && s.len() <= 128 && s.bytes().all(|c| (33..=126).contains(&c)) {
        Ok(())
    } else {
        Err(Error::InvalidRequest)
    }
}
fn validate_message(v: &Value) -> Result<Value> {
    // The renderer accepts completed attachments and larger legacy fields. The
    // ingress contract is deliberately narrower and accepts no paths/hashes.
    export_render::validate_message(v, true).map_err(|_| Error::InvalidRequest)?;
    bounded(&v["author"]["name"], 512)?;
    bounded(&v["text"], 65_536)?;
    if !v["reply"].is_null() {
        bounded(&v["reply"]["authorName"], 512)?;
        bounded(&v["reply"]["text"], 65_536)?;
    }
    let attachments = v["attachments"].as_array().ok_or(Error::InvalidRequest)?;
    if attachments.len() > 256 {
        return Err(Error::InvalidRequest);
    }
    for a in attachments {
        if a.as_object().is_none_or(|o| o.len() != 6) {
            return Err(Error::InvalidRequest);
        }
        bounded(&a["name"], 512)?;
        bounded(&a["mime"], 128)?;
        let mime = a["mime"].as_str().ok_or(Error::InvalidRequest)?;
        if !mime.is_empty()
            && !mime.split_once('/').is_some_and(|(x, y)| {
                !x.is_empty()
                    && !y.is_empty()
                    && [x, y].iter().all(|s| {
                        s.bytes()
                            .all(|c| c.is_ascii_alphanumeric() || b".+-".contains(&c))
                    })
            })
        {
            return Err(Error::InvalidRequest);
        }
        match a["status"].as_str() {
            Some("pending") if a["reason"].is_null() && a["type"] != "unsupported" => {}
            Some("unavailable")
                if a["reason"]
                    .as_str()
                    .is_some_and(|r| MEDIA_REASONS.contains(&r)) => {}
            _ => return Err(Error::InvalidRequest),
        }
    }
    let mut row = v.clone();
    if row.get("kind").is_none() {
        row["kind"] = json!("message");
    }
    Ok(row)
}
fn extension(mime: &str, kind: &str) -> &'static str {
    match mime.to_ascii_lowercase().as_str() {
        "image/jpeg" => "jpg",
        "image/png" => "png",
        "image/webp" => "webp",
        "image/gif" => "gif",
        "video/mp4" => "mp4",
        "video/webm" => "webm",
        "audio/ogg" => "ogg",
        "audio/mpeg" => "mp3",
        "audio/mp4" => "m4a",
        "audio/wav" => "wav",
        "application/pdf" => "pdf",
        // An API2 source can be transcoded by its CDN. Its real type is
        // verified from the asset head after transfer, so never guess an
        // extension before the bytes arrive.
        _ => {
            let _ = kind;
            "bin"
        }
    }
}
fn nonce() -> Result<String> {
    let mut b = [0u8; 16];
    getrandom::getrandom(&mut b).map_err(|_| Error::IoError)?;
    Ok(b.iter().map(|v| format!("{v:02x}")).collect())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Exporting,
    Rendering,
    Complete,
    Partial,
    Failed,
    Cancelled,
}
impl State {
    fn name(self) -> &'static str {
        match self {
            Self::Exporting => "exporting",
            Self::Rendering => "rendering",
            Self::Complete => "complete",
            Self::Partial => "partial",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }
    fn terminal(self) -> bool {
        matches!(
            self,
            Self::Complete | Self::Partial | Self::Failed | Self::Cancelled
        )
    }
}
#[derive(Clone, Default)]
struct Counts {
    messages: u64,
    attachments: u64,
    ready: u64,
    unavailable: u64,
    bytes: u64,
}
#[derive(Default)]
struct History {
    source: Option<u64>,
    backward: bool,
    forward: bool,
}

pub struct ExportJob {
    id: String,
    account_id: String,
    chat: Value,
    destination: PathBuf,
    cancel: Cancellation,
    workspace: ExportWorkspace,
    index: Option<ExportIndex>,
    state: State,
    error: Option<Error>,
    sequence: u32,
    last_hash: Option<[u8; 32]>,
    last_receipt: Option<Value>,
    counts: Counts,
    history: History,
    external_media: HashMap<(String, String), ExternalMediaLease>,
}
impl ExportJob {
    /// Destination must not exist. The host has already shown the directory picker.
    pub fn new(destination: &Path, chat: &Value, account_id: &str) -> Result<Self> {
        if !crate::mdata_login::account_id(account_id) {
            return Err(Error::InvalidRequest);
        }
        export_render::validate_chat(chat).map_err(|_| Error::InvalidRequest)?;
        bounded(&chat["title"], 512)?;
        let cancel = Cancellation::default();
        let workspace = ExportWorkspace::new(destination, cancel.clone())?;
        let index = ExportIndex::new(destination, cancel.clone())?;
        index.connection().execute_batch("CREATE TABLE messages(id TEXT PRIMARY KEY,stamp INTEGER,data TEXT NOT NULL); CREATE INDEX message_order ON messages(stamp IS NULL,stamp,length(id),id); CREATE TABLE assets(message_id TEXT,attachment_id TEXT,source TEXT UNIQUE,state TEXT NOT NULL,reason TEXT,bytes INTEGER,digest TEXT,chunk_bytes INTEGER NOT NULL DEFAULT 0,chunk_offset INTEGER,chunk_digest BLOB,PRIMARY KEY(message_id,attachment_id));")?;
        Ok(Self {
            id: nonce()?,
            account_id: account_id.into(),
            chat: chat.clone(),
            destination: destination.into(),
            cancel,
            workspace,
            index: Some(index),
            state: State::Exporting,
            error: None,
            sequence: 0,
            last_hash: None,
            last_receipt: None,
            counts: Counts::default(),
            history: History::default(),
            external_media: HashMap::new(),
        })
    }
    pub fn cancellation(&self) -> Cancellation {
        self.cancel.clone()
    }
    pub fn state(&self) -> State {
        self.state
    }
    /// Host uses this to bind one account; it is not a user-settable QML property.
    pub fn account_id(&self) -> &str {
        &self.account_id
    }
    /// The selected chat identity is fixed when the job is created. An API2
    /// reader derives its scope here, never from a later QML request.
    pub(crate) fn chat_id(&self) -> &str {
        self.chat["id"].as_str().unwrap()
    }
    pub(crate) fn can_start_api2(&self) -> bool {
        self.state == State::Exporting
            && self.sequence == 0
            && self.counts.messages == 0
            && self.counts.attachments == 0
            && self.external_media.is_empty()
            && self.cancel.check().is_ok()
    }
    pub(crate) fn message_count(&self) -> u64 {
        self.counts.messages
    }
    pub fn snapshot(&self) -> Value {
        let c = &self.counts;
        let mut v = json!({"schema":1,"jobId":self.id,"state":self.state.name(),"counts":{"messages":c.messages,"attachments":c.attachments,"ready":c.ready,"unavailable":c.unavailable,"mediaBytes":c.bytes}});
        if let Some(e) = self.error {
            v["error"] = json!(e.code());
        }
        v["downloadsPending"] = json!(self.has_external_media());
        if matches!(self.state, State::Complete | State::Partial) {
            v["htmlPath"] = json!(self.destination.join("index.html").to_string_lossy());
        }
        v
    }
    pub fn has_external_media(&self) -> bool {
        !self.external_media.is_empty()
    }
    pub(crate) fn open_media_writer(
        &mut self,
        message: &str,
        attachment: &str,
    ) -> Result<crate::export_storage::ExternalMediaWriter> {
        self.active()?;
        identifier(message)?;
        identifier(attachment)?;
        let lease = self
            .external_media
            .get(&(message.into(), attachment.into()))
            .ok_or(Error::InvalidState)?;
        lease.begin_writer(self.cancel.clone()).map_err(Into::into)
    }

    /// Admit only a pending attachment already received for this selected job.
    /// The caller supplies IDs, never a path, filename or protected media URL.
    pub fn start_media(&mut self, message: &str, attachment: &str) -> Result<Value> {
        self.active()?;
        identifier(message)?;
        identifier(attachment)?;
        let key = (message.to_owned(), attachment.to_owned());
        if let Some(lease) = self.external_media.get(&key) {
            return Ok(json!({"schema":1,"jobId":self.id,"nativePathFileURL":lease.file_url()}));
        }
        if !self.external_media.is_empty() {
            return Err(Error::InvalidState);
        }
        let db = self.index.as_ref().ok_or(Error::InvalidState)?.connection();
        let row = db.query_row("SELECT source,state,chunk_bytes FROM assets WHERE message_id=?1 AND attachment_id=?2",
            params![message, attachment], |r| Ok((r.get::<_,Option<String>>(0)?, r.get::<_,String>(1)?, r.get::<_,i64>(2)?)))
            .optional()?.ok_or(Error::InvalidRequest)?;
        if row.1 != "pending" || row.2 != 0 {
            return Err(Error::InvalidState);
        }
        let source = row.0.ok_or(Error::InvalidState)?;
        let extension = source.rsplit_once('.').ok_or(Error::InvalidState)?.1;
        let lease = ExternalMediaLease::new(&self.destination, extension)?;
        let receipt = json!({"schema":1,"jobId":self.id,"nativePathFileURL":lease.file_url()});
        self.external_media.insert(key, lease);
        Ok(receipt)
    }
    /// Read-only proof: only trusted host observations can change these flags.
    pub fn history(&self) -> Value {
        let valid = self.state == State::Exporting && self.cancel.check().is_ok();
        json!({"schema":1,"jobId":self.id,"history":{
            "ready":valid && self.history.source.is_some(),
            "backward":valid && self.history.backward,"forward":valid && self.history.forward}})
    }
    fn active(&mut self) -> Result<()> {
        if self.state != State::Exporting {
            return Err(Error::InvalidState);
        }
        if self.cancel.check().is_err() {
            self.fail(Error::Cancelled);
            return Err(Error::Cancelled);
        }
        Ok(())
    }
    fn fail(&mut self, error: Error) {
        self.error = Some(error);
        self.state = if error == Error::Cancelled || self.cancel.check().is_err() {
            State::Cancelled
        } else {
            State::Failed
        };
        let index_cleanup = self.index.take().map(|index| index.close()).transpose();
        let workspace_cleanup = self.workspace.abort();
        if index_cleanup.is_err() || workspace_cleanup.is_err() {
            self.error = Some(Error::CleanupFailed);
        }
    }
    fn operational<T>(&mut self, result: Result<T>) -> Result<T> {
        if let Err(e) = result {
            if !matches!(
                e,
                Error::InvalidRequest
                    | Error::InvalidState
                    | Error::SequenceConflict
                    | Error::LimitReached
            ) {
                self.fail(e);
            }
        }
        result
    }
    /// Only observations from the host-bound native history view count. QML
    /// assertions of completeness alone never produce a complete archive.
    pub fn bind_history(&mut self, source: u64) -> Result<()> {
        self.active()?;
        if source == 0 || self.history.source.is_some_and(|v| v != source) {
            return Err(Error::InvalidState);
        }
        self.history.source = Some(source);
        Ok(())
    }
    pub fn observe_history(
        &mut self,
        source: u64,
        direction: &str,
        count: u64,
        pending_done: bool,
        more_backward: bool,
        more_forward: bool,
    ) -> Result<()> {
        self.active()?;
        if count > 100_000
            || self.history.source != Some(source)
            || !["backward", "forward", "center"].contains(&direction)
        {
            return Err(Error::InvalidRequest);
        }
        if more_backward {
            self.history.backward = false;
        } else if count > 0 && pending_done && ["backward", "center"].contains(&direction) {
            self.history.backward = true;
        }
        if more_forward {
            self.history.forward = false;
        } else if count > 0 && pending_done && ["forward", "center"].contains(&direction) {
            self.history.forward = true;
        }
        Ok(())
    }
    /// Exact retries of the immediately previous page are idempotent. An
    /// overlapping history page keeps the first snapshot of each message.
    pub fn page(&mut self, sequence: u32, values: &[Value]) -> Result<Value> {
        self.active()?;
        if sequence >= MAX_PAGES || values.is_empty() || values.len() > 200 {
            return Err(Error::InvalidRequest);
        }
        let rows: Vec<Value> = values.iter().map(validate_message).collect::<Result<_>>()?;
        let bytes = serde_json::to_vec(&rows).map_err(|_| Error::InvalidRequest)?;
        if bytes.len() > MAX_PAGE_BYTES {
            return Err(Error::LimitReached);
        }
        let hash: [u8; 32] = Sha256::digest(&bytes).into();
        if sequence.checked_add(1) == Some(self.sequence) && self.last_hash == Some(hash) {
            return Ok(self.last_receipt.clone().unwrap());
        }
        if sequence != self.sequence {
            return Err(Error::SequenceConflict);
        }
        let outcome = (|| {
            let db = self
                .index
                .as_mut()
                .ok_or(Error::InvalidState)?
                .connection_mut();
            let tx = db.transaction()?;
            let mut new_rows = Vec::new();
            let mut seen = HashSet::new();
            let mut attachments = 0u64;
            for row in &rows {
                let id = row["id"].as_str().unwrap();
                let exists = tx
                    .query_row("SELECT 1 FROM messages WHERE id=?1", [id], |_| Ok(()))
                    .optional()?
                    .is_some();
                if !exists {
                    if !seen.insert(id) {
                        return Err(Error::InvalidRequest);
                    }
                    attachments += row["attachments"].as_array().unwrap().len() as u64;
                    new_rows.push(row);
                }
            }
            if self.counts.messages + new_rows.len() as u64 > export_render::MAX_MESSAGES
                || self.counts.attachments + attachments > export_render::MAX_ATTACHMENTS
            {
                return Err(Error::LimitReached);
            }
            let mut media = Vec::new();
            for row in &new_rows {
                self.cancel.check()?;
                let id = row["id"].as_str().unwrap();
                tx.execute(
                    "INSERT INTO messages VALUES(?1,?2,?3)",
                    params![id, row["timestampMs"].as_i64(), row.to_string()],
                )?;
                for a in row["attachments"].as_array().unwrap() {
                    let source = if a["status"] == "pending" {
                        let source = format!(
                            "media/{}.{}",
                            nonce()?,
                            extension(a["mime"].as_str().unwrap(), a["type"].as_str().unwrap())
                        );
                        self.workspace.reserve_asset(&source)?;
                        media.push(json!({"messageId":id,"attachmentId":a["id"],"source":source}));
                        Some(source)
                    } else {
                        None
                    };
                    tx.execute("INSERT INTO assets(message_id,attachment_id,source,state,reason) VALUES(?1,?2,?3,?4,?5)",params![id,a["id"].as_str(),source,a["status"].as_str(),a["reason"].as_str()])?;
                }
            }
            tx.commit()?;
            self.counts.messages += new_rows.len() as u64;
            self.counts.attachments += attachments;
            self.counts.unavailable += attachments - media.len() as u64;
            Ok(json!({"schema":1,"jobId":self.id,"sequence":sequence,"media":media}))
        })();
        let result = self.operational(outcome)?;
        self.sequence += 1;
        self.last_hash = Some(hash);
        self.last_receipt = Some(result.clone());
        Ok(result)
    }
    /// Bounded binary transfer. No untrusted path or server URL crosses here.
    /// Current storage contract caps chunked assets at 64 MiB; native large-file
    /// download integration is separate and must not claim support before wired.
    pub fn write_chunk(
        &mut self,
        message: &str,
        attachment: &str,
        offset: u64,
        data: &[u8],
    ) -> Result<u64> {
        self.active()?;
        identifier(message)?;
        identifier(attachment)?;
        if self
            .external_media
            .contains_key(&(message.into(), attachment.into()))
        {
            return Err(Error::InvalidState);
        }
        if data.is_empty()
            || data.len() > MAX_CHUNK_BYTES
            || offset
                .checked_add(data.len() as u64)
                .is_none_or(|n| n > MAX_CHUNKED_ASSET_BYTES)
        {
            return Err(Error::LimitReached);
        }
        let hash = Sha256::digest(data).to_vec();
        let outcome = (|| {
            let db = self.index.as_ref().ok_or(Error::InvalidState)?.connection();
            let row=db.query_row("SELECT source,state,chunk_bytes,chunk_offset,chunk_digest FROM assets WHERE message_id=?1 AND attachment_id=?2",params![message,attachment],|r|Ok((r.get::<_,Option<String>>(0)?,r.get::<_,String>(1)?,r.get::<_,i64>(2)?,r.get::<_,Option<i64>>(3)?,r.get::<_,Option<Vec<u8>>>(4)?))).optional()?.ok_or(Error::InvalidState)?;
            if row.1 != "pending" {
                return Err(Error::InvalidState);
            }
            let source = row.0.ok_or(Error::InvalidState)?;
            if row.3 == Some(offset as i64) && row.4.as_ref() == Some(&hash) {
                return u64::try_from(row.2).map_err(|_| Error::IoError);
            }
            if row.2 != offset as i64 {
                return Err(Error::SequenceConflict);
            }
            let next =
                self.workspace
                    .write_asset_chunk(&source, offset, data, MAX_CHUNKED_ASSET_BYTES)?;
            db.execute("UPDATE assets SET chunk_bytes=?1,chunk_offset=?2,chunk_digest=?3 WHERE message_id=?4 AND attachment_id=?5",params![next as i64,offset as i64,hash,message,attachment])?;
            Ok(next)
        })();
        self.operational(outcome)
    }
    pub fn complete_media(
        &mut self,
        message: &str,
        attachment: &str,
        state: &str,
        reason: Option<&str>,
    ) -> Result<Value> {
        identifier(message)?;
        identifier(attachment)?;
        if !["ready", "unavailable", "error", "cancelled"].contains(&state)
            || if state == "ready" {
                reason.is_some()
            } else {
                reason.is_none_or(|r| !MEDIA_REASONS.contains(&r))
            }
        {
            return Err(Error::InvalidRequest);
        }
        let key = (message.to_owned(), attachment.to_owned());
        // An admitted native task still owns its path after user cancellation.
        // Its eventual terminal callback can settle that exact lease, without
        // resurrecting an archive or accepting any new content.
        if self.state.terminal() || self.cancel.check().is_err() {
            if let Some(lease) = self.external_media.get_mut(&key) {
                lease.settle()?;
                self.external_media.remove(&key);
                if !self.state.terminal() {
                    self.fail(Error::Cancelled);
                }
                return Ok(self.snapshot());
            }
            return Err(Error::InvalidState);
        }
        self.active()?;
        let outcome = (|| {
            let db = self.index.as_ref().ok_or(Error::InvalidState)?.connection();
            let row=db.query_row("SELECT source,state,reason FROM assets WHERE message_id=?1 AND attachment_id=?2",params![message,attachment],|r|Ok((r.get::<_,Option<String>>(0)?,r.get::<_,String>(1)?,r.get::<_,Option<String>>(2)?))).optional()?.ok_or(Error::InvalidRequest)?;
            let source = row.0.ok_or(Error::InvalidRequest)?;
            if row.1 != "pending" {
                return if row.1 == state && row.2.as_deref() == reason {
                    Ok(())
                } else {
                    Err(Error::InvalidState)
                };
            }
            if let Some(lease) = self.external_media.get_mut(&key) {
                let imported = if state == "ready" {
                    self.workspace.import_download(&source, lease)
                } else {
                    Ok(())
                };
                let settled = lease.settle();
                if settled.is_ok() {
                    self.external_media.remove(&key);
                }
                imported?;
                settled?;
            }
            let proof = if state == "ready" {
                Some(
                    self.workspace
                        .verify_asset(&source, export_render::MAX_ASSET_BYTES)?,
                )
            } else {
                self.workspace.discard_asset(&source)?;
                None
            };
            db.execute("UPDATE assets SET state=?1,reason=?2,bytes=?3,digest=?4 WHERE message_id=?5 AND attachment_id=?6",params![state,reason,proof.as_ref().map(|p|p.bytes as i64),proof.as_ref().map(|p|&p.sha256),message,attachment])?;
            if let Some(p) = proof {
                self.counts.ready += 1;
                self.counts.bytes += p.bytes;
            } else {
                self.counts.unavailable += 1;
            }
            Ok(())
        })();
        self.operational(outcome)?;
        Ok(self.snapshot())
    }
    /// Blocking worker operation; at most one message plus lookahead is passed
    /// from the SQLite cursor to the existing streaming HTML renderer.
    fn finish_with_page_count(
        &mut self,
        completion: &Value,
        pages: Option<u32>,
        mut local_deleted: Option<&mut dyn Iterator<Item = export_render::Result<export_render::LocalDeletedRow>>>,
    ) -> Result<Value> {
        // Only the internal, account-bound API2 coordinator can create this
        // linked local section. A host-supplied completion cannot claim it.
        if completion.get("retainedLocal").is_some() || completion.get("retainedLocalCount").is_some() {
            return Err(Error::InvalidRequest);
        }
        let mut completion =
            export_render::validate_completion(completion).map_err(|_| Error::InvalidRequest)?;
        if self.state.terminal() {
            return Ok(self.snapshot());
        }
        self.active()?;
        let pending = self
            .index
            .as_ref()
            .unwrap()
            .connection()
            .query_row(
                "SELECT count(*) FROM assets WHERE state='pending'",
                [],
                |r| r.get::<_, i64>(0),
            )
            .map_err(Error::from);
        let pending = self.operational(pending)?;
        if pending != 0 {
            return Err(Error::InvalidState);
        }
        // Native view flags describe observed model state, not a verified
        // server EOF. Until an internal server proof exists, no caller can
        // publish an archive as complete.
        if completion["historyComplete"] == true {
            completion["historyComplete"] = json!(false);
            completion["stopReason"] = json!("unsupported_history");
        }
        completion["pagesFetched"] = json!(pages.unwrap_or(self.sequence));
        self.state = State::Rendering;
        let outcome = (|| {
            if let Some(rows) = local_deleted.as_deref_mut() {
                let count = export_render::render_local_deleted_into(&mut self.workspace, &self.chat, rows)?;
                completion["retainedLocal"] = json!(true);
                completion["retainedLocalCount"] = json!(count);
            }
            let db = self.index.as_ref().unwrap().connection();
            let mut stmt = db.prepare(
                "SELECT id,data FROM messages ORDER BY stamp IS NULL,stamp,length(id),id",
            )?;
            let mut rows = stmt.query([])?;
            let mut ended = false;
            let messages = std::iter::from_fn(|| {
                if ended {
                    return None;
                }
                let next = (|| -> export_render::Result<Option<Value>> {
                    self.cancel.check()?;
                    let Some(row) = rows.next().map_err(crate::export_index::sql_error)? else {
                        return Ok(None);
                    };
                    let id: String = row.get(0).map_err(crate::export_index::sql_error)?;
                    let data: String = row.get(1).map_err(crate::export_index::sql_error)?;
                    let mut message: Value =
                        serde_json::from_str(&data).map_err(|_| export_render::Error::IoError)?;
                    for a in message["attachments"]
                        .as_array_mut()
                        .ok_or(export_render::Error::IoError)?
                    {
                        let asset=db.query_row("SELECT source,state,reason FROM assets WHERE message_id=?1 AND attachment_id=?2",params![id,a["id"].as_str()],|r|Ok((r.get::<_,Option<String>>(0)?,r.get::<_,String>(1)?,r.get::<_,Option<String>>(2)?))).map_err(crate::export_index::sql_error)?;
                        a["source"] = if asset.1 == "ready" {
                            json!(asset.0)
                        } else {
                            Value::Null
                        };
                        a["status"] = json!(asset.1);
                        a["reason"] = json!(asset.2);
                    }
                    Ok(Some(message))
                })();
                match next {
                    Ok(Some(v)) => Some(Ok(v)),
                    Ok(None) => {
                        ended = true;
                        None
                    }
                    Err(e) => {
                        ended = true;
                        Some(Err(e))
                    }
                }
            });
            let summary = self.workspace.render(
                &self.chat,
                messages,
                &completion,
                export_render::RenderOptions { deduplicated: true },
                None,
            )?;
            self.cancel.check()?;
            let ready = summary["counts"]["mediaReady"]
                .as_u64()
                .ok_or(Error::IoError)?;
            let missing = summary["counts"]["mediaMissing"]
                .as_u64()
                .ok_or(Error::IoError)?;
            let unsupported = summary["counts"]["unsupported"]
                .as_u64()
                .ok_or(Error::IoError)?;
            let bytes = summary["counts"]["bytes"].as_u64().ok_or(Error::IoError)?;
            Ok::<_, Error>((ready, missing, unsupported, bytes))
        })();
        let outcome = outcome.and_then(|counts| {
            // Release the temporary message database before publication. A
            // failed cleanup must never be silently presented as a finished export.
            if let Some(index) = self.index.take() {
                index.close()?;
            }
            self.workspace.publish()?;
            Ok(counts)
        });
        match outcome {
            Ok((ready, missing, unsupported, bytes)) => {
                self.counts.ready = ready;
                self.counts.unavailable = missing;
                self.counts.bytes = bytes;
                self.state = if completion["historyComplete"] == true
                    && self.counts.unavailable == 0
                    && unsupported == 0
                {
                    State::Complete
                } else {
                    State::Partial
                };
                self.index.take();
                Ok(self.snapshot())
            }
            Err(e) => {
                self.fail(e);
                Err(e)
            }
        }
    }
    /// The host-facing finish cannot assert a server EOF. It reports the
    /// accepted host page count, which can differ from API2 network windows.
    pub fn finish(&mut self, completion: &Value) -> Result<Value> {
        self.finish_with_page_count(completion, None, None)
    }
    /// Only the native API2 coordinator supplies this window count and reason.
    /// The ordinary fail-closed finish path still decides publication/state.
    pub(crate) fn finish_api2(&mut self, reason: &str, windows: u16) -> Result<Value> {
        self.finish_with_page_count(
            &json!({"historyComplete":false,"stopReason":reason,"pagesFetched":windows}),
            Some(u32::from(windows)),
            None,
        )
    }
    /// Native selected-chat API2 finish with a local, account-scoped retained
    /// section. An error discards the entire staged archive before publication.
    pub(crate) fn finish_api2_with_local_deleted(
        &mut self,
        reason: &str,
        windows: u16,
        rows: &mut dyn Iterator<Item = export_render::Result<export_render::LocalDeletedRow>>,
    ) -> Result<Value> {
        self.finish_with_page_count(
            &json!({"historyComplete":false,"stopReason":reason,"pagesFetched":windows}),
            Some(u32::from(windows)),
            Some(rows),
        )
    }
    pub fn cancel(&mut self) -> Value {
        if !self.state.terminal() {
            self.cancel.cancel();
            self.fail(Error::Cancelled);
        }
        self.snapshot()
    }
}

#[cfg(all(test, windows))]
#[path = "export_job_tests.rs"]
mod tests;
