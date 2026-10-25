use super::*;
use crate::{
    mdata_archive::{self, ArchiveKind, MDataPolicy},
    mdata_profile, mdata_profile_stage,
};
use serde_json::json;
use std::{
    fs,
    io::{Cursor, Read},
    path::{Path, PathBuf},
    sync::OnceLock,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/mdata-recovery-swap";
const PASSWORD: &str = "Synthetic-Root-Swap-123";
const NEW_BYTES: &[u8] = b"new synthetic nested settings";
const OLD_BYTES: &[u8] = b"unchanged synthetic original profile";

struct Fixture {
    parent: PathBuf,
}
impl Fixture {
    fn new(existing: bool) -> Self {
        let parent = PathBuf::from(BASE).join(crate::startup_status::new_operation_id().unwrap());
        fs::create_dir_all(&parent).unwrap();
        let result = Self { parent };
        if existing {
            fs::create_dir_all(result.root().join("old/nested")).unwrap();
            fs::write(result.root().join("old/nested/original"), OLD_BYTES).unwrap();
        }
        result
    }
    fn root(&self) -> PathBuf {
        self.parent.join("MXMOD")
    }
    fn prepared(&self) -> super::super::PreparedRecovery {
        mdata_profile_stage::decode(&mut Cursor::new(archive()), PASSWORD, &self.parent)
            .unwrap()
            .prepare_recovery()
            .unwrap()
    }
    fn swap(&self) -> RootSwap {
        self.prepared().into_root_swap().unwrap()
    }
    fn assert_new(path: &Path) {
        assert!(fs::read(path.join("user42/nested/settings")).unwrap() == NEW_BYTES);
        assert!(fs::read(path.join("app/state")).unwrap() == b"synthetic app");
        assert!(fs::read(path.join("empty")).unwrap().is_empty());
        let config = fs::read(path.join("config.json")).unwrap();
        assert!(crate::launcher_config::validate_config(&config).is_ok());
    }
    fn assert_old(path: &Path) {
        assert!(fs::read(path.join("old/nested/original")).unwrap() == OLD_BYTES);
        assert!(!path.join("user42").exists());
    }
    fn remove_owned_empty_directory(&self, path: &Path) {
        let approved = fs::canonicalize(BASE).unwrap();
        let fixture = fs::canonicalize(&self.parent).unwrap();
        let target = fs::canonicalize(path).unwrap();
        assert!(fixture.starts_with(&approved) && fixture != approved);
        assert!(target.starts_with(&fixture) && target != fixture);
        fs::remove_dir(target).unwrap();
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let approved = fs::canonicalize(BASE).unwrap();
        let own = fs::canonicalize(&self.parent).unwrap();
        assert!(own.starts_with(&approved) && own != approved);
        fs::remove_dir_all(own).unwrap();
    }
}
fn archive() -> &'static Vec<u8> {
    static BYTES: OnceLock<Vec<u8>> = OnceLock::new();
    BYTES.get_or_init(|| {
        let files: [(&str, &[u8]); 3] = [("user42/nested/settings", NEW_BYTES),
            ("app/state", b"synthetic app"), ("empty", b"")];
        let manifest = json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
            "credentials":[], "files":files.iter().map(|(path,bytes)|json!({"path":path,"size":bytes.len()})).collect::<Vec<_>>()});
        let credentials = mdata_profile::credential_policy();
        let policy = MDataPolicy { credential_policy: &credentials,
            file_path_validator: &mdata_profile::included,
            credential_name_validator: Some(&mdata_profile::allowed_name) };
        let mut result = Vec::new();
        mdata_archive::encrypt_stream(&manifest, PASSWORD,
            &mut |path| {
                let bytes = files.iter().find(|(name, _)| *name == path).unwrap().1.to_vec();
                Ok(Box::new(Cursor::new(bytes)) as Box<dyn Read>)
            }, &mut result, &policy, ArchiveKind::Profile).unwrap();
        result
    })
}
fn paths(f: &Fixture, swap: &RootSwap) -> (PathBuf, PathBuf) {
    (
        f.parent.join(&swap.stage_name),
        f.parent.join(&swap.old_name),
    )
}

#[test]
fn successful_same_parent_publication_commits_and_preserves_the_old_tree() {
    let f = Fixture::new(true);
    let mut swap = f.swap();
    let (stage, old) = paths(&f, &swap);
    assert_eq!(swap.state(), RootSwapState::Ready);
    swap.revalidate().unwrap();
    assert_eq!(swap.commit(), Err(StageError::InvalidRequest));
    swap.swap().unwrap();
    assert_eq!(swap.state(), RootSwapState::Published);
    assert!(swap.old_moved() && swap.published());
    swap.revalidate().unwrap();
    assert_eq!(swap.swap(), Err(StageError::InvalidRequest));
    swap.commit().unwrap();
    assert_eq!(swap.state(), RootSwapState::Committed);
    assert_eq!(swap.commit(), Err(StageError::InvalidRequest));
    assert_eq!(swap.rollback(), Err(StageError::InvalidRequest));
    drop(swap);
    Fixture::assert_new(&f.root());
    Fixture::assert_old(&old);
    assert!(!stage.exists());
}

#[test]
fn rollback_returns_both_exact_roots_without_deleting_the_new_tree() {
    let f = Fixture::new(true);
    let mut swap = f.swap();
    let (stage, old) = paths(&f, &swap);
    swap.swap().unwrap();
    swap.rollback().unwrap();
    assert_eq!(swap.state(), RootSwapState::RolledBack);
    assert!(!swap.published() && !swap.old_moved());
    swap.rollback().unwrap();
    assert_eq!(swap.swap(), Err(StageError::InvalidRequest));
    assert_eq!(swap.commit(), Err(StageError::InvalidRequest));
    drop(swap);
    Fixture::assert_old(&f.root());
    Fixture::assert_new(&stage);
    assert!(!old.exists());
}

#[test]
fn missing_original_is_valid_for_publish_rollback_and_commit() {
    for commit in [false, true] {
        let f = Fixture::new(false);
        let mut swap = f.swap();
        let (stage, old) = paths(&f, &swap);
        swap.swap().unwrap();
        assert!(swap.published() && !swap.old_moved());
        if commit {
            swap.commit().unwrap();
        } else {
            swap.rollback().unwrap();
        }
        drop(swap);
        if commit {
            Fixture::assert_new(&f.root());
            assert!(!stage.exists());
        } else {
            assert!(!f.root().exists());
            Fixture::assert_new(&stage);
        }
        assert!(!old.exists());
    }
}

#[test]
fn before_name_collision_preserves_foreign_tree_and_allows_no_move_rollback() {
    let f = Fixture::new(true);
    let mut swap = f.swap();
    let (stage, old) = paths(&f, &swap);
    fs::create_dir(&old).unwrap();
    fs::write(old.join("foreign"), b"owned-test collision stand-in").unwrap();
    assert_eq!(swap.swap(), Err(StageError::FileExists));
    assert_eq!(swap.state(), RootSwapState::SwapFailed);
    assert!(!swap.old_moved() && !swap.published());
    swap.rollback().unwrap();
    drop(swap);
    Fixture::assert_old(&f.root());
    Fixture::assert_new(&stage);
    assert!(fs::read(old.join("foreign")).unwrap() == b"owned-test collision stand-in");
}

#[test]
fn later_foreign_mxmod_is_never_overwritten_when_initial_root_was_missing() {
    let f = Fixture::new(false);
    let mut swap = f.swap();
    let (stage, old) = paths(&f, &swap);
    fs::create_dir(f.root()).unwrap();
    fs::write(f.root().join("foreign"), b"preserve foreign creation").unwrap();
    assert_eq!(swap.swap(), Err(StageError::FileExists));
    assert!(!swap.published() && !swap.old_moved());
    swap.rollback().unwrap();
    drop(swap);
    assert!(fs::read(f.root().join("foreign")).unwrap() == b"preserve foreign creation");
    Fixture::assert_new(&stage);
    assert!(!old.exists());
}

#[test]
fn rollback_collision_preserves_all_roots_and_retry_moves_only_unresolved_flags() {
    let f = Fixture::new(true);
    let mut swap = f.swap();
    let (stage, old) = paths(&f, &swap);
    swap.swap().unwrap();
    fs::create_dir(&stage).unwrap();
    assert_eq!(swap.rollback(), Err(StageError::RollbackFailed));
    assert_eq!(swap.state(), RootSwapState::RollbackFailed);
    assert!(swap.published() && swap.old_moved());
    // Only this deliberately created, still-empty collision is removed by
    // test cleanup; the production rollback itself never deletes anything.
    f.remove_owned_empty_directory(&stage);
    swap.rollback().unwrap();
    assert!(!swap.published() && !swap.old_moved());
    drop(swap);
    Fixture::assert_old(&f.root());
    Fixture::assert_new(&stage);
    assert!(!old.exists());
}

#[test]
fn drop_before_or_after_publication_never_deletes_or_rolls_back_a_tree() {
    for publish in [false, true] {
        let f = Fixture::new(true);
        let mut swap = f.swap();
        let (stage, old) = paths(&f, &swap);
        if publish {
            swap.swap().unwrap();
        }
        drop(swap);
        if publish {
            Fixture::assert_new(&f.root());
            Fixture::assert_old(&old);
            assert!(!stage.exists());
        } else {
            Fixture::assert_old(&f.root());
            Fixture::assert_new(&stage);
            assert!(!old.exists());
        }
    }
}

#[test]
fn ready_rollback_is_an_explicit_no_move_abort() {
    let f = Fixture::new(true);
    let mut swap = f.swap();
    let (stage, old) = paths(&f, &swap);
    swap.rollback().unwrap();
    swap.rollback().unwrap();
    assert_eq!(swap.state(), RootSwapState::RolledBack);
    assert!(!swap.published() && !swap.old_moved());
    drop(swap);
    Fixture::assert_old(&f.root());
    Fixture::assert_new(&stage);
    assert!(!old.exists());
}
