//! User-authored quick replies only. No MAX/profile discovery, attachment-target
//! access, file picker, draft mutation, message sending or network operations.
//! Optional scope/attachments/parts are preserved byte-canonically with schema 1.
//! The native UI must stage each selected part and require manual Send.

use std::{collections::HashSet, fmt, path::Path};
use serde_json::{Value, json};
use crate::settings_io::{GuardedStore, SafeStorePath, StoreError, sha256};

pub const MAX_STORE_BYTES: usize = 256 * 1024;
pub const MAX_REPLIES: usize = 100;
pub const MAX_ATTACHMENTS: usize = 10;
pub const MAX_PARTS: usize = 10;
pub const MAX_FILE_URL: usize = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error { InvalidReplies, InvalidRevision, RevisionConflict, InvalidStore, UnsafePath, StoreBusy, IoError }
impl Error {
    pub fn code(self) -> &'static str { match self {
        Self::InvalidReplies => "invalid_replies", Self::InvalidRevision => "invalid_revision",
        Self::RevisionConflict => "revision_conflict", Self::InvalidStore => "invalid_store",
        Self::UnsafePath => "unsafe_path", Self::StoreBusy => "store_busy", Self::IoError => "io_error",
    } }
}
impl fmt::Display for Error { fn fmt(&self, f:&mut fmt::Formatter<'_>) -> fmt::Result { f.write_str(self.code()) } }
impl std::error::Error for Error {}
impl From<StoreError> for Error {
    fn from(error:StoreError) -> Self { match error {
        StoreError::InvalidSettings => Self::InvalidReplies, StoreError::InvalidRevision => Self::InvalidRevision,
        StoreError::RevisionConflict => Self::RevisionConflict, StoreError::InvalidStore => Self::InvalidStore,
        StoreError::UnsafePath => Self::UnsafePath, StoreError::StoreBusy => Self::StoreBusy,
        StoreError::FileExists | StoreError::IoError => Self::IoError,
    } }
}
pub type Result<T> = std::result::Result<T, Error>;
fn string(value:&Value, minimum:usize, maximum:usize) -> Result<&str> {
    value.as_str().filter(|text| (minimum..=maximum).contains(&text.chars().count()) && !text.contains('\0')).ok_or(Error::InvalidReplies)
}
fn keys(value:&Value, required:&[&str], allowed:&[&str]) -> bool {
    value.as_object().is_some_and(|map| required.iter().all(|key|map.contains_key(*key)) && map.keys().all(|key|allowed.contains(&key.as_str())))
}
pub fn valid_account_id(value:&str) -> bool { value.is_empty() || crate::account_preferences::valid_account_id(value) }
fn command_letter(c:char) -> bool { c.is_ascii_lowercase() || ('а'..='я').contains(&c) || c=='ё' }
pub fn valid_command(value:&str) -> bool {
    let mut chars=value.chars();
    (1..=32).contains(&value.chars().count()) && chars.next().is_some_and(command_letter)
        && chars.all(|c|command_letter(c) || c.is_ascii_digit() || c=='_')
}
fn hex(value:&str, length:usize) -> bool { value.len()==length && value.bytes().all(|b|b.is_ascii_digit() || (b'a'..=b'f').contains(&b)) }
fn control(value:&str) -> bool { value.chars().any(|c|u32::from(c)<32 || c=='\u{7f}') }
fn percent_decode(value:&str) -> Result<String> {
    let bytes=value.as_bytes(); let mut result=Vec::with_capacity(bytes.len()); let mut position=0;
    while position<bytes.len() {
        if bytes[position]==b'%' {
            if position+2>=bytes.len() { return Err(Error::InvalidReplies); }
            let a=char::from(bytes[position+1]).to_digit(16).ok_or(Error::InvalidReplies)?;
            let b=char::from(bytes[position+2]).to_digit(16).ok_or(Error::InvalidReplies)?;
            result.push((a*16+b) as u8); position+=3;
        } else {result.push(bytes[position]);position+=1;}
    }
    String::from_utf8(result).map_err(|_|Error::InvalidReplies)
}

/// Lexical validation only. The returned comparison key uses full, pinned
/// Python Unicode casefold after percent decoding, without dereferencing a URL.
/// The original spelling must remain in stored attachment metadata.
pub fn local_file_url(value:&str) -> Result<String> {
    if !(1..=MAX_FILE_URL).contains(&value.chars().count()) || !value.starts_with("file:///") || control(value) { return Err(Error::InvalidReplies); }
    // Validate escapes in the whole URL, as the Python precheck does. Raw '#'
    // and '?' delimiters are permitted only when their component is empty.
    let _=percent_decode(value)?;
    let path_and_query=if let Some((head,fragment))=value.split_once('#') {
        if !fragment.is_empty() {return Err(Error::InvalidReplies);} head
    } else {value};
    let path=if let Some((head,query))=path_and_query.split_once('?') {
        if !query.is_empty() {return Err(Error::InvalidReplies);} head
    } else {path_and_query};
    let decoded=percent_decode(path.strip_prefix("file://").ok_or(Error::InvalidReplies)?)?;
    let bytes=decoded.as_bytes();
    if !(5..=MAX_FILE_URL).contains(&decoded.chars().count()) || control(&decoded) || decoded.contains('\\')
        || bytes.len()<5 || bytes[0]!=b'/' || !bytes[1].is_ascii_alphabetic() || bytes[2]!=b':' || bytes[3]!=b'/' { return Err(Error::InvalidReplies); }
    for part in decoded[4..].split('/') {
        let stem=part.split('.').next().unwrap_or("").to_ascii_uppercase();
        let device=matches!(stem.as_str(),"CON"|"PRN"|"AUX"|"NUL")
            || stem.len()==4 && (stem.starts_with("COM") || stem.starts_with("LPT")) && (b'1'..=b'9').contains(&stem.as_bytes()[3]);
        if part.is_empty() || matches!(part,"."|"..") || part.ends_with(['.',' ']) || device
            || part.chars().any(|c|":<>\"|?*".contains(c)) {return Err(Error::InvalidReplies);}
    }
    Ok(crate::mdata_archive::path_comparison_key(&decoded))
}

fn attachments(value:&Value) -> Result<Value> {
    let files=value.as_array().filter(|files|(1..=MAX_ATTACHMENTS).contains(&files.len())).ok_or(Error::InvalidReplies)?;
    let mut seen=HashSet::new();let mut clean=Vec::new();
    for file in files {
        if !keys(file,&["url","kind"],&["url","kind"]) {return Err(Error::InvalidReplies);}
        let kind=file["kind"].as_str().filter(|kind|matches!(*kind,"file"|"media")).ok_or(Error::InvalidReplies)?;
        let url=string(&file["url"],1,MAX_FILE_URL)?;
        if !seen.insert(local_file_url(url)?) {return Err(Error::InvalidReplies);}
        clean.push(json!({"url":url,"kind":kind}));
    }
    Ok(Value::Array(clean))
}
fn part(value:&Value) -> Result<Value> {
    if !keys(value,&["text"],&["text","attachments"]) {return Err(Error::InvalidReplies);}
    part_fields(&value["text"],value.get("attachments"))
}
fn part_fields(text:&Value,files:Option<&Value>) -> Result<Value> {
    let text=string(text,0,5000)?;let mut clean=json!({"text":text});
    if let Some(files)=files {clean["attachments"]=attachments(files)?;}
    if text.is_empty() && clean.get("attachments").is_none() {return Err(Error::InvalidReplies);}
    Ok(clean)
}

/// Preserve row order and legacy omitted fields; uniqueness is global for IDs
/// and per (accountId, command) for commands. Empty/missing scope means common.
pub fn validate_replies(value:&Value) -> Result<Value> {
    let rows=value.as_array().filter(|rows|rows.len()<=MAX_REPLIES).ok_or(Error::InvalidReplies)?;
    let mut ids=HashSet::new();let mut commands=HashSet::new();let mut result=Vec::new();
    let mut encoded_bytes=b"{\"replies\":[],\"schema\":1}".len();
    for row in rows {
        if !keys(row,&["id","command","title","text"],&["id","command","title","text","accountId","attachments","parts"]) {return Err(Error::InvalidReplies);}
        let account=match row.get("accountId") {Some(value)=>value.as_str().ok_or(Error::InvalidReplies)?,None=>""};
        if !valid_account_id(account) {return Err(Error::InvalidReplies);}
        let id=row["id"].as_str().filter(|id|hex(id,32)).ok_or(Error::InvalidReplies)?;
        let command=row["command"].as_str().filter(|command|valid_command(command)).ok_or(Error::InvalidReplies)?;
        let title=string(&row["title"],0,80)?;
        if !ids.insert(id) || !commands.insert((account,command)) {return Err(Error::InvalidReplies);}
        let mut clean=part_fields(&row["text"],row.get("attachments"))?;
        clean["id"]=id.into();clean["command"]=command.into();clean["title"]=title.into();
        if row.get("accountId").is_some() {clean["accountId"]=account.into();}
        if let Some(parts)=row.get("parts") {
            let parts=parts.as_array().filter(|parts|(1..MAX_PARTS).contains(&parts.len())).ok_or(Error::InvalidReplies)?;
            clean["parts"]=Value::Array(parts.iter().map(part).collect::<Result<Vec<_>>>()?);
        }
        encoded_bytes+=serde_json::to_vec(&clean).map_err(|_|Error::InvalidReplies)?.len()+usize::from(!result.is_empty());
        if encoded_bytes>MAX_STORE_BYTES {return Err(Error::InvalidReplies);}
        result.push(clean);
    }
    Ok(Value::Array(result))
}

/// The caller supplies validated replies. Use validate_replies on arbitrary UI
/// input before this function; canonical bytes intentionally retain legacy keys.
pub fn canonical(clean:&Value) -> Result<Vec<u8>> {
    let bytes=serde_json::to_vec(&json!({"schema":1,"replies":clean})).map_err(|_|Error::InvalidReplies)?;
    if bytes.len()>MAX_STORE_BYTES {Err(Error::InvalidReplies)} else {Ok(bytes)}
}
pub fn snapshot_value(clean:&Value) -> Result<Value> {Ok(json!({"schema":1,"revision":sha256(&canonical(clean)?),"replies":clean}))}
pub fn parse(data:&[u8]) -> Result<Value> {
    if data.len()>MAX_STORE_BYTES {return Err(Error::InvalidStore);}
    let value=crate::session_archive::parse_json(data,"invalid_payload").map_err(|_|Error::InvalidStore)?;
    if !keys(&value,&["schema","replies"],&["schema","replies"]) || value["schema"].as_u64()!=Some(1) {return Err(Error::InvalidStore);}
    validate_replies(&value["replies"]).map_err(|_|Error::InvalidStore)
}
fn read(guard:&GuardedStore) -> Result<Value> {guard.read_bounded(MAX_STORE_BYTES)?.map_or(Ok(json!([])),|data|parse(&data))}

pub struct QuickReplyStore {path:SafeStorePath}
impl QuickReplyStore {
    pub fn new(path:impl AsRef<Path>) -> Result<Self> {Ok(Self{path:SafeStorePath::new(path)?})}
    pub fn snapshot(&self) -> Result<Value> {
        let Some(guard)=self.path.begin(false)? else {return snapshot_value(&json!([]));};
        // Serialize against writers before opening the destination without
        // delete sharing. This mutex is a kernel object, not an on-disk file.
        let _lock=guard.writer_lock()?;snapshot_value(&read(&guard)?)
    }
    pub fn replace(&self,revision:&str,replies:&Value) -> Result<Value> {
        if !hex(revision,64) {return Err(Error::InvalidRevision);}
        let clean=validate_replies(replies)?;let payload=canonical(&clean)?;
        let guard=self.path.begin(true)?.ok_or(Error::IoError)?;let _lock=guard.writer_lock()?;
        let check=||->crate::settings_io::Result<()> {
            let current=read(&guard).map_err(|error|match error {
                Error::InvalidStore=>StoreError::InvalidStore,Error::UnsafePath=>StoreError::UnsafePath,
                Error::StoreBusy=>StoreError::StoreBusy,_=>StoreError::IoError})?;
            let bytes=canonical(&current).map_err(|_|StoreError::InvalidStore)?;
            if sha256(&bytes)==revision {Ok(())} else {Err(StoreError::RevisionConflict)}
        };
        check()?;guard.atomic_replace(&payload,check)?;snapshot_value(&clean)
    }
}

/// Pure composer selection matching the current QML: personal commands shadow
/// common commands, foreign-account rows never appear, ordering is preserved.
/// prefix is already normalized lowercase without '/'. Returned templates are
/// draft material only; this module has no sending or file-opening operations.
pub fn composer_replies(replies:&Value,account:&str,prefix:&str) -> Result<Value> {
    if !valid_account_id(account) || !prefix.is_empty() && !valid_command(prefix) {return Err(Error::InvalidReplies);}
    let clean=validate_replies(replies)?;let rows=clean.as_array().ok_or(Error::InvalidReplies)?;
    let specific:HashSet<_>=rows.iter().filter(|row|row.get("accountId").and_then(Value::as_str).is_some_and(|id|!id.is_empty() && id==account))
        .map(|row|row["command"].as_str().unwrap()).collect();
    Ok(Value::Array(rows.iter().filter(|row| {
        let scope=row.get("accountId").and_then(Value::as_str).unwrap_or("");let command=row["command"].as_str().unwrap();
        (scope.is_empty() && !specific.contains(command) || !scope.is_empty() && scope==account) && command.starts_with(prefix)
    }).cloned().collect()))
}
