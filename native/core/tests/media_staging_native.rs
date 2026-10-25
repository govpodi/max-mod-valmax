#![cfg(windows)]
use std::{
    fs::{self, File, FileTimes, OpenOptions},
    path::{Path, PathBuf},
    time::{Duration, SystemTime},
};
use valmax_core::media_staging::{
    Cancellation, Error, MAX_CACHE_BYTES, MAX_FILE_BYTES, MediaSpec, StagingRoot, TTL_SECONDS,
};
const PNG: &[u8] = b"\x89PNG\r\n\x1a\nSYNTHETIC-PUBLIC-MEDIA";
fn now() -> u64 {
    SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs()
}
fn spec(bytes: &[u8]) -> MediaSpec<'static> {
    MediaSpec {
        mime: "image/png",
        expected_bytes: bytes.len() as u64,
    }
}
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut id = [0u8; 16];
        getrandom::getrandom(&mut id).unwrap();
        let path = Path::new("C:/ValMAXBuild/fixtures/media-staging")
            .join(id.iter().map(|b| format!("{b:02x}")).collect::<String>());
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn store(&self) -> StagingRoot {
        StagingRoot::open(&self.0).unwrap()
    }
    fn name(&self, n: u64, ext: &str) -> PathBuf {
        self.0.join(format!("valmedia-{n:032x}.{ext}"))
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize("C:/ValMAXBuild/fixtures/media-staging").unwrap();
        let path = fs::canonicalize(&self.0).unwrap();
        assert!(path.starts_with(&base) && path != base);
        fs::remove_dir_all(path).unwrap();
    }
}
fn err<T>(result: Result<T, Error>, expected: Error) {
    assert!(matches!(result,Err(e) if e == expected));
}
fn old(path: &Path) {
    OpenOptions::new()
        .write(true)
        .open(path)
        .unwrap()
        .set_times(FileTimes::new().set_modified(SystemTime::UNIX_EPOCH))
        .unwrap();
}

#[test]
fn media_staging_roundtrip_read_lease_then_drop() {
    let f = Fixture::new();
    let mut store = f.store();
    let selected = store
        .stage(PNG, spec(PNG), now(), &Cancellation::default())
        .unwrap();
    let path = selected.path().to_owned();
    assert_eq!(fs::read(&path).unwrap(), PNG);
    assert_eq!(selected.byte_length(), PNG.len() as u64);
    assert!(selected.file_url().starts_with("file:///C:/"));
    assert!(OpenOptions::new().write(true).open(&path).is_err());
    assert!(fs::remove_file(&path).is_err());
    drop(store); // the selected result keeps pinned ancestors alive
    assert!(fs::rename(&f.0, f.0.with_extension("other")).is_err());
    drop(selected);
    assert!(!path.exists());
}
#[test]
fn media_staging_retain_and_ttl_boundaries() {
    let f = Fixture::new();
    let mut store = f.store();
    let selected = store
        .stage(PNG, spec(PNG), now(), &Cancellation::default())
        .unwrap();
    let path = selected.path().to_owned();
    selected.retain_for_draft();
    assert!(path.exists());
    old(&path);
    assert_eq!(
        store
            .cleanup_expired(TTL_SECONDS, &Cancellation::default())
            .unwrap()
            .removed,
        0
    );
    assert_eq!(
        store
            .cleanup_expired(TTL_SECONDS + 1, &Cancellation::default())
            .unwrap()
            .removed,
        1
    );
    assert!(!path.exists());
}
#[test]
fn media_staging_open_max_reader_survives_expiry_and_stays_charged() {
    let f = Fixture::new();
    let path = f.name(1, "png");
    fs::write(&path, PNG).unwrap();
    old(&path);
    let reader = File::open(&path).unwrap();
    let mut store = f.store();
    let usage = store
        .cleanup_expired(now(), &Cancellation::default())
        .unwrap();
    assert_eq!(usage.removed, 0);
    assert_eq!(usage.bytes, PNG.len() as u64);
    assert_eq!(usage.files, 1);
    drop(reader);
    assert_eq!(
        store
            .cleanup_expired(now(), &Cancellation::default())
            .unwrap()
            .removed,
        1
    );
}
#[test]
fn media_staging_busy_cancel_preserves_orphan_for_ttl() {
    let f = Fixture::new();
    let mut store = f.store();
    let selected = store
        .stage(PNG, spec(PNG), now(), &Cancellation::default())
        .unwrap();
    let path = selected.path().to_owned();
    let reader = File::open(&path).unwrap();
    err(selected.cancel(), Error::CleanupFailed);
    assert_eq!(
        store
            .cleanup_expired(now(), &Cancellation::default())
            .unwrap()
            .bytes,
        PNG.len() as u64
    );
    drop(reader);
    old(&path);
    assert_eq!(
        store
            .cleanup_expired(now(), &Cancellation::default())
            .unwrap()
            .removed,
        1
    );
}
#[test]
fn media_staging_unknown_files_and_uppercase_names_are_not_deleted() {
    let f = Fixture::new();
    let unknown = f.0.join("keep-me.txt");
    fs::write(&unknown, b"UNKNOWN").unwrap();
    old(&unknown);
    let upper = f.0.join("valmedia-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF.png");
    fs::write(&upper, b"UNKNOWN").unwrap();
    old(&upper);
    let part = f.name(2, "part");
    fs::write(&part, b"OWNED-ORPHAN").unwrap();
    old(&part);
    let mut store = f.store();
    let usage = store
        .cleanup_expired(now(), &Cancellation::default())
        .unwrap();
    assert_eq!(usage.removed, 1);
    assert_eq!(usage.bytes, 0);
    assert_eq!(fs::read(unknown).unwrap(), b"UNKNOWN");
    assert!(upper.exists());
}
#[test]
fn media_staging_quota_counts_pending_lease_and_orphan() {
    let f = Fixture::new();
    let orphan = f.name(3, "part");
    let file = File::create(&orphan).unwrap();
    file.set_len(MAX_CACHE_BYTES - PNG.len() as u64).unwrap();
    drop(file);
    let mut store = f.store();
    let selected = store
        .stage(PNG, spec(PNG), now(), &Cancellation::default())
        .unwrap();
    assert_eq!(
        store
            .cleanup_expired(now(), &Cancellation::default())
            .unwrap()
            .bytes,
        MAX_CACHE_BYTES
    );
    err(
        store.stage(PNG, spec(PNG), now(), &Cancellation::default()),
        Error::CacheFull,
    );
    selected.cancel().unwrap();
    fs::remove_file(orphan).unwrap();
}
#[test]
fn media_staging_future_timestamp_is_not_expired() {
    let f = Fixture::new();
    let path = f.name(4, "jpg");
    fs::write(&path, PNG).unwrap();
    File::options()
        .write(true)
        .open(&path)
        .unwrap()
        .set_times(
            FileTimes::new().set_modified(
                SystemTime::UNIX_EPOCH + Duration::from_secs(now() + TTL_SECONDS * 3),
            ),
        )
        .unwrap();
    let mut store = f.store();
    assert_eq!(
        store
            .cleanup_expired(now(), &Cancellation::default())
            .unwrap()
            .removed,
        0
    );
}
#[test]
fn media_staging_input_bounds_and_signatures_before_io() {
    let f = Fixture::new();
    let mut store = f.store();
    let cancel = Cancellation::default();
    for (bytes, mime) in [
        (b"\xff\xd8\xfffake".as_slice(), "image/jpeg"),
        (PNG, "image/png"),
        (b"GIF89afake", "image/gif"),
        (b"RIFF1234WEBPfake", "image/webp"),
        (b"\x1a\x45\xdf\xa3webmfake", "video/webm"),
        (b"0000ftypisomfake", "video/mp4"),
    ] {
        let selected = store
            .stage(
                bytes,
                MediaSpec {
                    mime,
                    expected_bytes: bytes.len() as u64,
                },
                now(),
                &cancel,
            )
            .unwrap();
        selected.cancel().unwrap();
    }
    err(
        store.stage(
            PNG,
            MediaSpec {
                mime: "text/html",
                expected_bytes: PNG.len() as u64,
            },
            now(),
            &cancel,
        ),
        Error::UnsupportedMedia,
    );
    err(
        store.stage(
            PNG,
            MediaSpec {
                mime: "image/png",
                expected_bytes: MAX_FILE_BYTES + 1,
            },
            now(),
            &cancel,
        ),
        Error::FileTooLarge,
    );
    err(
        store.stage(
            PNG,
            MediaSpec {
                mime: "image/png",
                expected_bytes: 1,
            },
            now(),
            &cancel,
        ),
        Error::InvalidInput,
    );
    err(
        store.stage(
            b"bad",
            MediaSpec {
                mime: "image/png",
                expected_bytes: 3,
            },
            now(),
            &cancel,
        ),
        Error::UnsupportedMedia,
    );
    err(
        store.stage(&[], spec(&[]), now(), &cancel),
        Error::InvalidInput,
    );
    let mut maximum = vec![0u8; MAX_FILE_BYTES as usize];
    maximum[..8].copy_from_slice(&PNG[..8]);
    store
        .stage(&maximum, spec(&maximum), now(), &cancel)
        .unwrap()
        .cancel()
        .unwrap();
    maximum.push(0);
    err(
        store.stage(&maximum, spec(&maximum), now(), &cancel),
        Error::FileTooLarge,
    );
}
#[test]
fn media_staging_cancel_before_io_creates_nothing() {
    let f = Fixture::new();
    let mut store = f.store();
    let cancel = Cancellation::default();
    cancel.cancel();
    err(
        store.stage(PNG, spec(PNG), now(), &cancel),
        Error::Cancelled,
    );
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
}
#[test]
fn media_staging_rejects_nonempty_lock_without_overwriting() {
    let f = Fixture::new();
    fs::write(f.0.join(".valmedia.lock"), b"UNKNOWN").unwrap();
    let mut store = f.store();
    err(
        store.stage(PNG, spec(PNG), now(), &Cancellation::default()),
        Error::UnsafePath,
    );
    assert_eq!(fs::read(f.0.join(".valmedia.lock")).unwrap(), b"UNKNOWN");
}
#[test]
fn media_staging_hardlink_owned_name_is_never_deleted() {
    let f = Fixture::new();
    let target = f.0.join("unrelated.dat");
    fs::write(&target, b"UNKNOWN").unwrap();
    let linked = f.name(5, "png");
    fs::hard_link(&target, &linked).unwrap();
    let mut store = f.store();
    err(
        store.stage(PNG, spec(PNG), now(), &Cancellation::default()),
        Error::UnsafePath,
    );
    assert_eq!(fs::read(&target).unwrap(), b"UNKNOWN");
    assert!(linked.exists());
}
#[test]
fn media_staging_owned_named_directory_is_rejected_not_traversed() {
    let f = Fixture::new();
    let dir = f.name(6, "part");
    fs::create_dir(&dir).unwrap();
    fs::write(dir.join("unrelated"), b"UNKNOWN").unwrap();
    let mut store = f.store();
    assert!(
        store
            .stage(PNG, spec(PNG), now(), &Cancellation::default())
            .is_err()
    );
    assert_eq!(fs::read(dir.join("unrelated")).unwrap(), b"UNKNOWN");
}
#[test]
fn media_staging_paths_and_unicode_url() {
    for path in [
        "relative",
        "C:/",
        "C:/a/../b",
        "C:/a/",
        "C:/a//b",
        "C:/NUL",
        "C:/a:stream",
        "C:/a.",
        "C:/a ",
        "C:/COM¹",
        "//host/share/x",
        "\\\\?\\C:\\x",
    ] {
        assert!(StagingRoot::open(path).is_err());
    }
    let f = Fixture::new();
    let path = f.0.join("Тест # проценты% space");
    fs::create_dir(&path).unwrap();
    let mut store = StagingRoot::open(&path).unwrap();
    let selected = store
        .stage(PNG, spec(PNG), now(), &Cancellation::default())
        .unwrap();
    let url = selected.file_url();
    assert!(
        url.contains("%20") && url.contains("%23") && url.contains("%25") && url.contains("%D0")
    );
    selected.cancel().unwrap();
}
#[test]
fn media_staging_missing_root_is_not_created() {
    let f = Fixture::new();
    let path = f.0.join("missing");
    assert!(StagingRoot::open(&path).is_err());
    assert!(!path.exists());
}
#[test]
fn media_staging_junction_ancestor_rejected() {
    use std::os::windows::process::CommandExt;
    let f = Fixture::new();
    let outside = Fixture::new();
    let link = f.0.join("junction");
    let output = std::process::Command::new("cmd")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(link.to_str().unwrap().replace('/', "\\"))
        .arg(outside.0.to_str().unwrap().replace('/', "\\"))
        .creation_flags(0x08000000)
        .output()
        .unwrap();
    assert!(output.status.success());
    assert!(StagingRoot::open(&link).is_err());
    assert_eq!(fs::read_dir(&outside.0).unwrap().count(), 0);
    fs::remove_dir(&link).unwrap();
}
#[test]
fn media_staging_unknown_entry_count_is_bounded() {
    let f = Fixture::new();
    for i in 0..4097 {
        File::create(f.0.join(format!("unknown-{i}"))).unwrap();
    }
    let mut store = f.store();
    err(
        store.stage(PNG, spec(PNG), now(), &Cancellation::default()),
        Error::Unavailable,
    );
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 4098); // the persistent lock is the only addition
}
#[test]
fn media_staging_entry_capacity_reserves_new_file_slot() {
    let f = Fixture::new();
    for i in 0..4095 {
        File::create(f.0.join(format!("unknown-{i}"))).unwrap();
    }
    let mut store = f.store();
    assert_eq!(
        store
            .cleanup_expired(now(), &Cancellation::default())
            .unwrap()
            .bytes,
        0
    );
    err(
        store.stage(PNG, spec(PNG), now(), &Cancellation::default()),
        Error::CacheFull,
    );
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 4096);
    assert_eq!(
        store
            .cleanup_expired(now(), &Cancellation::default())
            .unwrap()
            .bytes,
        0
    );
}
