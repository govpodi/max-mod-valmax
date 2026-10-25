use super::*;
use std::{
    fs,
    os::windows::{fs::OpenOptionsExt, process::CommandExt},
};
const BASE: &str = "C:/ValMAXBuild/fixtures/login-import-storage";
struct Fixture {
    base: PathBuf,
    root: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let mut random = [0u8; 16];
        getrandom::getrandom(&mut random).unwrap();
        let suffix: String = random.iter().map(|b| format!("{b:02x}")).collect();
        let base = PathBuf::from(BASE).join(suffix);
        let root = base.join("MXMOD");
        fs::create_dir_all(&root).unwrap();
        Self { base, root }
    }
    fn store(&self, ids: &[&str]) -> LoginStorage {
        let mut store = LoginStorage::open(&self.root).unwrap();
        store
            .prepare(&ids.iter().map(|id| id.to_string()).collect::<Vec<_>>())
            .unwrap();
        store
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(BASE).unwrap();
        let own = fs::canonicalize(&self.base).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
fn err<T>(result: Result<T>) -> StorageError {
    match result {
        Ok(_) => panic!("expected finite error"),
        Err(e) => e,
    }
}

#[test]
fn explicit_root_ids_and_input_bounds_before_writes() {
    let f = Fixture::new();
    for path in [
        PathBuf::from("relative/MXMOD"),
        f.base.join("ONEME/MXMOD"),
        f.base.join("other"),
    ] {
        assert_eq!(err(LoginStorage::open(&path)), StorageError::UnsafePath);
    }
    assert_eq!(
        err(LoginStorage::open(&f.base.join("missing/MXMOD"))),
        StorageError::InvalidProfile
    );
    assert!(!f.base.join("missing").exists());
    let mut store = LoginStorage::open(&f.root).unwrap();
    for ids in [
        vec![],
        vec!["0"],
        vec!["01"],
        vec!["-1"],
        vec!["9223372036854775808"],
        vec!["1", "1"],
        vec!["1", "2", "3", "4"],
        vec!["../42"],
    ] {
        assert_eq!(
            store.prepare(&ids.iter().map(|id| id.to_string()).collect::<Vec<_>>()),
            Err(StorageError::InvalidRequest)
        );
    }
    store.prepare(&["9223372036854775807".into()]).unwrap();
    assert_eq!(
        store.revalidate("9223372036854775807"),
        Err(StorageError::InvalidRequest)
    );
    assert_eq!(
        store.ensure_auth("9223372036854775807", Some(b"")),
        Err(StorageError::InvalidPayload)
    );
    assert_eq!(
        store.ensure_auth(
            "9223372036854775807",
            Some(&vec![7; login::MAX_AUTH_BYTES + 1])
        ),
        Err(StorageError::InvalidPayload)
    );
    assert_eq!(
        store.ensure_auth("42", Some(b"synthetic")),
        Err(StorageError::InvalidRequest)
    );
    store.ensure_auth("9223372036854775807", None).unwrap();
    store.revalidate("9223372036854775807").unwrap();
    store.commit();
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

#[test]
fn stale_empty_and_non_json_auth_preserved_without_user_traversal() {
    let f = Fixture::new();
    fs::write(f.root.join("auth_1"), b"").unwrap();
    fs::write(f.root.join("auth_2"), [0xff, 0, 0x81, 0xfe]).unwrap();
    fs::create_dir(f.root.join("user1")).unwrap();
    fs::write(
        f.root.join("user1/unknown-private-child"),
        b"synthetic history stays untouched",
    )
    .unwrap();
    // A child that cannot be read is irrelevant: only the directory is pinned.
    let _child = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(f.root.join("user1/unknown-private-child"))
        .unwrap();
    let mut store = f.store(&["2", "1", "9007199254740993"]);
    store.ensure_auth("1", Some(b"incoming different")).unwrap();
    store.ensure_auth("2", Some(b"incoming different")).unwrap();
    store.ensure_auth("9007199254740993", None).unwrap();
    for id in ["1", "2", "9007199254740993"] {
        store.revalidate(id).unwrap();
    }
    assert!(
        fs::OpenOptions::new()
            .write(true)
            .open(f.root.join("auth_2"))
            .is_err()
    );
    assert!(fs::rename(f.root.join("user1"), f.root.join("moved-user")).is_err());
    store.rollback().unwrap();
    assert!(fs::read(f.root.join("auth_1")).unwrap().is_empty());
    assert!(fs::read(f.root.join("auth_2")).unwrap() == [0xff, 0, 0x81, 0xfe]);
    assert!(!f.root.join("auth_9007199254740993").exists());
    assert!(f.root.join("user1/unknown-private-child").exists());
}

#[test]
fn complete_preflight_rejects_collision_before_first_write() {
    let f = Fixture::new();
    fs::create_dir(f.root.join("auth_2")).unwrap();
    let mut store = LoginStorage::open(&f.root).unwrap();
    assert_eq!(
        store.prepare(&["1".into(), "2".into()]),
        Err(StorageError::ProfileCollision)
    );
    assert_eq!(
        store.ensure_auth("1", Some(b"synthetic")),
        Err(StorageError::InvalidRequest)
    );
    assert!(!f.root.join("auth_1").exists());
    fs::remove_dir(f.root.join("auth_2")).unwrap();
    fs::write(f.root.join("user2"), b"wrong type").unwrap();
    assert_eq!(
        store.prepare(&["1".into(), "2".into()]),
        Err(StorageError::ProfileCollision)
    );
    fs::remove_file(f.root.join("user2")).unwrap();
    fs::write(f.root.join("auth_2"), vec![1; login::MAX_AUTH_BYTES + 1]).unwrap();
    assert_eq!(
        store.prepare(&["1".into(), "2".into()]),
        Err(StorageError::InvalidProfile)
    );
    fs::remove_file(f.root.join("auth_2")).unwrap();
    fs::write(f.base.join("source"), b"synthetic").unwrap();
    fs::hard_link(f.base.join("source"), f.root.join("auth_2")).unwrap();
    assert_eq!(
        store.prepare(&["1".into(), "2".into()]),
        Err(StorageError::ProfileCollision)
    );
    assert!(!f.root.join("auth_1").exists());
}

#[test]
fn exclusive_create_commit_explicit_rollback_and_drop_keeps_valid_subset() {
    let f = Fixture::new();
    let mut store = f.store(&["1", "2"]);
    store.ensure_auth("2", Some(b"synthetic two")).unwrap();
    store.revalidate("2").unwrap();
    store.ensure_auth("1", Some(b"synthetic one")).unwrap();
    store.revalidate("1").unwrap();
    assert!(store.created == [1, 0]);
    assert!(File::open(f.root.join("auth_1")).is_err());
    assert!(fs::remove_file(f.root.join("auth_1")).is_err());
    store.rollback().unwrap();
    store.rollback().unwrap();
    assert!(!f.root.join("auth_1").exists() && !f.root.join("auth_2").exists());
    let mut committed = f.store(&["1"]);
    committed
        .ensure_auth("1", Some(b"synthetic retained"))
        .unwrap();
    committed.revalidate("1").unwrap();
    committed.commit();
    committed.rollback().unwrap();
    assert!(fs::read(f.root.join("auth_1")).unwrap() == b"synthetic retained");
    let mut dropped = f.store(&["2"]);
    dropped.ensure_auth("2", Some(b"synthetic subset")).unwrap();
    dropped.revalidate("2").unwrap();
    drop(dropped);
    assert!(fs::read(f.root.join("auth_2")).unwrap() == b"synthetic subset");
}

#[test]
fn new_missing_entries_are_rechecked_and_creation_never_overwrites() {
    let f = Fixture::new();
    let mut store = f.store(&["1", "2", "3"]);
    store.ensure_auth("1", None).unwrap();
    fs::create_dir(f.root.join("user1")).unwrap();
    assert_eq!(store.revalidate("1"), Err(StorageError::ProfileChanged));
    store.ensure_auth("2", None).unwrap();
    fs::write(f.root.join("auth_2"), b"other writer").unwrap();
    assert_eq!(store.revalidate("2"), Err(StorageError::ProfileChanged));
    fs::write(f.root.join("auth_3"), b"other writer").unwrap();
    assert_eq!(
        store.ensure_auth("3", Some(b"new import")),
        Err(StorageError::ProfileCollision)
    );
    assert_eq!(store.revalidate("3"), Err(StorageError::InvalidRequest));
    store.rollback().unwrap();
    assert!(f.root.join("user1").is_dir());
    assert!(fs::read(f.root.join("auth_2")).unwrap() == b"other writer");
    assert!(fs::read(f.root.join("auth_3")).unwrap() == b"other writer");
}

#[test]
fn partial_write_flush_failure_and_cleanup_failure_remain_owned_and_finite() {
    for fault in [Fault::Write, Fault::Flush, Fault::Cleanup] {
        let f = Fixture::new();
        let mut store = f.store(&["1"]);
        store.fault = fault;
        let result = store.ensure_auth("1", Some(b"synthetic partial payload"));
        assert_eq!(
            result,
            Err(if fault == Fault::Cleanup {
                StorageError::RollbackFailed
            } else {
                StorageError::IoError
            })
        );
        assert_eq!(store.revalidate("1"), Err(StorageError::InvalidRequest));
        if fault == Fault::Cleanup {
            assert!(f.root.join("auth_1").exists());
        } else {
            assert!(!f.root.join("auth_1").exists());
        }
        // The failed exclusive write has never been handed to the token writer.
        store.rollback().unwrap();
        assert!(!f.root.join("auth_1").exists());
    }
}

#[test]
fn changed_bytes_identity_and_replaced_object_are_preserved_on_rollback() {
    let f = Fixture::new();
    let mut store = f.store(&["1"]);
    store.ensure_auth("1", Some(b"original data")).unwrap();
    // Fault injection through our owned handle, not a claim that another writer
    // can bypass the exclusive lease. Same-size content must still be checked.
    let auth = store.slots[0].auth.as_mut().unwrap();
    auth.pin.file.seek(SeekFrom::Start(0)).unwrap();
    auth.pin.file.write_all(b"modified data").unwrap();
    auth.pin.file.sync_all().unwrap();
    assert_eq!(store.revalidate("1"), Err(StorageError::ProfileChanged));
    assert_eq!(store.rollback(), Err(StorageError::RollbackFailed));
    assert!(fs::read(f.root.join("auth_1")).unwrap() == b"modified data");

    let mut store = f.store(&["2"]);
    store.ensure_auth("2", Some(b"same contents")).unwrap();
    let auth = store.slots[0].auth.as_mut().unwrap();
    auth.pin.identity.id[15] ^= 1;
    assert_eq!(store.rollback(), Err(StorageError::RollbackFailed));
    assert!(fs::read(f.root.join("auth_2")).unwrap() == b"same contents");

    let mut store = f.store(&["3"]);
    store.ensure_auth("3", Some(b"same contents")).unwrap();
    // Explicitly invalidate the test lease, replace the object, and inject the
    // replacement handle. Production never releases its lease at this point.
    fs::write(f.base.join("dummy"), b"dummy").unwrap();
    let auth = store.slots[0].auth.as_mut().unwrap();
    drop(std::mem::replace(
        &mut auth.pin.file,
        File::open(f.base.join("dummy")).unwrap(),
    ));
    fs::rename(f.root.join("auth_3"), f.root.join("old-auth")).unwrap();
    fs::write(f.root.join("auth_3"), b"same contents").unwrap();
    auth.pin.file = File::open(f.root.join("auth_3")).unwrap();
    assert_eq!(store.rollback(), Err(StorageError::RollbackFailed));
    assert!(fs::read(f.root.join("auth_3")).unwrap() == b"same contents");
    assert!(f.root.join("old-auth").exists());
}

#[test]
fn own_junction_ancestors_and_direct_user_entries_are_rejected() {
    let f = Fixture::new();
    let target = f.base.join("owned-target");
    fs::create_dir(&target).unwrap();
    for link in [f.base.join("root-link"), f.root.join("user1")] {
        let output = std::process::Command::new("cmd.exe")
            .args(["/D", "/C", "mklink", "/J"])
            .arg(link.to_str().unwrap().replace('/', "\\"))
            .arg(target.to_str().unwrap().replace('/', "\\"))
            .creation_flags(0x08000000)
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "owned fixture junction creation failed"
        );
        if link.file_name().unwrap() == "root-link" {
            fs::create_dir(target.join("MXMOD")).unwrap();
            assert_eq!(
                err(LoginStorage::open(&link.join("MXMOD"))),
                StorageError::UnsafePath
            );
        } else {
            let mut store = LoginStorage::open(&f.root).unwrap();
            assert_eq!(
                store.prepare(&["1".into()]),
                Err(StorageError::ProfileCollision)
            );
        }
        fs::remove_dir(&link).unwrap();
    }
    assert!(!target.join("auth_1").exists());
}
