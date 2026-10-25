//! Private Windows operations for public-media staging. No path-based deletes.
use super::*;
use std::{
    collections::BTreeSet,
    ffi::{OsStr, c_void},
    mem,
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle},
    },
    ptr,
};
type Handle = *mut c_void;
const READ: u32 = 0x80000000;
const WRITE: u32 = 0x40000000;
const DELETE: u32 = 0x10000;
const OPEN_REPARSE: u32 = 0x200000;
#[repr(C)]
#[derive(Default)]
struct Info {
    attributes: u32,
    creation: [u32; 2],
    access: [u32; 2],
    write: [u32; 2],
    volume: u32,
    size_high: u32,
    size_low: u32,
    links: u32,
    index_high: u32,
    index_low: u32,
}
#[repr(C)]
#[derive(Default)]
struct IdInfo {
    volume: u64,
    id: [u8; 16],
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
#[repr(C)]
struct IoStatus {
    status: usize,
    information: usize,
}
#[repr(C)]
#[derive(Default)]
struct DeviceInfo {
    device: u32,
    characteristics: u32,
}
const _: () = assert!(mem::size_of::<Info>() == 52 && mem::size_of::<IdInfo>() == 24);
#[allow(clashing_extern_declarations)]
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
        info: *mut c_void,
        length: u32,
    ) -> i32;
    fn SetFileInformationByHandle(
        handle: Handle,
        class: u32,
        info: *const c_void,
        length: u32,
    ) -> i32;
    fn GetFinalPathNameByHandleW(handle: Handle, buffer: *mut u16, length: u32, flags: u32) -> u32;
    fn CompareStringOrdinal(
        a: *const u16,
        alen: i32,
        b: *const u16,
        blen: i32,
        ignore_case: i32,
    ) -> i32;
}
#[allow(clashing_extern_declarations)]
#[link(name = "ntdll")]
unsafe extern "system" {
    fn NtCreateFile(
        handle: *mut Handle,
        access: u32,
        attributes: *mut ObjectAttributes,
        status: *mut IoStatus,
        allocation: *const i64,
        attrs: u32,
        share: u32,
        disposition: u32,
        options: u32,
        ea: *const c_void,
        ea_length: u32,
    ) -> i32;
    fn RtlNtStatusToDosError(status: i32) -> u32;
    fn NtQueryVolumeInformationFile(
        handle: Handle,
        status: *mut IoStatus,
        info: *mut c_void,
        length: u32,
        class: u32,
    ) -> i32;
}
fn code(value: u32) -> Error {
    match value {
        32 | 33 => Error::Busy,
        112 | 39 => Error::NoSpace,
        267 | 4390 | 1920 | 1921 => Error::UnsafePath,
        _ => Error::Unavailable,
    }
}
fn last() -> Error {
    code(unsafe { GetLastError() })
}
pub(super) fn io_error(error: std::io::Error) -> Error {
    code(error.raw_os_error().unwrap_or(0) as u32)
}
fn open(
    parent: &File,
    name: &str,
    access: u32,
    share: u32,
    disposition: u32,
    directory: bool,
) -> Result<File> {
    if !component(name) {
        return Err(Error::UnsafePath);
    }
    let mut encoded: Vec<u16> = name.encode_utf16().collect();
    if encoded.len() > 32766 {
        return Err(Error::UnsafePath);
    }
    let mut text = UnicodeString {
        length: (encoded.len() * 2) as u16,
        maximum: (encoded.len() * 2) as u16,
        buffer: encoded.as_mut_ptr(),
    };
    let mut attrs = ObjectAttributes {
        length: mem::size_of::<ObjectAttributes>() as u32,
        root: parent.as_raw_handle(),
        name: &mut text,
        attributes: 0x40,
        security: ptr::null_mut(),
        quality: ptr::null_mut(),
    };
    let mut status = IoStatus {
        status: 0,
        information: 0,
    };
    let mut handle = ptr::null_mut();
    let result = unsafe {
        NtCreateFile(
            &mut handle,
            access | 0x100000,
            &mut attrs,
            &mut status,
            ptr::null(),
            0x80,
            share,
            disposition,
            OPEN_REPARSE | 0x20 | if directory { 1 } else { 0x40 },
            ptr::null(),
            0,
        )
    };
    if result < 0 {
        return Err(code(unsafe { RtlNtStatusToDosError(result) }));
    }
    Ok(unsafe { File::from_raw_handle(handle) })
}
fn info(file: &File, directory: bool) -> Result<Info> {
    let mut value = Info::default();
    if unsafe { GetFileType(file.as_raw_handle()) } != 1
        || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut value) } == 0
        || value.attributes & 0x400 != 0
        || (value.attributes & 0x10 != 0) != directory
        || (!directory && value.links != 1)
    {
        return Err(Error::UnsafePath);
    }
    Ok(value)
}
pub(super) fn identity(file: &File) -> Result<Identity> {
    let meta = info(file, false)?;
    let mut id = IdInfo::default();
    if unsafe {
        GetFileInformationByHandleEx(
            file.as_raw_handle(),
            18,
            &mut id as *mut _ as *mut c_void,
            mem::size_of::<IdInfo>() as u32,
        )
    } == 0
    {
        return Err(Error::UnsafePath);
    }
    Ok(Identity {
        volume: id.volume,
        id: id.id,
        size: (meta.size_high as u64) << 32 | meta.size_low as u64,
        created: (meta.creation[1] as u64) << 32 | meta.creation[0] as u64,
        modified: (meta.write[1] as u64) << 32 | meta.write[0] as u64,
    })
}
fn path_equal(file: &File, expected: &Path) -> Result<()> {
    let mut out = vec![0u16; 32768];
    let n = unsafe {
        GetFinalPathNameByHandleW(file.as_raw_handle(), out.as_mut_ptr(), out.len() as u32, 0)
    } as usize;
    let expected: Vec<u16> = format!("\\\\?\\{}", expected.display())
        .encode_utf16()
        .collect();
    if n == 0
        || n >= out.len()
        || unsafe {
            CompareStringOrdinal(
                out.as_ptr(),
                n as i32,
                expected.as_ptr(),
                expected.len() as i32,
                1,
            )
        } != 2
    {
        return Err(Error::UnsafePath);
    }
    Ok(())
}
pub(super) fn pin_root(path: &Path) -> Result<Vec<File>> {
    let mut held = Vec::new();
    for p in path.ancestors().collect::<Vec<_>>().into_iter().rev() {
        let file = if let Some(parent) = held.last() {
            open(
                parent,
                p.file_name()
                    .and_then(OsStr::to_str)
                    .ok_or(Error::UnsafePath)?,
                0x81,
                3,
                1,
                true,
            )?
        } else {
            let encoded: Vec<u16> = p.as_os_str().encode_wide().chain(Some(0)).collect();
            let raw = unsafe {
                CreateFileW(
                    encoded.as_ptr(),
                    0x81,
                    3,
                    ptr::null(),
                    3,
                    0x2000000 | OPEN_REPARSE,
                    ptr::null_mut(),
                )
            };
            if raw as isize == -1 {
                return Err(last());
            }
            let file = unsafe { File::from_raw_handle(raw) };
            let mut io = IoStatus {
                status: 0,
                information: 0,
            };
            let mut device = DeviceInfo::default();
            let status = unsafe {
                NtQueryVolumeInformationFile(
                    file.as_raw_handle(),
                    &mut io,
                    &mut device as *mut _ as *mut c_void,
                    mem::size_of::<DeviceInfo>() as u32,
                    4,
                )
            };
            if status < 0
                || io.information != 8
                || device.device != 7
                || device.characteristics & 0x10 != 0
            {
                return Err(Error::UnsafePath);
            }
            file
        };
        info(&file, true)?;
        path_equal(&file, p)?;
        held.push(file);
    }
    Ok(held)
}
pub(super) fn lock(parent: &File) -> Result<File> {
    let file = open(parent, ".valmedia.lock", READ | WRITE, 0, 3, false)?;
    if identity(&file)?.size != 0 {
        return Err(Error::UnsafePath);
    }
    Ok(file)
}
pub(super) fn create(parent: &File, name: &str) -> Result<File> {
    open(parent, name, READ | WRITE | DELETE, 0, 2, false)
}
pub(super) fn read(parent: &File, name: &str) -> Result<File> {
    let file = open(parent, name, READ, 1, 1, false)?;
    identity(&file)?;
    Ok(file)
}
pub(super) fn delete(file: &File) -> Result<()> {
    let yes = 1u8;
    if unsafe {
        SetFileInformationByHandle(
            file.as_raw_handle(),
            4,
            &yes as *const _ as *const c_void,
            1,
        )
    } == 0
    {
        Err(last())
    } else {
        Ok(())
    }
}
pub(super) fn remove_exact(parent: &File, name: &str, expected: Identity) -> Result<()> {
    let file = open(parent, name, READ | DELETE, 0, 1, false)?;
    if identity(&file)? != expected {
        return Err(Error::UnsafePath);
    }
    delete(&file)
}
pub(super) fn remove_object(parent: &File, name: &str, expected: Identity) -> Result<()> {
    let file = open(parent, name, READ | DELETE, 0, 1, false)?;
    let actual = identity(&file)?;
    if !actual.same_object(expected) || actual.size != expected.size {
        return Err(Error::UnsafePath);
    }
    delete(&file)
}

// One dedicated enumeration handle/cursor per scan. The entry FileId is only a
// hint and is not trusted for deletion; callers reopen relative to the root.
pub(super) fn names(root: &File) -> Result<Vec<String>> {
    // Opening "." would violate our component boundary. NtCreateFile with an
    // empty name reopens the exact directory object, without a path lookup.
    let mut text = UnicodeString {
        length: 0,
        maximum: 0,
        buffer: ptr::null_mut(),
    };
    let mut attrs = ObjectAttributes {
        length: mem::size_of::<ObjectAttributes>() as u32,
        root: root.as_raw_handle(),
        name: &mut text,
        attributes: 0x40,
        security: ptr::null_mut(),
        quality: ptr::null_mut(),
    };
    let mut status = IoStatus {
        status: 0,
        information: 0,
    };
    let mut raw = ptr::null_mut();
    let result = unsafe {
        NtCreateFile(
            &mut raw,
            0x100081,
            &mut attrs,
            &mut status,
            ptr::null(),
            0x80,
            3,
            1,
            OPEN_REPARSE | 0x21,
            ptr::null(),
            0,
        )
    };
    if result < 0 {
        return Err(code(unsafe { RtlNtStatusToDosError(result) }));
    }
    let directory = unsafe { File::from_raw_handle(raw) };
    info(&directory, true)?;
    let mut buffer = vec![0u64; 8192]; // 64 KiB, explicitly 8-byte aligned.
    let mut names = BTreeSet::new();
    let mut restart = true;
    loop {
        buffer.fill(0);
        let ok = unsafe {
            GetFileInformationByHandleEx(
                directory.as_raw_handle(),
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
            return Err(last());
        }
        let bytes =
            unsafe { std::slice::from_raw_parts(buffer.as_ptr() as *const u8, buffer.len() * 8) };
        for name in parse_names(bytes)? {
            if name == "." || name == ".." {
                continue;
            }
            if !names.insert(name) || names.len() > MAX_ENTRIES {
                return Err(Error::Unavailable);
            }
        }
    }
    Ok(names.into_iter().collect())
}
fn parse_names(bytes: &[u8]) -> Result<Vec<String>> {
    let mut out = Vec::new();
    let mut offset = 0usize;
    loop {
        if offset.checked_add(104).is_none_or(|n| n > bytes.len()) {
            return Err(Error::Unavailable);
        }
        let next = u32::from_le_bytes(bytes[offset..offset + 4].try_into().unwrap()) as usize;
        let len = u32::from_le_bytes(bytes[offset + 60..offset + 64].try_into().unwrap()) as usize;
        let end = if next == 0 {
            bytes.len()
        } else {
            if next < 104 || next % 8 != 0 {
                return Err(Error::Unavailable);
            }
            offset
                .checked_add(next)
                .filter(|n| *n <= bytes.len())
                .ok_or(Error::Unavailable)?
        };
        if len == 0 || len % 2 != 0 || len > end - offset - 104 {
            return Err(Error::Unavailable);
        }
        let encoded: Vec<u16> = bytes[offset + 104..offset + 104 + len]
            .chunks_exact(2)
            .map(|p| u16::from_le_bytes([p[0], p[1]]))
            .collect();
        let name = String::from_utf16(&encoded).map_err(|_| Error::UnsafePath)?;
        if name != "." && name != ".." && !component(&name) {
            return Err(Error::UnsafePath);
        }
        out.push(name);
        if out.len() > MAX_ENTRIES {
            return Err(Error::Unavailable);
        }
        if next == 0 {
            break;
        }
        offset = end;
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn entry(name: &str, next: u32) -> Vec<u8> {
        let encoded: Vec<u16> = name.encode_utf16().collect();
        let mut b = vec![0u8; (104 + encoded.len() * 2 + 7) / 8 * 8];
        b[..4].copy_from_slice(&next.to_le_bytes());
        b[60..64].copy_from_slice(&(encoded.len() as u32 * 2).to_le_bytes());
        for (i, c) in encoded.iter().enumerate() {
            b[104 + i * 2..106 + i * 2].copy_from_slice(&c.to_le_bytes());
        }
        b
    }
    #[test]
    fn media_staging_directory_parser_rejects_malformed_bounds() {
        let good = entry("name", 0);
        assert_eq!(parse_names(&good).unwrap(), vec!["name"]);
        for next in [1u32, 103, 105, 1000, u32::MAX] {
            let mut b = good.clone();
            b[..4].copy_from_slice(&next.to_le_bytes());
            assert!(parse_names(&b).is_err());
        }
        for len in [0u32, 1, 1000, u32::MAX] {
            let mut b = good.clone();
            b[60..64].copy_from_slice(&len.to_le_bytes());
            assert!(parse_names(&b).is_err());
        }
        let mut malformed = entry("a", 0);
        malformed[104..106].copy_from_slice(&0xd800u16.to_le_bytes());
        assert!(parse_names(&malformed).is_err());
        assert!(parse_names(&entry("bad:name", 0)).is_err());
        for n in 0..104 {
            assert!(parse_names(&vec![0u8; n]).is_err());
        }
        let mut chained = entry("first", 120);
        chained.resize(120, 0);
        chained.extend(entry("second", 0));
        assert_eq!(parse_names(&chained).unwrap(), vec!["first", "second"]);
    }
}
