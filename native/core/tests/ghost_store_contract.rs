use valmax_core::{ghost_store::{self, Settings, GhostSettingsStore}, settings_io::StoreError};
use serde_json::{Value, json};
use std::{fs, path::PathBuf};
fn directory() -> PathBuf {
    let mut random = [0u8; 8]; getrandom::getrandom(&mut random).unwrap();
    let path = PathBuf::from("C:/ValMAXBuild/test-data").join(format!("ghost-{}-{:x}", std::process::id(), u64::from_le_bytes(random)));
    fs::create_dir_all(&path).unwrap(); path
}

#[test]
fn legacy_preferences_migrate_without_enabling_new_filters() {
    let old = json!({"enabled":true,"hideTyping":false,"hideRead":true});
    let current = Settings::from_value(&old).unwrap();
    assert!(current.enabled && current.hide_read);
    assert!(!current.hide_online && !current.hide_stories);
    assert_eq!(ghost_store::parse(&serde_json::to_vec(&json!({"schema":1,"settings":old})).unwrap()).unwrap(), current);
    for value in [json!({}), json!({"enabled":1,"hideTyping":true,"hideRead":true}),
        json!({"enabled":true,"hideTyping":true,"hideRead":true,"hideOnline":true}),
        json!({"enabled":true,"hideTyping":true,"hideRead":true,"extra":false,"hideStories":false})] {
        assert_eq!(Settings::from_value(&value), Err(StoreError::InvalidSettings));
    }
}

#[test]
fn exact_python_canonical_and_revision() {
    let fixture: Value = serde_json::from_str(include_str!("fixtures/ghost_defaults.json")).unwrap();
    let settings = Settings::default();
    assert_eq!(String::from_utf8(settings.canonical()).unwrap(), fixture["canonical"].as_str().unwrap());
    assert_eq!(settings.snapshot(), fixture["snapshot"]);
    for data in [b"{\"schema\":1,\"schema\":1,\"settings\":{}}".as_slice(), b"{\"schema\":true,\"settings\":{}}", b"{\"schema\":1.0,\"settings\":{}}"] {
        assert_eq!(ghost_store::parse(data), Err(StoreError::InvalidStore));
    }
}

#[test]
fn durable_compare_swap_does_not_claim_runtime_applied() {
    let dir = directory(); let path = dir.join("ghost-settings.json");
    let store = GhostSettingsStore::new(&path).unwrap();
    let initial = store.snapshot().unwrap();
    assert!(!path.exists());
    let revision = initial["revision"].as_str().unwrap();
    let mut value = Settings::default().value(); value["enabled"] = true.into();
    let saved = store.replace(revision, &value).unwrap();
    assert!(saved.get("applied").is_none());
    assert!(saved.get("ready").is_none());
    assert_eq!(GhostSettingsStore::new(&path).unwrap().snapshot().unwrap(), saved);
    assert_eq!(store.replace(revision, &value), Err(StoreError::RevisionConflict));
    let mut broken = fs::read(&path).unwrap(); broken.extend_from_slice(b" garbage");
    fs::write(&path, broken).unwrap();
    assert_eq!(store.snapshot(), Err(StoreError::InvalidStore));
    assert_eq!(store.replace(saved["revision"].as_str().unwrap(), &value), Err(StoreError::InvalidStore));
    fs::remove_dir_all(dir).unwrap();
}
