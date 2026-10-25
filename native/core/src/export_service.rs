//! One explicit export per runtime. Calls that touch SQLite/files are synchronous
//! worker operations. An explicit selected job may also use one bound, pinned
//! API2 history reader; no media URL or raw response crosses the JSON route.
//!
//! The slot protects admission and ownership only. Disk work happens under the
//! selected job's mutex after releasing the slot; concurrent requests return
//! `busy`. Host shutdown closes admission before setting the cancellation flag.
use crate::{
    api2_history_contract::{PageEvidence, SelectedScope},
    api2_history_transport::{
        self, HistoryAttachmentKind, HistoryCancel, HistoryMessage, HistoryMessageId,
        HistoryMediaSource, HistoryMessageKind, HistoryPage, HistoryReader, TransportError,
    },
    api2_media_download::{self, MediaError},
    export_job::{
        self, ExportJob, MAX_CHUNK_BYTES, MAX_CHUNKED_ASSET_BYTES, MAX_PAGE_BYTES, MAX_PAGES, State,
    },
    export_local_retention::LocalDeletedStream,
    export_render,
    export_storage::Cancellation,
    login_credentials::LoginCredentials,
    selected_path::selected_directory_url,
};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::{HashMap, VecDeque},
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex, MutexGuard, TryLockError,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    thread,
    time::{Duration, Instant},
};
use subtle::ConstantTimeEq;
use zeroize::Zeroizing;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ExportServiceError {
    Job(export_job::Error),
    Busy,
    NotFound,
    UnsupportedOperation,
}
impl ExportServiceError {
    pub fn code(self) -> &'static str {
        match self {
            Self::Job(e) => e.code(),
            Self::Busy => "busy",
            Self::NotFound => "not_found",
            Self::UnsupportedOperation => "unsupported_operation",
        }
    }
}
impl From<export_job::Error> for ExportServiceError {
    fn from(e: export_job::Error) -> Self {
        Self::Job(e)
    }
}
type Result<T> = std::result::Result<T, ExportServiceError>;
fn invalid() -> ExportServiceError {
    export_job::Error::InvalidRequest.into()
}
fn io_error() -> ExportServiceError {
    export_job::Error::IoError.into()
}
fn cancelled() -> ExportServiceError {
    export_job::Error::Cancelled.into()
}
fn terminal(state: State) -> bool {
    matches!(
        state,
        State::Complete | State::Partial | State::Failed | State::Cancelled
    )
}

struct JobHandle {
    id: [u8; 16],
    token: Zeroizing<[u8; 32]>,
    cancel: Cancellation,
    terminal: AtomicBool,
    history_source: AtomicU64,
    api2_active: AtomicBool,
    api2_cancel: HistoryCancel,
    api2: Mutex<Api2Progress>,
    job: Mutex<ExportJob>,
}
impl Drop for JobHandle {
    fn drop(&mut self) {
        self.cancel.cancel();
        self.api2_cancel.cancel();
        // Final Arc ownership: no operation can still borrow this job. Explicit
        // cancellation closes the index before workspace cleanup, even after a
        // previous caller panicked. No slot lock is held here.
        let job = self.job.get_mut().unwrap_or_else(|e| e.into_inner());
        job.cancel();
    }
}

const API2_MAX_WINDOWS: u16 = 1000;
const API2_PAGE_TARGET_BYTES: usize = MAX_PAGE_BYTES - 16_384;
const API2_MAX_MEDIA_QUEUED_TASKS: usize = 20_000;
const API2_MAX_MEDIA_URL_BYTES: usize = 32 * 1024 * 1024;
const API2_MEDIA_SPACING: Duration = Duration::from_millis(250);
const API2_MEDIA_WAIT_SLICE: Duration = Duration::from_millis(25);
const API2_HISTORY_RETRY_SPACING: Duration = Duration::from_millis(500);

fn wait_api2_history_retry(cancel: &HistoryCancel) -> std::result::Result<(), TransportError> {
    let until = Instant::now() + API2_HISTORY_RETRY_SPACING;
    while Instant::now() < until {
        if cancel.is_cancelled() {
            return Err(TransportError::Cancelled);
        }
        thread::sleep(until.saturating_duration_since(Instant::now()).min(API2_MEDIA_WAIT_SLICE));
    }
    if cancel.is_cancelled() {
        Err(TransportError::Cancelled)
    } else {
        Ok(())
    }
}

/// A VIDEO_PLAY request may use a history connection that sat idle while the
/// preceding photos and voice clips were downloaded. Rebind that same selected
/// job once after a transient transport failure; denied or malformed media
/// stays unavailable without a retry storm.
fn retry_video_resolution<T, F, W>(
    mut attempt: F,
    mut wait: W,
) -> Result<std::result::Result<T, MediaError>>
where
    F: FnMut() -> Result<std::result::Result<T, MediaError>>,
    W: FnMut() -> Result<()>,
{
    let first = attempt()?;
    let retry = first
        .as_ref()
        .err()
        .is_some_and(|error| matches!(error, MediaError::Network | MediaError::Timeout));
    if !retry {
        return Ok(first);
    }
    wait()?;
    attempt()
}

/// A VIDEO_PLAY reply can contain several validated MP4 variants. Try the
/// highest once, then the lowest once if transfer failed for a recoverable
/// media reason. Both attempts share one deadline and one pinned writer; the
/// writer is truncated before the second URL so no partial first variant can
/// become a published asset. Permission and TLS failures never fall back.
fn download_video_with_fallback<W, F, R, P>(
    sources: &[Zeroizing<String>],
    writer: &mut W,
    mut fetch: F,
    mut reset: R,
    mut pause: P,
) -> std::result::Result<u64, MediaError>
where
    F: FnMut(&str, &mut W) -> std::result::Result<u64, MediaError>,
    R: FnMut(&mut W) -> std::result::Result<(), MediaError>,
    P: FnMut() -> std::result::Result<(), MediaError>,
{
    let first = sources.first().ok_or(MediaError::NotAvailable)?;
    let result = fetch(first.as_str(), writer);
    let Err(error) = result else {
        return result;
    };
    let fallback = sources.iter().rev().find(|candidate| candidate.as_str() != first.as_str());
    let Some(fallback) = fallback else {
        return Err(error);
    };
    if !matches!(error, MediaError::TooLarge | MediaError::Timeout | MediaError::Network
        | MediaError::NotAvailable | MediaError::InvalidResponse | MediaError::InvalidMedia)
    {
        return Err(error);
    }
    pause()?;
    reset(writer)?;
    fetch(fallback.as_str(), writer)
}

struct Api2MediaTask {
    message_id: String,
    attachment_id: String,
    source: HistoryMediaSource,
}

fn api2_media_source_bytes(source: &HistoryMediaSource) -> usize {
    match source {
        HistoryMediaSource::AudioUrl(url) | HistoryMediaSource::PhotoUrl(url) => url.len(),
        HistoryMediaSource::VideoRef(reference) => {
            reference.token.len() + match &reference.message_id {
                HistoryMessageId::Text(value) => value.len(),
                _ => 8,
            }
        }
    }
}

struct Api2Progress {
    reader: Option<HistoryReader>,
    next_from_ms: Option<i64>,
    windows_fetched: u16,
    page_sequence: u32,
    own_id: Option<i64>,
    id_salt: Zeroizing<[u8; 32]>,
    seen: HashMap<[u8; 32], [u8; 32]>,
    boundary_time_ms: Option<i64>,
    boundary: HashMap<[u8; 32], [u8; 32]>,
    media_queue: VecDeque<Api2MediaTask>,
    media_url_bytes: usize,
    media_total: u32,
    media_processed: u32,
    last_media_completed: Option<Instant>,
    history_stop_reason: Option<&'static str>,
    done: bool,
}
impl Api2Progress {
    fn new() -> Result<Self> {
        let mut id_salt = Zeroizing::new([0_u8; 32]);
        getrandom::getrandom(id_salt.as_mut()).map_err(|_| io_error())?;
        Ok(Self {
            reader: None,
            next_from_ms: None,
            windows_fetched: 0,
            page_sequence: 0,
            own_id: None,
            id_salt,
            seen: HashMap::new(),
            boundary_time_ms: None,
            boundary: HashMap::new(),
            media_queue: VecDeque::new(),
            media_url_bytes: 0,
            media_total: 0,
            media_processed: 0,
            last_media_completed: None,
            history_stop_reason: None,
            done: false,
        })
    }
    fn receipt(&self, mut snapshot: Value) -> Value {
        let phase = if self.done {
            "done"
        } else if self.history_stop_reason.is_some() {
            "media"
        } else {
            "history"
        };
        snapshot["api2"] = json!({"windowsFetched":self.windows_fetched,
            "mediaProcessed":self.media_processed,"mediaTotal":self.media_total,
            "phase":phase,"done":self.done});
        public_result(snapshot)
    }
}

struct Api2PreparedRow {
    id_key: [u8; 32],
    fingerprint: [u8; 32],
    stamp_ms: i64,
    normalized_bytes: usize,
    value: Value,
    media: Vec<Api2MediaTask>,
}
struct Api2Chunk {
    rows: Vec<Value>,
    media: Vec<Api2MediaTask>,
}
struct Api2PreparedPage {
    rows: Vec<Api2PreparedRow>,
    boundary_time_ms: Option<i64>,
    boundary: HashMap<[u8; 32], [u8; 32]>,
    next_from_ms: Option<i64>,
    stop_reason: Option<&'static str>,
}
#[derive(Default)]
struct Slot {
    closed: bool,
    starting: bool,
    current: Option<Arc<JobHandle>>,
}
#[derive(Default)]
pub struct ExportService {
    slot: Mutex<Slot>,
    retention_profile: Option<PathBuf>,
}
pub(crate) struct DownloadSink {
    writer: crate::export_storage::ExternalMediaWriter,
    _job: Arc<JobHandle>,
}
impl DownloadSink {
    pub(crate) fn write(
        &mut self,
        bytes: &[u8],
    ) -> std::result::Result<(), crate::export_render::Error> {
        self.writer.write(bytes)
    }
    pub(crate) fn close(&mut self) -> std::result::Result<(), crate::export_render::Error> {
        self.writer.close()
    }
}

/// Reserves the sole start admission while filesystem work runs without the
/// slot lock. Failure/unwind clears only this reservation, preserving any
/// previous terminal result until a new job successfully commits.
struct StartReservation<'a> {
    service: &'a ExportService,
    active: bool,
}
impl Drop for StartReservation<'_> {
    fn drop(&mut self) {
        if self.active {
            self.service
                .slot
                .lock()
                .unwrap_or_else(|e| e.into_inner())
                .starting = false;
        }
    }
}
impl<'a> StartReservation<'a> {
    fn commit(mut self, job: Arc<JobHandle>) -> Result<()> {
        let old = {
            let mut slot = self.service.slot.lock().map_err(|_| io_error())?;
            if slot.closed {
                return Err(cancelled());
            }
            let old = slot.current.replace(job);
            slot.starting = false;
            self.active = false;
            old
        };
        // Dropping a previous job can close files; never do it under slot lock.
        drop(old);
        Ok(())
    }
}

fn try_lock<T>(lock: &Mutex<T>) -> Result<MutexGuard<'_, T>> {
    lock.try_lock().map_err(|e| match e {
        TryLockError::WouldBlock => ExportServiceError::Busy,
        TryLockError::Poisoned(_) => io_error(),
    })
}
fn exact(value: &Value, keys: &[&str]) -> Result<()> {
    let object = value.as_object().ok_or_else(invalid)?;
    if object.len() != keys.len()
        || keys.iter().any(|key| !object.contains_key(*key))
        || value["schema"].as_u64() != Some(1)
    {
        return Err(invalid());
    }
    Ok(())
}
fn hex<const N: usize>(text: &str) -> Option<[u8; N]> {
    if text.len() != N * 2 {
        return None;
    }
    let nibble = |b| match b {
        b'0'..=b'9' => Some(b - b'0'),
        b'a'..=b'f' => Some(b - b'a' + 10),
        _ => None,
    };
    let mut result = [0; N];
    for (pair, output) in text.as_bytes().chunks_exact(2).zip(&mut result) {
        *output = (nibble(pair[0])? << 4) | nibble(pair[1])?;
    }
    Some(result)
}
fn encode_hex(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 16] = b"0123456789abcdef";
    let mut text = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        text.push(ALPHABET[(b >> 4) as usize] as char);
        text.push(ALPHABET[(b & 15) as usize] as char);
    }
    text
}
fn identifier(value: &Value) -> Result<&str> {
    let text = value.as_str().ok_or_else(invalid)?;
    if text.is_empty() || text.len() > 128 || !text.bytes().all(|b| (33..=126).contains(&b)) {
        return Err(invalid());
    }
    Ok(text)
}

#[cfg(windows)]
fn output_name() -> Result<String> {
    #[repr(C)]
    #[derive(Default)]
    struct SystemTime {
        year: u16,
        month: u16,
        day_of_week: u16,
        day: u16,
        hour: u16,
        minute: u16,
        second: u16,
        milliseconds: u16,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetLocalTime(time: *mut SystemTime);
    }
    let mut time = SystemTime::default();
    // OS clock only; neither the name nor its random suffix contains chat data.
    unsafe {
        GetLocalTime(&mut time);
    }
    let mut suffix = [0u8; 16];
    getrandom::getrandom(&mut suffix).map_err(|_| io_error())?;
    Ok(format!(
        "ValMAX-Chat-{:04}-{:02}-{:02}_{:02}-{:02}-{:02}-{}",
        time.year,
        time.month,
        time.day,
        time.hour,
        time.minute,
        time.second,
        encode_hex(&suffix)
    ))
}
#[cfg(not(windows))]
fn output_name() -> Result<String> {
    Err(io_error())
}

fn public_result(mut value: Value) -> Value {
    // The selected output URL is the only local path returned to the exporter.
    // UTF-8 bytes are escaped explicitly; no server/media source URL is exposed.
    if let Some(path) = value["htmlPath"].as_str() {
        let mut url = String::from("file:///");
        for b in path.replace('\\', "/").bytes() {
            if b.is_ascii_alphanumeric() || b"/-_.~:".contains(&b) {
                url.push(b as char);
            } else {
                url.push_str(&format!("%{b:02X}"));
            }
        }
        value["htmlFileURL"] = json!(url);
    }
    value
}

/// A private, type-separated archive identity. The salt is never returned to
/// QML or written into the archive, and wire IDs are not used as HTML anchors.
fn api2_message_key(
    salt: &[u8; 32],
    id: &HistoryMessageId,
) -> std::result::Result<[u8; 32], &'static str> {
    let mut hash = Sha256::new();
    hash.update(b"ValMAX selected API2 message identity v1\0");
    hash.update(salt);
    match id {
        HistoryMessageId::Text(value) => {
            if value.is_empty()
                || value.len() > 128
                || !value.bytes().all(|byte| (33..=126).contains(&byte))
            {
                return Err("history_error");
            }
            hash.update([0]);
            hash.update(value.as_bytes());
        }
        HistoryMessageId::Unsigned(value) if *value != 0 => {
            hash.update([1]);
            hash.update(value.to_be_bytes());
        }
        HistoryMessageId::Negative(value) if *value < 0 => {
            hash.update([2]);
            hash.update(value.to_be_bytes());
        }
        _ => return Err("history_error"),
    }
    Ok(hash.finalize().into())
}

fn api2_attachment(kind: HistoryAttachmentKind) -> (&'static str, &'static str, &'static str) {
    use HistoryAttachmentKind as A;
    match kind {
        A::Photo => ("image", "Фото", "not_downloaded"),
        A::Video => ("video", "Видео", "not_downloaded"),
        A::Audio => ("audio", "Аудиозапись", "not_downloaded"),
        A::File => ("file", "Файл", "not_downloaded"),
        A::Sticker => ("unsupported", "Стикер", "unsupported"),
        A::Share => ("unsupported", "Поделились содержимым", "unsupported"),
        A::Call => ("unsupported", "Звонок", "unsupported"),
        A::Location => ("unsupported", "Местоположение", "unsupported"),
        A::Control | A::InlineKeyboard | A::Other => {
            ("unsupported", "Неподдерживаемое вложение", "unsupported")
        }
    }
}

fn prepare_api2_row(
    message: &HistoryMessage,
    own_id: i64,
    salt: &[u8; 32],
) -> std::result::Result<Api2PreparedRow, &'static str> {
    if message.text.chars().count() > 65_536 || message.text.contains('\0') {
        return Err("message_too_large");
    }
    if message.attachments.len() > 256 {
        return Err("attachment_limit");
    }
    if !message.media_sources.is_empty()
        && message.media_sources.len() != message.attachments.len()
    {
        return Err("history_error");
    }
    let id_key = api2_message_key(salt, &message.id)?;
    let id = encode_hex(&id_key);
    let mut attachments = Vec::with_capacity(message.attachments.len());
    let mut media = Vec::new();
    for (index, kind) in message.attachments.iter().enumerate() {
        let (mut export_kind, mut name, reason) = api2_attachment(*kind);
        let mut mime = "";
        let attachment_id = format!("a{index}");
        let source = message.media_sources.get(index).and_then(|source| source.as_ref());
        let pending = match source {
            Some(HistoryMediaSource::AudioUrl(url))
                if *kind == HistoryAttachmentKind::Audio
                    && api2_media_download::eligible_audio_url(url) =>
            {
                media.push(Api2MediaTask {
                    message_id: id.clone(),
                    attachment_id: attachment_id.clone(),
                    source: HistoryMediaSource::AudioUrl(url.clone()),
                });
                export_kind = "voice";
                name = "Голосовое сообщение";
                true
            }
            Some(HistoryMediaSource::PhotoUrl(url))
                if *kind == HistoryAttachmentKind::Photo
                    && api2_media_download::eligible_photo_url(url) =>
            {
                media.push(Api2MediaTask {
                    message_id: id.clone(),
                    attachment_id: attachment_id.clone(),
                    source: HistoryMediaSource::PhotoUrl(url.clone()),
                });
                true
            }
            Some(HistoryMediaSource::VideoRef(reference))
                if *kind == HistoryAttachmentKind::Video
                    && reference.video_id > 0
                    && reference.message_id == message.id =>
            {
                media.push(Api2MediaTask {
                    message_id: id.clone(),
                    attachment_id: attachment_id.clone(),
                    source: HistoryMediaSource::VideoRef(reference.clone()),
                });
                mime = "video/mp4";
                true
            }
            _ => false,
        };
        attachments.push(json!({"id":attachment_id,"type":export_kind,"name":name,"mime":mime,
            "status":if pending {"pending"} else {"unavailable"},
            "reason":if pending {Value::Null} else {json!(reason)}}));
    }
    let kind = if message.unsupported_visible_content || message.rich_content_present {
        "unsupported"
    } else {
        match message.kind {
            HistoryMessageKind::User => "message",
            HistoryMessageKind::Service => "service",
            HistoryMessageKind::Channel | HistoryMessageKind::Other => "unsupported",
        }
    };
    let row = json!({"id":id,"author":{"id":message.sender_id.to_string(),
        "name":if message.sender_id==own_id {"Я"} else {"Отправитель"}},
        "timestampMs":message.time_ms,"text":message.text.as_str(),
        "outgoing":message.sender_id==own_id,"reply":null,"attachments":attachments,"kind":kind});
    let normalized = export_render::validate_message(&row, true).map_err(|_| "history_error")?;
    let normalized = serde_json::to_vec(&normalized).map_err(|_| "history_error")?;
    if normalized.len().saturating_add(2) > API2_PAGE_TARGET_BYTES {
        return Err("message_too_large");
    }
    let fingerprint = Sha256::digest(&normalized).into();
    Ok(Api2PreparedRow {
        id_key,
        fingerprint,
        stamp_ms: message.time_ms,
        normalized_bytes: normalized.len(),
        value: row,
        media,
    })
}

/// The transport has already validated its frame and scope. Repeat the page
/// checks here so the disk coordinator can also be tested with synthetic DTOs.
/// At each inclusive timestamp every previously seen boundary row must recur
/// with identical export-visible content before a new cursor is accepted.
fn prepare_api2_page(
    page: &HistoryPage,
    from_ms: i64,
    own_id: i64,
    progress: &Api2Progress,
) -> std::result::Result<Api2PreparedPage, &'static str> {
    if from_ms <= 0 || page.messages.len() > 100 {
        return Err("history_error");
    }
    if page.messages.is_empty() {
        if page.evidence != PageEvidence::EmptyWindow {
            return Err("history_error");
        }
        return Ok(Api2PreparedPage {
            rows: Vec::new(),
            boundary_time_ms: None,
            boundary: HashMap::new(),
            next_from_ms: None,
            stop_reason: Some("unsupported_history"),
        });
    }
    // Ordinary API2 windows were observed in ascending time order. An
    // unexpected reorder invalidates the inclusive timestamp cursor.
    if page
        .messages
        .windows(2)
        .any(|pair| pair[0].time_ms > pair[1].time_ms)
    {
        return Err("history_error");
    }
    let oldest = page
        .messages
        .iter()
        .map(|message| message.time_ms)
        .min()
        .ok_or("history_error")?;
    let expected = if oldest == from_ms {
        PageEvidence::NoTimeProgress {
            count: page.messages.len(),
        }
    } else {
        PageEvidence::AdvanceCandidate {
            next_from_ms: oldest,
            count: page.messages.len(),
        }
    };
    if oldest <= 0 || oldest > from_ms || page.evidence != expected {
        return Err("history_error");
    }
    if progress
        .boundary_time_ms
        .is_some_and(|time| time != from_ms)
    {
        return Err("history_error");
    }
    let mut all = HashMap::with_capacity(page.messages.len());
    let mut fresh = Vec::with_capacity(page.messages.len());
    for message in &page.messages {
        if message.time_ms <= 0 || message.time_ms > from_ms {
            return Err("history_error");
        }
        let row = prepare_api2_row(message, own_id, &progress.id_salt)?;
        if all
            .insert(row.id_key, (row.stamp_ms, row.fingerprint))
            .is_some()
        {
            return Err("history_error");
        }
        match progress.seen.get(&row.id_key) {
            Some(previous) if previous != &row.fingerprint => return Err("history_error"),
            Some(_) => {}
            None => fresh.push(row),
        }
    }
    for (id, fingerprint) in &progress.boundary {
        if !all
            .get(id)
            .is_some_and(|(stamp, current)| *stamp == from_ms && current == fingerprint)
        {
            return Err("history_error");
        }
    }
    let boundary = all
        .into_iter()
        .filter_map(|(id, (stamp, fingerprint))| (stamp == oldest).then_some((id, fingerprint)))
        .collect();
    let no_new_rows = fresh.is_empty();
    let stop_reason = if oldest == from_ms || no_new_rows {
        Some("no_progress")
    } else {
        None
    };
    Ok(Api2PreparedPage {
        rows: fresh,
        boundary_time_ms: Some(oldest),
        boundary,
        next_from_ms: (stop_reason.is_none()).then_some(oldest),
        stop_reason,
    })
}

fn api2_chunks(rows: Vec<Api2PreparedRow>) -> Vec<Api2Chunk> {
    let mut chunks = Vec::new();
    let mut current = Vec::new();
    let mut media = Vec::new();
    let mut bytes = 2_usize; // JSON array brackets
    for row in rows {
        let extra = row.normalized_bytes + usize::from(!current.is_empty());
        if !current.is_empty() && (current.len() == 200 || bytes + extra > API2_PAGE_TARGET_BYTES) {
            chunks.push(Api2Chunk {
                rows: std::mem::take(&mut current),
                media: std::mem::take(&mut media),
            });
            bytes = 2;
        }
        bytes += row.normalized_bytes + usize::from(!current.is_empty());
        current.push(row.value);
        media.extend(row.media);
    }
    if !current.is_empty() {
        chunks.push(Api2Chunk { rows: current, media });
    }
    chunks
}

impl ExportService {
    /// Runtime-selected profile. The service never discovers another account.
    pub(crate) fn with_retention_profile(profile: &Path) -> Self {
        Self {
            slot: Mutex::new(Slot::default()),
            retention_profile: Some(profile.to_path_buf()),
        }
    }

    pub(crate) fn open_download(
        &self,
        job_id: &str,
        token: &str,
        message: &str,
        attachment: &str,
    ) -> Result<DownloadSink> {
        let handle = self.lookup(job_id, token)?;
        let writer = self.with_job(&handle, |job| {
            if handle.api2_active.load(Ordering::Acquire) {
                return Err(export_job::Error::InvalidState.into());
            }
            job.open_media_writer(message, attachment)
                .map_err(Into::into)
        })?;
        Ok(DownloadSink {
            writer,
            _job: handle,
        })
    }
    fn reserve_start(&self) -> Result<StartReservation<'_>> {
        let mut slot = try_lock(&self.slot)?;
        if slot.closed {
            return Err(cancelled());
        }
        if slot.starting
            || slot
                .current
                .as_ref()
                .is_some_and(|job| !job.terminal.load(Ordering::Acquire))
        {
            return Err(ExportServiceError::Busy);
        }
        slot.starting = true;
        Ok(StartReservation {
            service: self,
            active: true,
        })
    }
    fn lookup(&self, id: &str, token: &str) -> Result<Arc<JobHandle>> {
        let id = hex::<16>(id).ok_or(ExportServiceError::NotFound)?;
        let token = Zeroizing::new(hex::<32>(token).ok_or(ExportServiceError::NotFound)?);
        let job = {
            let slot = try_lock(&self.slot)?;
            slot.current.clone().ok_or(ExportServiceError::NotFound)?
        };
        // Both fixed-length values are compared even when the first differs.
        let same = job.id.ct_eq(&id) & job.token.as_slice().ct_eq(token.as_slice());
        if !bool::from(same) {
            return Err(ExportServiceError::NotFound);
        }
        Ok(job)
    }
    fn with_job<T>(
        &self,
        handle: &JobHandle,
        action: impl FnOnce(&mut ExportJob) -> Result<T>,
    ) -> Result<T> {
        let mut job = match handle.job.try_lock() {
            Ok(job) => job,
            Err(TryLockError::WouldBlock) => return Err(ExportServiceError::Busy),
            Err(TryLockError::Poisoned(_)) => {
                // An unexpected panic revokes the job; runtime teardown owns
                // cleanup. In particular, history remains a read-only command.
                handle.cancel.cancel();
                return Err(io_error());
            }
        };
        let result = action(&mut job);
        handle.terminal.store(
            terminal(job.state()) && !job.has_external_media(),
            Ordering::Release,
        );
        result
    }
    fn start(&self, value: &Value) -> Result<Value> {
        let account = value["accountId"].as_str().ok_or_else(invalid)?;
        if !crate::mdata_login::account_id(account) {
            return Err(invalid());
        }
        export_render::validate_chat(&value["chat"]).map_err(|_| invalid())?;
        if value["chat"]["title"]
            .as_str()
            .is_none_or(|s| s.chars().count() > 512)
        {
            return Err(invalid());
        }
        let directory = selected_directory_url(value["directory"].as_str().ok_or_else(invalid)?)
            .map_err(|_| ExportServiceError::from(export_job::Error::UnsafePath))?;
        let reservation = self.reserve_start()?;
        let destination = directory.join(output_name()?);
        let mut token = Zeroizing::new([0u8; 32]);
        getrandom::getrandom(token.as_mut()).map_err(|_| io_error())?;
        let api2 = Api2Progress::new()?;
        let job = ExportJob::new(&destination, &value["chat"], account)?;
        let mut response = job.snapshot();
        let id =
            hex::<16>(response["jobId"].as_str().ok_or_else(io_error)?).ok_or_else(io_error)?;
        response["jobToken"] = json!(encode_hex(token.as_slice()));
        response["limits"] = json!({"maxPages":MAX_PAGES,"maxMessages":export_render::MAX_MESSAGES,
            "maxAttachments":export_render::MAX_ATTACHMENTS,"maxPageMessages":200,"maxPageBytes":MAX_PAGE_BYTES,
            "maxAudioBytes":MAX_CHUNKED_ASSET_BYTES,"maxChunkBytes":MAX_CHUNK_BYTES});
        let handle = Arc::new(JobHandle {
            id,
            token,
            cancel: job.cancellation(),
            terminal: AtomicBool::new(false),
            history_source: AtomicU64::new(0),
            api2_active: AtomicBool::new(false),
            api2_cancel: HistoryCancel::new(),
            api2: Mutex::new(api2),
            job: Mutex::new(job),
        });
        if let Err(error) = reservation.commit(handle.clone()) {
            handle.cancel.cancel();
            let cleanup = self.with_job(&handle, |job| Ok(job.cancel()));
            if cleanup
                .as_ref()
                .is_ok_and(|v| v["error"] == "cleanup_failed")
            {
                return Err(export_job::Error::CleanupFailed.into());
            }
            return Err(error);
        }
        Ok(response)
    }

    fn claim_api2(&self, handle: &JobHandle) -> Result<(SelectedScope, i64)> {
        self.with_job(handle, |job| {
            // Only the native history host can set this source. It ties the
            // job/token to a live pinned view before credential/socket access.
            // The UI still supplies the view's account/chat identity.
            if handle.history_source.load(Ordering::Acquire) == 0
                || handle.api2_active.load(Ordering::Acquire)
                || !job.can_start_api2()
            {
                return Err(export_job::Error::InvalidState.into());
            }
            let selected =
                SelectedScope::parse(job.account_id(), job.chat_id()).map_err(|_| invalid())?;
            let own_id = job.account_id().parse::<i64>().map_err(|_| invalid())?;
            handle.api2_active.store(true, Ordering::Release);
            Ok((selected, own_id))
        })
    }

    /// Rebind a stale API2 socket only to the same live, already claimed
    /// account and chat. A reconnect never accepts a new scope from QML.
    fn active_api2_scope(&self, handle: &JobHandle, own_id: i64) -> Result<SelectedScope> {
        self.with_job(handle, |job| {
            if handle.history_source.load(Ordering::Acquire) == 0
                || !handle.api2_active.load(Ordering::Acquire)
                || handle.api2_cancel.is_cancelled()
                || handle.cancel.check().is_err()
                || job.state() != State::Exporting
                || job.account_id().parse::<i64>().ok() != Some(own_id)
            {
                return Err(export_job::Error::InvalidState.into());
            }
            SelectedScope::parse(job.account_id(), job.chat_id()).map_err(|_| invalid())
        })
    }

    fn abort_api2(&self, handle: &JobHandle, error: ExportServiceError) -> ExportServiceError {
        // A scope/auth/cancellation failure must never publish a prefix. The
        // independent reader flag interrupts a socket read without taking the
        // job mutex; cancellation of the selected workspace follows.
        handle.api2_cancel.cancel();
        handle.cancel.cancel();
        let cleanup = self.with_job(handle, |job| Ok(job.cancel()));
        if cleanup
            .as_ref()
            .is_ok_and(|value| value["error"] == "cleanup_failed")
        {
            export_job::Error::CleanupFailed.into()
        } else {
            error
        }
    }

    fn finish_api2(
        &self,
        handle: &JobHandle,
        progress: &mut Api2Progress,
        reason: &'static str,
    ) -> Result<Value> {
        if !progress.media_queue.is_empty() {
            return Err(export_job::Error::InvalidState.into());
        }
        progress.reader.take();
        let result = self.with_job(handle, |job| {
            if job.state() != State::Exporting {
                return Err(if job.state() == State::Cancelled {
                    cancelled()
                } else {
                    export_job::Error::InvalidState.into()
                });
            }
            // An explicit empty window may publish an empty *partial* archive.
            // Other failures before the first accepted message have no useful
            // text and leave no archive at all.
            if job.message_count() == 0 && reason != "unsupported_history" {
                return Err(export_job::Error::InvalidState.into());
            }
            let snapshot = if let Some(profile) = self.retention_profile.as_deref() {
                SelectedScope::parse(job.account_id(), job.chat_id())
                    .map_err(|_| invalid())?;
                let owner = job.account_id().parse::<i64>().map_err(|_| invalid())?;
                let chat = job.chat_id().parse::<i64>().map_err(|_| invalid())?;
                let retained = LocalDeletedStream::selected(
                    profile, owner, chat, job.cancellation(),
                ).map_err(export_job::Error::from)?;
                if let Some(mut rows) = retained {
                    job.finish_api2_with_local_deleted(reason, progress.windows_fetched, &mut rows)?
                } else {
                    job.finish_api2(reason, progress.windows_fetched)?
                }
            } else {
                job.finish_api2(reason, progress.windows_fetched)?
            };
            if job.state() != State::Partial {
                return Err(io_error());
            }
            Ok(snapshot)
        });
        match result {
            Ok(snapshot) => {
                progress.done = true;
                Ok(progress.receipt(snapshot))
            }
            Err(error) => Err(self.abort_api2(handle, error)),
        }
    }

    fn stop_api2_history(
        &self,
        handle: &JobHandle,
        progress: &mut Api2Progress,
        reason: &'static str,
    ) -> Result<Value> {
        if !progress.media_queue.iter().any(|task| matches!(task.source, HistoryMediaSource::VideoRef(_))) {
            progress.reader.take();
        }
        progress.next_from_ms = None;
        progress.history_stop_reason = Some(reason);
        if progress.media_queue.is_empty() {
            self.finish_api2(handle, progress, reason)
        } else {
            self.with_job(handle, |job| Ok(progress.receipt(job.snapshot())))
        }
    }

    fn transport_error_api2(
        &self,
        handle: &JobHandle,
        progress: &mut Api2Progress,
        error: TransportError,
    ) -> Result<Value> {
        use TransportError as T;
        // A failed history request may poison its TLS stream. A later VIDEO
        // task can rebind a fresh LOGIN session for this same selected job.
        progress.reader.take();
        let reason = match error {
            T::Cancelled
            | T::TrustPin
            | T::Tls
            | T::Credential
            | T::InvalidToken
            | T::AccountMismatch
            | T::HistoryScope => None,
            T::TimedOut => Some("timeout"),
            T::LimitReached => Some("page_limit"),
            T::Io | T::Protocol | T::ServerRejected | T::Clock => Some("history_error"),
        };
        if let Some(reason) = reason {
            if self.with_job(handle, |job| Ok(job.message_count()))? > 0 {
                return self.stop_api2_history(handle, progress, reason);
            }
        }
        let public_error = match error {
            T::Cancelled => cancelled(),
            T::LimitReached => export_job::Error::LimitReached.into(),
            T::AccountMismatch | T::HistoryScope | T::Credential | T::InvalidToken => {
                export_job::Error::InvalidState.into()
            }
            _ => io_error(),
        };
        Err(self.abort_api2(handle, public_error))
    }

    fn video_transport_error(error: TransportError) -> std::result::Result<MediaError, ExportServiceError> {
        use TransportError as T;
        match error {
            T::Credential | T::InvalidToken | T::AccountMismatch | T::HistoryScope => {
                Err(export_job::Error::InvalidState.into())
            }
            T::Cancelled => Ok(MediaError::Cancelled),
            T::TimedOut => Ok(MediaError::Timeout),
            T::ServerRejected | T::LimitReached => Ok(MediaError::NotAvailable),
            T::TrustPin => Ok(MediaError::TrustPin),
            T::Tls => Ok(MediaError::Tls),
            T::Io => Ok(MediaError::Network),
            T::Protocol | T::Clock => Ok(MediaError::InvalidResponse),
        }
    }

    fn resolve_api2_video_once<C: LoginCredentials>(
        &self,
        handle: &JobHandle,
        progress: &mut Api2Progress,
        source: &api2_history_transport::HistoryVideoRef,
        credentials: &C,
    ) -> Result<std::result::Result<Vec<Zeroizing<String>>, MediaError>> {
        if progress.reader.is_none() {
            let selected = self.active_api2_scope(
                handle,
                progress.own_id.ok_or_else(io_error)?,
            )?;
            match api2_history_transport::open_selected_reader(
                selected,
                credentials,
                handle.api2_cancel.clone(),
            ) {
                Ok(reader) => progress.reader = Some(reader),
                Err(error) => return Self::video_transport_error(error).map(Err),
            }
        }
        let result = progress.reader.as_mut().ok_or_else(io_error)?.resolve_video_sources(source);
        match result {
            Ok(url) => Ok(Ok(url)),
            Err(error) => {
                if error != TransportError::ServerRejected {
                    progress.reader.take();
                }
                Self::video_transport_error(error).map(Err)
            }
        }
    }

    fn resolve_api2_video<C: LoginCredentials>(
        &self,
        handle: &JobHandle,
        progress: &mut Api2Progress,
        source: &api2_history_transport::HistoryVideoRef,
        credentials: &C,
    ) -> Result<std::result::Result<Vec<Zeroizing<String>>, MediaError>> {
        retry_video_resolution(
            || self.resolve_api2_video_once(handle, progress, source, credentials),
            || wait_api2_history_retry(&handle.api2_cancel).map_err(|_| cancelled()),
        )
    }

    /// Common production/synthetic ingestion path. One already validated
    /// network window becomes bounded, normalized SQLite rows or a terminal
    /// Partial archive. The caller holds only the reader-state mutex here.
    fn ingest_api2_page(
        &self,
        handle: &JobHandle,
        progress: &mut Api2Progress,
        page: &HistoryPage,
        from_ms: i64,
    ) -> Result<Value> {
        // A signed media URL is consumed before the next history window. This
        // keeps the lifetime of each URL bounded by its own page, including on
        // very large chats, and prevents a caller from bypassing the drain.
        if progress.done || progress.next_from_ms != Some(from_ms)
            || !progress.media_queue.is_empty()
        {
            return Err(export_job::Error::InvalidState.into());
        }
        progress.windows_fetched = progress
            .windows_fetched
            .checked_add(1)
            .filter(|n| *n <= API2_MAX_WINDOWS)
            .ok_or_else(io_error)?;
        let prepared = match prepare_api2_page(
            page,
            from_ms,
            progress.own_id.ok_or_else(io_error)?,
            progress,
        ) {
            Ok(value) => value,
            Err(reason) => return self.stop_api2_history(handle, progress, reason),
        };
        let fresh: Vec<_> = prepared
            .rows
            .iter()
            .map(|row| (row.id_key, row.fingerprint))
            .collect();
        let chunks = api2_chunks(prepared.rows);
        let new_media_count: usize = chunks.iter().map(|chunk| chunk.media.len()).sum();
        let new_media_bytes: usize = chunks
            .iter()
            .flat_map(|chunk| &chunk.media)
            .map(|task| api2_media_source_bytes(&task.source))
            .sum();
        if progress.media_queue.len().saturating_add(new_media_count)
            > API2_MAX_MEDIA_QUEUED_TASKS
            || progress.media_url_bytes.saturating_add(new_media_bytes)
                > API2_MAX_MEDIA_URL_BYTES
        {
            return self.stop_api2_history(handle, progress, "attachment_limit");
        }
        let capacity = usize::try_from(MAX_PAGES.saturating_sub(progress.page_sequence))
            .map_err(|_| io_error())?;
        if chunks.len() > capacity {
            return self.stop_api2_history(handle, progress, "page_limit");
        }
        let committed = self.with_job(handle, |job| {
            if !handle.api2_active.load(Ordering::Acquire) || job.state() != State::Exporting {
                return Err(export_job::Error::InvalidState.into());
            }
            for chunk in &chunks {
                let receipt = job.page(progress.page_sequence, &chunk.rows)?;
                let Some(reserved) = receipt["media"].as_array() else {
                    return Err(io_error());
                };
                if reserved.len() != chunk.media.len()
                    || reserved.iter().zip(&chunk.media).any(|(media, task)| {
                        media["messageId"] != task.message_id
                            || media["attachmentId"] != task.attachment_id
                    })
                {
                    return Err(io_error());
                }
                progress.page_sequence += 1;
            }
            Ok(job.snapshot())
        });
        let snapshot = match committed {
            Ok(value) => value,
            Err(ExportServiceError::Job(export_job::Error::LimitReached)) => {
                return self.stop_api2_history(handle, progress, "message_limit");
            }
            Err(error) => return Err(self.abort_api2(handle, error)),
        };
        for (id, fingerprint) in fresh {
            progress.seen.insert(id, fingerprint);
        }
        // Direct photo/voice URLs may expire. VIDEO_PLAY references are
        // resolved on demand, so keep their relative order but process them
        // after the direct URLs from this window.
        let mut video_refs = VecDeque::new();
        for chunk in chunks {
            for task in chunk.media {
                if matches!(task.source, HistoryMediaSource::VideoRef(_)) {
                    video_refs.push_back(task);
                } else {
                    progress.media_queue.push_back(task);
                }
            }
        }
        progress.media_queue.extend(video_refs);
        progress.media_url_bytes += new_media_bytes;
        progress.media_total += new_media_count as u32;
        progress.boundary_time_ms = prepared.boundary_time_ms;
        progress.boundary = prepared.boundary;
        progress.next_from_ms = prepared.next_from_ms;
        if let Some(reason) = prepared.stop_reason {
            return self.stop_api2_history(handle, progress, reason);
        }
        if progress.windows_fetched >= API2_MAX_WINDOWS {
            return self.stop_api2_history(handle, progress, "page_limit");
        }
        Ok(progress.receipt(snapshot))
    }

    fn process_api2_media<C: LoginCredentials>(
        &self,
        handle: &JobHandle,
        progress: &mut Api2Progress,
        credentials: &C,
    ) -> Result<Value> {
        if let Some(previous) = progress.last_media_completed {
            while Instant::now() < previous + API2_MEDIA_SPACING {
                if handle.api2_cancel.is_cancelled() {
                    return Err(self.abort_api2(handle, cancelled()));
                }
                thread::sleep(
                    (previous + API2_MEDIA_SPACING)
                        .saturating_duration_since(Instant::now())
                        .min(API2_MEDIA_WAIT_SLICE),
                );
            }
        }
        let task = progress.media_queue.pop_front().ok_or_else(io_error)?;
        progress.media_url_bytes = progress.media_url_bytes.saturating_sub(api2_media_source_bytes(&task.source));
        let writer = self.with_job(handle, |job| {
            if !handle.api2_active.load(Ordering::Acquire) || job.state() != State::Exporting {
                return Err(export_job::Error::InvalidState.into());
            }
            job.start_media(&task.message_id, &task.attachment_id)?;
            job.open_media_writer(&task.message_id, &task.attachment_id)
                .map_err(Into::into)
        });
        let mut writer = match writer {
            Ok(value) => value,
            Err(error) => return Err(self.abort_api2(handle, error)),
        };
        let download = match &task.source {
            HistoryMediaSource::AudioUrl(url) => {
                api2_media_download::download_audio(url, &handle.api2_cancel, &mut writer)
            }
            HistoryMediaSource::PhotoUrl(url) => {
                api2_media_download::download_photo(url, &handle.api2_cancel, &mut writer)
            }
            HistoryMediaSource::VideoRef(reference) => {
                match self.resolve_api2_video(handle, progress, reference, credentials) {
                    Ok(Ok(sources)) => {
                        let deadline = Instant::now()
                            + api2_media_download::MAX_MEDIA_TRANSFER_DURATION;
                        download_video_with_fallback(
                            &sources,
                            &mut writer,
                            |url, output| api2_media_download::download_video(
                                url,
                                &handle.api2_cancel,
                                output,
                                deadline,
                            ),
                            |output| output.rewind_truncate().map_err(MediaError::Storage),
                            || wait_api2_history_retry(&handle.api2_cancel)
                                .map_err(|_| MediaError::Cancelled),
                        )
                    }
                    Ok(Err(error)) => Err(error),
                    Err(error) => {
                        let _ = writer.close();
                        drop(writer);
                        return Err(self.abort_api2(handle, error));
                    }
                }
            }
        };
        let close = writer.close();
        drop(writer);
        let media_status = match download {
            Ok(_) if close.is_ok() => ("ready", None),
            Ok(_) => ("error", Some("download_failed")),
            Err(MediaError::Storage(_)) => ("error", Some("download_failed")),
            Err(MediaError::Cancelled) => ("cancelled", Some("cancelled")),
            Err(error) => ("unavailable", Some(error.reason())),
        };
        let settled = self.with_job(handle, |job| {
            job.complete_media(
                &task.message_id,
                &task.attachment_id,
                media_status.0,
                media_status.1,
            )
            .map_err(Into::into)
        });
        let snapshot = match settled {
            Ok(value) => value,
            Err(error) => return Err(self.abort_api2(handle, error)),
        };
        if let Err(error) = close {
            return Err(self.abort_api2(handle, export_job::Error::from(error).into()));
        }
        if let Err(MediaError::Storage(error)) = download {
            return Err(self.abort_api2(handle, export_job::Error::from(error).into()));
        }
        if handle.api2_cancel.is_cancelled() || handle.cancel.check().is_err() {
            return Err(self.abort_api2(handle, cancelled()));
        }
        progress.media_processed += 1;
        progress.last_media_completed = Some(Instant::now());
        match (progress.media_queue.is_empty(), progress.history_stop_reason) {
            (true, Some(reason)) => self.finish_api2(handle, progress, reason),
            _ => Ok(progress.receipt(snapshot)),
        }
    }

    /// One bound API2 GET_HISTORY window per call. The value can only identify
    /// an existing selected job; it cannot change account, chat, host, cursor,
    /// page size, credentials, or output directory. No response content leaves
    /// Rust except counts and a final local file URL.
    pub(crate) fn api2_next<C: LoginCredentials>(
        &self,
        value: &Value,
        credentials: &C,
    ) -> Result<Value> {
        exact(value, &["schema", "jobId", "jobToken"])?;
        let handle = self.lookup(
            value["jobId"]
                .as_str()
                .ok_or(ExportServiceError::NotFound)?,
            value["jobToken"]
                .as_str()
                .ok_or(ExportServiceError::NotFound)?,
        )?;
        let mut progress = try_lock(&handle.api2)?;
        if progress.done {
            return self.with_job(&handle, |job| Ok(progress.receipt(job.snapshot())));
        }
        if !progress.media_queue.is_empty() {
            return self.process_api2_media(&handle, &mut progress, credentials);
        }
        if let Some(reason) = progress.history_stop_reason {
            return self.finish_api2(&handle, &mut progress, reason);
        }
        if progress.reader.is_none() {
            let first_connection = progress.own_id.is_none();
            let selected = if let Some(own_id) = progress.own_id {
                match self.active_api2_scope(&handle, own_id) {
                    Ok(value) => value,
                    Err(error) => return Err(self.abort_api2(&handle, error)),
                }
            } else {
                let (selected, own_id) = self.claim_api2(&handle)?;
                progress.own_id = Some(own_id);
                selected
            };
            let reader = match api2_history_transport::open_selected_reader(
                selected,
                credentials,
                handle.api2_cancel.clone(),
            ) {
                Ok(value) => value,
                Err(error) => return self.transport_error_api2(&handle, &mut progress, error),
            };
            if first_connection {
                // Anchor newest cursor after the first LOGIN, not after a
                // reconnect: later sessions must keep the saved cursor.
                let from = match HistoryReader::initial_from_ms() {
                    Ok(value) => value,
                    Err(error) => return self.transport_error_api2(&handle, &mut progress, error),
                };
                progress.next_from_ms = Some(from);
            }
            progress.reader = Some(reader);
        }
        let from_ms = progress.next_from_ms.ok_or_else(io_error)?;
        let page = match progress.reader.as_mut().unwrap().read_backward(from_ms) {
            Ok(value) => value,
            Err(error) if matches!(error, TransportError::Io | TransportError::TimedOut) => {
                // The bound socket can expire while a page's media is being
                // downloaded. Retry this *uncommitted* cursor once, with a
                // cancellable pause, on the same selected account and chat.
                progress.reader.take();
                if let Err(error) = wait_api2_history_retry(&handle.api2_cancel) {
                    return self.transport_error_api2(&handle, &mut progress, error);
                }
                let selected = match self.active_api2_scope(
                    &handle,
                    progress.own_id.ok_or_else(io_error)?,
                ) {
                    Ok(value) => value,
                    Err(error) => return Err(self.abort_api2(&handle, error)),
                };
                let reader = match api2_history_transport::open_selected_reader(
                    selected,
                    credentials,
                    handle.api2_cancel.clone(),
                ) {
                    Ok(value) => value,
                    Err(error) => return self.transport_error_api2(&handle, &mut progress, error),
                };
                progress.reader = Some(reader);
                match progress.reader.as_mut().unwrap().read_backward(from_ms) {
                    Ok(value) => value,
                    Err(error) => return self.transport_error_api2(&handle, &mut progress, error),
                }
            }
            Err(error) => return self.transport_error_api2(&handle, &mut progress, error),
        };
        self.ingest_api2_page(&handle, &mut progress, &page, from_ms)
    }

    /// Selected-chat command body, without the outer runtime operation. SDK
    /// media downloads receive an isolated lease, never the sealed asset path.
    pub fn execute(&self, action: &str, value: &Value) -> Result<Value> {
        let keys: &[&str] = match action {
            "start" => &["schema", "accountId", "chat", "directory"],
            "page" => &["schema", "jobId", "jobToken", "sequence", "messages"],
            "media-start" => &["schema", "jobId", "jobToken", "messageId", "attachmentId"],
            "media-chunk" => &[
                "schema",
                "jobId",
                "jobToken",
                "messageId",
                "attachmentId",
                "offset",
                "data",
            ],
            "media" => &[
                "schema",
                "jobId",
                "jobToken",
                "messageId",
                "attachmentId",
                "status",
                "reason",
            ],
            "finish" => &["schema", "jobId", "jobToken", "completion"],
            "status" | "history" | "cancel" => &["schema", "jobId", "jobToken"],
            _ => return Err(ExportServiceError::UnsupportedOperation),
        };
        exact(value, keys)?;
        if action == "start" {
            return self.start(value);
        }
        let handle = self.lookup(
            value["jobId"]
                .as_str()
                .ok_or(ExportServiceError::NotFound)?,
            value["jobToken"]
                .as_str()
                .ok_or(ExportServiceError::NotFound)?,
        )?;
        if action == "cancel" {
            handle.cancel.cancel();
            handle.api2_cancel.cancel();
        }
        let result = match action {
            "page" => {
                let sequence = value["sequence"]
                    .as_u64()
                    .filter(|n| *n < MAX_PAGES as u64)
                    .ok_or_else(invalid)?;
                let rows = value["messages"]
                    .as_array()
                    .filter(|a| !a.is_empty() && a.len() <= 200)
                    .ok_or_else(invalid)?;
                if serde_json::to_vec(rows).map_err(|_| invalid())?.len() > MAX_PAGE_BYTES {
                    return Err(export_job::Error::LimitReached.into());
                }
                self.with_job(&handle, |job| {
                    if handle.api2_active.load(Ordering::Acquire) {
                        return Err(export_job::Error::InvalidState.into());
                    }
                    job.page(sequence as u32, rows).map_err(Into::into)
                })
            }
            "media-start" => {
                let message = identifier(&value["messageId"])?;
                let attachment = identifier(&value["attachmentId"])?;
                self.with_job(&handle, |job| {
                    if handle.api2_active.load(Ordering::Acquire) {
                        return Err(export_job::Error::InvalidState.into());
                    }
                    job.start_media(message, attachment).map_err(Into::into)
                })
            }
            "media-chunk" => {
                let message = identifier(&value["messageId"])?;
                let attachment = identifier(&value["attachmentId"])?;
                let offset = value["offset"]
                    .as_u64()
                    .filter(|n| *n < MAX_CHUNKED_ASSET_BYTES)
                    .ok_or_else(invalid)?;
                let data = value["data"]
                    .as_str()
                    .filter(|s| (4..=65_536).contains(&s.len()))
                    .ok_or_else(invalid)?;
                let decoded = Zeroizing::new(STANDARD.decode(data).map_err(|_| invalid())?);
                if decoded.is_empty()
                    || decoded.len() > MAX_CHUNK_BYTES
                    || STANDARD.encode(decoded.as_slice()) != data
                {
                    return Err(invalid());
                }
                if offset
                    .checked_add(decoded.len() as u64)
                    .is_none_or(|n| n > MAX_CHUNKED_ASSET_BYTES)
                {
                    return Err(export_job::Error::LimitReached.into());
                }
                self.with_job(&handle, |job| {
                    if handle.api2_active.load(Ordering::Acquire) {
                        return Err(export_job::Error::InvalidState.into());
                    }
                    let next = job.write_chunk(message, attachment, offset, decoded.as_slice())?;
                    Ok(json!({"schema":1,"jobId":value["jobId"],"offset":next}))
                })
            }
            "media" => {
                let message = identifier(&value["messageId"])?;
                let attachment = identifier(&value["attachmentId"])?;
                let status = value["status"].as_str().ok_or_else(invalid)?;
                let reason = if value["reason"].is_null() {
                    None
                } else {
                    Some(value["reason"].as_str().ok_or_else(invalid)?)
                };
                self.with_job(&handle, |job| {
                    if handle.api2_active.load(Ordering::Acquire) {
                        return Err(export_job::Error::InvalidState.into());
                    }
                    job.complete_media(message, attachment, status, reason)
                        .map_err(Into::into)
                })
            }
            "finish" => self.with_job(&handle, |job| {
                if handle.api2_active.load(Ordering::Acquire) {
                    return Err(export_job::Error::InvalidState.into());
                }
                job.finish(&value["completion"]).map_err(Into::into)
            }),
            "history" => self.with_job(&handle, |job| Ok(job.history())),
            "cancel" => self.with_job(&handle, |job| Ok(job.cancel())),
            _ => self.with_job(&handle, |job| {
                // status may finish a pending cancellation after a previous
                // worker released the mutex. history above never performs I/O.
                if handle.cancel.check().is_err() && !terminal(job.state()) {
                    job.cancel();
                }
                Ok(job.snapshot())
            }),
        };
        result.map(public_result)
    }

    /// Host-only source binding. These methods have no JSON route and must only
    /// receive observations from the already selected native account/chat view.
    pub fn bind_history(&self, job_id: &str, job_token: &str, source: u64) -> Result<()> {
        let job = self.lookup(job_id, job_token)?;
        self.with_job(&job, |job| job.bind_history(source).map_err(Into::into))?;
        job.history_source.store(source, Ordering::Release);
        Ok(())
    }
    /// Trusted native view destruction revokes the selected export immediately,
    /// even while its worker owns the job mutex. Signal only; publication checks
    /// this cancellation token. Completed archives remain published as before.
    pub fn invalidate_history(&self, job_id: &str, job_token: &str, source: u64) -> Result<()> {
        let job = self.lookup(job_id, job_token)?;
        if source == 0 || job.history_source.load(Ordering::Acquire) != source {
            return Err(export_job::Error::InvalidState.into());
        }
        job.cancel.cancel();
        job.api2_cancel.cancel();
        Ok(())
    }
    #[allow(clippy::too_many_arguments)]
    pub fn observe_history(
        &self,
        job_id: &str,
        job_token: &str,
        source: u64,
        direction: &str,
        count: u64,
        pending_done: bool,
        more_backward: bool,
        more_forward: bool,
    ) -> Result<()> {
        let job = self.lookup(job_id, job_token)?;
        self.with_job(&job, |job| {
            job.observe_history(
                source,
                direction,
                count,
                pending_done,
                more_backward,
                more_forward,
            )
            .map_err(Into::into)
        })
    }

    /// Irreversible host shutdown gate. No job lock, filesystem work, or waiting
    /// for a worker here. A start already outside the slot cannot commit after
    /// this closes admission. The host then waits for its own worker queue.
    pub fn cancel_pending(&self) {
        let current = {
            let mut slot = self.slot.lock().unwrap_or_else(|e| e.into_inner());
            slot.closed = true;
            slot.current.clone()
        };
        if let Some(job) = current {
            job.cancel.cancel();
            job.api2_cancel.cancel();
        }
    }
}
impl Drop for ExportService {
    fn drop(&mut self) {
        let slot = self.slot.get_mut().unwrap_or_else(|e| e.into_inner());
        slot.closed = true;
        let current = slot.current.take();
        if let Some(job) = current {
            job.cancel.cancel();
            job.api2_cancel.cancel();
            drop(job);
        }
    }
}

#[cfg(all(test, windows))]
#[path = "export_service_tests.rs"]
mod tests;

#[cfg(all(test, windows))]
#[path = "export_native_media_tests.rs"]
mod native_media_tests;
