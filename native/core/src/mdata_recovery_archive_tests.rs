use super::*;
use std::{
    ffi::c_void,
    fs,
    os::windows::{fs::OpenOptionsExt, io::AsRawHandle, process::CommandExt},
    process::Command,
    ptr,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-recovery-archive";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("mdata-recovery-archive"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut bytes = [0u8; 16];
        getrandom::getrandom(&mut bytes).unwrap();
        let name: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let path = fixture_base().join(name);
        fs::create_dir_all(path.join(recovery::BACKUP_DIRECTORY)).unwrap();
        Self(path)
    }
    fn root(&self) -> PathBuf {
        self.0.join("MXMOD")
    }
    fn source(&self) -> PathBuf {
        self.0.join(recovery::BACKUP_DIRECTORY).join("own.mdata")
    }
    fn marker_path(&self) -> PathBuf {
        self.0.join(recovery::FILENAME)
    }
    fn marker(&self) -> RecoveryMarker {
        recovery::write(&self.root(), &self.source()).unwrap()
    }
    fn populate(&self, bytes: &[u8]) -> RecoveryMarker {
        fs::write(self.source(), bytes).unwrap();
        self.marker()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(fixture_base()).unwrap();
        let own = fs::canonicalize(&self.0).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
fn open_error(root: &Path, marker: &RecoveryMarker) -> RecoveryArchiveError {
    match RecoveryArchive::open(root, marker) {
        Err(error) => error,
        Ok(_) => panic!("unexpected open"),
    }
}

fn move_owned_directory(fixture: &Fixture, source: &Path, target: &Path) -> io::Result<()> {
    let approved = fs::canonicalize(fixture_base())?;
    let fixture_base = fs::canonicalize(&fixture.0)?;
    let source = fs::canonicalize(source)?;
    let target_parent = fs::canonicalize(target.parent().unwrap())?;
    assert!(fixture_base.starts_with(&approved) && fixture_base != approved);
    assert!(source.starts_with(&fixture_base) && source != fixture_base);
    assert_eq!(target_parent, fixture_base);
    assert!(
        matches!(fs::symlink_metadata(target), Err(error) if error.kind() == io::ErrorKind::NotFound)
    );
    let target = target_parent.join(target.file_name().unwrap());
    fs::rename(source, target)
}

#[test]
fn exact_marker_stream_eof_and_retained_source_handles() {
    let f = Fixture::new();
    let bytes = b"synthetic encrypted-source stand-in, not a parsed archive";
    let marker = f.populate(bytes);
    // An interrupted restore can leave MXMOD absent. This reader creates none.
    assert!(!f.root().exists());
    let mut reader = RecoveryArchive::open(&f.root(), &marker).unwrap();
    assert_eq!(
        reader.verify_complete(),
        Err(RecoveryArchiveError::InvalidArchive)
    );
    assert_eq!(reader.read(&mut []).unwrap(), 0);
    assert_eq!(reader.consumed, 0);
    assert!(fs::OpenOptions::new().write(true).open(f.source()).is_err());
    assert!(fs::rename(f.source(), f.0.join("replacement.mdata")).is_err());
    assert!(
        move_owned_directory(&f, f.source().parent().unwrap(), &f.0.join("moved-backups")).is_err()
    );
    let mut got = [0u8; 7];
    let mut offset = 0;
    loop {
        let n = reader.read(&mut got).unwrap();
        if n == 0 {
            break;
        }
        assert_eq!(&got[..n], &bytes[offset..offset + n]);
        offset += n;
    }
    assert_eq!(offset, bytes.len());
    reader.verify_complete().unwrap();
    reader.verify_complete().unwrap();
    reader.revalidate().unwrap();
    drop(reader);
    assert_eq!(fs::read(f.source()).unwrap(), bytes);
    assert_eq!(recovery::read(&f.root()).unwrap(), Some(marker));
    assert!(!f.root().exists());
}

#[test]
fn exact_marker_required_and_missing_backup_never_falls_back() {
    let f = Fixture::new();
    let marker = f.populate(b"owned synthetic bytes");
    let mut foreign = marker.clone();
    foreign.operation_id = if marker.operation_id == "0".repeat(32) {
        "1".repeat(32)
    } else {
        "0".repeat(32)
    };
    assert_eq!(
        open_error(&f.root(), &foreign),
        RecoveryArchiveError::RecoveryRequired
    );
    foreign = marker.clone();
    foreign.schema = 2;
    assert_eq!(
        open_error(&f.root(), &foreign),
        RecoveryArchiveError::RecoveryRequired
    );
    foreign = marker.clone();
    foreign.backup_file = f.0.join("foreign.mdata").to_str().unwrap().into();
    assert_eq!(
        open_error(&f.root(), &foreign),
        RecoveryArchiveError::RecoveryRequired
    );
    let marker_bytes = fs::read(f.marker_path()).unwrap();
    fs::remove_file(f.marker_path()).unwrap();
    assert_eq!(
        open_error(&f.root(), &marker),
        RecoveryArchiveError::RecoveryRequired
    );
    fs::write(f.marker_path(), &marker_bytes).unwrap();
    fs::remove_file(f.source()).unwrap();
    fs::write(
        f.source().parent().unwrap().join("another.mdata"),
        b"no fallback",
    )
    .unwrap();
    assert_eq!(
        open_error(&f.root(), &marker),
        RecoveryArchiveError::IoError
    );
    assert!(!f.source().exists());
}

#[test]
fn changed_marker_is_rechecked_after_open_and_at_completion() {
    let f = Fixture::new();
    let marker = f.populate(b"123");
    let mut reader = RecoveryArchive::open(&f.root(), &marker).unwrap();
    let mut data = [0u8; 3];
    reader.read_exact(&mut data).unwrap();
    let mut changed = marker.clone();
    changed.operation_id = if marker.operation_id == "0".repeat(32) {
        "1".repeat(32)
    } else {
        "0".repeat(32)
    };
    fs::write(f.marker_path(), serde_json::to_vec(&changed).unwrap()).unwrap();
    assert_eq!(
        reader.revalidate(),
        Err(RecoveryArchiveError::RecoveryRequired)
    );
    assert_eq!(
        reader.verify_complete(),
        Err(RecoveryArchiveError::RecoveryRequired)
    );
    fs::write(f.marker_path(), b"{").unwrap();
    assert_eq!(
        reader.revalidate(),
        Err(RecoveryArchiveError::RecoveryRequired)
    );
    fs::remove_file(f.marker_path()).unwrap();
    assert_eq!(
        reader.verify_complete(),
        Err(RecoveryArchiveError::RecoveryRequired)
    );
}

#[test]
fn source_busy_hardlink_directory_and_explicit_root_constraints() {
    let f = Fixture::new();
    let marker = f.populate(b"fixture");
    let writer = fs::OpenOptions::new()
        .write(true)
        .share_mode(7)
        .open(f.source())
        .unwrap();
    assert_eq!(open_error(&f.root(), &marker), RecoveryArchiveError::Busy);
    drop(writer);
    let alias = f.0.join("own-hardlink.mdata");
    fs::hard_link(f.source(), &alias).unwrap();
    assert_eq!(
        open_error(&f.root(), &marker),
        RecoveryArchiveError::UnsafePath
    );
    fs::remove_file(alias).unwrap();
    fs::remove_file(f.source()).unwrap();
    fs::create_dir(f.source()).unwrap();
    assert_eq!(
        open_error(&f.root(), &marker),
        RecoveryArchiveError::UnsafePath
    );
    for bad in [
        f.0.join("OTHER"),
        f.0.join("ONEME/MXMOD"),
        PathBuf::from("MXMOD"),
        PathBuf::from("\\\\server\\share\\MXMOD"),
        f.0.join("MXMOD:stream"),
    ] {
        assert_eq!(open_error(&bad, &marker), RecoveryArchiveError::UnsafePath);
    }
}

#[test]
fn reparse_backup_parent_is_rejected_without_following_it() {
    let f = Fixture::new();
    let marker = f.populate(b"only our fixture");
    let backup_dir = f.source().parent().unwrap().to_owned();
    let own_target = f.0.join("own-target");
    move_owned_directory(&f, &backup_dir, &own_target).unwrap();
    let output = Command::new("cmd.exe")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(backup_dir.to_str().unwrap().replace('/', "\\"))
        .arg(own_target.to_str().unwrap().replace('/', "\\"))
        .creation_flags(0x0800_0000)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "own fixture junction creation failed"
    );
    let result = open_error(&f.root(), &marker);
    // Unlink this exact fixture junction before recursive fixture cleanup.
    fs::remove_dir(&backup_dir).unwrap();
    assert_eq!(result, RecoveryArchiveError::UnsafePath);
    assert_eq!(
        fs::read(own_target.join("own.mdata")).unwrap(),
        b"only our fixture"
    );
}

#[repr(C)]
struct FileTime {
    low: u32,
    high: u32,
}
#[repr(C)]
#[derive(Default)]
struct StandardInfo {
    allocation: i64,
    end: i64,
    links: u32,
    delete_pending: u8,
    directory: u8,
}
#[allow(clashing_extern_declarations)]
#[link(name = "kernel32")]
unsafe extern "system" {
    fn SetFileTime(
        file: *mut c_void,
        creation: *const FileTime,
        access: *const FileTime,
        write: *const FileTime,
    ) -> i32;
    fn DeviceIoControl(
        file: *mut c_void,
        code: u32,
        input: *const c_void,
        input_bytes: u32,
        output: *mut c_void,
        output_bytes: u32,
        returned: *mut u32,
        overlapped: *mut c_void,
    ) -> i32;
    fn GetFileInformationByHandleEx(
        file: *mut c_void,
        class: u32,
        info: *mut c_void,
        length: u32,
    ) -> i32;
}

#[test]
fn write_time_change_via_existing_attribute_handle_invalidates_reader() {
    let f = Fixture::new();
    let marker = f.populate(b"metadata-only synthetic test");
    // WRITE_ATTRIBUTES is not content-write access. Existing metadata-only
    // handles can outlive acquisition of a read lease, hence fingerprint checks.
    let attributes = fs::OpenOptions::new()
        .access_mode(0x100)
        .share_mode(7)
        .open(f.source())
        .unwrap();
    let mut reader = RecoveryArchive::open(&f.root(), &marker).unwrap();
    let value = 132_000_000_000_000_000u64;
    let write = FileTime {
        low: value as u32,
        high: (value >> 32) as u32,
    };
    assert_ne!(
        unsafe { SetFileTime(attributes.as_raw_handle(), ptr::null(), ptr::null(), &write) },
        0
    );
    assert_eq!(
        reader.revalidate(),
        Err(RecoveryArchiveError::SourceChanged)
    );
    assert!(reader.read(&mut [0u8; 1]).is_err());
    assert_eq!(
        reader.verify_complete(),
        Err(RecoveryArchiveError::SourceChanged)
    );
}

#[test]
fn full_archive_bound_is_streamed_and_sparse_oversize_is_rejected() {
    let f = Fixture::new();
    let marker = f.marker();
    assert!(MAX_ARCHIVE_BYTES > 4 * 1024 * 1024 * 1024);
    let file = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .open(f.source())
        .unwrap();
    let mut returned = 0;
    assert_ne!(
        unsafe {
            DeviceIoControl(
                file.as_raw_handle(),
                0x0009_00c4,
                ptr::null(),
                0,
                ptr::null_mut(),
                0,
                &mut returned,
                ptr::null_mut(),
            )
        },
        0
    );
    file.set_len(MAX_ARCHIVE_BYTES + 1).unwrap();
    let mut info = StandardInfo::default();
    assert_eq!(std::mem::size_of::<StandardInfo>(), 24);
    assert_ne!(
        unsafe {
            GetFileInformationByHandleEx(
                file.as_raw_handle(),
                1,
                &mut info as *mut _ as *mut c_void,
                std::mem::size_of::<StandardInfo>() as u32,
            )
        },
        0
    );
    assert!(
        info.allocation <= 4096,
        "sparse fixture must not allocate its logical size"
    );
    drop(file);
    assert_eq!(
        open_error(&f.root(), &marker),
        RecoveryArchiveError::TooLarge
    );
    fs::OpenOptions::new()
        .write(true)
        .open(f.source())
        .unwrap()
        .set_len(MAX_ARCHIVE_BYTES)
        .unwrap();
    let mut reader = RecoveryArchive::open(&f.root(), &marker).unwrap();
    let mut bytes = [1u8; 16];
    reader.read_exact(&mut bytes).unwrap();
    assert_eq!(bytes, [0u8; 16]);
    assert_eq!(reader.size, MAX_ARCHIVE_BYTES);
    assert_eq!(reader.consumed, 16);
    assert_eq!(
        reader.verify_complete(),
        Err(RecoveryArchiveError::InvalidArchive)
    );
}
