//! Non-secret MData recovery latch. Explicit local MXMOD root only: this does
//! not import/restore a profile, inspect credentials, discover data or launch
//! clients. A malformed/partial/inaccessible marker always requires recovery.
//! The caller still owns the broader transfer transaction and its mutex.
use crate::settings_io::{GuardedStore, SafeStorePath};
use serde::{Deserialize, Serialize};
use std::{
    fmt,
    path::{Path, PathBuf},
};

pub const FILENAME: &str = "MXMOD-MData-Recovery.json";
pub const BACKUP_DIRECTORY: &str = "MXMOD-MData-Backups";
pub const MAX_BYTES: usize = 16_384;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct RecoveryRequired;
impl RecoveryRequired {
    pub fn code(self) -> &'static str {
        "recovery_required"
    }
}
impl fmt::Display for RecoveryRequired {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for RecoveryRequired {}
pub type Result<T> = std::result::Result<T, RecoveryRequired>;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct RecoveryMarker {
    pub schema: u8,
    pub operation_id: String,
    pub profile_root: String,
    pub backup_file: String,
}

fn root_path(root: &Path) -> Result<PathBuf> {
    let safe = SafeStorePath::new(root).map_err(|_| RecoveryRequired)?;
    if safe.path().file_name().and_then(|s| s.to_str()) != Some("MXMOD") {
        return Err(RecoveryRequired);
    }
    Ok(safe.path().to_owned())
}
fn marker_path(root: &Path) -> Result<(PathBuf, SafeStorePath)> {
    let root = root_path(root)?;
    let path = SafeStorePath::new(root.parent().ok_or(RecoveryRequired)?.join(FILENAME))
        .map_err(|_| RecoveryRequired)?;
    Ok((root, path))
}

/// Equivalent explicit-root candidate, without environment fallback/discovery.
/// Existing ancestors (including MXMOD itself) are validated, never created.
pub fn root_candidate(explicit_local_root: &Path) -> Result<PathBuf> {
    let config =
        crate::launcher_config::config_path(explicit_local_root).map_err(|_| RecoveryRequired)?;
    let candidate = SafeStorePath::new(config).map_err(|_| RecoveryRequired)?;
    let _pins = candidate.begin(false).map_err(|_| RecoveryRequired)?;
    Ok(candidate
        .path()
        .parent()
        .ok_or(RecoveryRequired)?
        .to_owned())
}

fn backup_path(root: &Path, backup: &Path, writing: bool) -> Result<PathBuf> {
    let safe = SafeStorePath::new(backup).map_err(|_| RecoveryRequired)?;
    let actual = safe.path();
    let wanted = root
        .parent()
        .ok_or(RecoveryRequired)?
        .join(BACKUP_DIRECTORY);
    let extension = actual
        .extension()
        .and_then(|s| s.to_str())
        .ok_or(RecoveryRequired)?;
    if if writing {
        extension != "mdata"
    } else {
        !extension.eq_ignore_ascii_case("mdata")
    } {
        return Err(RecoveryRequired);
    }
    #[cfg(windows)]
    if !win::same_path(actual.parent().ok_or(RecoveryRequired)?, &wanted)? {
        return Err(RecoveryRequired);
    }
    #[cfg(not(windows))]
    {
        let _ = wanted;
        return Err(RecoveryRequired);
    }
    Ok(actual.to_owned())
}

// Python json.loads(bytes) recognizes UTF-8/16/32, including their BOMs. Keep
// that read compatibility; writers always emit bounded UTF-8. Invalid Unicode
// and duplicate decoded keys still fail through the shared strict JSON parser.
fn json_utf8(data: &[u8]) -> Result<Vec<u8>> {
    enum Encoding {
        Utf8,
        Utf16(bool),
        Utf32(bool),
    }
    let (encoding, bytes) = if data.starts_with(b"\xff\xfe\0\0") {
        (Encoding::Utf32(true), &data[4..])
    } else if data.starts_with(b"\0\0\xfe\xff") {
        (Encoding::Utf32(false), &data[4..])
    } else if data.starts_with(b"\xff\xfe") {
        (Encoding::Utf16(true), &data[2..])
    } else if data.starts_with(b"\xfe\xff") {
        (Encoding::Utf16(false), &data[2..])
    } else if data.starts_with(b"\xef\xbb\xbf") {
        (Encoding::Utf8, &data[3..])
    } else if data.len() >= 4 && data[0] == 0 {
        (
            if data[1] == 0 {
                Encoding::Utf32(false)
            } else {
                Encoding::Utf16(false)
            },
            data,
        )
    } else if data.len() >= 4 && data[1] == 0 {
        (
            if data[2] == 0 && data[3] == 0 {
                Encoding::Utf32(true)
            } else {
                Encoding::Utf16(true)
            },
            data,
        )
    } else if data.len() == 2 && data[0] == 0 {
        (Encoding::Utf16(false), data)
    } else if data.len() == 2 && data[1] == 0 {
        (Encoding::Utf16(true), data)
    } else {
        (Encoding::Utf8, data)
    };
    match encoding {
        Encoding::Utf8 => Ok(bytes.to_vec()),
        Encoding::Utf16(little) => {
            if bytes.len() % 2 != 0 {
                return Err(RecoveryRequired);
            }
            let units: Vec<_> = bytes
                .chunks_exact(2)
                .map(|b| {
                    if little {
                        u16::from_le_bytes([b[0], b[1]])
                    } else {
                        u16::from_be_bytes([b[0], b[1]])
                    }
                })
                .collect();
            String::from_utf16(&units)
                .map(String::into_bytes)
                .map_err(|_| RecoveryRequired)
        }
        Encoding::Utf32(little) => {
            if bytes.len() % 4 != 0 {
                return Err(RecoveryRequired);
            }
            let mut text = String::new();
            for b in bytes.chunks_exact(4) {
                let unit = if little {
                    u32::from_le_bytes([b[0], b[1], b[2], b[3]])
                } else {
                    u32::from_be_bytes([b[0], b[1], b[2], b[3]])
                };
                text.push(char::from_u32(unit).ok_or(RecoveryRequired)?);
            }
            Ok(text.into_bytes())
        }
    }
}
fn parse(root: &Path, data: &[u8]) -> Result<RecoveryMarker> {
    if data.len() > MAX_BYTES {
        return Err(RecoveryRequired);
    }
    let value = crate::session_archive::parse_json(&json_utf8(data)?, "invalid_payload")
        .map_err(|_| RecoveryRequired)?;
    let fields = value.as_object().ok_or(RecoveryRequired)?;
    if fields.len() != 4 || value["schema"].as_u64() != Some(1) {
        return Err(RecoveryRequired);
    }
    let marker: RecoveryMarker = serde_json::from_value(value).map_err(|_| RecoveryRequired)?;
    if !crate::startup_status::valid_operation_id(&marker.operation_id)
        || marker.profile_root != root.to_str().ok_or(RecoveryRequired)?
    {
        return Err(RecoveryRequired);
    }
    backup_path(root, Path::new(&marker.backup_file), false)?;
    Ok(marker)
}
fn read_held(root: &Path, guard: &GuardedStore) -> Result<Option<RecoveryMarker>> {
    guard
        .read_bounded(MAX_BYTES)
        .map_err(|_| RecoveryRequired)?
        .map(|bytes| parse(root, &bytes))
        .transpose()
}
pub fn read(root: &Path) -> Result<Option<RecoveryMarker>> {
    let (root, path) = marker_path(root)?;
    let Some(guard) = path.begin(false).map_err(|_| RecoveryRequired)? else {
        return Ok(None);
    };
    read_held(&root, &guard)
}
pub fn ensure_clear(root: &Path) -> Result<()> {
    if read(root)?.is_some() {
        Err(RecoveryRequired)
    } else {
        Ok(())
    }
}

/// Flush a new marker, then publish without replacing an existing object. No
/// parent, profile, backup directory or backup file is created or inspected.
pub fn write(root: &Path, backup: &Path) -> Result<RecoveryMarker> {
    let (root, path) = marker_path(root)?;
    let backup = backup_path(&root, backup, true)?;
    let guard = path
        .begin(false)
        .map_err(|_| RecoveryRequired)?
        .ok_or(RecoveryRequired)?;
    let _writer = guard.writer_lock().map_err(|_| RecoveryRequired)?;
    if read_held(&root, &guard)?.is_some() {
        return Err(RecoveryRequired);
    }
    let marker = RecoveryMarker {
        schema: 1,
        operation_id: crate::startup_status::new_operation_id().map_err(|_| RecoveryRequired)?,
        profile_root: root.to_str().ok_or(RecoveryRequired)?.to_owned(),
        backup_file: backup.to_str().ok_or(RecoveryRequired)?.to_owned(),
    };
    let bytes = serde_json::to_vec(&marker).map_err(|_| RecoveryRequired)?;
    if bytes.len() > MAX_BYTES {
        return Err(RecoveryRequired);
    }
    guard.atomic_create(&bytes).map_err(|_| RecoveryRequired)?;
    Ok(marker)
}

/// Missing marker is idempotent. Otherwise compare the complete expected record
/// while holding the exact file open without write/delete sharing, then delete
/// that handle. A foreign, malformed or busy marker is never removed.
pub fn clear(root: &Path, expected: &RecoveryMarker) -> Result<()> {
    let (root, path) = marker_path(root)?;
    let Some(guard) = path.begin(false).map_err(|_| RecoveryRequired)? else {
        return Ok(());
    };
    let _writer = guard.writer_lock().map_err(|_| RecoveryRequired)?;
    #[cfg(windows)]
    {
        win::clear_held(&root, &guard, expected)
    }
    #[cfg(not(windows))]
    {
        let _ = (root, guard, expected);
        Err(RecoveryRequired)
    }
}

#[cfg(windows)]
mod win {
    use super::*;
    use std::{
        ffi::c_void,
        fs::File,
        io::Read,
        os::windows::{
            ffi::OsStrExt,
            io::{AsRawHandle, FromRawHandle},
        },
        ptr,
    };
    #[repr(C)]
    #[derive(Default, PartialEq, Eq)]
    struct FileTime {
        low: u32,
        high: u32,
    }
    #[repr(C)]
    #[derive(Default)]
    struct Info {
        attributes: u32,
        creation: FileTime,
        access: FileTime,
        write: FileTime,
        volume: u32,
        size_high: u32,
        size_low: u32,
        links: u32,
        index_high: u32,
        index_low: u32,
    }
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn CreateFileW(
            name: *const u16,
            access: u32,
            share: u32,
            security: *const c_void,
            creation: u32,
            flags: u32,
            template: *mut c_void,
        ) -> *mut c_void;
        fn GetLastError() -> u32;
        fn GetFileType(handle: *mut c_void) -> u32;
        fn GetFileInformationByHandle(handle: *mut c_void, info: *mut Info) -> i32;
        fn GetFinalPathNameByHandleW(
            handle: *mut c_void,
            buffer: *mut u16,
            length: u32,
            flags: u32,
        ) -> u32;
        fn SetFileInformationByHandle(
            handle: *mut c_void,
            class: u32,
            buffer: *const c_void,
            length: u32,
        ) -> i32;
        fn LCMapStringEx(
            locale: *const u16,
            flags: u32,
            source: *const u16,
            source_length: i32,
            destination: *mut u16,
            destination_length: i32,
            version: *const c_void,
            reserved: *const c_void,
            sort: isize,
        ) -> i32;
    }
    fn normalized(path: &Path) -> Result<Vec<u16>> {
        let text = path.to_str().ok_or(RecoveryRequired)?.replace('/', "\\");
        let source: Vec<u16> = text.encode_utf16().collect();
        let locale = [0u16];
        let count = unsafe {
            LCMapStringEx(
                locale.as_ptr(),
                0x100,
                source.as_ptr(),
                source.len() as i32,
                ptr::null_mut(),
                0,
                ptr::null(),
                ptr::null(),
                0,
            )
        };
        if count <= 0 {
            return Err(RecoveryRequired);
        }
        let mut output = vec![0u16; count as usize];
        if unsafe {
            LCMapStringEx(
                locale.as_ptr(),
                0x100,
                source.as_ptr(),
                source.len() as i32,
                output.as_mut_ptr(),
                count,
                ptr::null(),
                ptr::null(),
                0,
            )
        } != count
        {
            return Err(RecoveryRequired);
        }
        Ok(output)
    }
    pub(super) fn same_path(left: &Path, right: &Path) -> Result<bool> {
        Ok(normalized(left)? == normalized(right)?)
    }
    fn validate(file: &File, path: &Path) -> Result<Info> {
        let handle = file.as_raw_handle();
        let mut info = Info::default();
        if unsafe { GetFileType(handle) } != 1
            || unsafe { GetFileInformationByHandle(handle, &mut info) } == 0
            || info.attributes & (0x10 | 0x400) != 0
            || info.links != 1
        {
            return Err(RecoveryRequired);
        }
        let mut text = vec![0u16; 32768];
        let count =
            unsafe { GetFinalPathNameByHandleW(handle, text.as_mut_ptr(), text.len() as u32, 0) }
                as usize;
        if count == 0 || count >= text.len() {
            return Err(RecoveryRequired);
        }
        let actual = String::from_utf16(&text[..count]).map_err(|_| RecoveryRequired)?;
        if !same_path(
            Path::new(actual.strip_prefix("\\\\?\\").unwrap_or(&actual)),
            path,
        )? {
            return Err(RecoveryRequired);
        }
        Ok(info)
    }
    pub(super) fn clear_held(
        root: &Path,
        guard: &GuardedStore,
        expected: &RecoveryMarker,
    ) -> Result<()> {
        // GuardedStore pins every ancestor without delete sharing. Thus the
        // absolute open below cannot be redirected by renaming an ancestor.
        let name: Vec<u16> = guard
            .path()
            .as_os_str()
            .encode_wide()
            .chain(Some(0))
            .collect();
        let raw = unsafe {
            CreateFileW(
                name.as_ptr(),
                0x80000000 | 0x00010000,
                1,
                ptr::null(),
                3,
                0x00200000,
                ptr::null_mut(),
            )
        };
        if raw as isize == -1 {
            return if matches!(unsafe { GetLastError() }, 2 | 3) {
                Ok(())
            } else {
                Err(RecoveryRequired)
            };
        }
        let file = unsafe { File::from_raw_handle(raw) };
        let before = validate(&file, guard.path())?;
        let length = ((before.size_high as u64) << 32) | u64::from(before.size_low);
        if length > MAX_BYTES as u64 {
            return Err(RecoveryRequired);
        }
        let mut bytes = Vec::new();
        (&file)
            .take((MAX_BYTES + 1) as u64)
            .read_to_end(&mut bytes)
            .map_err(|_| RecoveryRequired)?;
        let after = validate(&file, guard.path())?;
        if bytes.len() as u64 != length
            || before.write != after.write
            || (before.volume, before.index_high, before.index_low)
                != (after.volume, after.index_high, after.index_low)
            || parse(root, &bytes)? != *expected
        {
            return Err(RecoveryRequired);
        }
        let remove = 1u8;
        if unsafe {
            SetFileInformationByHandle(
                file.as_raw_handle(),
                4,
                &remove as *const _ as *const c_void,
                1,
            )
        } == 0
        {
            return Err(RecoveryRequired);
        }
        Ok(())
    }
}
