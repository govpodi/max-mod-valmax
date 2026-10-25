//! MXMODMD v1/profile and v2/login authenticated streaming containers.
//!
//! This module has no filesystem, credential-store, process or network access.
//! Decryption factories MUST supply private staging sinks. Bytes in those sinks
//! are not an installed/complete archive until `decrypt_stream` returns `Ok`:
//! every error requires discarding all staging files, including a final-tag or
//! trailing-data error after files have been written. The caller alone commits
//! the returned manifest and staged files atomically. The manifest is sensitive
//! internal data, never a log/status object. Owned secret buffers use best-effort
//! zeroization; allocator/compiler/library copies are not guaranteed erased.

use crate::session_archive::{self as session, ArchiveError, ArchivePolicy, OwnedValue};
use aes_gcm::{Aes256Gcm, KeyInit, Nonce, aead::{Aead, Payload}};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{collections::HashSet, fmt, io::{self, Read, Write}, panic::{AssertUnwindSafe, catch_unwind}};
use zeroize::{Zeroize, Zeroizing};

#[path = "mdata_casefold.rs"]
mod unicode;

pub const MAGIC: &[u8; 8] = b"MXMODMD\x01";
pub const LOGIN_MAGIC: &[u8; 8] = b"MXMODMD\x02";
pub const CHUNK_BYTES: usize = 1024 * 1024;
pub const MAX_FILES: usize = 10_000;
pub const MAX_FILE_BYTES: u64 = 4 * 1024 * 1024 * 1024;
pub const MAX_MANIFEST_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_HEADER_BYTES: usize = 1024;
pub const MAX_PLAINTEXT_BYTES: u64 = 4 + MAX_MANIFEST_BYTES as u64 + MAX_FILE_BYTES;
pub const UNICODE_VERSION: &str = unicode::UNICODE_VERSION;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArchiveKind { Profile, Login }
impl ArchiveKind {
    fn magic(self) -> &'static [u8; 8] { match self { Self::Profile => MAGIC, Self::Login => LOGIN_MAGIC } }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum MDataError {
    InvalidPassword, AuthenticationFailed, InvalidArchive, UnsupportedVersion,
    InvalidPayload, PolicyViolation, TooLarge, CryptoUnavailable, IoError,
    SourceChanged, UnsafePath, UnsupportedCredential, InsufficientSpace,
}
impl MDataError {
    pub fn code(self) -> &'static str {
        match self {
            Self::InvalidPassword => "invalid_password", Self::AuthenticationFailed => "authentication_failed",
            Self::InvalidArchive => "invalid_archive", Self::UnsupportedVersion => "unsupported_version",
            Self::InvalidPayload => "invalid_payload", Self::PolicyViolation => "policy_violation",
            Self::TooLarge => "too_large", Self::CryptoUnavailable => "crypto_unavailable",
            Self::IoError => "io_error", Self::SourceChanged => "source_changed", Self::UnsafePath => "unsafe_path",
            Self::UnsupportedCredential => "unsupported_credential", Self::InsufficientSpace => "insufficient_space",
        }
    }
}
impl fmt::Display for MDataError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str(self.code()) }
}
impl std::error::Error for MDataError {}
impl From<ArchiveError> for MDataError {
    fn from(error: ArchiveError) -> Self {
        match error {
            ArchiveError::InvalidPassword => Self::InvalidPassword,
            ArchiveError::AuthenticationFailed => Self::AuthenticationFailed,
            ArchiveError::InvalidArchive => Self::InvalidArchive,
            ArchiveError::UnsupportedVersion => Self::UnsupportedVersion,
            ArchiveError::InvalidPayload => Self::InvalidPayload,
            ArchiveError::PolicyViolation => Self::PolicyViolation,
            ArchiveError::TooLarge => Self::TooLarge,
            ArchiveError::CryptoUnavailable => Self::CryptoUnavailable,
        }
    }
}
impl From<io::Error> for MDataError {
    fn from(error: io::Error) -> Self {
        // Windows ERROR_DISK_FULL / ERROR_HANDLE_DISK_FULL; Unix ENOSPC.
        if error.kind() == io::ErrorKind::StorageFull
            || (cfg!(windows) && matches!(error.raw_os_error(), Some(112 | 39)))
            || (cfg!(unix) && error.raw_os_error() == Some(28)) { Self::InsufficientSpace }
        else { Self::IoError }
    }
}

/// All decisions come from the caller, never from fields inside an archive.
pub struct MDataPolicy<'a> {
    pub credential_policy: &'a ArchivePolicy,
    pub file_path_validator: &'a dyn Fn(&str) -> bool,
    pub credential_name_validator: Option<&'a dyn Fn(&str) -> bool>,
}
impl fmt::Debug for MDataPolicy<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { f.write_str("<MData explicit credential/file policy>") }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ExportSummary { pub schema: u8, pub files: usize, pub file_bytes: u64, pub data_chunks: u64, pub archive_bytes: u64 }

pub fn archive_kind(prefix: &[u8]) -> Result<ArchiveKind, MDataError> {
    match prefix { p if p == MAGIC => Ok(ArchiveKind::Profile), p if p == LOGIN_MAGIC => Ok(ArchiveKind::Login), _ => Err(MDataError::InvalidArchive) }
}

/// Full, version-pinned Python casefold, without NFC/NFKC normalization.
pub fn path_comparison_key(path: &str) -> String { unicode::casefold(path) }

fn device(stem: &str) -> bool {
    if let Some(upper) = unicode::ascii_upper(stem) {
        matches!(upper.as_str(), "CON" | "PRN" | "AUX" | "NUL" | "CONIN$" | "CONOUT$" | "CLOCK$")
            || (upper.len() == 4 && (upper.starts_with("COM") || upper.starts_with("LPT"))
                && (b'1'..=b'9').contains(&upper.as_bytes()[3]))
    } else {
        ['¹', '²', '³'].iter().any(|suffix| stem.strip_suffix(*suffix)
            .and_then(unicode::ascii_upper).is_some_and(|head| head == "COM" || head == "LPT"))
    }
}

/// Unicode relative paths. Percent signs are literal and never URL-decoded.
pub fn valid_path(value: &str) -> bool {
    (1..=1024).contains(&value.chars().count()) && value.len() <= 4096
        && !value.chars().any(|c| c.is_control() || "\\:<>|?*\"".contains(c))
        && value.split('/').all(|part| {
            (1..=255).contains(&part.chars().count()) && !matches!(part, "." | "..")
                && !part.ends_with(['.', ' ']) && part.encode_utf16().count() <= 255
                && !device(part.split('.').next().unwrap_or("").trim_end_matches([' ', '.']))
        })
}
pub fn valid_file_path(value: &str) -> bool { valid_path(value) }

fn exact(value: &Value, names: &[&str]) -> bool {
    value.as_object().is_some_and(|map| map.len() == names.len() && names.iter().all(|name| map.contains_key(*name)))
}
fn accepted(callback: &dyn Fn(&str) -> bool, value: &str) -> Result<(), MDataError> {
    if catch_unwind(AssertUnwindSafe(|| callback(value))).unwrap_or(false) { Ok(()) }
    else { Err(MDataError::PolicyViolation) }
}
fn canonical(value: &Value) -> Result<Zeroizing<Vec<u8>>, MDataError> { Ok(Zeroizing::new(session::canonical(value)?)) }

pub fn validate_manifest(manifest: &Value, policy: &MDataPolicy<'_>) -> Result<Value, MDataError> {
    if !exact(manifest, &["schema", "sourceClient", "sourceVersion", "credentials", "files"]) { return Err(MDataError::InvalidPayload); }
    let files = manifest["files"].as_array().ok_or(MDataError::InvalidPayload)?;
    if files.len() > MAX_FILES { return Err(MDataError::TooLarge); }
    let records = manifest["credentials"].as_array().ok_or(MDataError::InvalidPayload)?;
    if records.len() > session::MAX_CREDENTIALS { return Err(MDataError::TooLarge); }
    let approved;
    let credential_policy = if let Some(validator) = policy.credential_name_validator {
        let mut names = Vec::new(); let mut seen = HashSet::new();
        for record in records {
            if !exact(record, &["name", "blob"]) { return Err(MDataError::InvalidPayload); }
            let name = record["name"].as_str().ok_or(MDataError::InvalidPayload)?;
            if !(1..=256).contains(&name.chars().count()) || name.chars().any(char::is_control) { return Err(MDataError::InvalidPayload); }
            if !name.is_ascii() { return Err(MDataError::UnsupportedCredential); }
            if !seen.insert(name.to_ascii_lowercase()) { return Err(MDataError::InvalidPayload); }
            accepted(validator, name)?; names.push(name.to_owned());
        }
        approved = ArchivePolicy { credential_names: names, ..Default::default() }; &approved
    } else { policy.credential_policy };
    credential_policy.validate()?;
    if manifest["schema"].as_u64() != Some(1) || !manifest["sourceClient"].is_string() || !manifest["sourceVersion"].is_string() { return Err(MDataError::InvalidPayload); }
    if manifest["sourceClient"] != session::SOURCE_CLIENT || manifest["sourceVersion"] != session::SOURCE_VERSION { return Err(MDataError::UnsupportedVersion); }
    session::validate_credential_records(records, credential_policy)?;
    let mut clean = OwnedValue(json!({"schema":manifest["schema"], "sourceClient":manifest["sourceClient"],
        "sourceVersion":manifest["sourceVersion"], "credentials":manifest["credentials"], "files":[]}));
    if canonical(&clean.0)?.len() > session::MAX_PLAINTEXT_BYTES { return Err(MDataError::TooLarge); }
    let mut seen = HashSet::new(); let mut total = 0u64;
    for item in files {
        if !exact(item, &["path", "size"]) { return Err(MDataError::InvalidPayload); }
        let path = item["path"].as_str().ok_or(MDataError::InvalidPayload)?;
        let size = item["size"].as_u64().ok_or(MDataError::InvalidPayload)?;
        if !valid_path(path) || !seen.insert(path_comparison_key(path)) { return Err(MDataError::InvalidPayload); }
        accepted(policy.file_path_validator, path)?;
        total = total.checked_add(size).filter(|n| *n <= MAX_FILE_BYTES).ok_or(MDataError::TooLarge)?;
        clean.0["files"].as_array_mut().ok_or(MDataError::InvalidPayload)?.push(json!({"path":path,"size":size}));
    }
    if canonical(&clean.0)?.len() > MAX_MANIFEST_BYTES { return Err(MDataError::TooLarge); }
    Ok(std::mem::take(&mut clean.0))
}

fn read_some<R: Read + ?Sized>(source: &mut R, buffer: &mut [u8]) -> Result<usize, MDataError> {
    let size = catch_unwind(AssertUnwindSafe(|| source.read(buffer))).map_err(|_| MDataError::IoError)??;
    if size > buffer.len() { Err(MDataError::IoError) } else { Ok(size) }
}
fn read_exact<R: Read + ?Sized>(source: &mut R, size: usize) -> Result<Zeroizing<Vec<u8>>, MDataError> {
    let mut result = Zeroizing::new(vec![0; size]); let mut position = 0;
    while position < size {
        let end = size.min(position + CHUNK_BYTES);
        let read = read_some(source, &mut result[position..end])?;
        if read == 0 { return Err(MDataError::InvalidArchive); } position += read;
    }
    Ok(result)
}
fn write_all<W: Write + ?Sized>(output: &mut W, bytes: &[u8]) -> Result<(), MDataError> {
    let mut position = 0;
    while position < bytes.len() {
        let count = catch_unwind(AssertUnwindSafe(|| output.write(&bytes[position..]))).map_err(|_| MDataError::IoError)??;
        if count == 0 || count > bytes.len() - position { return Err(MDataError::IoError); } position += count;
    }
    Ok(())
}
fn flush<W: Write + ?Sized>(output: &mut W) -> Result<(), MDataError> {
    catch_unwind(AssertUnwindSafe(|| output.flush())).map_err(|_| MDataError::IoError)??; Ok(())
}
fn cipher(password: &[u8], salt: &[u8]) -> Result<Aes256Gcm, MDataError> {
    let key = session::derive(password, salt)?;
    Aes256Gcm::new_from_slice(key.as_ref()).map_err(|_| MDataError::CryptoUnavailable)
}
fn file_bytes(manifest: &Value) -> u64 { manifest["files"].as_array().unwrap().iter().map(|f| f["size"].as_u64().unwrap()).sum() }

struct ChunkWriter<'a, W: Write + ?Sized> {
    output: &'a mut W, cipher: Aes256Gcm, prefix: [u8; 8], aad: Vec<u8>,
    expected: u64, total: u64, counter: u64, buffer: Zeroizing<Vec<u8>>, archive_bytes: u64,
}
impl<W: Write + ?Sized> ChunkWriter<'_, W> {
    fn emit(&mut self, terminal: bool) -> Result<(), MDataError> {
        let counter = u32::try_from(self.counter).map_err(|_| MDataError::TooLarge)?.to_be_bytes();
        let mut frame = [0u8; 5]; frame[..4].copy_from_slice(&(self.buffer.len() as u32).to_be_bytes()); frame[4] = u8::from(terminal);
        let mut nonce = [0u8; 12]; nonce[..8].copy_from_slice(&self.prefix); nonce[8..].copy_from_slice(&counter);
        let mut aad = self.aad.clone(); aad.extend_from_slice(&counter); aad.extend_from_slice(&frame);
        let ciphertext = self.cipher.encrypt(Nonce::from_slice(&nonce), Payload { msg: &self.buffer, aad: &aad }).map_err(|_| MDataError::CryptoUnavailable)?;
        write_all(self.output, &frame)?; write_all(self.output, &ciphertext)?;
        self.archive_bytes += (frame.len() + ciphertext.len()) as u64; self.counter += 1;
        self.buffer.zeroize(); self.buffer.clear(); Ok(())
    }
    fn feed(&mut self, bytes: &[u8]) -> Result<(), MDataError> {
        self.total = self.total.checked_add(bytes.len() as u64).filter(|n| *n <= self.expected).ok_or(MDataError::SourceChanged)?;
        let mut position = 0;
        while position < bytes.len() {
            let count = (CHUNK_BYTES - self.buffer.len()).min(bytes.len() - position);
            self.buffer.extend_from_slice(&bytes[position..position + count]); position += count;
            if self.buffer.len() == CHUNK_BYTES { self.emit(false)?; }
        }
        Ok(())
    }
    fn finish(&mut self) -> Result<(), MDataError> {
        if self.total != self.expected { return Err(MDataError::SourceChanged); }
        if !self.buffer.is_empty() { self.emit(false)?; } self.emit(true)?; flush(self.output)
    }
}

/// The output is complete only after success; discard partial output on error.
pub fn encrypt_stream<W, F>(manifest: &Value, password: &str, source_factory: &mut F, output: &mut W,
    policy: &MDataPolicy<'_>, kind: ArchiveKind) -> Result<ExportSummary, MDataError>
where W: Write + ?Sized, F: FnMut(&str) -> Result<Box<dyn Read>, MDataError> + ?Sized {
    let password = session::password_bytes(password)?;
    let clean = OwnedValue(validate_manifest(manifest, policy)?); let encoded = canonical(&clean.0)?;
    let file_bytes = file_bytes(&clean.0); let expected = 4 + encoded.len() as u64 + file_bytes;
    let mut salt = [0u8; 16]; let mut prefix = [0u8; 8];
    getrandom::getrandom(&mut salt).map_err(|_| MDataError::CryptoUnavailable)?;
    getrandom::getrandom(&mut prefix).map_err(|_| MDataError::CryptoUnavailable)?;
    let header = canonical(&json!({"schema":1,"sourceClient":session::SOURCE_CLIENT,"sourceVersion":session::SOURCE_VERSION,
        "cipher":"AES-256-GCM","chunkBytes":CHUNK_BYTES,"plaintextBytes":expected,"noncePrefix":STANDARD.encode(prefix),
        "kdf":{"name":"scrypt","n":32768,"r":8,"p":1,"salt":STANDARD.encode(salt)}}))?;
    let mut aad = kind.magic().to_vec(); aad.extend_from_slice(&(header.len() as u16).to_be_bytes()); aad.extend_from_slice(&header);
    let cipher = cipher(password, &salt)?; write_all(output, &aad)?; let archive_bytes = aad.len() as u64;
    let mut writer = ChunkWriter { output, cipher, prefix, aad, expected, total:0, counter:0,
        buffer:Zeroizing::new(Vec::with_capacity(CHUNK_BYTES)), archive_bytes };
    writer.feed(&(encoded.len() as u32).to_be_bytes())?; writer.feed(&encoded)?;
    let mut buffer = Zeroizing::new(vec![0; CHUNK_BYTES]);
    for item in clean.0["files"].as_array().unwrap() {
        let mut source = catch_unwind(AssertUnwindSafe(|| source_factory(item["path"].as_str().unwrap()))).map_err(|_| MDataError::IoError)??;
        let mut remaining = item["size"].as_u64().unwrap();
        while remaining != 0 {
            let read = read_some(&mut *source, &mut buffer[..remaining.min(CHUNK_BYTES as u64) as usize])?;
            if read == 0 { return Err(MDataError::SourceChanged); }
            writer.feed(&buffer[..read])?; remaining -= read as u64;
        }
        if read_some(&mut *source, &mut buffer[..1])? != 0 { return Err(MDataError::SourceChanged); }
    }
    writer.finish()?;
    Ok(ExportSummary { schema:1, files:clean.0["files"].as_array().unwrap().len(), file_bytes,
        data_chunks:writer.counter - 1, archive_bytes:writer.archive_bytes })
}

struct Header { kind: ArchiveKind, salt: Zeroizing<Vec<u8>>, prefix: [u8; 8], aad: Vec<u8>, expected: u64 }
fn read_header<R: Read + ?Sized>(source: &mut R) -> Result<Header, MDataError> {
    let prefix = read_exact(source, 10)?; let kind = archive_kind(&prefix[..8])?;
    let length = u16::from_be_bytes(prefix[8..10].try_into().unwrap()) as usize;
    if !(2..=MAX_HEADER_BYTES).contains(&length) { return Err(MDataError::InvalidArchive); }
    let raw = read_exact(source, length)?; let header = OwnedValue(session::parse_json(&raw, "invalid_archive")?);
    let h = &header.0;
    if !exact(h, &["schema","sourceClient","sourceVersion","cipher","chunkBytes","plaintextBytes","noncePrefix","kdf"])
        || h["schema"].as_u64() != Some(1) || !h["sourceClient"].is_string() || !h["sourceVersion"].is_string()
        || h["cipher"].as_str() != Some("AES-256-GCM") || h["chunkBytes"].as_u64() != Some(CHUNK_BYTES as u64)
        || !h["plaintextBytes"].as_u64().is_some_and(|n| (6..=MAX_PLAINTEXT_BYTES).contains(&n)) { return Err(MDataError::InvalidArchive); }
    if h["sourceClient"] != session::SOURCE_CLIENT || h["sourceVersion"] != session::SOURCE_VERSION { return Err(MDataError::UnsupportedVersion); }
    let kdf = &h["kdf"];
    if !exact(kdf, &["name","n","r","p","salt"]) || kdf["name"].as_str() != Some("scrypt")
        || kdf["n"].as_u64() != Some(32768) || kdf["r"].as_u64() != Some(8) || kdf["p"].as_u64() != Some(1) { return Err(MDataError::InvalidArchive); }
    let salt = session::decode_base64(&kdf["salt"], 16, ArchiveError::InvalidArchive)?;
    let nonce = session::decode_base64(&h["noncePrefix"], 8, ArchiveError::InvalidArchive)?;
    if salt.len() != 16 || nonce.len() != 8 || *canonical(h)? != *raw { return Err(MDataError::InvalidArchive); }
    let mut aad = prefix.to_vec(); aad.extend_from_slice(&raw);
    Ok(Header { kind, salt, prefix:nonce[..].try_into().unwrap(), aad, expected:h["plaintextBytes"].as_u64().unwrap() })
}

struct PlainReader<'a, R: Read + ?Sized> {
    source: &'a mut R, cipher: Aes256Gcm, prefix: [u8; 8], aad: Vec<u8>, expected: u64,
    total: u64, counter: u64, buffer: Zeroizing<Vec<u8>>, position: usize, ended: bool,
}
impl<R: Read + ?Sized> PlainReader<'_, R> {
    fn next(&mut self) -> Result<Zeroizing<Vec<u8>>, MDataError> {
        if self.ended { return Err(MDataError::InvalidArchive); }
        let counter = u32::try_from(self.counter).map_err(|_| MDataError::InvalidArchive)?.to_be_bytes();
        let frame = read_exact(self.source, 5)?;
        let length = u32::from_be_bytes(frame[..4].try_into().unwrap()) as usize; let flag = frame[4];
        if length > CHUNK_BYTES || flag > 1 || (flag == 1 && length != 0) || (flag == 0 && length == 0) { return Err(MDataError::InvalidArchive); }
        let ciphertext = read_exact(self.source, length + 16)?;
        let mut nonce = [0u8;12]; nonce[..8].copy_from_slice(&self.prefix); nonce[8..].copy_from_slice(&counter);
        let mut aad = self.aad.clone(); aad.extend_from_slice(&counter); aad.extend_from_slice(&frame);
        let plaintext = Zeroizing::new(self.cipher.decrypt(Nonce::from_slice(&nonce), Payload { msg:&ciphertext, aad:&aad }).map_err(|_| MDataError::AuthenticationFailed)?);
        self.counter += 1; self.total += plaintext.len() as u64;
        if self.total > self.expected { return Err(MDataError::InvalidArchive); }
        if flag == 1 {
            if self.total != self.expected || read_some(self.source, &mut [0u8;1])? != 0 { return Err(MDataError::InvalidArchive); }
            self.ended = true;
        }
        Ok(plaintext)
    }
    fn read_exact(&mut self, size: usize) -> Result<Zeroizing<Vec<u8>>, MDataError> {
        let mut result = Zeroizing::new(Vec::with_capacity(size));
        while result.len() < size {
            if self.position == self.buffer.len() {
                self.buffer = self.next()?; self.position = 0;
                if self.buffer.is_empty() { return Err(MDataError::InvalidArchive); }
            }
            let count = (size - result.len()).min(self.buffer.len() - self.position);
            result.extend_from_slice(&self.buffer[self.position..self.position + count]);
            self.buffer[self.position..self.position + count].zeroize(); self.position += count;
        }
        Ok(result)
    }
    fn finish(&mut self) -> Result<(), MDataError> {
        if self.position != self.buffer.len() || !self.next()?.is_empty() || !self.ended { return Err(MDataError::InvalidArchive); } Ok(())
    }
}

/// Write only to PRIVATE STAGING. On any error discard every sink. Commit files
/// and credentials only after `Ok`, which verifies the terminal frame and EOF.
/// Preflight sees authenticated counts before any sink is opened; it must not
/// install state. Successful return transfers ownership of sensitive manifest.
pub fn decrypt_stream<R, F>(source: &mut R, password: &str, staging_sink_factory: &mut F,
    policy: &MDataPolicy<'_>, preflight: Option<&mut dyn FnMut(u64, usize) -> Result<(), MDataError>>,
    expected_kind: Option<ArchiveKind>) -> Result<Value, MDataError>
where R: Read + ?Sized, F: FnMut(&str, u64) -> Result<Box<dyn Write>, MDataError> + ?Sized {
    let password = session::password_bytes(password)?; let header = read_header(source)?;
    if expected_kind.is_some_and(|expected| expected != header.kind) { return Err(MDataError::InvalidArchive); }
    let mut reader = PlainReader { source, cipher:cipher(password, &header.salt)?, prefix:header.prefix,
        aad:header.aad, expected:header.expected, total:0, counter:0, buffer:Zeroizing::new(Vec::new()), position:0, ended:false };
    let length = u32::from_be_bytes(reader.read_exact(4)?[..].try_into().unwrap()) as usize;
    if length > MAX_MANIFEST_BYTES { return Err(MDataError::TooLarge); }
    if length < 2 { return Err(MDataError::InvalidPayload); }
    let raw = reader.read_exact(length)?; let parsed = OwnedValue(session::parse_json(&raw, "invalid_payload")?);
    let mut clean = OwnedValue(validate_manifest(&parsed.0, policy)?); let file_bytes = file_bytes(&clean.0);
    if *canonical(&clean.0)? != *raw || 4 + length as u64 + file_bytes != header.expected { return Err(MDataError::InvalidArchive); }
    if let Some(check) = preflight { catch_unwind(AssertUnwindSafe(|| check(file_bytes, clean.0["files"].as_array().unwrap().len()))).map_err(|_| MDataError::IoError)??; }
    for item in clean.0["files"].as_array().unwrap() {
        let mut remaining = item["size"].as_u64().unwrap();
        let mut sink = catch_unwind(AssertUnwindSafe(|| staging_sink_factory(item["path"].as_str().unwrap(), remaining))).map_err(|_| MDataError::IoError)??;
        while remaining != 0 {
            let data = reader.read_exact(remaining.min(CHUNK_BYTES as u64) as usize)?;
            write_all(&mut *sink, &data)?; remaining -= data.len() as u64;
        }
        flush(&mut *sink)?;
    }
    reader.finish()?; Ok(std::mem::take(&mut clean.0))
}
