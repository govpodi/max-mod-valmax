//! Explicit staging/recovery handles: bounded inventory and sibling moves.
//! No profile discovery, path-based deletion, or automatic cleanup on Drop.
use super::{Result, StageError};
#[path = "win_inventory.rs"]
mod inventory;
pub(super) use inventory::Child;
use std::{
    cmp::Ordering,
    ffi::c_void,
    fs::File,
    mem,
    os::windows::{
        ffi::OsStrExt,
        io::{AsRawHandle, FromRawHandle},
    },
    path::{Path, PathBuf},
    ptr,
};

type Handle = *mut c_void;
const READ: u32 = 0x8000_0000;
const WRITE: u32 = 0x4000_0000;
const DELETE: u32 = 0x0001_0000;
const SYNCHRONIZE: u32 = 0x0010_0000;
const OPEN_REPARSE: u32 = 0x0020_0000;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(super) struct Identity {
    volume: u64,
    id: [u8; 16],
}

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
// FILE_RENAME_INFORMATION uses the SDK-compatible BOOLEAN/Flags union.
// Native class FileRenameInformation (10) preserves the relative RootDirectory;
// the Win32 FileRenameInfo wrapper rejects this combination on supported hosts.
#[repr(C)]
struct RenameInfo {
    flags: u32,
    root: Handle,
    length: u32,
    name: [u16; 1],
}
const _: () = assert!(mem::size_of::<Info>() == 52 && mem::size_of::<IdInfo>() == 24);
#[cfg(target_pointer_width = "64")]
const _: () = assert!(
    mem::offset_of!(RenameInfo, root) == 8
        && mem::offset_of!(RenameInfo, length) == 16
        && mem::offset_of!(RenameInfo, name) == 20
        && mem::size_of::<RenameInfo>() == 24
);

#[derive(Clone, Copy, Debug)]
pub(super) struct RenameError {
    pub moved: bool,
    pub error: StageError,
}

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
    fn CompareStringOrdinal(
        left: *const u16,
        left_length: i32,
        right: *const u16,
        right_length: i32,
        ignore_case: i32,
    ) -> i32;
    fn GetDiskFreeSpaceExW(
        directory: *const u16,
        available: *mut u64,
        total: *mut u64,
        free: *mut u64,
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
    fn NtSetInformationFile(
        handle: Handle,
        status: *mut IoStatus,
        info: *const c_void,
        length: u32,
        class: u32,
    ) -> i32;
    fn NtQueryVolumeInformationFile(
        handle: Handle,
        status: *mut IoStatus,
        info: *mut c_void,
        length: u32,
        class: u32,
    ) -> i32;
}

fn io_error(code: u32) -> StageError {
    match code {
        39 | 112 => StageError::InsufficientSpace,
        80 | 183 => StageError::FileExists,
        _ => StageError::IoError,
    }
}
fn valid_name(name: &str) -> bool {
    !name.contains('/') && crate::mdata_archive::valid_path(name)
}
fn ordinal_compare(left: &str, right: &str) -> Result<Ordering> {
    let left: Vec<u16> = left.encode_utf16().collect();
    let right: Vec<u16> = right.encode_utf16().collect();
    if left.len() > 32767 || right.len() > 32767 {
        return Err(StageError::UnsafePath);
    }
    match unsafe {
        CompareStringOrdinal(
            left.as_ptr(),
            left.len() as i32,
            right.as_ptr(),
            right.len() as i32,
            1,
        )
    } {
        1 => Ok(Ordering::Less),
        2 => Ok(Ordering::Equal),
        3 => Ok(Ordering::Greater),
        _ => Err(StageError::IoError),
    }
}
fn ordinal_equal(left: &str, right: &str) -> Result<bool> {
    Ok(ordinal_compare(left, right)? == Ordering::Equal)
}
/// Windows ordinal comparison, deliberately not Unicode full casefold.
pub(super) fn compare_names(left: &str, right: &str) -> Result<Ordering> {
    if !valid_name(left) || !valid_name(right) {
        return Err(StageError::UnsafePath);
    }
    ordinal_compare(left, right)
}
fn local_path(path: &Path) -> Result<String> {
    let text = path
        .to_str()
        .ok_or(StageError::UnsafePath)?
        .replace('/', "\\");
    let bytes = text.as_bytes();
    if bytes.len() < 3
        || !bytes[0].is_ascii_alphabetic()
        || bytes[1] != b':'
        || bytes[2] != b'\\'
        || text.encode_utf16().count() >= 32767
        || (text.len() > 3 && !text[3..].trim_end_matches('\\').split('\\').all(valid_name))
    {
        return Err(StageError::UnsafePath);
    }
    Ok(if text.len() > 3 {
        text.trim_end_matches('\\').to_owned()
    } else {
        text
    })
}
fn check(file: &File, path: &Path, directory: bool) -> Result<(Identity, u64)> {
    let mut info = Info::default();
    if unsafe { GetFileType(file.as_raw_handle()) } != 1
        || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
        || info.attributes & 0x400 != 0
        || (info.attributes & 0x10 != 0) != directory
        || (!directory && info.links != 1)
    {
        return Err(StageError::ProfileChanged);
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
        return Err(StageError::ProfileChanged);
    }
    let actual = String::from_utf16(&name[..count]).map_err(|_| StageError::ProfileChanged)?;
    let actual = actual
        .strip_prefix("\\\\?\\")
        .ok_or(StageError::ProfileChanged)?;
    if !ordinal_equal(actual, &local_path(path)?)? {
        return Err(StageError::ProfileChanged);
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
        return Err(StageError::ProfileChanged);
    }
    Ok((
        Identity {
            volume: id.volume,
            id: id.id,
        },
        ((info.size_high as u64) << 32) | info.size_low as u64,
    ))
}
fn local_disk(file: &File) -> Result<()> {
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
            mem::size_of::<DeviceInfo>() as u32,
            4,
        )
    } < 0
        || io.information != mem::size_of::<DeviceInfo>()
        || device.device != 7
        || device.characteristics & 0x10 != 0
    {
        return Err(StageError::UnsafePath);
    }
    Ok(())
}
fn mark_deleted(file: &File) -> Result<()> {
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
        Err(StageError::CleanupFailed)
    } else {
        Ok(())
    }
}

fn rename_buffer(parent: Handle, name: &str) -> Result<(Vec<usize>, u32)> {
    if parent.is_null() || !valid_name(name) {
        return Err(StageError::UnsafePath);
    }
    let encoded: Vec<u16> = name.encode_utf16().collect();
    // Extra struct tail/padding keeps the buffer >= sizeof(FILE_RENAME_INFORMATION).
    // The filename length excludes the zero terminator in the zeroed buffer.
    let size = mem::size_of::<RenameInfo>() + encoded.len() * 2;
    let mut buffer = vec![0usize; size.div_ceil(mem::size_of::<usize>())];
    let raw = buffer.as_mut_ptr().cast::<RenameInfo>();
    unsafe {
        (*raw).flags = 0; // Never replace an existing target.
        (*raw).root = parent;
        (*raw).length = (encoded.len() * 2) as u32;
        ptr::copy_nonoverlapping(
            encoded.as_ptr(),
            buffer
                .as_mut_ptr()
                .cast::<u8>()
                .add(mem::offset_of!(RenameInfo, name))
                .cast::<u16>(),
            encoded.len(),
        );
    }
    Ok((buffer, size as u32))
}

pub(super) struct Directory {
    file: File,
    path: PathBuf,
    identity: Identity,
    parent_identity: Option<Identity>,
    owned: bool,
}
impl Directory {
    /// Caller holds its checked ancestor guard. Opening gives no cleanup authority.
    pub(super) fn open_parent(path: &Path) -> Result<Self> {
        let text = local_path(path)?;
        let encoded: Vec<u16> = text.encode_utf16().chain(Some(0)).collect();
        let handle = unsafe {
            CreateFileW(
                encoded.as_ptr(),
                SYNCHRONIZE | 0x81,
                3,
                ptr::null(),
                3,
                0x0200_0000 | OPEN_REPARSE,
                ptr::null_mut(),
            )
        };
        if handle as isize == -1 {
            return Err(io_error(unsafe { GetLastError() }));
        }
        let file = unsafe { File::from_raw_handle(handle) };
        let (identity, _) = check(&file, path, true).map_err(|_| StageError::UnsafePath)?;
        local_disk(&file)?;
        Ok(Self {
            file,
            path: PathBuf::from(text),
            identity,
            parent_identity: None,
            owned: false,
        })
    }
    pub(super) fn path(&self) -> &Path {
        &self.path
    }
    pub(super) fn identity(&self) -> Identity {
        self.identity
    }
    pub(super) fn verify(&self) -> Result<()> {
        if check(&self.file, &self.path, true)?.0 != self.identity {
            return Err(StageError::ProfileChanged);
        }
        Ok(())
    }
    pub(super) fn inventory(&self) -> Result<Vec<Child>> {
        inventory::inventory(self)
    }
    /// Open an explicitly named sibling candidate without granting deletion
    /// ownership. An existing reparse point or wrong type is never "missing".
    pub(super) fn open_move_child(&self, name: &str) -> Result<Option<Self>> {
        if !valid_name(name) {
            return Err(StageError::UnsafePath);
        }
        self.verify()?;
        let path = self.path.join(name);
        local_path(&path)?;
        let mut encoded: Vec<u16> = name.encode_utf16().collect();
        let mut text = UnicodeString {
            length: (encoded.len() * 2) as u16,
            maximum: (encoded.len() * 2) as u16,
            buffer: encoded.as_mut_ptr(),
        };
        let mut object = ObjectAttributes {
            length: mem::size_of::<ObjectAttributes>() as u32,
            root: self.file.as_raw_handle(),
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
        let status = unsafe {
            NtCreateFile(
                &mut handle,
                READ | DELETE | SYNCHRONIZE,
                &mut object,
                &mut io,
                ptr::null(),
                0,
                0,
                1,
                OPEN_REPARSE | 0x20 | 1,
                ptr::null(),
                0,
            )
        };
        if status < 0 {
            let code = unsafe { RtlNtStatusToDosError(status) };
            #[cfg(test)]
            eprintln!(
                "mdata_stage_open_move ntstatus={:#010x} winerror={code}",
                status as u32
            );
            // Only these precise not-found statuses represent an absent child.
            if matches!(status as u32, 0xc000_0034 | 0xc000_003a) {
                self.verify()?;
                return Ok(None);
            }
            return Err(if matches!(code, 267 | 4390 | 1920 | 1921) {
                StageError::UnsafePath
            } else {
                io_error(code)
            });
        }
        if handle.is_null() || handle as isize == -1 {
            return Err(StageError::IoError);
        }
        let file = unsafe { File::from_raw_handle(handle) };
        let (identity, _) = check(&file, &path, true)?;
        local_disk(&file)?;
        if io.information != 1 || identity.volume != self.identity.volume {
            return Err(StageError::ProfileChanged);
        }
        self.verify()?;
        Ok(Some(Self {
            file,
            path,
            identity,
            parent_identity: Some(self.identity),
            owned: false,
        }))
    }
    /// Move this exact directory handle to a new name under its original
    /// pinned parent. Descendant handles must be released by the caller first.
    /// A post-move verification failure preserves both the new path and moved.
    pub(super) fn rename_sibling(
        &mut self,
        parent: &Directory,
        name: &str,
    ) -> std::result::Result<(), RenameError> {
        let before = |error| RenameError {
            moved: false,
            error,
        };
        if !valid_name(name)
            || self.parent_identity != Some(parent.identity)
            || self.identity.volume != parent.identity.volume
        {
            return Err(before(StageError::UnsafePath));
        }
        parent.verify().map_err(before)?;
        self.verify().map_err(before)?;
        let original_parent = self
            .path
            .parent()
            .ok_or_else(|| before(StageError::UnsafePath))?;
        if !ordinal_equal(
            &local_path(original_parent).map_err(before)?,
            &local_path(&parent.path).map_err(before)?,
        )
        .map_err(before)?
        {
            return Err(before(StageError::UnsafePath));
        }
        let target = parent.path.join(name);
        let target_text = local_path(&target).map_err(before)?;
        if ordinal_equal(&local_path(&self.path).map_err(before)?, &target_text).map_err(before)? {
            return Err(before(StageError::FileExists));
        }
        let (buffer, size) = rename_buffer(parent.file.as_raw_handle(), name).map_err(before)?;
        let mut io = IoStatus {
            status: 0,
            information: 0,
        };
        // These directory handles use FILE_SYNCHRONOUS_IO_NONALERT. Match
        // settings_io's native relative rename without a path-based fallback.
        let status = unsafe {
            NtSetInformationFile(
                self.file.as_raw_handle(),
                &mut io,
                buffer.as_ptr().cast::<c_void>(),
                size,
                10,
            )
        };
        if status < 0 {
            let code = unsafe { RtlNtStatusToDosError(status) };
            #[cfg(test)]
            eprintln!(
                "mdata_stage_rename ntstatus={:#010x} winerror={code}",
                status as u32
            );
            return Err(before(io_error(code)));
        }
        // No fallible work or allocation may intervene before recording this.
        self.path = target;
        parent
            .verify()
            .and_then(|()| self.verify())
            .map_err(|error| RenameError { moved: true, error })
    }
    fn create(&self, name: &str, directory: bool) -> Result<(File, PathBuf, Identity)> {
        if !valid_name(name) {
            return Err(StageError::UnsafePath);
        }
        self.verify()?;
        let path = self.path.join(name);
        local_path(&path)?;
        let mut encoded: Vec<u16> = name.encode_utf16().collect();
        let mut text = UnicodeString {
            length: (encoded.len() * 2) as u16,
            maximum: (encoded.len() * 2) as u16,
            buffer: encoded.as_mut_ptr(),
        };
        let mut object = ObjectAttributes {
            length: mem::size_of::<ObjectAttributes>() as u32,
            root: self.file.as_raw_handle(),
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
        // FILE_CREATE is exclusive; type options forbid aliasing a file as a
        // directory. Share=0 pins the new object until its explicit transition.
        let status = unsafe {
            NtCreateFile(
                &mut handle,
                READ | WRITE | DELETE | SYNCHRONIZE,
                &mut object,
                &mut io,
                ptr::null(),
                if directory { 0x10 } else { 0x80 },
                0,
                2,
                OPEN_REPARSE | 0x20 | if directory { 1 } else { 0x40 },
                ptr::null(),
                0,
            )
        };
        if status < 0 {
            return Err(io_error(unsafe { RtlNtStatusToDosError(status) }));
        }
        if handle.is_null() || handle as isize == -1 {
            return Err(StageError::CleanupFailed);
        }
        let file = unsafe { File::from_raw_handle(handle) };
        // If post-create verification fails, retain the enclosing staging tree
        // for explicit failure handling. Never guess cleanup authority by path.
        let (identity, _) =
            check(&file, &path, directory).map_err(|_| StageError::CleanupFailed)?;
        if io.information != 2 || identity.volume != self.identity.volume || self.verify().is_err()
        {
            return Err(StageError::CleanupFailed);
        }
        Ok((file, path, identity))
    }
    pub(super) fn create_child(&self, name: &str) -> Result<Self> {
        let (file, path, identity) = self.create(name, true)?;
        Ok(Self {
            file,
            path,
            identity,
            parent_identity: Some(self.identity),
            owned: true,
        })
    }
    pub(super) fn create_file(&self, name: &str) -> Result<OwnedFile> {
        let (file, path, identity) = self.create(name, false)?;
        Ok(OwnedFile {
            file,
            path,
            identity,
        })
    }
    pub(super) fn available_bytes(&self) -> Result<u64> {
        self.verify()?;
        let text: Vec<u16> = self.path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut available = 0;
        if unsafe {
            GetDiskFreeSpaceExW(
                text.as_ptr(),
                &mut available,
                ptr::null_mut(),
                ptr::null_mut(),
            )
        } == 0
        {
            return Err(io_error(unsafe { GetLastError() }));
        }
        self.verify()?;
        Ok(available)
    }
    /// OS refuses non-empty directories. Caller drops child handles in reverse
    /// ledger order first; this method never enumerates or recursively removes.
    pub(super) fn delete_owned(&self) -> Result<()> {
        if !self.owned {
            return Err(StageError::CleanupFailed);
        }
        self.verify().map_err(|_| StageError::CleanupFailed)?;
        mark_deleted(&self.file)
    }
}

pub(super) struct OwnedFile {
    file: File,
    path: PathBuf,
    identity: Identity,
}
impl OwnedFile {
    #[cfg(test)]
    pub(super) fn path(&self) -> &Path {
        &self.path
    }
    pub(super) fn identity(&self) -> Identity {
        self.identity
    }
    fn verify(&self) -> Result<u64> {
        let (identity, size) = check(&self.file, &self.path, false)?;
        if identity != self.identity {
            return Err(StageError::ProfileChanged);
        }
        Ok(size)
    }
    /// Duplicates this handle, never reopens the path. One writer only; the
    /// duplicate shares its file cursor and must close before ledger cleanup.
    pub(super) fn writer_clone(&self) -> Result<File> {
        self.verify()?;
        self.file
            .try_clone()
            .map_err(|error| io_error(error.raw_os_error().unwrap_or(0) as u32))
    }
    pub(super) fn verify_size(&self, expected: u64) -> Result<()> {
        if self.verify()? != expected {
            return Err(StageError::ProfileChanged);
        }
        Ok(())
    }
    pub(super) fn replace_contents(&self, bytes: &[u8]) -> Result<()> {
        use std::io::{Seek, SeekFrom, Write};
        let mut file = self.writer_clone()?;
        file.seek(SeekFrom::Start(0)).map_err(StageError::from)?;
        file.write_all(bytes).map_err(StageError::from)?;
        file.set_len(bytes.len() as u64).map_err(StageError::from)?;
        file.sync_all().map_err(StageError::from)?;
        self.verify_size(bytes.len() as u64)
    }
    pub(super) fn flush(&self) -> Result<()> {
        self.verify()?;
        self.file
            .sync_all()
            .map_err(|error| io_error(error.raw_os_error().unwrap_or(0) as u32))
    }
    pub(super) fn delete_owned(&self) -> Result<()> {
        self.verify().map_err(|_| StageError::CleanupFailed)?;
        mark_deleted(&self.file)
    }
}

#[cfg(test)]
mod move_tests {
    use super::*;

    #[test]
    fn rename_buffer_matches_sdk_layout_and_utf16_byte_length() {
        let parent = 0x1234usize as Handle; // Opaque test value, never dereferenced.
        let name = "new-😀";
        let encoded: Vec<u16> = name.encode_utf16().collect();
        let (buffer, size) = rename_buffer(parent, name).unwrap();
        assert!(size as usize >= mem::size_of::<RenameInfo>() + encoded.len() * 2);
        assert_eq!(
            (buffer.as_ptr() as usize) % mem::align_of::<RenameInfo>(),
            0
        );
        unsafe {
            let info = &*buffer.as_ptr().cast::<RenameInfo>();
            assert_eq!(info.flags, 0);
            assert_eq!(info.root, parent);
            assert_eq!(info.length, (encoded.len() * 2) as u32);
            let text = std::slice::from_raw_parts(
                buffer
                    .as_ptr()
                    .cast::<u8>()
                    .add(mem::offset_of!(RenameInfo, name))
                    .cast::<u16>(),
                encoded.len() + 1,
            );
            assert_eq!(&text[..encoded.len()], encoded);
            assert_eq!(text[encoded.len()], 0);
        }
    }

    #[test]
    fn rename_buffer_rejects_non_component_targets_before_native_call() {
        let parent = 0x1234usize as Handle;
        for name in [
            "",
            ".",
            "..",
            "../target",
            "a/b",
            "a\\b",
            "a:b",
            "nul",
            "bad.",
            "bad ",
            "a\0b",
        ] {
            assert!(matches!(
                rename_buffer(parent, name),
                Err(StageError::UnsafePath)
            ));
        }
        assert!(matches!(
            rename_buffer(ptr::null_mut(), "target"),
            Err(StageError::UnsafePath)
        ));
        assert!(matches!(
            rename_buffer(parent, &"a".repeat(256)),
            Err(StageError::UnsafePath)
        ));
    }
}
