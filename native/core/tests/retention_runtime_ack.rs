//! Synthetic native-host receipt: saved intent is never an active capture proof.
use std::{fs, path::{Path, PathBuf}};
use serde_json::json;
use valmax_core::{retention_ack::NativeStatus, retention_preferences::revision,
    runtime::Runtime};

fn fixture_root() -> PathBuf {
    let base = Path::new("E:/ValMAXBuild/fixtures/synthetic/retention-ack-p11");
    fs::create_dir_all(base).unwrap();
    let mut random = [0_u8; 16];
    getrandom::getrandom(&mut random).unwrap();
    let root = base.join(random.iter().map(|part| format!("{part:02x}"))
        .collect::<String>());
    fs::create_dir(&root).unwrap();
    fs::create_dir(root.join("profile")).unwrap();
    fs::create_dir(root.join("runtime")).unwrap();
    root
}

fn runtime(root: &Path) -> Runtime {
    Runtime::open(&json!({"profileDirectory":root.join("profile"),
        "runtimeDirectory":root.join("runtime"),"existingClient":true})).unwrap()
}

fn host(enabled: bool, generation: u64) -> NativeStatus {
    let mut bytes = [0_u8; 64];
    bytes.copy_from_slice(revision(enabled).as_bytes());
    NativeStatus {size:184,version:1,error:0,installed:1,images_exact:1,
        listeners:3,gates:0,owner_join:1,store_ready:1,worker_available:1,
        worker_ready:enabled.into(),preference_enabled:enabled.into(),applied:1,
        reserved:0,generation,revision:bytes,captured:0,marked:0,missing:0,
        missed:0,failures:0,queued:0,in_flight:0,pending:0,reserved_tail:0}
}

#[test]
fn enable_waits_for_new_host_generation_and_revoke_keeps_saved_intent_inactive() {
    let root = fixture_root();
    let rt = runtime(&root);
    let get = || rt.call(&json!({"operation":"retention.get"})).unwrap();
    let set = |expected: String, enabled: bool| rt.call(&json!({
        "operation":"retention.set","revision":expected,"enabled":enabled}));
    assert_eq!(get()["capabilities"]["enable"], false);
    assert_eq!(set(revision(false), true), Err("retention_unavailable"));
    assert!(rt.acknowledge_retention(host(false, 1)));
    assert_eq!(get()["runtime"]["ready"], true);
    assert_eq!(get()["runtime"]["enabled"], false);
    let saved = set(revision(false), true).unwrap();
    assert_eq!(saved["enabled"], true);
    assert_eq!(saved["runtime"]["ready"], true);
    assert_eq!(saved["runtime"]["applied"], false);
    assert_eq!(saved["runtime"]["enabled"], false);
    assert!(rt.acknowledge_retention(host(false, 1)));
    assert_eq!(get()["runtime"]["enabled"], false);
    assert!(rt.acknowledge_retention(host(true, 2)));
    assert_eq!(get()["runtime"]["enabled"], true);

    // The native bridge disables admission before the saved-setting CAS.
    assert!(rt.acknowledge_retention(host(false, 3)));
    assert_eq!(get()["runtime"]["enabled"], false);
    let off = set(revision(true), false).unwrap();
    assert_eq!(off["enabled"], false);
    assert_eq!(off["runtime"]["applied"], false);
    assert!(rt.acknowledge_retention(host(false, 4)));
    assert_eq!(get()["runtime"]["applied"], true);
    let mut failed = host(false, 4);
    failed.error = 5;
    assert!(rt.acknowledge_retention(failed));
    assert_eq!(get()["runtime"]["ready"], false);
    assert_eq!(get()["runtime"]["applied"], true);
    assert_eq!(get()["runtime"]["error"], "storage");
    assert_eq!(set(revision(false), true), Err("retention_unavailable"));
}
