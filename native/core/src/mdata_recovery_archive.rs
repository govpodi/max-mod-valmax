//! Read-only streaming access to the backup named by an exact recovery latch.
//! No profile discovery, archive authentication, restore, credential access or
//! marker clearing occurs here. The caller owns the transfer mutex and must
//! authenticate a Profile archive and call verify_complete before publication.
//! The explicit MXMOD directory may be absent after an interrupted restore.
use crate::{
    mdata_archive,
    mdata_recovery_state::{self as recovery, RecoveryMarker},
    settings_io::{GuardedStore, SafeStorePath, StoreError},
};
use std::{
    fmt,
    fs::File,
    io::{self, Read},
    path::{Path, PathBuf},
};

/// Conservative bound for all framing accepted by the full-profile codec:
/// every data frame may contain only one plaintext byte plus 21 overhead bytes.
/// The header and empty terminal frame are additional. This is a file-size bound,
/// never an allocation size. Memory remains bounded; reading/authenticating a
/// maximally fragmented archive can take a long time. The coordinator must
/// provide cancellation between streaming operations.
pub const MAX_ARCHIVE_BYTES: u64 = {
    let Some(payload) = mdata_archive::MAX_PLAINTEXT_BYTES.checked_mul(22) else {
        panic!("archive bound overflow")
    };
    let Some(total) = payload.checked_add(8 + 2 + mdata_archive::MAX_HEADER_BYTES as u64 + 21)
    else {
        panic!("archive bound overflow")
    };
    total
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RecoveryArchiveError {
    RecoveryRequired,
    UnsafePath,
    Busy,
    IoError,
    SourceChanged,
    TooLarge,
    InvalidArchive,
}
impl RecoveryArchiveError {
    pub fn code(self) -> &'static str {
        match self {
            Self::RecoveryRequired => "recovery_required",
            Self::UnsafePath => "unsafe_path",
            Self::Busy => "busy",
            Self::IoError => "io_error",
            Self::SourceChanged => "source_changed",
            Self::TooLarge => "too_large",
            Self::InvalidArchive => "invalid_archive",
        }
    }
}
impl fmt::Display for RecoveryArchiveError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code())
    }
}
impl std::error::Error for RecoveryArchiveError {}
pub type Result<T> = std::result::Result<T, RecoveryArchiveError>;
fn store_error(error: StoreError) -> RecoveryArchiveError {
    match error {
        StoreError::UnsafePath => RecoveryArchiveError::UnsafePath,
        StoreError::StoreBusy => RecoveryArchiveError::Busy,
        _ => RecoveryArchiveError::IoError,
    }
}
fn io_error(error: io::Error) -> RecoveryArchiveError {
    if matches!(error.raw_os_error(), Some(32 | 33)) {
        RecoveryArchiveError::Busy
    } else {
        RecoveryArchiveError::IoError
    }
}
fn root_path(root: &Path) -> Result<PathBuf> {
    let safe = SafeStorePath::new(root).map_err(store_error)?;
    if safe.path().file_name().and_then(|s| s.to_str()) != Some("MXMOD")
        || safe
            .path()
            .to_str()
            .ok_or(RecoveryArchiveError::UnsafePath)?
            .split(['/', '\\'])
            .any(|p| p.eq_ignore_ascii_case("ONEME"))
    {
        return Err(RecoveryArchiveError::UnsafePath);
    }
    Ok(safe.path().to_owned())
}
fn check_marker(root: &Path, expected: &RecoveryMarker) -> Result<()> {
    // read validates schema, operation, explicit root and backup containment.
    // Equality also validates an untrusted/hand-built expected record; only the
    // validated on-disk backup path may reach the file opener.
    if recovery::read(root)
        .map_err(|_| RecoveryArchiveError::RecoveryRequired)?
        .as_ref()
        != Some(expected)
    {
        return Err(RecoveryArchiveError::RecoveryRequired);
    }
    Ok(())
}

/// Owns a read handle with no write/delete sharing and all checked backup
/// ancestors. It exposes neither a pathname fallback nor seek/raw handle access.
/// Drop only closes handles. Metadata contains no decrypted archive content.
pub struct RecoveryArchive {
    file: File,
    _parents: GuardedStore,
    root: PathBuf,
    marker: RecoveryMarker,
    #[cfg(windows)]
    fingerprint: win::Fingerprint,
    consumed: u64,
    size: u64,
    failure: Option<RecoveryArchiveError>,
}
impl RecoveryArchive {
    pub fn open(root: &Path, expected: &RecoveryMarker) -> Result<Self> {
        let root = root_path(root)?;
        check_marker(&root, expected)?;
        let safe = SafeStorePath::new(Path::new(&expected.backup_file)).map_err(store_error)?;
        let parents = safe
            .begin(false)
            .map_err(store_error)?
            .ok_or(RecoveryArchiveError::IoError)?;
        #[cfg(windows)]
        {
            let (file, fingerprint) = win::open(safe.path())?;
            if fingerprint.size > MAX_ARCHIVE_BYTES {
                return Err(RecoveryArchiveError::TooLarge);
            }
            let result = Self {
                file,
                _parents: parents,
                root,
                marker: expected.clone(),
                size: fingerprint.size,
                fingerprint,
                consumed: 0,
                failure: None,
            };
            result.revalidate()?;
            Ok(result)
        }
        #[cfg(not(windows))]
        {
            let _ = (root, expected, parents);
            Err(RecoveryArchiveError::IoError)
        }
    }

    fn check_file(&self) -> Result<()> {
        if let Some(error) = self.failure {
            return Err(error);
        }
        #[cfg(windows)]
        {
            if win::fingerprint(&self.file).map_err(|_| RecoveryArchiveError::SourceChanged)?
                != self.fingerprint
            {
                return Err(RecoveryArchiveError::SourceChanged);
            }
            Ok(())
        }
        #[cfg(not(windows))]
        {
            Err(RecoveryArchiveError::IoError)
        }
    }

    /// Checks the exact marker again, with file checks on both sides. Marker
    /// equality is an observation; the caller's transfer mutex remains required.
    pub fn revalidate(&self) -> Result<()> {
        self.check_file()?;
        check_marker(&self.root, &self.marker)?;
        self.check_file()
    }

    /// Requires that the consumer read every byte, then independently observes
    /// EOF and the unchanged marker/file. This does NOT authenticate a container.
    /// An early call returns invalid_archive and does not consume unread bytes.
    pub fn verify_complete(&mut self) -> Result<()> {
        self.revalidate()?;
        if self.consumed != self.size {
            return Err(RecoveryArchiveError::InvalidArchive);
        }
        let mut tail = [0u8; 1];
        if self.read_checked(&mut tail)? != 0 {
            return Err(RecoveryArchiveError::SourceChanged);
        }
        self.revalidate()
    }

    fn read_checked(&mut self, output: &mut [u8]) -> Result<usize> {
        self.check_file()?;
        if output.is_empty() {
            return Ok(0);
        }
        // Bound each OS operation independently of the caller's buffer length.
        let count = output.len().min(mdata_archive::CHUNK_BYTES);
        let read = self.file.read(&mut output[..count]).map_err(io_error)?;
        self.check_file()?;
        let next = self
            .consumed
            .checked_add(read as u64)
            .ok_or(RecoveryArchiveError::TooLarge)?;
        if next > self.size || (read == 0 && next != self.size) {
            return Err(RecoveryArchiveError::SourceChanged);
        }
        self.consumed = next;
        Ok(read)
    }
}
impl Read for RecoveryArchive {
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        match self.read_checked(output) {
            Ok(count) => Ok(count),
            Err(error) => {
                self.failure = Some(error);
                Err(io::Error::other(error))
            }
        }
    }
}

#[cfg(windows)]
mod win {
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
    #[derive(Default)]
    struct BasicInfo {
        creation: i64,
        access: i64,
        write: i64,
        change: i64,
        attributes: u32,
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
    const _: () = assert!(
        mem::size_of::<Info>() == 52
            && mem::size_of::<IdInfo>() == 24
            && mem::size_of::<BasicInfo>() == 40
    );
    #[derive(Clone, Copy, PartialEq, Eq)]
    pub(super) struct Fingerprint {
        volume: u64,
        id: [u8; 16],
        pub(super) size: u64,
        creation: i64,
        write: i64,
        change: i64,
        attributes: u32,
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
        fn GetFileType(handle: Handle) -> u32;
        fn GetFileInformationByHandle(handle: Handle, info: *mut Info) -> i32;
        fn GetFileInformationByHandleEx(
            handle: Handle,
            class: u32,
            info: *mut c_void,
            length: u32,
        ) -> i32;
        fn GetFinalPathNameByHandleW(
            handle: Handle,
            buffer: *mut u16,
            length: u32,
            flags: u32,
        ) -> u32;
        fn CompareStringOrdinal(
            left: *const u16,
            left_length: i32,
            right: *const u16,
            right_length: i32,
            ignore_case: i32,
        ) -> i32;
    }
    #[allow(clashing_extern_declarations)]
    #[link(name = "ntdll")]
    unsafe extern "system" {
        fn NtQueryVolumeInformationFile(
            handle: Handle,
            status: *mut IoStatus,
            info: *mut c_void,
            length: u32,
            class: u32,
        ) -> i32;
    }
    pub(super) fn fingerprint(file: &File) -> Result<Fingerprint> {
        let mut info = Info::default();
        if unsafe { GetFileType(file.as_raw_handle()) } != 1
            || unsafe { GetFileInformationByHandle(file.as_raw_handle(), &mut info) } == 0
            || info.attributes & (0x10 | 0x400) != 0
            || info.links != 1
        {
            return Err(RecoveryArchiveError::UnsafePath);
        }
        let mut id = IdInfo::default();
        let mut basic = BasicInfo::default();
        if unsafe {
            GetFileInformationByHandleEx(
                file.as_raw_handle(),
                18,
                &mut id as *mut _ as *mut c_void,
                mem::size_of::<IdInfo>() as u32,
            )
        } == 0
            || unsafe {
                GetFileInformationByHandleEx(
                    file.as_raw_handle(),
                    0,
                    &mut basic as *mut _ as *mut c_void,
                    mem::size_of::<BasicInfo>() as u32,
                )
            } == 0
        {
            return Err(RecoveryArchiveError::IoError);
        }
        Ok(Fingerprint {
            volume: id.volume,
            id: id.id,
            size: ((info.size_high as u64) << 32) | info.size_low as u64,
            creation: basic.creation,
            write: basic.write,
            change: basic.change,
            attributes: basic.attributes,
        })
    }
    pub(super) fn open(path: &Path) -> Result<(File, Fingerprint)> {
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let handle = unsafe {
            CreateFileW(
                wide.as_ptr(),
                0x8000_0000,
                1,
                ptr::null(),
                3,
                0x0020_0000 | 0x0200_0000 | 0x0800_0000,
                ptr::null_mut(),
            )
        };
        if handle as isize == -1 {
            return Err(io_error(io::Error::last_os_error()));
        }
        let file = unsafe { File::from_raw_handle(handle) };
        let initial = fingerprint(&file)?;
        let mut device = DeviceInfo::default();
        let mut status = IoStatus {
            status: 0,
            information: 0,
        };
        if unsafe {
            NtQueryVolumeInformationFile(
                file.as_raw_handle(),
                &mut status,
                &mut device as *mut _ as *mut c_void,
                mem::size_of::<DeviceInfo>() as u32,
                4,
            )
        } < 0
            || status.information != mem::size_of::<DeviceInfo>()
            || device.device != 7
            || device.characteristics & 0x10 != 0
        {
            return Err(RecoveryArchiveError::UnsafePath);
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
            return Err(RecoveryArchiveError::UnsafePath);
        }
        let actual =
            String::from_utf16(&name[..count]).map_err(|_| RecoveryArchiveError::UnsafePath)?;
        let actual = actual
            .strip_prefix("\\\\?\\")
            .ok_or(RecoveryArchiveError::UnsafePath)?;
        let actual: Vec<u16> = actual.encode_utf16().collect();
        if unsafe {
            CompareStringOrdinal(
                actual.as_ptr(),
                actual.len() as i32,
                wide.as_ptr(),
                (wide.len() - 1) as i32,
                1,
            )
        } != 2
        {
            return Err(RecoveryArchiveError::UnsafePath);
        }
        if fingerprint(&file)? != initial {
            return Err(RecoveryArchiveError::SourceChanged);
        }
        Ok((file, initial))
    }
}

#[cfg(all(test, windows))]
#[path = "mdata_recovery_archive_tests.rs"]
mod tests;
