#![cfg(windows)]
use std::{
    fs,
    os::windows::{fs::OpenOptionsExt, process::CommandExt},
    path::PathBuf,
};
use valmax_core::mdata_recovery_state::{self as state, RecoveryMarker, RecoveryRequired};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut bytes = [0u8; 16];
        getrandom::getrandom(&mut bytes).unwrap();
        let id: String = bytes.iter().map(|b| format!("{b:02x}")).collect();
        let path = PathBuf::from("C:/ValMAXBuild/fixtures/mdata-recovery-state").join(id);
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn root(&self) -> PathBuf {
        self.0.join("MXMOD")
    }
    fn backup(&self) -> PathBuf {
        self.0.join("MXMOD-MData-Backups/synthetic.mdata")
    }
    fn marker(&self) -> PathBuf {
        self.0.join(state::FILENAME)
    }
    fn write(&self) -> RecoveryMarker {
        state::write(&self.root(), &self.backup()).unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize("C:/ValMAXBuild/fixtures/mdata-recovery-state").unwrap();
        let own = fs::canonicalize(&self.0).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
#[test]
fn absent_is_read_only_and_create_clear_preserve_the_schema() {
    let f = Fixture::new();
    assert_eq!(state::read(&f.root()).unwrap(), None);
    state::ensure_clear(&f.root()).unwrap();
    assert_eq!(
        state::root_candidate(&f.0).unwrap(),
        PathBuf::from(f.root().to_str().unwrap().replace('/', "\\"))
    );
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
    let marker = f.write();
    assert_eq!(state::read(&f.root()).unwrap(), Some(marker.clone()));
    assert_eq!(marker.schema, 1);
    assert_eq!(marker.operation_id.len(), 32);
    assert_eq!(state::ensure_clear(&f.root()), Err(RecoveryRequired));
    // The latch is alongside MXMOD; it must work even when an interrupted
    // restore left the profile directory absent. No backup data is accessed.
    assert!(!f.root().exists() && !f.backup().parent().unwrap().exists());
    let value: serde_json::Value = serde_json::from_slice(&fs::read(f.marker()).unwrap()).unwrap();
    assert_eq!(value.as_object().unwrap().len(), 4);
    assert_eq!(value["operationId"], marker.operation_id);
    state::clear(&f.root(), &marker).unwrap();
    state::clear(&f.root(), &marker).unwrap();
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
}
#[test]
fn foreign_operation_or_backup_cannot_clear_or_overwrite() {
    let f = Fixture::new();
    let first = f.write();
    let bytes = fs::read(f.marker()).unwrap();
    let mut other = first.clone();
    other.operation_id = "0".repeat(32);
    assert_eq!(state::clear(&f.root(), &other), Err(RecoveryRequired));
    other = first.clone();
    other.backup_file = other
        .backup_file
        .replace("synthetic.mdata", "another.mdata");
    assert_eq!(state::clear(&f.root(), &other), Err(RecoveryRequired));
    assert_eq!(state::write(&f.root(), &f.backup()), Err(RecoveryRequired));
    assert_eq!(fs::read(f.marker()).unwrap(), bytes);
    state::clear(&f.root(), &first).unwrap();
}
#[test]
fn malformed_partial_or_unknown_json_remains_a_blocking_marker() {
    let f = Fixture::new();
    let marker = f.write();
    let good = serde_json::to_value(&marker).unwrap();
    let mut bad = vec![
        vec![],
        b"{".to_vec(),
        b"null".to_vec(),
        b"[]".to_vec(),
        vec![b' '; state::MAX_BYTES + 1],
        br#"{"schema":1,"schema":1}"#.to_vec(),
        vec![0xff, 0x00],
    ];
    bad.push(
        serde_json::to_string(&good)
            .unwrap()
            .replace("\"schema\":1", "\"schema\":1,\"schema\":1")
            .into_bytes(),
    );
    for (key, value) in [
        ("schema", serde_json::json!(true)),
        ("schema", serde_json::json!(1.0)),
        ("operationId", serde_json::json!("A".repeat(32))),
        ("extra", serde_json::json!(0)),
        ("profileRoot", serde_json::json!("C:\\elsewhere\\MXMOD")),
        ("backupFile", serde_json::json!("C:\\foreign.mdata")),
    ] {
        let mut copy = good.clone();
        copy[key] = value;
        bad.push(serde_json::to_vec(&copy).unwrap());
    }
    for data in bad {
        fs::write(f.marker(), &data).unwrap();
        assert_eq!(state::read(&f.root()), Err(RecoveryRequired));
        assert_eq!(state::ensure_clear(&f.root()), Err(RecoveryRequired));
        assert_eq!(state::write(&f.root(), &f.backup()), Err(RecoveryRequired));
        assert_eq!(state::clear(&f.root(), &marker), Err(RecoveryRequired));
        assert_eq!(fs::read(f.marker()).unwrap(), data);
    }
}
#[test]
fn windows_paths_and_python_json_byte_encodings_are_compatible() {
    let f = Fixture::new();
    let marker = f.write();
    let utf8 = serde_json::to_vec(&marker).unwrap();
    let text = std::str::from_utf8(&utf8).unwrap();
    let mut variants = vec![[b"\xef\xbb\xbf".as_slice(), &utf8].concat()];
    for little in [false, true] {
        let utf16: Vec<u8> = text
            .encode_utf16()
            .flat_map(|c| {
                if little {
                    c.to_le_bytes()
                } else {
                    c.to_be_bytes()
                }
            })
            .collect();
        let utf32: Vec<u8> = text
            .chars()
            .flat_map(|c| {
                if little {
                    (c as u32).to_le_bytes()
                } else {
                    (c as u32).to_be_bytes()
                }
            })
            .collect();
        variants.push(utf16.clone());
        variants.push(utf32.clone());
        variants.push(
            [
                if little {
                    b"\xff\xfe".as_slice()
                } else {
                    b"\xfe\xff".as_slice()
                },
                &utf16,
            ]
            .concat(),
        );
        variants.push(
            [
                if little {
                    b"\xff\xfe\0\0".as_slice()
                } else {
                    b"\0\0\xfe\xff".as_slice()
                },
                &utf32,
            ]
            .concat(),
        );
    }
    for bytes in variants {
        fs::write(f.marker(), bytes).unwrap();
        assert_eq!(state::read(&f.root()).unwrap(), Some(marker.clone()));
    }
    let mut mixed = marker.clone();
    mixed.backup_file = mixed
        .backup_file
        .replace("MXMOD-MData-Backups", "mxmod-mdata-backups")
        .replace(".mdata", ".MDATA");
    fs::write(f.marker(), serde_json::to_vec(&mixed).unwrap()).unwrap();
    assert_eq!(state::read(&f.root()).unwrap(), Some(mixed.clone()));
    state::clear(&f.root(), &mixed).unwrap();
    assert_eq!(
        state::write(&f.root(), &f.backup().with_extension("MDATA")),
        Err(RecoveryRequired)
    );
    assert_eq!(
        state::write(&f.root(), &f.0.join("foreign.mdata")),
        Err(RecoveryRequired)
    );
    for path in [
        "relative/MXMOD",
        "C:/x/../MXMOD",
        "C:/x//MXMOD",
        "//server/share/MXMOD",
        "C:/ONEME",
        "C:/x/MXMOD:ads",
    ] {
        assert_eq!(
            state::read(std::path::Path::new(path)),
            Err(RecoveryRequired)
        );
    }
}
#[test]
fn busy_hardlinked_and_directory_markers_are_preserved() {
    let f = Fixture::new();
    let marker = f.write();
    let bytes = fs::read(f.marker()).unwrap();
    let held = fs::OpenOptions::new()
        .read(true)
        .share_mode(1)
        .open(f.marker())
        .unwrap();
    assert_eq!(state::clear(&f.root(), &marker), Err(RecoveryRequired));
    drop(held);
    let alias = f.0.join("owned-alias");
    fs::hard_link(f.marker(), &alias).unwrap();
    assert_eq!(state::read(&f.root()), Err(RecoveryRequired));
    assert_eq!(state::clear(&f.root(), &marker), Err(RecoveryRequired));
    assert_eq!(state::write(&f.root(), &f.backup()), Err(RecoveryRequired));
    assert_eq!(fs::read(&alias).unwrap(), bytes);
    fs::remove_file(alias).unwrap();
    state::clear(&f.root(), &marker).unwrap();
    fs::create_dir(f.marker()).unwrap();
    assert_eq!(state::read(&f.root()), Err(RecoveryRequired));
    assert_eq!(state::clear(&f.root(), &marker), Err(RecoveryRequired));
    assert_eq!(state::write(&f.root(), &f.backup()), Err(RecoveryRequired));
    assert!(f.marker().is_dir());
}
#[test]
fn competing_creators_publish_one_complete_record() {
    let f = Fixture::new();
    let barrier = std::sync::Arc::new(std::sync::Barrier::new(3));
    let mut threads = vec![];
    for _ in 0..2 {
        let barrier = barrier.clone();
        let root = f.root();
        let backup = f.backup();
        threads.push(std::thread::spawn(move || {
            barrier.wait();
            state::write(&root, &backup)
        }));
    }
    barrier.wait();
    let results: Vec<_> = threads.into_iter().map(|t| t.join().unwrap()).collect();
    assert_eq!(results.iter().filter(|r| r.is_ok()).count(), 1);
    let winner = results.into_iter().find_map(Result::ok).unwrap();
    assert_eq!(state::read(&f.root()).unwrap(), Some(winner.clone()));
    state::clear(&f.root(), &winner).unwrap();
    assert_eq!(fs::read_dir(&f.0).unwrap().count(), 0);
}
#[test]
fn missing_parents_and_reparse_ancestors_are_not_created_or_followed() {
    let f = Fixture::new();
    let missing = f.0.join("missing/deeper/MXMOD");
    assert_eq!(state::read(&missing).unwrap(), None);
    assert_eq!(state::write(&missing, &f.backup()), Err(RecoveryRequired));
    assert!(!f.0.join("missing").exists());
    let target = f.0.join("owned-target");
    fs::create_dir(&target).unwrap();
    let junction = f.0.join("owned-junction");
    let output = std::process::Command::new("cmd.exe")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(junction.to_str().unwrap().replace('/', "\\"))
        .arg(target.to_str().unwrap().replace('/', "\\"))
        .creation_flags(0x08000000)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "own synthetic junction must be created: status={:?}, stdout={}, stderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let root = junction.join("MXMOD");
    assert_eq!(state::read(&root), Err(RecoveryRequired));
    assert_eq!(
        state::write(&root, &junction.join("MXMOD-MData-Backups/own.mdata")),
        Err(RecoveryRequired)
    );
    assert_eq!(fs::read_dir(&target).unwrap().count(), 0);
    fs::remove_dir(junction).unwrap();
}
