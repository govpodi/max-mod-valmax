//! Metadata-only handle enumeration. The outer SafeStorePath guard retains all
//! ancestors while this dedicated directory handle owns its enumeration cursor.
use super::*;
use std::{
    collections::BTreeSet,
    ffi::c_void,
    fs::File,
    mem,
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle},
    },
    ptr,
};

type Handle = *mut c_void;
#[repr(C)]
#[derive(Default)]
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
#[repr(C)]
struct IoStatus {
    status: usize,
    information: usize,
}
#[repr(C)]
struct UnicodeString {
    length: u16,
    maximum: u16,
    buffer: *mut u16,
}
#[repr(C)]
struct ObjectAttributes {
    length: u32,
    root: Handle,
    name: *mut UnicodeString,
    attributes: u32,
    security: *mut c_void,
    quality: *mut c_void,
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
        template: Handle,
    ) -> Handle;
    fn GetLastError() -> u32;
    fn GetFileType(handle: Handle) -> u32;
    fn GetFileInformationByHandle(handle: Handle, info: *mut Info) -> i32;
    fn GetFileInformationByHandleEx(
        handle: Handle,
        class: u32,
        buffer: *mut c_void,
        size: u32,
    ) -> i32;
    fn GetFinalPathNameByHandleW(handle: Handle, buffer: *mut u16, length: u32, flags: u32) -> u32;
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
#[link(name = "ntdll")]
unsafe extern "system" {
    fn NtCreateFile(
        handle: *mut Handle,
        access: u32,
        attributes: *mut ObjectAttributes,
        status: *mut IoStatus,
        allocation: *const i64,
        file_attributes: u32,
        share: u32,
        disposition: u32,
        options: u32,
        ea: *const c_void,
        ea_length: u32,
    ) -> i32;
    fn RtlNtStatusToDosError(status: i32) -> u32;
}
fn normalized(path: &Path) -> Result<Vec<u16>> {
    let text = path
        .to_str()
        .ok_or(CaptureError::UnsafePath)?
        .replace('/', "\\");
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
        return Err(CaptureError::UnsafePath);
    }
    let mut result = vec![0u16; count as usize];
    if unsafe {
        LCMapStringEx(
            locale.as_ptr(),
            0x100,
            source.as_ptr(),
            source.len() as i32,
            result.as_mut_ptr(),
            count,
            ptr::null(),
            ptr::null(),
            0,
        )
    } != count
    {
        return Err(CaptureError::UnsafePath);
    }
    Ok(result)
}
fn verify(file: &File, path: &Path, directory: bool) -> Result<Info> {
    let handle = file.as_raw_handle();
    let mut info = Info::default();
    if unsafe { GetFileType(handle) } != 1
        || unsafe { GetFileInformationByHandle(handle, &mut info) } == 0
        || info.attributes & 0x400 != 0
        || (info.attributes & 0x10 != 0) != directory
        || !directory && info.links != 1
    {
        return Err(CaptureError::UnsafePath);
    }
    let mut output = vec![0u16; 32768];
    let count =
        unsafe { GetFinalPathNameByHandleW(handle, output.as_mut_ptr(), output.len() as u32, 0) }
            as usize;
    if count == 0 || count >= output.len() {
        return Err(CaptureError::UnsafePath);
    }
    let actual = String::from_utf16(&output[..count]).map_err(|_| CaptureError::UnsafePath)?;
    if normalized(Path::new(actual.strip_prefix("\\\\?\\").unwrap_or(&actual)))?
        != normalized(path)?
    {
        return Err(CaptureError::UnsafePath);
    }
    Ok(info)
}
pub(super) struct Directory(File);
impl Directory {
    pub(super) fn open(root: &Path) -> Result<Self> {
        let text: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
        // All parents and this root are already held without DELETE sharing.
        // OPEN_REPARSE_POINT plus final-path verification refuses redirection.
        let raw = unsafe {
            CreateFileW(
                text.as_ptr(),
                0x100081,
                3,
                ptr::null(),
                3,
                0x02200000,
                ptr::null_mut(),
            )
        };
        if raw as isize == -1 {
            return Err(if matches!(unsafe { GetLastError() }, 2 | 3) {
                CaptureError::InvalidProfile
            } else {
                CaptureError::IoError
            });
        }
        let file = unsafe { File::from_raw_handle(raw) };
        verify(&file, root, true)?;
        Ok(Self(file))
    }
    fn metadata(&self, root: &Path, name: &str) -> Result<usize> {
        if login::auth_id(name).is_none() {
            return Err(CaptureError::InvalidProfile);
        }
        let mut text: Vec<u16> = name.encode_utf16().collect();
        let mut name = UnicodeString {
            length: (text.len() * 2) as u16,
            maximum: (text.len() * 2) as u16,
            buffer: text.as_mut_ptr(),
        };
        let mut object = ObjectAttributes {
            length: mem::size_of::<ObjectAttributes>() as u32,
            root: self.0.as_raw_handle(),
            name: &mut name,
            attributes: 0x40,
            security: ptr::null_mut(),
            quality: ptr::null_mut(),
        };
        let mut status = IoStatus {
            status: 0,
            information: 0,
        };
        let mut raw = ptr::null_mut();
        // Attributes only: listing never opens auth data for reading. Such an
        // open is not a data-read/share lease; this is point-in-time metadata.
        // Capture obtains separate stable data-read handles, then repeats both
        // listing and byte comparisons before returning its bounded snapshot.
        let result = unsafe {
            NtCreateFile(
                &mut raw,
                0x100080,
                &mut object,
                &mut status,
                ptr::null(),
                0x80,
                1,
                1,
                0x200020,
                ptr::null(),
                0,
            )
        };
        if result < 0 {
            let error = unsafe { RtlNtStatusToDosError(result) };
            return Err(match error {
                2 | 3 => CaptureError::ProfileChanged,
                267 | 4390 | 1920 | 1921 => CaptureError::UnsafePath,
                _ => CaptureError::IoError,
            });
        }
        let file = unsafe { File::from_raw_handle(raw) };
        let name = String::from_utf16(&text).map_err(|_| CaptureError::InvalidProfile)?;
        let info = verify(&file, &root.join(name), false)?;
        let size = ((info.size_high as u64) << 32) | u64::from(info.size_low);
        if !(1..=login::MAX_AUTH_BYTES as u64).contains(&size) {
            return Err(CaptureError::InvalidProfile);
        }
        Ok(size as usize)
    }
    pub(super) fn listing(&self, root: &Path) -> Result<Vec<AuthFile>> {
        verify(&self.0, root, true)?;
        let mut names = BTreeSet::new();
        let mut buffer = vec![0u64; 8192]; // aligned, fixed 64 KiB kernel buffer
        let mut restart = true;
        let mut enumerated = 0usize;
        loop {
            buffer.fill(0);
            let ok = unsafe {
                GetFileInformationByHandleEx(
                    self.0.as_raw_handle(),
                    if restart { 11 } else { 10 },
                    buffer.as_mut_ptr() as *mut c_void,
                    (buffer.len() * 8) as u32,
                )
            };
            restart = false;
            if ok == 0 {
                if unsafe { GetLastError() } == 18 {
                    break;
                }
                return Err(CaptureError::IoError);
            }
            let bytes = unsafe {
                std::slice::from_raw_parts(buffer.as_ptr() as *const u8, buffer.len() * 8)
            };
            for name in parse_names(bytes)? {
                enumerated += 1;
                if enumerated > MAX_DIRECTORY_ENTRIES + 2 {
                    return Err(CaptureError::InvalidProfile);
                }
                if matches!(name.as_str(), "." | "..") {
                    continue;
                }
                if !names.insert(name) || names.len() > MAX_DIRECTORY_ENTRIES {
                    return Err(CaptureError::InvalidProfile);
                }
            }
        }
        let mut result = Vec::new();
        for name in names {
            if !name
                .as_bytes()
                .get(..5)
                .is_some_and(|p| p.eq_ignore_ascii_case(b"auth_"))
            {
                continue;
            }
            if login::auth_id(&name).is_none() {
                return Err(CaptureError::InvalidProfile);
            }
            let size = self.metadata(root, &name)?;
            result.push(AuthFile { path: name, size });
            if result.len() > MAX_CANDIDATES {
                return Err(CaptureError::InvalidProfile);
            }
        }
        verify(&self.0, root, true)?;
        Ok(result)
    }
}

// FileIdBothDirectoryInfo offsets are fixed by the Windows ABI. Directory IDs
// and sizes from this listing are never trusted as file-object validation.
fn parse_names(bytes: &[u8]) -> Result<Vec<String>> {
    let mut result = Vec::new();
    let mut offset = 0usize;
    loop {
        if offset.checked_add(104).is_none_or(|end| end > bytes.len()) {
            return Err(CaptureError::InvalidProfile);
        }
        let next = u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        let length =
            u32::from_le_bytes(bytes[offset + 60..offset + 64].try_into().unwrap()) as usize;
        let end = if next == 0 {
            bytes.len()
        } else {
            if next < 104 || next % 8 != 0 {
                return Err(CaptureError::InvalidProfile);
            }
            offset
                .checked_add(next)
                .filter(|end| *end <= bytes.len())
                .ok_or(CaptureError::InvalidProfile)?
        };
        if length == 0 || length % 2 != 0 || length > end - offset - 104 {
            return Err(CaptureError::InvalidProfile);
        }
        let wide: Vec<u16> = bytes[offset + 104..offset + 104 + length]
            .chunks_exact(2)
            .map(|p| u16::from_le_bytes([p[0], p[1]]))
            .collect();
        let name = String::from_utf16(&wide).map_err(|_| CaptureError::InvalidProfile)?;
        if name.contains(['\0', '/', '\\', ':']) {
            return Err(CaptureError::InvalidProfile);
        }
        result.push(name);
        if result.len() > MAX_DIRECTORY_ENTRIES {
            return Err(CaptureError::InvalidProfile);
        }
        if next == 0 {
            break;
        }
        offset = end;
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bounded_directory_record_chain() {
        let mut record = vec![0u8; 128];
        record[60..64].copy_from_slice(&12u32.to_le_bytes());
        for (i, c) in "auth_2".encode_utf16().enumerate() {
            record[104 + i * 2..106 + i * 2].copy_from_slice(&c.to_le_bytes());
        }
        assert_eq!(parse_names(&record).unwrap(), vec!["auth_2"]);
        for (next, length) in [(103, 12), (105, 12), (136, 12), (0, 25), (0, 26), (0, 0)] {
            let mut bad = record.clone();
            bad[..4].copy_from_slice(&u32::to_le_bytes(next));
            bad[60..64].copy_from_slice(&u32::to_le_bytes(length));
            assert!(parse_names(&bad).is_err());
        }
    }
}
