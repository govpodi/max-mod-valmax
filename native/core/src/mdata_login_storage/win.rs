//! Direct-child Windows handles; no profile enumeration or path-based deletion.
use super::*;
use std::{
    ffi::c_void,
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
    fn GetFinalPathNameByHandleW(handle: Handle, buffer: *mut u16, length: u32, flags: u32) -> u32;
    fn SetFileInformationByHandle(
        handle: Handle,
        class: u32,
        info: *const c_void,
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
#[allow(clashing_extern_declarations)]
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
    fn NtQueryVolumeInformationFile(
        handle: Handle,
        status: *mut IoStatus,
        info: *mut c_void,
        length: u32,
        class: u32,
    ) -> i32;
}
fn norm(value: &str) -> Result<Vec<u16>> {
    let input: Vec<u16> = value.replace('/', "\\").encode_utf16().collect();
    let locale = [0u16];
    let count = unsafe {
        LCMapStringEx(
            locale.as_ptr(),
            0x100,
            input.as_ptr(),
            input.len() as i32,
            ptr::null_mut(),
            0,
            ptr::null(),
            ptr::null(),
            0,
        )
    };
    if count <= 0 {
        return Err(StorageError::UnsafePath);
    }
    let mut output = vec![0u16; count as usize];
    if unsafe {
        LCMapStringEx(
            locale.as_ptr(),
            0x100,
            input.as_ptr(),
            input.len() as i32,
            output.as_mut_ptr(),
            count,
            ptr::null(),
            ptr::null(),
            0,
        )
    } != count
    {
        return Err(StorageError::UnsafePath);
    }
    Ok(output)
}
pub(super) fn verify(file: &File, path: &Path, directory: bool) -> Result<Identity> {
    let mut info = Info::default();
    if unsafe { GetFileType(file.as_raw_handle()) } != 1
        || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
        || info.attributes & 0x400 != 0
        || (info.attributes & 0x10 != 0) != directory
        || !directory && info.links != 1
    {
        return Err(StorageError::ProfileCollision);
    }
    if !directory
        && ((info.size_high as u64) << 32 | info.size_low as u64) > login::MAX_AUTH_BYTES as u64
    {
        return Err(StorageError::InvalidProfile);
    }
    let mut name = vec![0u16; 32768];
    let count = unsafe {
        GetFinalPathNameByHandleW(
            file.as_raw_handle(),
            name.as_mut_ptr(),
            name.len() as u32,
            0,
        )
    } as usize;
    if count == 0 || count >= name.len() {
        return Err(StorageError::ProfileCollision);
    }
    let actual = String::from_utf16(&name[..count]).map_err(|_| StorageError::ProfileCollision)?;
    if norm(actual.strip_prefix("\\\\?\\").unwrap_or(&actual))?
        != norm(path.to_str().ok_or(StorageError::UnsafePath)?)?
    {
        return Err(StorageError::ProfileCollision);
    }
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
        return Err(StorageError::ProfileCollision);
    }
    Ok(Identity {
        volume: id.volume,
        id: id.id,
    })
}
fn pin(file: File, path: &Path, directory: bool) -> Result<Pin> {
    let identity = verify(&file, path, directory)?;
    Ok(Pin { file, identity })
}
fn valid_name(name: &str) -> bool {
    login::auth_id(name).is_some() || name.strip_prefix("user").is_some_and(login::account_id)
}
pub(super) struct Directory(File);
impl Directory {
    pub(super) fn open(root: &Path) -> Result<Self> {
        let text: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
        // The caller already holds every ancestor including root no-delete.
        let handle = unsafe {
            CreateFileW(
                text.as_ptr(),
                0x100081,
                3,
                ptr::null(),
                3,
                0x02000000 | OPEN_REPARSE,
                ptr::null_mut(),
            )
        };
        if handle as isize == -1 {
            return Err(if matches!(unsafe { GetLastError() }, 2 | 3) {
                StorageError::InvalidProfile
            } else {
                StorageError::IoError
            });
        }
        let file = unsafe { File::from_raw_handle(handle) };
        verify(&file, root, true).map_err(|_| StorageError::UnsafePath)?;
        let mut device = DeviceInfo::default();
        let mut io = IoStatus {
            status: 0,
            information: 0,
        };
        if unsafe {
            NtQueryVolumeInformationFile(
                file.as_raw_handle(),
                &mut io,
                &mut device as *mut _ as *mut c_void,
                8,
                4,
            )
        } < 0
            || io.information != 8
            || device.device != 7
            || device.characteristics & 0x10 != 0
        {
            return Err(StorageError::UnsafePath);
        }
        Ok(Self(file))
    }
    fn open_at(&self, name: &str, access: u32, share: u32, create: bool) -> Result<Option<File>> {
        if !valid_name(name) {
            return Err(StorageError::InvalidRequest);
        }
        let mut encoded: Vec<u16> = name.encode_utf16().collect();
        let mut text = UnicodeString {
            length: (encoded.len() * 2) as u16,
            maximum: (encoded.len() * 2) as u16,
            buffer: encoded.as_mut_ptr(),
        };
        let mut object = ObjectAttributes {
            length: mem::size_of::<ObjectAttributes>() as u32,
            root: self.0.as_raw_handle(),
            name: &mut text,
            attributes: 0x40,
            security: ptr::null_mut(),
            quality: ptr::null_mut(),
        };
        let mut io = IoStatus {
            status: 0,
            information: 0,
        };
        let mut handle = ptr::null_mut();
        // No FILE_DIRECTORY_FILE/FILE_NON_DIRECTORY_FILE: inspect the opened
        // object by handle, so wrong types/reparse points are never "missing".
        let status = unsafe {
            NtCreateFile(
                &mut handle,
                access | 0x100000,
                &mut object,
                &mut io,
                ptr::null(),
                0x80,
                share,
                if create { 2 } else { 1 },
                OPEN_REPARSE | 0x20,
                ptr::null(),
                0,
            )
        };
        if status < 0 {
            let code = unsafe { RtlNtStatusToDosError(status) };
            if !create && matches!(code, 2 | 3) {
                return Ok(None);
            }
            return Err(
                if matches!(code, 32 | 33 | 80 | 183 | 267 | 4390 | 1920 | 1921 | 5) {
                    StorageError::ProfileCollision
                } else {
                    StorageError::IoError
                },
            );
        }
        Ok(Some(unsafe { File::from_raw_handle(handle) }))
    }
    pub(super) fn user(&self, root: &Path, name: &str) -> Result<Option<Pin>> {
        // LIST_DIRECTORY participates in share checks; do not enumerate it.
        self.open_at(name, 0x81, 3, false)?
            .map(|file| pin(file, &root.join(name), true))
            .transpose()
    }
    pub(super) fn auth(&self, root: &Path, name: &str) -> Result<Option<Pin>> {
        self.open_at(name, READ, 1, false)?
            .map(|file| pin(file, &root.join(name), false))
            .transpose()
    }
    pub(super) fn exists(&self, name: &str) -> Result<bool> {
        // No payload dereference: any object at a previously missing name is
        // a collision, including a directory, reparse point or inaccessible file.
        Ok(self.open_at(name, 0x80, 7, false)?.is_some())
    }
    pub(super) fn create_auth(&self, root: &Path, name: &str) -> Result<Pin> {
        if login::auth_id(name).is_none() {
            return Err(StorageError::InvalidRequest);
        }
        let file = self
            .open_at(name, READ | WRITE | DELETE, 0, true)?
            .ok_or(StorageError::IoError)?;
        match verify(&file, &root.join(name), false) {
            Ok(identity) => Ok(Pin { file, identity }),
            Err(error) => {
                // Only this newly created exclusive handle can be removed.
                // A changed link/type is not cleanup authority.
                let mut info = Info::default();
                let ordinary =
                    unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } != 0
                        && info.attributes & (0x400 | 0x10) == 0
                        && info.links == 1;
                if !ordinary || delete(&file).is_err() {
                    return Err(StorageError::RollbackFailed);
                }
                Err(error)
            }
        }
    }
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
        Err(StorageError::RollbackFailed)
    } else {
        Ok(())
    }
}
