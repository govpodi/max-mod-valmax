#![cfg(windows)]
//! Owned synthetic directories only; no MXMOD, WinCred or installed client.
use std::{fs,path::PathBuf,sync::{Arc,Barrier},thread};
use serde_json::{Value,json};
use valmax_core::{optimization_store::{self as store,Settings,OptimizationSettingsStore,ACTIVE_FILENAME},settings_io::{SafeStorePath,StoreError}};
fn fixture()->PathBuf {
    let mut entropy=[0u8;16];getrandom::getrandom(&mut entropy).unwrap();
    let name:String=entropy.iter().map(|b|format!("{b:02x}")).collect();
    let path=PathBuf::from("C:/ValMAXBuild/tmp").join(format!("optimization-native-{name}"));
    fs::create_dir_all(&path).unwrap();path
}
fn changed()->Value {let mut value=Settings::default().value();value["enabled"]=json!(false);value["listPrefetch"]=json!(false);value}

#[test] fn lazy_missing_read_never_creates_parent_or_file() {
    let root=fixture();let path=root.join("absent").join("prefs.json");let s=OptimizationSettingsStore::new(&path).unwrap();
    let value=s.snapshot().unwrap();assert_eq!(value.settings,Settings::default());assert!(!value.restart_required);assert!(!path.parent().unwrap().exists());
}
#[test] fn canonical_python_golden_and_strict_shape() {
    let bytes=br#"{"schema":1,"settings":{"enabled":true,"hiddenPageWork":true,"historyBookkeeping":true,"historyCache":true,"listPrefetch":true,"listScheduling":true,"photoDecodeResize":true,"profileRows":true,"readyCache":true,"smoothWheel":true,"windowPosition":true}}"#;
    assert_eq!(Settings::default().canonical(),bytes);assert_eq!(store::parse(bytes).unwrap(),Settings::default());
    for value in [json!({}),json!([]),json!(true)] {assert_eq!(Settings::from_value(&value),Err(StoreError::InvalidSettings));}
    for key in store::KEYS {let mut value=Settings::default().value();value[key]=json!(1);assert_eq!(Settings::from_value(&value),Err(StoreError::InvalidSettings));}
    for bytes in [br#"{"schema":1,"schema":1,"settings":{}}"#.as_slice(),br#"{"schema":true,"settings":{}}"#,br#"{"schema":1.0,"settings":{}}"#,b"{} trailing",b"\xff",b"",b"{\"x\":NaN}"] {assert_eq!(store::parse(bytes),Err(StoreError::InvalidStore));}
    assert_eq!(store::parse(&vec![b' ';4097]),Err(StoreError::InvalidStore));
}
#[test] fn desired_active_restart_and_existing_client_match_python() {
    let root=fixture();let path=root.join("profile").join("prefs.json");let runtime=root.join("runtime");let s=OptimizationSettingsStore::new(&path).unwrap();
    s.activate(&runtime,false).unwrap();let active=fs::read(runtime.join(ACTIVE_FILENAME)).unwrap();
    let original=s.snapshot().unwrap();let saved=s.replace(&original.revision,&changed()).unwrap();assert!(saved.restart_required);assert_eq!(saved.applied_settings,Settings::default());
    assert_eq!(fs::read(&path).unwrap(),Settings::from_value(&changed()).unwrap().canonical());
    s.activate(&runtime,false).unwrap();assert_eq!(fs::read(runtime.join(ACTIVE_FILENAME)).unwrap(),active);
    let recovered=OptimizationSettingsStore::new(&path).unwrap();recovered.activate(&runtime,true).unwrap();assert!(recovered.snapshot().unwrap().restart_required);
    let next=OptimizationSettingsStore::new(&path).unwrap();next.activate(&runtime,false).unwrap();assert!(!next.snapshot().unwrap().restart_required);
    assert_eq!(store::parse(&fs::read(runtime.join(ACTIVE_FILENAME)).unwrap()).unwrap(),Settings::from_value(&changed()).unwrap());
}
#[test] fn stale_revision_and_malformed_settings_never_overwrite() {
    let root=fixture();let path=root.join("prefs.json");let s=OptimizationSettingsStore::new(&path).unwrap();let original=s.snapshot().unwrap();
    for revision in ["", "A", &"a".repeat(63),&"A".repeat(64)] {assert!(matches!(s.replace(revision,&changed()),Err(StoreError::InvalidRevision)));}
    assert!(!path.exists());s.replace(&original.revision,&changed()).unwrap();let saved=fs::read(&path).unwrap();
    assert!(matches!(s.replace(&original.revision,&Settings::default().value()),Err(StoreError::RevisionConflict)));assert_eq!(fs::read(&path).unwrap(),saved);
    fs::write(&path,b"{\"schema\":1,\"settings\":\"synthetic-invalid\"}").unwrap();let malformed=fs::read(&path).unwrap();
    assert!(matches!(s.snapshot(),Err(StoreError::InvalidStore)));assert!(matches!(s.replace(&original.revision,&changed()),Err(StoreError::InvalidStore)));assert_eq!(fs::read(&path).unwrap(),malformed);
}
#[test] fn rejects_hardlinked_destination_and_active_without_modifying_source() {
    let root=fixture();let source=root.join("original.json");let bytes=Settings::default().canonical();fs::write(&source,&bytes).unwrap();
    let target=root.join("prefs.json");fs::hard_link(&source,&target).unwrap();let s=OptimizationSettingsStore::new(&target).unwrap();
    assert!(matches!(s.snapshot(),Err(StoreError::UnsafePath)));assert!(matches!(s.replace(&Settings::default().revision(),&changed()),Err(StoreError::UnsafePath)));assert_eq!(fs::read(&source).unwrap(),bytes);
    let runtime=root.join("runtime");fs::create_dir(&runtime).unwrap();fs::hard_link(&source,runtime.join(ACTIVE_FILENAME)).unwrap();
    let clean=OptimizationSettingsStore::new(root.join("clean.json")).unwrap();assert_eq!(clean.activate(&runtime,false),Err(StoreError::UnsafePath));assert_eq!(fs::read(&source).unwrap(),bytes);
}
#[test] fn temporary_cleanup_and_ancestor_identity_hold_on_failure() {
    let root=fixture();let path=root.join("prefs.json");let safe=SafeStorePath::new(&path).unwrap();let guard=safe.begin(true).unwrap().unwrap();let _lock=guard.writer_lock().unwrap();
    let before:Vec<_>=fs::read_dir(&root).unwrap().map(|x|x.unwrap().file_name()).collect();
    assert_eq!(guard.atomic_replace(b"synthetic",||Err(StoreError::RevisionConflict)),Err(StoreError::RevisionConflict));
    let after:Vec<_>=fs::read_dir(&root).unwrap().map(|x|x.unwrap().file_name()).collect();assert_eq!(before,after);
    assert!(fs::rename(&root,root.with_extension("moved")).is_err());assert!(!path.exists());
}
#[test] fn independent_stores_serialize_writers_using_shared_revision() {
    let root=fixture();let path=root.join("prefs.json");let revision=Settings::default().revision();let barrier=Arc::new(Barrier::new(2));
    let handles:Vec<_>=(0..2).map(|_|{let path=path.clone();let revision=revision.clone();let barrier=barrier.clone();thread::spawn(move||{let s=OptimizationSettingsStore::new(path).unwrap();barrier.wait();s.replace(&revision,&changed())})}).collect();
    let results:Vec<_>=handles.into_iter().map(|h|h.join().unwrap()).collect();assert_eq!(results.iter().filter(|r|r.is_ok()).count(),1);assert_eq!(results.iter().filter(|r|matches!(r,Err(StoreError::RevisionConflict))).count(),1);
}
#[test] fn unsafe_spelling_and_directory_as_file_fail_closed() {
    for path in ["relative.json","C:relative.json","C:/../file","C:/a/./file","C:/a/NUL.json","C:/a/file:stream","C:/a/file.","C:/a/file ","//server/share/a","\\\\?\\C:\\a"] {assert!(matches!(SafeStorePath::new(path),Err(StoreError::UnsafePath)));}
    let root=fixture();let s=OptimizationSettingsStore::new(&root).unwrap();assert!(matches!(s.snapshot(),Err(StoreError::UnsafePath)));
}
