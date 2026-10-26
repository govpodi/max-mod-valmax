//! Durable retention indexes compatible with the former Python marker stores.
//! A separate store holds DPAPI-protected text already received by an account.
//! Event classification and media remain outside this account-scoped store.
#![cfg(windows)]

use crate::settings_io::{SafeStorePath, StoreError, sha256};
use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine as _};
use rusqlite::{Connection, OpenFlags, OptionalExtension, params, params_from_iter};
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet, ffi::c_void, fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom},
    os::windows::{fs::OpenOptionsExt, io::AsRawHandle},
    path::{Path, PathBuf}, time::{SystemTime, UNIX_EPOCH},
};
use zeroize::{Zeroize, Zeroizing};

pub const MARKER_FILE: &str = "retained-messages.sqlite3";
pub const RECEIVED_FILE: &str = "received-message-ids.sqlite3";
pub const CONTENT_FILE: &str = "retained-message-content.sqlite3";
pub const MAX_EVENT_IDS: usize = 4096;
pub const MAX_LOOKUP_IDS: usize = 256;
pub const MAX_CAPTURE_ROWS: usize = 128;
const MAX_KNOWN_IDS: i64 = 65536;
const MAX_DB_BYTES: u64 = 128 * 1024 * 1024;
const MAX_JOURNAL_BYTES: u64 = MAX_DB_BYTES + 1024 * 1024;
const JOURNAL_MAGIC: [u8; 8] = [0xd9, 0xd5, 0x05, 0xf9, 0x20, 0xa1, 0x63, 0xd7];
const MARKER_APP_ID: i64 = 0x5641_4c52;
const RECEIVED_APP_ID: i64 = 0x5641_4c4b;
const CONTENT_APP_ID: i64 = 0x5641_4c43;
const MAX_TEXT_BYTES: usize = 64 * 1024;
// 6x covers worst-case JSON escaping of UTF-8 bytes; 512 bytes covers each
// item's keys, IDs and timestamps. Keep the full response under the QML 8 MiB cap.
const MAX_INLINE_PAGE_ESTIMATED_BYTES: usize = 6 * 1024 * 1024;
const META_SQL: &str = "CREATE TABLE retention_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL CHECK(revision>=0))";
const MARKER_SQL: &str = "CREATE TABLE deleted_messages (account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id TEXT NOT NULL, deleted_at_ms INTEGER NOT NULL CHECK(deleted_at_ms>=0), PRIMARY KEY(account_id,chat_id,message_id)) WITHOUT ROWID";
const RECEIVED_SQL: &str = "CREATE TABLE received_messages (account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id TEXT NOT NULL, PRIMARY KEY(account_id,chat_id,message_id)) WITHOUT ROWID";
const CONTENT_SQL_V1: &str = "CREATE TABLE retained_content (account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id TEXT NOT NULL, received_at_ms INTEGER NOT NULL CHECK(received_at_ms>=0), protected_text BLOB NOT NULL, deleted_at_ms INTEGER CHECK(deleted_at_ms IS NULL OR deleted_at_ms>=0), PRIMARY KEY(account_id,chat_id,message_id)) WITHOUT ROWID";
const CONTENT_SQL: &str = "CREATE TABLE retained_content (account_id TEXT NOT NULL, chat_id TEXT NOT NULL, message_id TEXT NOT NULL, received_at_ms INTEGER NOT NULL CHECK(received_at_ms>=0), protected_text BLOB NOT NULL, deleted_at_ms INTEGER CHECK(deleted_at_ms IS NULL OR deleted_at_ms>=0), order_key INTEGER CHECK(order_key IS NULL OR order_key>0), PRIMARY KEY(account_id,chat_id,message_id)) WITHOUT ROWID";
const CONTENT_ORDER_COLUMN: &str = "ALTER TABLE retained_content ADD COLUMN order_key INTEGER CHECK(order_key IS NULL OR order_key>0)";
const CONTENT_ORDER_INDEX_SQL: &str = "CREATE INDEX retained_content_order_idx ON retained_content(account_id,chat_id,order_key,CAST(message_id AS INTEGER)) WHERE deleted_at_ms IS NOT NULL AND order_key IS NOT NULL";

type Result<T> = std::result::Result<T, Error>;
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Error { InvalidRequest, StaleCursor, InvalidStore, UnsafePath, StoreBusy, IoError, NoSpace, KnownCapacity }
impl Error {
    pub fn code(self) -> &'static str { match self {
        Self::InvalidRequest => "invalid_request", Self::StaleCursor => "stale_cursor",
        Self::InvalidStore => "invalid_store",
        Self::UnsafePath => "unsafe_path", Self::StoreBusy => "store_busy",
        Self::IoError => "io_error", Self::NoSpace => "no_space",
        Self::KnownCapacity => "known_capacity",
    }}
}
fn path_error(error: StoreError) -> Error { match error {
    StoreError::UnsafePath => Error::UnsafePath,
    StoreError::StoreBusy => Error::StoreBusy,
    _ => Error::IoError,
}}
fn sql_error(error: rusqlite::Error) -> Error { match error.sqlite_error_code() {
    Some(rusqlite::ErrorCode::DiskFull) => Error::NoSpace,
    Some(rusqlite::ErrorCode::DatabaseBusy | rusqlite::ErrorCode::DatabaseLocked) => Error::StoreBusy,
    _ => Error::InvalidStore,
}}
fn validate_ids(owner: i64, chat: i64, ids: &[i64], limit: usize) -> Result<()> {
    if owner <= 0 || chat == 0 || ids.is_empty() || ids.len() > limit
        || ids.iter().any(|&id| id <= 0) { return Err(Error::InvalidRequest); }
    let unique: HashSet<_> = ids.iter().collect();
    if unique.len() != ids.len() { return Err(Error::InvalidRequest); }
    Ok(())
}
fn sidecar_exists(path: &Path) -> Result<bool> {
    match std::fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
        Err(_) => Err(Error::UnsafePath),
    }
}

#[repr(C)] #[derive(Default, Clone, Copy)]
struct FileTime { low: u32, high: u32 }
#[repr(C)] #[derive(Default, Clone, Copy)]
struct FileInfo {
    attributes: u32, creation: FileTime, access: FileTime, write: FileTime,
    volume: u32, size_high: u32, size_low: u32, links: u32, index_high: u32, index_low: u32,
}
impl FileInfo {
    fn identity(self) -> (u32, u32, u32) { (self.volume, self.index_high, self.index_low) }
    fn size(self) -> u64 { (u64::from(self.size_high) << 32) | u64::from(self.size_low) }
}
#[allow(clashing_extern_declarations)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn GetFileInformationByHandle(handle: *mut c_void, info: *mut FileInfo) -> i32;
    fn GetFinalPathNameByHandleW(handle: *mut c_void, buffer: *mut u16, len: u32, flags: u32) -> u32;
    fn CompareStringOrdinal(left: *const u16, left_len: i32, right: *const u16, right_len: i32, ignore_case: i32) -> i32;
}
fn file_info(handle: *mut c_void, path: &Path) -> Result<FileInfo> {
    let mut info = FileInfo::default();
    if unsafe { GetFileInformationByHandle(handle, &mut info) } == 0
        || info.attributes & 0x400 != 0 || info.attributes & 0x10 != 0 || info.links != 1 {
        return Err(Error::UnsafePath);
    }
    let mut buffer = [0u16; 32768];
    let len = unsafe { GetFinalPathNameByHandleW(handle, buffer.as_mut_ptr(), buffer.len() as u32, 0) } as usize;
    if len == 0 || len >= buffer.len() { return Err(Error::UnsafePath); }
    let actual = String::from_utf16(&buffer[..len]).map_err(|_| Error::UnsafePath)?;
    let actual = actual.strip_prefix(r"\\?\").unwrap_or(&actual);
    let expected = path.to_str().ok_or(Error::UnsafePath)?.replace('/', r"\");
    let a: Vec<u16> = actual.encode_utf16().collect();
    let b: Vec<u16> = expected.encode_utf16().collect();
    if unsafe { CompareStringOrdinal(a.as_ptr(), a.len() as i32, b.as_ptr(), b.len() as i32, 1) } != 2 {
        return Err(Error::UnsafePath);
    }
    Ok(info)
}
struct Pin { file: File, info: FileInfo }
fn pin(path: &Path) -> Result<Option<Pin>> {
    let file = match OpenOptions::new().read(true).share_mode(0x1 | 0x2).custom_flags(0x0020_0000).open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied => return Err(Error::UnsafePath),
        Err(_) => return Err(Error::IoError),
    };
    let info = file_info(file.as_raw_handle(), path)?;
    Ok(Some(Pin { file, info }))
}
fn verify_connection(connection: &Connection, path: &Path, expected: FileInfo) -> Result<()> {
    let mut handle: *mut c_void = std::ptr::null_mut();
    let rc = unsafe { rusqlite::ffi::sqlite3_file_control(connection.handle(), c"main".as_ptr(),
        rusqlite::ffi::SQLITE_FCNTL_WIN32_GET_HANDLE, (&mut handle as *mut *mut c_void).cast()) };
    if rc != rusqlite::ffi::SQLITE_OK || handle.is_null() || handle as isize == -1
        || file_info(handle, path)?.identity() != expected.identity() { return Err(Error::UnsafePath); }
    Ok(())
}
fn open_db(path: &Path, pin: &Pin, write: bool) -> Result<Connection> {
    let flags = (if write { OpenFlags::SQLITE_OPEN_READ_WRITE } else { OpenFlags::SQLITE_OPEN_READ_ONLY })
        | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE | OpenFlags::SQLITE_OPEN_NO_MUTEX;
    let connection = Connection::open_with_flags(path, flags).map_err(sql_error)?;
    verify_connection(&connection, path, pin.info)?;
    connection.busy_timeout(std::time::Duration::from_secs(2)).map_err(sql_error)?;
    connection.execute_batch("PRAGMA trusted_schema=OFF; PRAGMA mmap_size=0; PRAGMA temp_store=MEMORY;").map_err(sql_error)?;
    Ok(connection)
}
fn check_journal(pin: &Pin) -> Result<()> {
    let size = pin.info.size();
    if size > MAX_JOURNAL_BYTES || size > 0 && size < 8 { return Err(Error::InvalidStore); }
    if size == 0 { return Ok(()); }
    let mut file = pin.file.try_clone().map_err(|_| Error::IoError)?;
    let mut header = [0u8; 8];
    file.read_exact(&mut header).map_err(|_| Error::InvalidStore)?;
    if header != [0; 8] && header != JOURNAL_MAGIC { return Err(Error::InvalidStore); }
    if size >= 16 {
        file.seek(SeekFrom::End(-8)).map_err(|_| Error::IoError)?;
        let mut tail = [0u8; 8];
        file.read_exact(&mut tail).map_err(|_| Error::InvalidStore)?;
        if tail == JOURNAL_MAGIC { return Err(Error::InvalidStore); }
    }
    Ok(())
}
fn validate_schema(connection: &Connection, app_id: i64, table_sql: &str) -> Result<(i64, i64)> {
    let found_id: i64 = connection.query_row("PRAGMA application_id", [], |row| row.get(0)).map_err(sql_error)?;
    let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0)).map_err(sql_error)?;
    if found_id != app_id || !(version == 1 || app_id == CONTENT_APP_ID && version == 2) {
        return Err(Error::InvalidStore);
    }
    let mut rows = Vec::new();
    let mut query = connection.prepare("SELECT type,name,sql FROM sqlite_master").map_err(sql_error)?;
    let values = query.query_map([], |row| Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?))).map_err(sql_error)?;
    for value in values { rows.push(value.map_err(sql_error)?); }
    rows.sort();
    let table_name = match app_id {
        MARKER_APP_ID => "deleted_messages",
        RECEIVED_APP_ID => "received_messages",
        CONTENT_APP_ID => "retained_content",
        _ => return Err(Error::InvalidStore),
    };
    let expected_content = if app_id == CONTENT_APP_ID && version == 1 { CONTENT_SQL_V1 } else { table_sql };
    let mut expected = vec![("table".to_owned(), "retention_meta".to_owned(), META_SQL.to_owned()),
        ("table".to_owned(), table_name.to_owned(), expected_content.to_owned())];
    if app_id == CONTENT_APP_ID && version == 2 {
        expected.push(("index".to_owned(), "retained_content_order_idx".to_owned(),
            CONTENT_ORDER_INDEX_SQL.to_owned()));
    }
    expected.sort();
    if rows != expected { return Err(Error::InvalidStore); }
    let mut query = connection.prepare("SELECT singleton,revision FROM retention_meta LIMIT 2").map_err(sql_error)?;
    let values = query.query_map([], |row| Ok((row.get::<_, i64>(0)?, row.get::<_, i64>(1)?))).map_err(sql_error)?;
    let rows = values.collect::<rusqlite::Result<Vec<_>>>().map_err(sql_error)?;
    if rows.len() != 1 || rows[0].0 != 1 || rows[0].1 < 0 { return Err(Error::InvalidStore); }
    Ok((rows[0].1, version))
}
fn create_schema(connection: &mut Connection, app_id: i64, table_sql: &str) -> Result<()> {
    let transaction = connection.transaction().map_err(sql_error)?;
    transaction.execute_batch(META_SQL).map_err(sql_error)?;
    transaction.execute_batch(table_sql).map_err(sql_error)?;
    if app_id == CONTENT_APP_ID {
        transaction.execute_batch(CONTENT_ORDER_INDEX_SQL).map_err(sql_error)?;
    }
    transaction.execute("INSERT INTO retention_meta VALUES(1,0)", []).map_err(sql_error)?;
    let version = if app_id == CONTENT_APP_ID { 2 } else { 1 };
    transaction.execute_batch(&format!("PRAGMA application_id={app_id}; PRAGMA user_version={version};")).map_err(sql_error)?;
    transaction.commit().map_err(sql_error)
}
fn migrate_content_v1(connection: &mut Connection) -> Result<()> {
    let transaction = connection.transaction().map_err(sql_error)?;
    transaction.execute_batch(CONTENT_ORDER_COLUMN).map_err(sql_error)?;
    transaction.execute_batch(CONTENT_ORDER_INDEX_SQL).map_err(sql_error)?;
    transaction.execute_batch("PRAGMA user_version=2").map_err(sql_error)?;
    transaction.commit().map_err(sql_error)
}
fn with_db<T>(path: &SafeStorePath, create: bool, app_id: i64, table_sql: &str,
    operation: impl FnOnce(Option<&mut Connection>, i64) -> Result<T>) -> Result<T> {
    let guarded = match path.begin(create).map_err(path_error)? {
        Some(guarded) => guarded, None => return operation(None, 0),
    };
    let _lock = guarded.writer_lock().map_err(path_error)?;
    let existing = pin(path.path())?;
    if existing.is_none() && !create {
        for suffix in ["-journal", "-wal", "-shm"] {
            let sibling = PathBuf::from(format!("{}{}", path.path().display(), suffix));
            if sidecar_exists(&sibling)? { return Err(Error::InvalidStore); }
        }
        return operation(None, 0);
    }
    let fresh = existing.is_none();
    if fresh { guarded.atomic_create(&[]).map_err(path_error)?; }
    let main = pin(path.path())?.ok_or(Error::UnsafePath)?;
    if main.info.size() > MAX_DB_BYTES { return Err(Error::InvalidStore); }
    for suffix in ["-wal", "-shm"] {
        if sidecar_exists(Path::new(&format!("{}{}", path.path().display(), suffix)))? { return Err(Error::InvalidStore); }
    }
    let journal_path = PathBuf::from(format!("{}-journal", path.path().display()));
    let journal_guard = SafeStorePath::new(&journal_path).map_err(path_error)?
        .begin(create).map_err(path_error)?;
    let mut journal = pin(&journal_path)?;
    if create && journal.is_none() {
        journal_guard.as_ref().ok_or(Error::UnsafePath)?.atomic_create(&[]).map_err(path_error)?;
        journal = pin(&journal_path)?;
    }
    if let Some(ref pin) = journal { check_journal(pin)?; }
    if !fresh {
        let mut header = [0u8; 100];
        main.file.try_clone().map_err(|_| Error::IoError)?.read_exact(&mut header).map_err(|_| Error::InvalidStore)?;
        if &header[..16] != b"SQLite format 3\0" || header[18] != 1 || header[19] != 1 { return Err(Error::InvalidStore); }
        let probe = open_db(path.path(), &main, false)?;
        validate_schema(&probe, app_id, table_sql)?;
    }
    let mut connection = open_db(path.path(), &main, create)?;
    if create {
        connection.execute_batch("PRAGMA locking_mode=EXCLUSIVE; PRAGMA synchronous=FULL;").map_err(sql_error)?;
        let mode: String = connection.query_row("PRAGMA journal_mode=PERSIST", [], |row| row.get(0)).map_err(sql_error)?;
        if !mode.eq_ignore_ascii_case("persist") { return Err(Error::InvalidStore); }
        let page_size: i64 = connection.query_row("PRAGMA page_size", [], |row| row.get(0)).map_err(sql_error)?;
        if !(512..=65536).contains(&page_size) { return Err(Error::InvalidStore); }
        connection.execute_batch(&format!("PRAGMA max_page_count={};", MAX_DB_BYTES / page_size as u64)).map_err(sql_error)?;
        if fresh { create_schema(&mut connection, app_id, table_sql)?; }
    }
    let (mut revision, version) = validate_schema(&connection, app_id, table_sql)?;
    if create && app_id == CONTENT_APP_ID && version == 1 {
        migrate_content_v1(&mut connection)?;
        let (migrated_revision, migrated_version) = validate_schema(&connection, app_id, table_sql)?;
        if migrated_version != 2 { return Err(Error::InvalidStore); }
        revision = migrated_revision;
    }
    operation(Some(&mut connection), revision)
}

#[repr(C)]
struct DataBlob { len: u32, data: *mut u8 }
#[link(name = "crypt32")]
unsafe extern "system" {
    fn CryptProtectData(input: *const DataBlob, description: *const u16, entropy: *const DataBlob,
        reserved: *const c_void, prompt: *const c_void, flags: u32, output: *mut DataBlob) -> i32;
    fn CryptUnprotectData(input: *const DataBlob, description: *mut *mut u16, entropy: *const DataBlob,
        reserved: *const c_void, prompt: *const c_void, flags: u32, output: *mut DataBlob) -> i32;
}
#[link(name = "kernel32")]
unsafe extern "system" { fn LocalFree(pointer: *mut c_void) -> *mut c_void; }
struct OwnedBlob(DataBlob);
impl Drop for OwnedBlob {
    fn drop(&mut self) {
        if !self.0.data.is_null() {
            unsafe {
                if self.0.len != 0 { std::slice::from_raw_parts_mut(self.0.data, self.0.len as usize).zeroize(); }
                LocalFree(self.0.data.cast());
            }
        }
    }
}
fn entropy(owner: i64, chat: i64, message: i64) -> [u8; 32] {
    let mut hash = Sha256::new();
    hash.update(b"ValMAX.RetentionContent.v1\0");
    for id in [owner, chat, message] { hash.update(id.to_le_bytes()); }
    hash.finalize().into()
}
fn dpapi(data: &[u8], owner: i64, chat: i64, message: i64, protect: bool) -> Result<Zeroizing<Vec<u8>>> {
    if data.is_empty() || data.len() > MAX_TEXT_BYTES + 4096 { return Err(Error::InvalidRequest); }
    let mut scoped = entropy(owner, chat, message);
    let source = DataBlob { len: data.len() as u32, data: data.as_ptr().cast_mut() };
    let extra = DataBlob { len: scoped.len() as u32, data: scoped.as_mut_ptr() };
    let mut output = OwnedBlob(DataBlob { len: 0, data: std::ptr::null_mut() });
    let result = unsafe {
        if protect {
            CryptProtectData(&source, std::ptr::null(), &extra, std::ptr::null(),
                std::ptr::null(), 1, &mut output.0)
        } else {
            CryptUnprotectData(&source, std::ptr::null_mut(), &extra, std::ptr::null(),
                std::ptr::null(), 1, &mut output.0)
        }
    };
    scoped.zeroize();
    if result == 0 || output.0.data.is_null() || output.0.len == 0
        || output.0.len as usize > MAX_TEXT_BYTES + 4096 { return Err(Error::InvalidStore); }
    let bytes = unsafe { std::slice::from_raw_parts(output.0.data, output.0.len as usize) };
    Ok(Zeroizing::new(bytes.to_vec()))
}

pub struct RetentionStore { markers: SafeStorePath, received: SafeStorePath, content: SafeStorePath }
#[derive(Debug, PartialEq, Eq)]
pub struct Lookup { pub deleted_ids: Vec<i64>, pub revision: String }
pub struct SavedText { pub id: i64, pub text: Zeroizing<String> }
#[derive(Clone, Copy)]
pub struct IncomingText<'a> {
    pub id: i64, pub received_at_ms: i64, pub order_key: Option<i64>, pub text: &'a str,
}
pub struct DeletedText {
    pub id: i64, pub order_key: Option<i64>,
    pub received_at_ms: i64, pub deleted_at_ms: i64,
    pub text: Zeroizing<String>,
}
pub struct DeletedTextPage {
    pub items: Vec<DeletedText>, pub next_cursor: Option<String>, pub revision: String,
}
struct DeletedCursor {
    owner: i64, chat: i64, revision: String, deleted_at_ms: i64, id: i64,
    legacy_only: bool,
}
struct RangeCursor {
    owner: i64, chat: i64, revision: String,
    from_order_key: i64, to_order_key: i64, order_key: i64, id: i64,
}
fn combine_visibility_revision(content: &str, marker: &str) -> String {
    sha256(format!("valmax-retention-visible-v1:{content}:{marker}").as_bytes())
}
fn deleted_cursor_encode(cursor: &DeletedCursor) -> String {
    let version = if cursor.legacy_only { "l1" } else { "v1" };
    URL_SAFE_NO_PAD.encode(format!("{version}:{}:{}:{}:{}:{}", cursor.owner, cursor.chat,
        cursor.revision, cursor.deleted_at_ms, cursor.id))
}
fn deleted_cursor_decode(value: &str) -> Result<DeletedCursor> {
    if value.is_empty() || value.len() > 256 { return Err(Error::InvalidRequest); }
    let plain = URL_SAFE_NO_PAD.decode(value).map_err(|_| Error::InvalidRequest)?;
    let plain = std::str::from_utf8(&plain).map_err(|_| Error::InvalidRequest)?;
    let fields = plain.split(':').collect::<Vec<_>>();
    if fields.len() != 6 || !matches!(fields[0], "v1" | "l1")
        || fields[3].len() != 64 || !fields[3].bytes().all(|c| c.is_ascii_hexdigit() && !c.is_ascii_uppercase()) {
        return Err(Error::InvalidRequest);
    }
    let parse = |value: &str| -> Result<i64> {
        let number = value.parse::<i64>().map_err(|_| Error::InvalidRequest)?;
        if number.to_string() != value { return Err(Error::InvalidRequest); }
        Ok(number)
    };
    let (owner,chat,deleted_at_ms,id) = (parse(fields[1])?,parse(fields[2])?,parse(fields[4])?,parse(fields[5])?);
    if owner <= 0 || chat == 0 || deleted_at_ms < 0 || id <= 0 { return Err(Error::InvalidRequest); }
    Ok(DeletedCursor { owner, chat, revision: fields[3].to_owned(), deleted_at_ms, id,
        legacy_only: fields[0] == "l1" })
}
fn range_cursor_encode(cursor: &RangeCursor) -> String {
    URL_SAFE_NO_PAD.encode(format!("r1:{}:{}:{}:{}:{}:{}:{}", cursor.owner,
        cursor.chat, cursor.revision, cursor.from_order_key, cursor.to_order_key,
        cursor.order_key, cursor.id))
}
fn range_cursor_decode(value: &str) -> Result<RangeCursor> {
    if value.is_empty() || value.len() > 256 { return Err(Error::InvalidRequest); }
    let plain = URL_SAFE_NO_PAD.decode(value).map_err(|_| Error::InvalidRequest)?;
    let plain = std::str::from_utf8(&plain).map_err(|_| Error::InvalidRequest)?;
    let fields = plain.split(':').collect::<Vec<_>>();
    if fields.len() != 8 || fields[0] != "r1" || fields[3].len() != 64
        || !fields[3].bytes().all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase()) {
        return Err(Error::InvalidRequest);
    }
    let parse = |raw: &str| -> Result<i64> {
        let value = raw.parse::<i64>().map_err(|_| Error::InvalidRequest)?;
        if value.to_string() != raw { return Err(Error::InvalidRequest); }
        Ok(value)
    };
    let cursor = RangeCursor {
        owner: parse(fields[1])?, chat: parse(fields[2])?, revision: fields[3].to_owned(),
        from_order_key: parse(fields[4])?, to_order_key: parse(fields[5])?,
        order_key: parse(fields[6])?, id: parse(fields[7])?,
    };
    if cursor.owner <= 0 || cursor.chat == 0 || cursor.from_order_key <= 0
        || cursor.to_order_key < cursor.from_order_key || cursor.order_key < cursor.from_order_key
        || cursor.order_key > cursor.to_order_key || cursor.id <= 0 {
        return Err(Error::InvalidRequest);
    }
    Ok(cursor)
}
impl RetentionStore {
    pub fn new(profile: impl AsRef<Path>) -> Result<Self> {
        let profile = profile.as_ref();
        Ok(Self {
            markers: SafeStorePath::new(profile.join(MARKER_FILE)).map_err(path_error)?,
            received: SafeStorePath::new(profile.join(RECEIVED_FILE)).map_err(path_error)?,
            content: SafeStorePath::new(profile.join(CONTENT_FILE)).map_err(path_error)?,
        })
    }
    pub fn preflight(&self) -> Result<()> {
        with_db(&self.markers, true, MARKER_APP_ID, MARKER_SQL, |_, _| Ok(()))?;
        with_db(&self.received, true, RECEIVED_APP_ID, RECEIVED_SQL, |connection, _| {
            let connection = connection.ok_or(Error::InvalidStore)?;
            let mut query = connection.prepare("SELECT account_id,chat_id,message_id FROM received_messages LIMIT ?1").map_err(sql_error)?;
            let rows = query.query_map([MAX_KNOWN_IDS + 1], |row|
                Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?, row.get::<_, String>(2)?))).map_err(sql_error)?;
            let mut count = 0;
            for row in rows {
                count += 1;
                if count > MAX_KNOWN_IDS { return Err(Error::KnownCapacity); }
                let (account, chat, message) = row.map_err(sql_error)?;
                let parsed = (account.parse::<i64>(), chat.parse::<i64>(), message.parse::<i64>());
                let (Ok(account_id), Ok(chat_id), Ok(message_id)) = parsed else { return Err(Error::InvalidStore); };
                if account_id <= 0 || chat_id == 0 || message_id <= 0
                    || account_id.to_string() != account || chat_id.to_string() != chat
                    || message_id.to_string() != message { return Err(Error::InvalidStore); }
            }
            Ok(())
        })?;
        with_db(&self.content, true, CONTENT_APP_ID, CONTENT_SQL, |_, _| Ok(()))
    }
    pub fn remember(&self, owner: i64, chat: i64, ids: &[i64]) -> Result<usize> {
        validate_ids(owner, chat, ids, MAX_EVENT_IDS)?;
        with_db(&self.received, true, RECEIVED_APP_ID, RECEIVED_SQL, |connection, _| {
            let connection = connection.ok_or(Error::InvalidStore)?;
            let transaction = connection.transaction().map_err(sql_error)?;
            let mut added = 0;
            for id in ids {
                added += transaction.execute("INSERT OR IGNORE INTO received_messages VALUES(?1,?2,?3)",
                    params![owner.to_string(), chat.to_string(), id.to_string()]).map_err(sql_error)?;
            }
            let count: i64 = transaction.query_row("SELECT count(*) FROM received_messages", [], |row| row.get(0)).map_err(sql_error)?;
            if count > MAX_KNOWN_IDS { return Err(Error::KnownCapacity); }
            transaction.commit().map_err(sql_error)?;
            Ok(added)
        })
    }
    /// A trusted caller supplies text already received by this account. It is
    /// protected for the current Windows user and bound to the three routing IDs.
    /// A duplicate receipt may update an edit until the first deletion marker.
    pub fn capture_text(&self, owner: i64, chat: i64, id: i64, received_at_ms: i64, text: &str) -> Result<()> {
        self.capture_text_batch(owner, chat, &[IncomingText { id, received_at_ms, order_key: None, text }])
    }
    /// Protect a bounded upsert vector, then commit it in one SQLite transaction.
    /// No plaintext is written to disk. Input text remains caller-owned.
    pub fn capture_text_batch(&self, owner: i64, chat: i64, rows: &[IncomingText<'_>]) -> Result<()> {
        if rows.is_empty() || rows.len() > MAX_CAPTURE_ROWS { return Err(Error::InvalidRequest); }
        let ids = rows.iter().map(|row| row.id).collect::<Vec<_>>();
        validate_ids(owner, chat, &ids, MAX_CAPTURE_ROWS)?;
        let mut protected = Vec::with_capacity(rows.len());
        for row in rows {
            if row.received_at_ms < 0 || row.order_key.is_some_and(|key| key <= 0)
                || row.text.is_empty() || row.text.len() > MAX_TEXT_BYTES {
                return Err(Error::InvalidRequest);
            }
            protected.push(dpapi(row.text.as_bytes(), owner, chat, row.id, true)?);
        }
        with_db(&self.content, true, CONTENT_APP_ID, CONTENT_SQL, |connection, _| {
            let connection = connection.ok_or(Error::InvalidStore)?;
            let transaction = connection.transaction().map_err(sql_error)?;
            for (row, ciphertext) in rows.iter().zip(protected.iter()) {
                transaction.execute(
                    "INSERT INTO retained_content \
                     (account_id,chat_id,message_id,received_at_ms,protected_text,deleted_at_ms,order_key) \
                     VALUES(?1,?2,?3,?4,?5,NULL,?6) \
                     ON CONFLICT(account_id,chat_id,message_id) DO UPDATE SET \
                     received_at_ms=excluded.received_at_ms, protected_text=excluded.protected_text, \
                     order_key=COALESCE(retained_content.order_key,excluded.order_key) \
                     WHERE retained_content.deleted_at_ms IS NULL",
                    params![owner.to_string(), chat.to_string(), row.id.to_string(),
                        row.received_at_ms, ciphertext.as_slice(), row.order_key],
                ).map_err(sql_error)?;
            }
            let count: i64 = transaction.query_row("SELECT count(*) FROM retained_content", [], |row| row.get(0)).map_err(sql_error)?;
            if count > MAX_KNOWN_IDS { return Err(Error::KnownCapacity); }
            transaction.commit().map_err(sql_error)
        })
    }
    fn mark_content_deleted(&self, owner: i64, chat: i64, ids: &[i64], deleted_at_ms: i64) -> Result<HashSet<i64>> {
        with_db(&self.content, true, CONTENT_APP_ID, CONTENT_SQL, |connection, revision| {
            let connection = connection.ok_or(Error::InvalidStore)?;
            let transaction = connection.transaction().map_err(sql_error)?;
            let mut found = HashSet::new();
            let mut new_marks = 0;
            for id in ids {
                let existing: Option<Option<i64>> = transaction.query_row(
                    "SELECT deleted_at_ms FROM retained_content WHERE account_id=?1 AND chat_id=?2 AND message_id=?3",
                    params![owner.to_string(), chat.to_string(), id.to_string()], |row| row.get(0),
                ).optional().map_err(sql_error)?;
                if let Some(previous) = existing {
                    found.insert(*id);
                    if previous.is_none() {
                        new_marks += transaction.execute(
                            "UPDATE retained_content SET deleted_at_ms=?4 WHERE account_id=?1 AND chat_id=?2 AND message_id=?3 AND deleted_at_ms IS NULL",
                            params![owner.to_string(), chat.to_string(), id.to_string(), deleted_at_ms],
                        ).map_err(sql_error)?;
                    }
                }
            }
            if new_marks != 0 {
                if revision == i64::MAX { return Err(Error::InvalidStore); }
                transaction.execute("UPDATE retention_meta SET revision=revision+1 WHERE singleton=1", []).map_err(sql_error)?;
            }
            transaction.commit().map_err(sql_error)?;
            Ok(found)
        })
    }
    pub fn lookup_text(&self, owner: i64, chat: i64, ids: &[i64]) -> Result<Vec<SavedText>> {
        validate_ids(owner, chat, ids, MAX_LOOKUP_IDS)?;
        with_db(&self.content, false, CONTENT_APP_ID, CONTENT_SQL, |connection, _| {
            let Some(connection) = connection else { return Ok(Vec::new()); };
            let mut found = std::collections::HashMap::new();
            for id in ids {
                let encrypted: Option<Vec<u8>> = connection.query_row(
                    "SELECT protected_text FROM retained_content WHERE account_id=?1 AND chat_id=?2 AND message_id=?3 AND deleted_at_ms IS NOT NULL",
                    params![owner.to_string(), chat.to_string(), id.to_string()], |row| row.get(0),
                ).optional().map_err(sql_error)?;
                if let Some(encrypted) = encrypted {
                    let encrypted = Zeroizing::new(encrypted);
                    let mut plain = dpapi(&encrypted, owner, chat, *id, false)?;
                    if plain.is_empty() || plain.len() > MAX_TEXT_BYTES || std::str::from_utf8(&plain).is_err() {
                        return Err(Error::InvalidStore);
                    }
                    let value = String::from_utf8(std::mem::take(&mut *plain)).map_err(|_| Error::InvalidStore)?;
                    found.insert(*id, Zeroizing::new(value));
                }
            }
            Ok(ids.iter().filter_map(|id| found.remove(id).map(|text| SavedText { id: *id, text })).collect())
        })
    }
    pub fn content_revision(&self) -> Result<String> {
        with_db(&self.content, false, CONTENT_APP_ID, CONTENT_SQL, |_, revision| {
            Ok(sha256(format!("valmax-retention-content-v1:{revision}").as_bytes()))
        })
    }
    pub fn marker_revision(&self) -> Result<String> {
        with_db(&self.markers, false, MARKER_APP_ID, MARKER_SQL, |_, revision| {
            Ok(sha256(format!("valmax-retention-v1:{revision}").as_bytes()))
        })
    }
    pub fn visibility_revision(&self) -> Result<String> {
        Ok(combine_visibility_revision(&self.content_revision()?, &self.marker_revision()?))
    }
    /// Selected-chat metadata count of markers without a paired deleted
    /// content row. This does not claim that a ciphertext can be decrypted.
    pub fn marker_only_count(&self, owner: i64, chat: i64) -> Result<u64> {
        if owner <= 0 || chat == 0 { return Err(Error::InvalidRequest); }
        let ids = with_db(&self.markers, false, MARKER_APP_ID, MARKER_SQL, |connection, _| {
            let Some(connection) = connection else { return Ok(Zeroizing::new(Vec::<i64>::new())); };
            let mut query = connection.prepare(
                "SELECT message_id FROM deleted_messages WHERE account_id=?1 AND chat_id=?2"
            ).map_err(sql_error)?;
            let rows = query.query_map(params![owner.to_string(),chat.to_string()],
                |row| row.get::<_,String>(0)).map_err(sql_error)?;
            let mut ids = Zeroizing::new(Vec::new());
            for row in rows {
                let text = row.map_err(sql_error)?;
                let id = text.parse::<i64>().map_err(|_| Error::InvalidStore)?;
                if id <= 0 || id.to_string() != text { return Err(Error::InvalidStore); }
                ids.push(id);
                if ids.len() > MAX_KNOWN_IDS as usize { return Err(Error::KnownCapacity); }
            }
            Ok(ids)
        })?;
        with_db(&self.content, false, CONTENT_APP_ID, CONTENT_SQL, |connection, _| {
            let Some(connection) = connection else { return Ok(ids.len() as u64); };
            let mut query = connection.prepare(
                "SELECT 1 FROM retained_content WHERE account_id=?1 AND chat_id=?2 \
                 AND message_id=?3 AND deleted_at_ms IS NOT NULL"
            ).map_err(sql_error)?;
            let mut missing = 0u64;
            for id in ids.iter() {
                let present: Option<i64> = query.query_row(
                    params![owner.to_string(),chat.to_string(),id.to_string()],
                    |row| row.get(0)).optional().map_err(sql_error)?;
                if present.is_none() { missing += 1; }
            }
            Ok(missing)
        })
    }
    /// Keyset page of locally captured text with both durable delete records.
    /// Filtering is by the exact selected account and chat; no global list.
    pub fn list_deleted_text(&self, owner: i64, chat: i64,
        cursor: Option<&str>, limit: usize) -> Result<DeletedTextPage> {
        self.list_deleted_text_filtered(owner, chat, cursor, limit, false)
    }
    /// Old captures without an original order key remain discoverable in the
    /// selected chat even when newer ordered captures fill the first pages.
    pub fn list_deleted_text_legacy(&self, owner: i64, chat: i64,
        cursor: Option<&str>, limit: usize) -> Result<DeletedTextPage> {
        self.list_deleted_text_filtered(owner, chat, cursor, limit, true)
    }
    fn list_deleted_text_filtered(&self, owner: i64, chat: i64,
        cursor: Option<&str>, limit: usize, legacy_only: bool) -> Result<DeletedTextPage> {
        if owner <= 0 || chat == 0 || !(1..=100).contains(&limit) {
            return Err(Error::InvalidRequest);
        }
        let cursor = cursor.map(deleted_cursor_decode).transpose()?;
        if cursor.as_ref().is_some_and(|value| value.owner != owner || value.chat != chat
            || value.legacy_only != legacy_only) {
            return Err(Error::InvalidRequest);
        }
        struct Candidate { id: i64, order_key: Option<i64>, received_at_ms: i64, deleted_at_ms: i64,
            ciphertext: Zeroizing<Vec<u8>> }
        let (mut candidates, content_revision) = with_db(
            &self.content, false, CONTENT_APP_ID, CONTENT_SQL, |connection, revision| {
                let content_revision = sha256(format!("valmax-retention-content-v1:{revision}").as_bytes());
                let Some(connection) = connection else { return Ok((Vec::new(), content_revision)); };
                let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0)).map_err(sql_error)?;
                let order_projection = if version == 1 { "NULL" } else { "order_key" };
                let legacy_filter = if legacy_only && version != 1 { " AND order_key IS NULL" } else { "" };
                let sql = format!(
                    "SELECT message_id,received_at_ms,deleted_at_ms,protected_text,{order_projection} FROM retained_content \
                     WHERE account_id=?1 AND chat_id=?2 AND deleted_at_ms IS NOT NULL \
                     {legacy_filter} AND (?3=0 OR deleted_at_ms<?4 OR (deleted_at_ms=?4 AND CAST(message_id AS INTEGER)<?5)) \
                     ORDER BY deleted_at_ms DESC,CAST(message_id AS INTEGER) DESC LIMIT ?6"
                );
                let mut query = connection.prepare(&sql).map_err(sql_error)?;
                let rows = query.query_map(params![owner.to_string(),chat.to_string(),
                    i64::from(cursor.is_some()),cursor.as_ref().map_or(0,|value| value.deleted_at_ms),
                    cursor.as_ref().map_or(0,|value| value.id),(limit+1) as i64],
                    |row| Ok((row.get::<_,String>(0)?,row.get::<_,i64>(1)?,
                        row.get::<_,i64>(2)?,row.get::<_,Vec<u8>>(3)?,
                        row.get::<_,Option<i64>>(4)?))).map_err(sql_error)?;
                let mut candidates = Vec::new();
                for row in rows {
                    let (id_text,received_at_ms,deleted_at_ms,ciphertext,order_key) = row.map_err(sql_error)?;
                    let id = id_text.parse::<i64>().map_err(|_| Error::InvalidStore)?;
                    if id <= 0 || id.to_string() != id_text || received_at_ms < 0 || deleted_at_ms < 0
                        || order_key.is_some_and(|key| key <= 0) {
                        return Err(Error::InvalidStore);
                    }
                    candidates.push(Candidate { id, order_key, received_at_ms, deleted_at_ms,
                        ciphertext: Zeroizing::new(ciphertext) });
                }
                Ok((candidates,content_revision))
            })?;
        let count_more = candidates.len() > limit;
        if count_more { candidates.pop(); }
        let ids = candidates.iter().map(|value| value.id).collect::<Vec<_>>();
        let (visible, marker_revision) = if ids.is_empty() {
            (HashSet::new(), self.marker_revision()?)
        } else {
            let lookup = self.lookup(owner, chat, &ids)?;
            (lookup.deleted_ids.into_iter().collect::<HashSet<_>>(), lookup.revision)
        };
        let revision = combine_visibility_revision(&content_revision,&marker_revision);
        if cursor.as_ref().is_some_and(|value| value.revision != revision) {
            return Err(Error::StaleCursor);
        }
        let mut items = Vec::new();
        let mut estimated_bytes = 1024usize;
        let mut last_processed = None;
        let mut size_more = false;
        for candidate in candidates {
            let position = (candidate.deleted_at_ms,candidate.id);
            if !visible.contains(&candidate.id) {
                last_processed = Some(position);
                continue;
            }
            let mut plain = dpapi(&candidate.ciphertext,owner,chat,candidate.id,false)?;
            if plain.is_empty() || plain.len() > MAX_TEXT_BYTES || std::str::from_utf8(&plain).is_err() {
                return Err(Error::InvalidStore);
            }
            let value = Zeroizing::new(String::from_utf8(std::mem::take(&mut *plain))
                .map_err(|_| Error::InvalidStore)?);
            let row_estimate = value.len().saturating_mul(6).saturating_add(512);
            if !items.is_empty() && estimated_bytes.saturating_add(row_estimate)
                > MAX_INLINE_PAGE_ESTIMATED_BYTES {
                size_more = true;
                break;
            }
            estimated_bytes = estimated_bytes.saturating_add(row_estimate);
            last_processed = Some(position);
            items.push(DeletedText { id:candidate.id, order_key:candidate.order_key,
                received_at_ms:candidate.received_at_ms,
                deleted_at_ms:candidate.deleted_at_ms, text:value });
        }
        let next_cursor = if count_more || size_more { last_processed.map(|(deleted_at_ms,id)|
            deleted_cursor_encode(&DeletedCursor { owner, chat, revision:revision.clone(),
                deleted_at_ms, id, legacy_only }))
        } else { None };
        Ok(DeletedTextPage { items, next_cursor, revision })
    }
    /// Bounded page for a visible history interval. Legacy rows with no
    /// original order key are intentionally absent from this range.
    pub fn list_deleted_text_range(&self, owner: i64, chat: i64,
        from_order_key: i64, to_order_key: i64, cursor: Option<&str>,
        limit: usize) -> Result<DeletedTextPage> {
        if owner <= 0 || chat == 0 || from_order_key <= 0
            || to_order_key < from_order_key || !(1..=100).contains(&limit) {
            return Err(Error::InvalidRequest);
        }
        let cursor = cursor.map(range_cursor_decode).transpose()?;
        if cursor.as_ref().is_some_and(|value| value.owner != owner || value.chat != chat
            || value.from_order_key != from_order_key || value.to_order_key != to_order_key) {
            return Err(Error::InvalidRequest);
        }
        struct Candidate {
            id: i64, order_key: i64, received_at_ms: i64, deleted_at_ms: i64,
            ciphertext: Zeroizing<Vec<u8>>,
        }
        let (mut candidates, content_revision) = with_db(
            &self.content, false, CONTENT_APP_ID, CONTENT_SQL, |connection, revision| {
                let content_revision = sha256(format!("valmax-retention-content-v1:{revision}").as_bytes());
                let Some(connection) = connection else { return Ok((Vec::new(), content_revision)); };
                let version: i64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0)).map_err(sql_error)?;
                if version == 1 { return Ok((Vec::new(), content_revision)); }
                let mut query = connection.prepare(
                    "SELECT message_id,received_at_ms,deleted_at_ms,protected_text,order_key \
                     FROM retained_content WHERE account_id=?1 AND chat_id=?2 \
                     AND deleted_at_ms IS NOT NULL AND order_key IS NOT NULL \
                     AND order_key>=?3 AND order_key<=?4 \
                     AND (?5=0 OR order_key>?6 OR (order_key=?6 AND CAST(message_id AS INTEGER)>?7)) \
                     ORDER BY order_key ASC,CAST(message_id AS INTEGER) ASC LIMIT ?8"
                ).map_err(sql_error)?;
                let rows = query.query_map(params![owner.to_string(),chat.to_string(),
                    from_order_key,to_order_key,i64::from(cursor.is_some()),
                    cursor.as_ref().map_or(0,|value| value.order_key),
                    cursor.as_ref().map_or(0,|value| value.id),(limit+1) as i64],
                    |row| Ok((row.get::<_,String>(0)?,row.get::<_,i64>(1)?,
                        row.get::<_,i64>(2)?,row.get::<_,Vec<u8>>(3)?,
                        row.get::<_,i64>(4)?))).map_err(sql_error)?;
                let mut candidates = Vec::new();
                for row in rows {
                    let (id_text,received_at_ms,deleted_at_ms,ciphertext,order_key) = row.map_err(sql_error)?;
                    let id = id_text.parse::<i64>().map_err(|_| Error::InvalidStore)?;
                    if id <= 0 || id.to_string() != id_text || received_at_ms < 0
                        || deleted_at_ms < 0 || order_key < from_order_key
                        || order_key > to_order_key { return Err(Error::InvalidStore); }
                    candidates.push(Candidate { id, order_key, received_at_ms,
                        deleted_at_ms, ciphertext: Zeroizing::new(ciphertext) });
                }
                Ok((candidates,content_revision))
            })?;
        let count_more = candidates.len() > limit;
        if count_more { candidates.pop(); }
        let ids = candidates.iter().map(|value| value.id).collect::<Vec<_>>();
        let (visible, marker_revision) = if ids.is_empty() {
            (HashSet::new(), self.marker_revision()?)
        } else {
            let lookup = self.lookup(owner, chat, &ids)?;
            (lookup.deleted_ids.into_iter().collect::<HashSet<_>>(), lookup.revision)
        };
        let revision = combine_visibility_revision(&content_revision,&marker_revision);
        if cursor.as_ref().is_some_and(|value| value.revision != revision) {
            return Err(Error::StaleCursor);
        }
        let mut items = Vec::new();
        let mut estimated_bytes = 1024usize;
        let mut last_processed = None;
        let mut size_more = false;
        for candidate in candidates {
            let position = (candidate.order_key,candidate.id);
            if !visible.contains(&candidate.id) {
                last_processed = Some(position);
                continue;
            }
            let mut plain = dpapi(&candidate.ciphertext,owner,chat,candidate.id,false)?;
            if plain.is_empty() || plain.len() > MAX_TEXT_BYTES || std::str::from_utf8(&plain).is_err() {
                return Err(Error::InvalidStore);
            }
            let value = Zeroizing::new(String::from_utf8(std::mem::take(&mut *plain))
                .map_err(|_| Error::InvalidStore)?);
            let row_estimate = value.len().saturating_mul(6).saturating_add(512);
            if !items.is_empty() && estimated_bytes.saturating_add(row_estimate)
                > MAX_INLINE_PAGE_ESTIMATED_BYTES {
                size_more = true;
                break;
            }
            estimated_bytes = estimated_bytes.saturating_add(row_estimate);
            last_processed = Some(position);
            items.push(DeletedText { id:candidate.id, order_key:Some(candidate.order_key),
                received_at_ms:candidate.received_at_ms,
                deleted_at_ms:candidate.deleted_at_ms, text:value });
        }
        if self.content_revision()? != content_revision || self.marker_revision()? != marker_revision {
            return Err(Error::StoreBusy);
        }
        let next_cursor = if count_more || size_more {
            last_processed.map(|(order_key,id)| range_cursor_encode(&RangeCursor {
                owner, chat, revision:revision.clone(), from_order_key, to_order_key,
                order_key, id,
            }))
        } else { None };
        Ok(DeletedTextPage { items, next_cursor, revision })
    }
    pub fn mark_received_delete(&self, owner: i64, chat: i64, ids: &[i64], deleted_at_ms: i64) -> Result<Vec<bool>> {
        validate_ids(owner, chat, ids, MAX_EVENT_IDS)?;
        if deleted_at_ms < 0 { return Err(Error::InvalidRequest); }
        // A content row is the receipt; an ID-only queue record cannot make
        // a deletion eligible. First durably mark protected content, then publish
        // the legacy marker and return the durable receipt bits to the passive worker.
        let found = self.mark_content_deleted(owner, chat, ids, deleted_at_ms)?;
        if found.is_empty() { return Ok(vec![false; ids.len()]); }
        with_db(&self.markers, true, MARKER_APP_ID, MARKER_SQL, |connection, revision| {
            let connection = connection.ok_or(Error::InvalidStore)?;
            let transaction = connection.transaction().map_err(sql_error)?;
            let mut added = 0;
            for id in ids.iter().filter(|id| found.contains(id)) {
                added += transaction.execute("INSERT OR IGNORE INTO deleted_messages VALUES(?1,?2,?3,?4)",
                    params![owner.to_string(), chat.to_string(), id.to_string(), deleted_at_ms]).map_err(sql_error)?;
            }
            if added != 0 {
                if revision == i64::MAX { return Err(Error::InvalidStore); }
                transaction.execute("UPDATE retention_meta SET revision=revision+1 WHERE singleton=1", []).map_err(sql_error)?;
            }
            transaction.commit().map_err(sql_error)?;
            Ok(ids.iter().map(|id| found.contains(id)).collect())
        })
    }
    pub fn lookup(&self, owner: i64, chat: i64, ids: &[i64]) -> Result<Lookup> {
        validate_ids(owner, chat, ids, MAX_LOOKUP_IDS)?;
        with_db(&self.markers, false, MARKER_APP_ID, MARKER_SQL, |connection, revision| {
            let mut found = HashSet::new();
            if let Some(connection) = connection {
                let placeholders = (0..ids.len()).map(|_| "?").collect::<Vec<_>>().join(",");
                let sql = format!("SELECT message_id FROM deleted_messages WHERE account_id=? AND chat_id=? AND message_id IN ({placeholders})");
                let mut values = vec![owner.to_string(), chat.to_string()];
                values.extend(ids.iter().map(i64::to_string));
                let mut query = connection.prepare(&sql).map_err(sql_error)?;
                let rows = query.query_map(params_from_iter(values), |row| row.get::<_, String>(0)).map_err(sql_error)?;
                for row in rows {
                    let text = row.map_err(sql_error)?;
                    let id: i64 = text.parse().map_err(|_| Error::InvalidStore)?;
                    if id <= 0 || id.to_string() != text { return Err(Error::InvalidStore); }
                    found.insert(id);
                }
            }
            Ok(Lookup { deleted_ids: ids.iter().copied().filter(|id| found.contains(id)).collect(),
                revision: sha256(format!("valmax-retention-v1:{revision}").as_bytes()) })
        })
    }
    pub fn now_ms() -> Result<i64> {
        let ms = SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_| Error::IoError)?.as_millis();
        i64::try_from(ms).map_err(|_| Error::IoError)
    }
}

// Byte-for-byte equivalent to native/hooks/retention_api.h. The worker obtains
// the request from that bounded queue; this function owns no hooks or threads.
#[repr(C)] #[derive(Clone, Copy)]
pub struct RetentionRequest {
    pub size: u32, pub version: u32, pub kind: u32, pub count: u32,
    pub nonce: u64, pub epoch: u64, pub generation: u64,
    pub owner: i64, pub chat: i64, pub deadline_tick: u64,
    pub ids_digest: [u8; 64],
}
#[repr(C)] #[derive(Clone, Copy)]
pub struct RetentionAck {
    pub size: u32, pub version: u32, pub result: u32, pub reserved: u32,
    pub request: RetentionRequest, pub retained: [u8; 512],
}
const _: () = assert!(std::mem::size_of::<RetentionRequest>() == 128);
const _: () = assert!(std::mem::size_of::<RetentionAck>() == 656);
fn digest_ids(ids: &[i64]) -> [u8; 64] {
    let mut hash = Sha256::new();
    hash.update(b"valmax-retention-ids-v1\0");
    hash.update((ids.len() as u32).to_le_bytes());
    for id in ids { hash.update(id.to_le_bytes()); }
    let text = format!("{:x}", hash.finalize());
    let mut output = [0u8; 64]; output.copy_from_slice(text.as_bytes()); output
}
fn profile_from_utf16(pointer: *const u16, length: usize) -> Result<PathBuf> {
    if pointer.is_null() || length == 0 || length > 32768 { return Err(Error::InvalidRequest); }
    let units = unsafe { std::slice::from_raw_parts(pointer, length) };
    if units.contains(&0) { return Err(Error::InvalidRequest); }
    Ok(PathBuf::from(String::from_utf16(units).map_err(|_| Error::InvalidRequest)?))
}
#[repr(C)] #[derive(Clone, Copy)]
pub struct RetentionCaptureRow {
    pub message: i64,
    pub received_at_ms: i64,
    pub utf8_text: *const u8,
    pub text_length: usize,
}
#[repr(C)] #[derive(Clone, Copy)]
pub struct RetentionCaptureRowV2 {
    pub message: i64,
    pub received_at_ms: i64,
    pub utf8_text: *const u8,
    pub text_length: usize,
    pub order_key: i64,
}
const _: () = assert!(std::mem::size_of::<RetentionCaptureRow>() == 32);
const _: () = assert!(std::mem::size_of::<RetentionCaptureRowV2>() == 40);
/// Host-only preflight performed off the UI thread before any native callback
/// can submit content. It opens only the explicit, guarded profile stores.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_retention_preflight(
    profile: *const u16, profile_len: usize) -> u32 {
    let result = std::panic::catch_unwind(|| {
        RetentionStore::new(profile_from_utf16(profile, profile_len)?)?.preflight()
    });
    match result {
        Ok(Ok(())) => 0,
        Ok(Err(Error::InvalidRequest | Error::UnsafePath)) => 1,
        _ => 5,
    }
}
/// Host-only asynchronous worker entry. A successful return means both the
/// protected content mark and legacy marker were durably committed for every
/// set bit. Missing captures have zero bits. The caller owns all buffers.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_retention_mark_delete_batch(
    profile: *const u16, profile_len: usize, owner: i64, chat: i64,
    ids: *const i64, count: usize, bits: *mut u8, bits_len: usize) -> u32 {
    let result = std::panic::catch_unwind(|| {
        if ids.is_null() || bits.is_null() || count == 0 || count > MAX_CAPTURE_ROWS
            || bits_len != count.div_ceil(8) {
            return Err(Error::InvalidRequest);
        }
        let ids = unsafe { std::slice::from_raw_parts(ids, count) };
        validate_ids(owner, chat, ids, MAX_CAPTURE_ROWS)?;
        let marked = RetentionStore::new(profile_from_utf16(profile, profile_len)?)?
            .mark_received_delete(owner, chat, ids, RetentionStore::now_ms()?)?;
        Ok(marked)
    });
    match result {
        Ok(Ok(marked)) => {
            let output = unsafe { std::slice::from_raw_parts_mut(bits, bits_len) };
            output.fill(0);
            for (index, retained) in marked.iter().enumerate() {
                if *retained { output[index / 8] |= 1 << (index % 8); }
            }
            0
        }
        Ok(Err(Error::InvalidRequest | Error::UnsafePath)) => 1,
        _ => 5,
    }
}
/// Host-only bounded capture of one routed MAX upsert vector. Rows and text
/// buffers are caller-owned and must stay immutable until this call returns.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_retention_capture_text_batch(
    profile: *const u16, profile_len: usize, owner: i64, chat: i64,
    rows: *const RetentionCaptureRow, count: usize) -> u32 {
    let result = std::panic::catch_unwind(|| {
        let profile = profile_from_utf16(profile, profile_len)?;
        if rows.is_null() || count == 0 || count > MAX_CAPTURE_ROWS {
            return Err(Error::InvalidRequest);
        }
        let raw = unsafe { std::slice::from_raw_parts(rows, count) };
        let mut incoming = Vec::with_capacity(count);
        for row in raw {
            if row.utf8_text.is_null() || row.text_length == 0 || row.text_length > MAX_TEXT_BYTES {
                return Err(Error::InvalidRequest);
            }
            let bytes = unsafe { std::slice::from_raw_parts(row.utf8_text, row.text_length) };
            let text = std::str::from_utf8(bytes).map_err(|_| Error::InvalidRequest)?;
            incoming.push(IncomingText { id: row.message, received_at_ms: row.received_at_ms,
                order_key: None, text });
        }
        RetentionStore::new(profile)?.capture_text_batch(owner, chat, &incoming)
    });
    match result {
        Ok(Ok(())) => 0,
        Ok(Err(Error::InvalidRequest | Error::UnsafePath)) => 1,
        _ => 5,
    }
}
/// Host-only v2 capture. The appended positive order key is copied from the
/// pinned MAX message `time` field; zero means unknown. The old ABI stores null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_retention_capture_text_batch_v2(
    profile: *const u16, profile_len: usize, owner: i64, chat: i64,
    rows: *const RetentionCaptureRowV2, count: usize) -> u32 {
    let result = std::panic::catch_unwind(|| {
        let profile = profile_from_utf16(profile, profile_len)?;
        if rows.is_null() || count == 0 || count > MAX_CAPTURE_ROWS {
            return Err(Error::InvalidRequest);
        }
        let raw = unsafe { std::slice::from_raw_parts(rows, count) };
        let mut incoming = Vec::with_capacity(count);
        for row in raw {
            if row.order_key < 0 || row.utf8_text.is_null() || row.text_length == 0
                || row.text_length > MAX_TEXT_BYTES {
                return Err(Error::InvalidRequest);
            }
            let bytes = unsafe { std::slice::from_raw_parts(row.utf8_text, row.text_length) };
            let text = std::str::from_utf8(bytes).map_err(|_| Error::InvalidRequest)?;
            incoming.push(IncomingText { id: row.message, received_at_ms: row.received_at_ms,
                order_key: (row.order_key > 0).then_some(row.order_key), text });
        }
        RetentionStore::new(profile)?.capture_text_batch(owner, chat, &incoming)
    });
    match result {
        Ok(Ok(())) => 0,
        Ok(Err(Error::InvalidRequest | Error::UnsafePath)) => 1,
        _ => 5,
    }
}
/// Host-only content receipt. The caller must have received this text through
/// the authorized account and owns the input buffer until return. Text is never
/// returned through this API or written in plaintext to the archive.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_retention_capture_text(
    profile: *const u16, profile_len: usize, owner: i64, chat: i64, id: i64,
    received_at_ms: i64, text: *const u8, text_len: usize) -> u32 {
    let result = std::panic::catch_unwind(|| {
        let profile = profile_from_utf16(profile, profile_len)?;
        if text.is_null() || text_len == 0 || text_len > MAX_TEXT_BYTES { return Err(Error::InvalidRequest); }
        let bytes = unsafe { std::slice::from_raw_parts(text, text_len) };
        let value = std::str::from_utf8(bytes).map_err(|_| Error::InvalidRequest)?;
        RetentionStore::new(profile)?.capture_text(owner, chat, id, received_at_ms, value)
    });
    match result {
        Ok(Ok(())) => 0,
        Ok(Err(Error::InvalidRequest | Error::UnsafePath)) => 1,
        _ => 5,
    }
}
/// Host-only synchronous worker entry point. A durable failure returns ACK.result=1,
/// so the queue fails closed. Invalid pointers and request fields return 1 without
/// writing an ACK. Buffers must remain valid throughout the call.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn valmax_core_retention_store_process(
    profile: *const u16, profile_len: usize, request: *const RetentionRequest,
    ids: *const i64, output: *mut RetentionAck) -> u32 {
    let outcome = std::panic::catch_unwind(|| {
        if profile.is_null() || profile_len == 0 || profile_len > 32768 || request.is_null()
            || ids.is_null() || output.is_null() { return Err(Error::InvalidRequest); }
        let request = unsafe { *request };
        if request.size as usize != std::mem::size_of::<RetentionRequest>() || request.version != 1
            || !matches!(request.kind, 1 | 2) || request.count == 0 || request.count as usize > MAX_EVENT_IDS
            || request.nonce == 0 || request.epoch == 0 || request.generation == 0 { return Err(Error::InvalidRequest); }
        let ids = unsafe { std::slice::from_raw_parts(ids, request.count as usize) };
        validate_ids(request.owner, request.chat, ids, MAX_EVENT_IDS)?;
        if request.ids_digest != digest_ids(ids) { return Err(Error::InvalidRequest); }
        let mut ack = RetentionAck { size: std::mem::size_of::<RetentionAck>() as u32,
            version: 1, result: 1, reserved: 0, request, retained: [0; 512] };
        let result = profile_from_utf16(profile, profile_len)
            .and_then(RetentionStore::new)
            .and_then(|store| match request.kind {
                1 => store.remember(request.owner, request.chat, ids).map(|_| Vec::new()),
                2 => RetentionStore::now_ms().and_then(|now| store.mark_received_delete(request.owner, request.chat, ids, now)),
                _ => unreachable!(),
            });
        if let Ok(bits) = result {
            ack.result = 0;
            for (index, retained) in bits.iter().enumerate() {
                if *retained { ack.retained[index / 8] |= 1 << (index % 8); }
            }
        }
        unsafe { *output = ack; }
        Ok(())
    });
    if matches!(outcome, Ok(Ok(()))) { 0 } else { 1 }
}

#[cfg(test)]
#[path = "retention_store_tests.rs"]
mod tests;
