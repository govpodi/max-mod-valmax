use super::*;
use crate::mdata_archive::{ArchiveKind, MDataError, MDataPolicy};
use serde_json::{Value, json};
use std::{
    fs,
    io::{Cursor, Seek, SeekFrom},
    sync::OnceLock,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-profile-stage";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("mdata-profile-stage"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}
const PASSWORD: &str = "Synthetic-Recovery-Password-123";

struct Fixture {
    parent: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let parent = fixture_base().join(crate::startup_status::new_operation_id().unwrap());
        fs::create_dir_all(&parent).unwrap();
        Self { parent }
    }
    fn empty(&self) {
        assert_eq!(fs::read_dir(&self.parent).unwrap().count(), 0);
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        // Test cleanup has authority only over this generated fixture. No live
        // profile path is ever supplied to this module or removed here.
        let base = fs::canonicalize(fixture_base()).unwrap();
        let own = fs::canonicalize(&self.parent).unwrap();
        assert!(own.starts_with(&base) && own != base);
        fs::remove_dir_all(own).unwrap();
    }
}
fn error<T>(value: Result<T>) -> StageError {
    match value {
        Ok(_) => panic!("expected finite failure"),
        Err(e) => e,
    }
}
fn put(stage: &Stage, path: &str, bytes: &[u8]) {
    let mut writer = stage.sink(path, bytes.len() as u64).unwrap();
    writer.write_all(bytes).unwrap();
    writer.flush().unwrap();
}
fn read_slot(stage: &Stage, index: usize) -> Vec<u8> {
    let state = stage.state.borrow();
    let mut file = state.files[index].file.writer_clone().unwrap();
    file.seek(SeekFrom::Start(0)).unwrap();
    let mut result = Vec::new();
    file.read_to_end(&mut result).unwrap();
    result
}
fn manifest(files: &[(&str, Vec<u8>)], credentials: &[(&str, &[u8])]) -> Value {
    json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
        "credentials": credentials.iter().map(|(name,blob)|json!({"name":name,"blob":STANDARD.encode(blob)})).collect::<Vec<_>>(),
        "files":files.iter().map(|(path,data)|json!({"path":path,"size":data.len()})).collect::<Vec<_>>()})
}
fn encode(files: &[(&str, Vec<u8>)], credentials: &[(&str, &[u8])], kind: ArchiveKind) -> Vec<u8> {
    let policy = mdata_profile::credential_policy();
    let policy = MDataPolicy {
        credential_policy: &policy,
        file_path_validator: &|_| true,
        credential_name_validator: Some(&mdata_profile::allowed_name),
    };
    let manifest = manifest(files, credentials);
    let mut archive = Vec::new();
    mdata_archive::encrypt_stream(
        &manifest,
        PASSWORD,
        &mut |path| {
            let data = &files.iter().find(|(name, _)| *name == path).unwrap().1;
            Ok(Box::new(Cursor::new(data.clone())) as Box<dyn Read>)
        },
        &mut archive,
        &policy,
        kind,
    )
    .unwrap();
    archive
}
fn valid_archive() -> &'static Vec<u8> {
    static ARCHIVE: OnceLock<Vec<u8>> = OnceLock::new();
    ARCHIVE.get_or_init(|| {
        encode(
            &[
                (
                    "USER42/history/part.db",
                    vec![0x53; mdata_archive::CHUNK_BYTES + 17],
                ),
                ("user42/settings", b"SYNTHETIC SETTINGS".to_vec()),
                ("app/state.db", b"SYNTHETIC APP DB".to_vec()),
                ("empty", vec![]),
            ],
            &[
                ("history_storage_key_42@MXMOD", b"SYNTHETIC HISTORY KEY"),
                ("app_data_storage_key_Common@MXMOD", b"SYNTHETIC APP KEY"),
            ],
            ArchiveKind::Profile,
        )
    })
}

#[test]
fn explicit_existing_parent_is_required_and_profiles_are_never_traversed() {
    let f = Fixture::new();
    for path in [
        PathBuf::from("relative"),
        f.parent.join("missing"),
        f.parent.join("MXMOD"),
        f.parent.join("MXMOD/nested"),
        f.parent.join("ONEME"),
    ] {
        assert_eq!(error(Stage::create(&path)), StageError::UnsafePath);
    }
    f.empty();
    let stage = Stage::create(&f.parent).unwrap();
    assert!(stage.path().parent() == Some(f.parent.as_path()));
    assert!(
        stage
            .path()
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with(".MXMOD-MData-stage-")
    );
    stage.discard().unwrap();
    stage.discard().unwrap();
    drop(stage);
    f.empty();
}

#[test]
fn native_handles_preserve_bytes_and_windows_directory_aliases() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    let root = stage.path();
    stage.prepare(7, 5).unwrap();
    put(&stage, "A/one", b"1");
    put(&stage, "a/two", b"22");
    put(&stage, "ß/three", b"3");
    put(&stage, "ss/four", b"44");
    put(&stage, "данные/五", b"5");
    assert_eq!(stage.state.borrow().directories.len(), 5);
    let state = stage.state.borrow();
    assert!(state.directories[1].directory.identity() != state.directories[2].directory.identity());
    assert!(state.files[0].file.identity() != state.files[1].file.identity());
    let file = state.files[0].file.path().to_owned();
    drop(state);
    assert!(fs::OpenOptions::new().write(true).open(&file).is_err());
    assert!(fs::rename(&root, f.parent.join("renamed")).is_err());
    stage.seal().unwrap();
    assert!(read_slot(&stage, 0) == b"1");
    assert!(read_slot(&stage, 1) == b"22");
    assert!(read_slot(&stage, 4) == b"5");
    assert_eq!(stage.seal(), Err(StageError::InvalidPayload));
    assert_eq!(error(stage.sink("late", 0)), StageError::InvalidRequest);
    stage.discard().unwrap();
    drop(stage);
    f.empty();
}

#[test]
fn duplicate_casefold_and_file_directory_collisions_never_overwrite() {
    for (first, second) in [
        ("A", "a"),
        ("ß/file", "ss/FILE"),
        ("a", "a/b"),
        ("a/b", "a"),
    ] {
        let f = Fixture::new();
        let stage = Stage::create(&f.parent).unwrap();
        stage.prepare(2, 2).unwrap();
        put(&stage, first, b"1");
        assert!(stage.sink(second, 1).is_err());
        assert!(read_slot(&stage, 0) == b"1");
        stage.discard().unwrap();
        drop(stage);
        f.empty();
    }
}

#[test]
fn many_siblings_reuse_windows_case_aliases_with_bounded_name_comparisons() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    stage.prepare(0, 512).unwrap();
    // Reverse insertion exercises sorted-index insertion at the beginning.
    for index in (0..256).rev() {
        put(&stage, &format!("Dir{index:03}/a"), b"");
    }
    for index in 0..256 {
        put(&stage, &format!("dIR{index:03}/b"), b"");
    }
    assert_eq!(stage.state.borrow().directories.len(), 257);
    assert!(stage.state.borrow().name_comparisons.get() < 512 * 9);
    stage.seal().unwrap();
    stage.discard().unwrap();
    drop(stage);
    f.empty();
}

#[test]
fn too_many_profile_ids_preserve_the_size_error_after_cleanup() {
    let f = Fixture::new();
    let names: Vec<_> = (1..=64).map(|id| format!("auth_{id}")).collect();
    let files: Vec<_> = names
        .iter()
        .map(|name| (name.as_str(), Vec::new()))
        .collect();
    let archive = encode(&files, &[], ArchiveKind::Profile);
    assert_eq!(
        error(decode(&mut Cursor::new(archive), PASSWORD, &f.parent)),
        StageError::TooLarge
    );
    f.empty();
}

#[test]
fn unsafe_paths_and_capacity_are_checked_before_opening_files() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    assert_eq!(error(stage.sink("a", 0)), StageError::InvalidRequest);
    assert_eq!(
        stage.prepare(mdata_archive::MAX_FILE_BYTES + 1, 1),
        Err(StageError::TooLarge)
    );
    assert_eq!(
        stage.prepare(0, mdata_archive::MAX_FILES + 1),
        Err(StageError::TooLarge)
    );
    stage.prepare(1, 1).unwrap();
    assert_eq!(stage.prepare(1, 1), Err(StageError::InvalidRequest));
    for path in [
        "../outside",
        "a:stream",
        "a\\b",
        "CON",
        "a//b",
        "a/",
        "logs/log",
        ".mdata-hidden/file",
        "backup.mdata",
    ] {
        assert_eq!(error(stage.sink(path, 0)), StageError::UnsafePath);
    }
    assert_eq!(
        error(stage.sink("too-large", 2)),
        StageError::InvalidPayload
    );
    put(&stage, "a", b"1");
    assert_eq!(error(stage.sink("excess", 0)), StageError::InvalidPayload);
    stage.seal().unwrap();
    stage.discard().unwrap();
    drop(stage);
    f.empty();
}

#[test]
fn incomplete_and_overlong_writers_cannot_seal() {
    for overlong in [false, true] {
        let f = Fixture::new();
        let stage = Stage::create(&f.parent).unwrap();
        stage.prepare(2, 1).unwrap();
        let mut writer = stage.sink("data", 2).unwrap();
        assert_eq!(stage.discard(), Err(StageError::InvalidRequest));
        assert_eq!(stage.seal(), Err(StageError::InvalidPayload));
        if overlong {
            assert!(writer.write_all(b"123").is_err());
        } else {
            writer.write_all(b"1").unwrap();
        }
        drop(writer);
        assert_eq!(stage.seal(), Err(StageError::InvalidPayload));
        assert_eq!(error(stage.sink("other", 0)), StageError::InvalidRequest);
        stage.discard().unwrap();
        drop(stage);
        f.empty();
    }
}

#[test]
fn writer_outliving_wrapper_holds_ledger_until_last_close() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    stage.prepare(1, 1).unwrap();
    let mut writer = stage.sink("a/b", 1).unwrap();
    drop(stage);
    assert_eq!(fs::read_dir(&f.parent).unwrap().count(), 1);
    writer.write_all(b"1").unwrap();
    drop(writer);
    f.empty();
}

#[test]
fn discard_reports_foreign_child_without_deleting_it() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    stage.prepare(1, 1).unwrap();
    put(&stage, "owned", b"1");
    let foreign = stage.path().join("foreign");
    fs::write(&foreign, b"SYNTHETIC FOREIGN CHILD").unwrap();
    assert_eq!(stage.discard(), Err(StageError::CleanupFailed));
    assert_eq!(stage.discard(), Err(StageError::CleanupFailed));
    drop(stage);
    assert!(fs::read(&foreign).unwrap() == b"SYNTHETIC FOREIGN CHILD");
    assert!(!foreign.parent().unwrap().join("owned").exists());
}

#[test]
fn new_hardlink_is_detected_even_when_windows_allows_its_creation() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    stage.prepare(1, 1).unwrap();
    put(&stage, "owned", b"1");
    let file = stage.state.borrow().files[0].file.path().to_owned();
    let alias = f.parent.join("alias");
    // CreateHardLink may succeed despite share=0. Identity/type/link-count
    // checks, rather than assumed sharing behavior, must stop acceptance.
    match fs::hard_link(&file, &alias) {
        Ok(()) => {
            assert_eq!(stage.seal(), Err(StageError::ProfileChanged));
            assert_eq!(stage.discard(), Err(StageError::CleanupFailed));
            assert_eq!(stage.discard(), Err(StageError::CleanupFailed));
            drop(stage);
            assert!(fs::read(&file).unwrap() == b"1");
            assert!(fs::read(&alias).unwrap() == b"1");
        }
        Err(_) => {
            stage.seal().unwrap();
            stage.discard().unwrap();
            drop(stage);
            f.empty();
        }
    }
}

#[test]
fn complete_profile_stream_is_sealed_but_never_published() {
    let f = Fixture::new();
    let live = f.parent.join("MXMOD");
    fs::create_dir(&live).unwrap();
    fs::write(live.join("sentinel"), b"UNCHANGED SYNTHETIC PROFILE").unwrap();
    let mut staged = decode(&mut Cursor::new(valid_archive()), PASSWORD, &f.parent).unwrap();
    assert_eq!(staged.file_count(), 4);
    assert_eq!(
        staged.manifest.0["credentials"].as_array().unwrap().len(),
        2
    );
    assert!(staged.stage.state.borrow().sealed);
    assert!(read_slot(&staged.stage, 0) == vec![0x53; mdata_archive::CHUNK_BYTES + 17]);
    assert!(read_slot(&staged.stage, 3).is_empty());
    assert!(fs::read(live.join("sentinel")).unwrap() == b"UNCHANGED SYNTHETIC PROFILE");
    assert_eq!(fs::read_dir(&live).unwrap().count(), 1);
    staged.discard().unwrap();
    drop(staged);
    assert_eq!(fs::read_dir(&f.parent).unwrap().count(), 1);
}

#[test]
fn empty_full_backup_is_valid_but_compact_login_is_not_recovery() {
    for kind in [ArchiveKind::Profile, ArchiveKind::Login] {
        let f = Fixture::new();
        let archive = encode(&[], &[], kind);
        let result = decode(&mut Cursor::new(archive), PASSWORD, &f.parent);
        if kind == ArchiveKind::Profile {
            let staged = result.unwrap();
            assert_eq!(staged.file_count(), 0);
            drop(staged);
        } else {
            assert_eq!(
                error(result),
                StageError::Archive(MDataError::InvalidArchive)
            );
        }
        f.empty();
    }
}

#[test]
fn wrong_password_truncation_terminal_tamper_and_trailing_data_leave_no_stage() {
    let mut terminal = valid_archive().clone();
    *terminal.last_mut().unwrap() ^= 1;
    let mut trailing = valid_archive().clone();
    trailing.push(0);
    let truncated = valid_archive()[..valid_archive().len() - 21].to_vec();
    for (archive, password, expected) in [
        (
            valid_archive().clone(),
            "Wrong-Synthetic-Password",
            MDataError::AuthenticationFailed,
        ),
        (terminal, PASSWORD, MDataError::AuthenticationFailed),
        (trailing, PASSWORD, MDataError::InvalidArchive),
        (truncated, PASSWORD, MDataError::InvalidArchive),
    ] {
        let f = Fixture::new();
        assert_eq!(
            error(decode(&mut Cursor::new(archive), password, &f.parent)),
            StageError::Archive(expected)
        );
        f.empty();
    }
}

#[test]
fn authenticated_archive_still_requires_consistent_storage_keys() {
    for (files, credentials) in [
        (vec![("user42/history/main.db", b"DB".to_vec())], vec![]),
        (
            vec![("app/state.db", b"DB".to_vec())],
            vec![("app_data_storage_key_Common@MXMOD", b"".as_slice())],
        ),
        (
            vec![],
            vec![("web-42_0@MXMOD", b"SYNTHETIC WEB VALUE".as_slice())],
        ),
    ] {
        let f = Fixture::new();
        let archive = encode(&files, &credentials, ArchiveKind::Profile);
        assert_eq!(
            error(decode(&mut Cursor::new(archive), PASSWORD, &f.parent)),
            StageError::InvalidPayload
        );
        f.empty();
    }
}

#[test]
fn excluded_files_are_rejected_and_authenticated_file_conflicts_clean_up() {
    for files in [
        vec![("logs/log", vec![1])],
        vec![("a", vec![1]), ("a/b", vec![2])],
    ] {
        let f = Fixture::new();
        let archive = encode(&files, &[], ArchiveKind::Profile);
        assert!(decode(&mut Cursor::new(archive), PASSWORD, &f.parent).is_err());
        f.empty();
    }
}

#[test]
fn recovery_replaces_only_root_config_with_current_defaults_and_removes_old_tail() {
    let f = Fixture::new();
    let archive = encode(
        &[
            ("CONFIG.JSON", vec![b'x'; 40_000]),
            ("user42/config.json", b"SYNTHETIC NESTED CONFIG".to_vec()),
        ],
        &[],
        ArchiveKind::Profile,
    );
    let staged = decode(&mut Cursor::new(archive), PASSWORD, &f.parent).unwrap();
    let original = staged.manifest.0.clone();
    let mut prepared = staged.prepare_recovery().unwrap();
    assert_eq!(prepared.file_count(), 2);
    let current = read_slot(&prepared.inner.stage, 0);
    let value = crate::launcher_config::validate_config(&current).unwrap();
    assert_eq!(value, crate::launcher_config::make_config().unwrap());
    assert!(current == serde_json::to_vec_pretty(&value).unwrap());
    assert!(read_slot(&prepared.inner.stage, 1) == b"SYNTHETIC NESTED CONFIG");
    assert!(prepared.inner.manifest.0 == original);
    prepared.revalidate().unwrap();
    assert_eq!(
        prepared.inner.stage.prepare_config(),
        Err(StageError::InvalidRequest)
    );
    prepared.discard().unwrap();
    assert_eq!(prepared.revalidate(), Err(StageError::InvalidRequest));
    drop(prepared);
    f.empty();
}

#[test]
fn missing_config_is_created_exclusively_after_authentication() {
    let f = Fixture::new();
    let archive = encode(&[], &[], ArchiveKind::Profile);
    let staged = decode(&mut Cursor::new(archive), PASSWORD, &f.parent).unwrap();
    let prepared = staged.prepare_recovery().unwrap();
    assert_eq!(prepared.file_count(), 1);
    assert_eq!(prepared.inner.file_count(), 0);
    assert!(crate::launcher_config::validate_config(&read_slot(&prepared.inner.stage, 0)).is_ok());
    prepared.revalidate().unwrap();
    drop(prepared);
    f.empty();
}

#[test]
fn root_directory_named_config_blocks_recovery_without_adopting_or_removing_foreign_data() {
    let f = Fixture::new();
    let archive = encode(
        &[("config.json/nested", b"SYNTHETIC".to_vec())],
        &[],
        ArchiveKind::Profile,
    );
    let staged = decode(&mut Cursor::new(archive), PASSWORD, &f.parent).unwrap();
    assert_eq!(error(staged.prepare_recovery()), StageError::InvalidPayload);
    f.empty();
    let archive = encode(&[], &[], ArchiveKind::Profile);
    let staged = decode(&mut Cursor::new(archive), PASSWORD, &f.parent).unwrap();
    let foreign = staged.stage.path().join("config.json");
    fs::write(&foreign, b"SYNTHETIC FOREIGN CONFIG").unwrap();
    assert_eq!(error(staged.prepare_recovery()), StageError::CleanupFailed);
    assert!(fs::read(&foreign).unwrap() == b"SYNTHETIC FOREIGN CONFIG");
}

#[test]
fn exact_inventory_rejects_unknown_children_before_seal_and_after_prepare() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    stage.prepare(1, 1).unwrap();
    put(&stage, "dir/owned", b"1");
    let foreign = stage.path().join("dir/unowned");
    fs::write(&foreign, b"SYNTHETIC UNKNOWN").unwrap();
    assert_eq!(stage.seal(), Err(StageError::ProfileChanged));
    assert_eq!(stage.discard(), Err(StageError::CleanupFailed));
    drop(stage);
    assert!(fs::read(&foreign).unwrap() == b"SYNTHETIC UNKNOWN");

    let archive = encode(&[], &[], ArchiveKind::Profile);
    let staged = decode(&mut Cursor::new(archive), PASSWORD, &f.parent).unwrap();
    let mut prepared = staged.prepare_recovery().unwrap();
    let foreign_dir = prepared.inner.stage.path().join("unknown-directory");
    fs::create_dir(&foreign_dir).unwrap();
    assert_eq!(prepared.revalidate(), Err(StageError::ProfileChanged));
    assert_eq!(prepared.discard(), Err(StageError::CleanupFailed));
    drop(prepared);
    assert!(foreign_dir.is_dir());
}

#[test]
fn partial_config_write_or_flush_failure_never_restores_prepared_state() {
    for fail_after_full_write in [false, true] {
        let f = Fixture::new();
        let archive = encode(
            &[("config.json", b"SYNTHETIC OLD".to_vec())],
            &[],
            ArchiveKind::Profile,
        );
        let staged = decode(&mut Cursor::new(archive), PASSWORD, &f.parent).unwrap();
        let error = staged.stage.prepare_config_with(|file, bytes| {
            if fail_after_full_write {
                file.replace_contents(bytes)?;
            } else {
                let mut writer = file.writer_clone()?;
                writer.seek(SeekFrom::Start(0))?;
                writer.set_len(0)?;
                writer.write_all(&bytes[..7])?;
            }
            Err(StageError::IoError)
        });
        assert_eq!(error, Err(StageError::IoError));
        assert!(staged.stage.state.borrow().failed.get());
        assert!(!staged.stage.state.borrow().recovery_prepared);
        assert_eq!(
            staged.stage.prepare_config(),
            Err(StageError::InvalidRequest)
        );
        assert_eq!(
            staged.stage.revalidate_prepared(),
            Err(StageError::InvalidRequest)
        );
        drop(staged);
        f.empty();
    }
}

#[test]
fn maximum_archive_file_count_allows_one_derived_config() {
    let f = Fixture::new();
    let stage = Stage::create(&f.parent).unwrap();
    stage.prepare(0, mdata_archive::MAX_FILES).unwrap();
    for index in 0..mdata_archive::MAX_FILES {
        put(&stage, &format!("file{index:05}"), b"");
    }
    stage.seal().unwrap();
    stage.prepare_config().unwrap();
    assert_eq!(
        stage.state.borrow().files.len(),
        mdata_archive::MAX_FILES + 1
    );
    stage.revalidate_prepared().unwrap();
    stage.discard().unwrap();
    drop(stage);
    f.empty();
}
