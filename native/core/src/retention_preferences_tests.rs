//! Configuration-only tests. All fixtures remain under an owned D build root.
use super::*;
use crate::{ffi::*, runtime::Runtime};
use std::{fs, path::PathBuf, ptr, slice};

fn fixture() -> PathBuf {
    let mut entropy = [0u8; 16];
    getrandom::getrandom(&mut entropy).unwrap();
    let suffix: String = entropy.iter().map(|byte| format!("{byte:02x}")).collect();
    let base = PathBuf::from("D:/ValMAXBuild/build/retention-preferences-tests-20261021");
    let directory = base.join(format!("{}-{suffix}", std::process::id()));
    assert!(directory.is_absolute() && directory.starts_with(&base) && directory != base);
    fs::create_dir_all(&directory).unwrap();
    // Keep these small fixtures as proof inputs; no recursive cleanup or profiles.
    directory
}

fn expected(enabled: bool) -> Value {
    json!({"schema":2,"revision":revision(enabled),"enabled":enabled,
        "capabilities":{"capture":false,"lookup":false,"enable":false,"disable":true},
        "runtime":{"ready":false,"applied":false,"enabled":false,"historySyncVerified":false,
            "error":"retention_not_integrated","retainedEvents":null,"retainedMessages":null}})
}
fn expected_runtime(enabled: bool) -> Value {
    let mut value = expected(enabled);
    // Persisted lookup is available even when capture has not been enabled.
    value["capabilities"]["lookup"] = json!(true);
    value
}

#[test]
fn exact_legacy_canonical_and_revisions() {
    assert_eq!(canonical(false), b"{\"enabled\":false,\"schema\":1}\n");
    assert_eq!(canonical(true), b"{\"enabled\":true,\"schema\":1}\n");
    assert_eq!(revision(false), "13928b04fa47313d79c7afd8db1f495abdff8f1b5fcdd622c5ab5101ad9bab7a");
    assert_eq!(revision(true), "4176419678f0a5a37f6b057fea32f61c9688f7f7d7e9bde3196e26afa2dd6149");
    assert_eq!(parse(canonical(false)), Ok(false));
    assert_eq!(parse(canonical(true)), Ok(true));
}

#[test]
fn missing_default_is_off_without_creating_parent_or_file() {
    let root = fixture();
    let path = root.join("not-created").join(FILENAME);
    let store = RetentionPreferencesStore::new(&path).unwrap();
    assert_eq!(store.snapshot().unwrap(), expected(false));
    assert!(!path.parent().unwrap().exists());
    assert_eq!(fs::read_dir(root).unwrap().count(), 0);
}

#[test]
fn existing_true_is_readable_preserves_bytes_and_never_claims_capture() {
    let root = fixture(); let path = root.join(FILENAME);
    let original = b" \r\n{ \"schema\": 1, \"enabled\": true }\r\n";
    fs::write(&path, original).unwrap();
    let store = RetentionPreferencesStore::new(&path).unwrap();
    assert_eq!(store.snapshot().unwrap(), expected(true));
    assert_eq!(fs::read(&path).unwrap(), original);
    assert_eq!(RetentionPreferencesStore::new(&path).unwrap().snapshot().unwrap(), expected(true));
    assert_eq!(fs::read_dir(root).unwrap().count(), 1);
}

#[test]
fn enable_true_is_unavailable_before_any_filesystem_operation() {
    let root = fixture(); let path = root.join("not-created").join(FILENAME);
    let store = RetentionPreferencesStore::new(&path).unwrap();
    assert_eq!(store.replace(&revision(false), &json!(true)), Err("retention_unavailable"));
    assert_eq!(store.replace(&"f".repeat(64), &json!(true)), Err("retention_unavailable"));
    assert!(!path.parent().unwrap().exists());
    let path = root.join(FILENAME); fs::write(&path, b"SYNTHETIC_INVALID_CONFIG").unwrap();
    let store = RetentionPreferencesStore::new(&path).unwrap();
    assert_eq!(store.replace(&revision(false), &json!(true)), Err("retention_unavailable"));
    assert_eq!(fs::read(path).unwrap(), b"SYNTHETIC_INVALID_CONFIG");
}

#[test]
fn enable_requires_explicit_host_gate_and_remains_only_saved_intent() {
    let root = fixture(); let path = root.join(FILENAME);
    let store = RetentionPreferencesStore::new(&path).unwrap();
    assert_eq!(store.replace_with_host(&revision(false), &json!(true), false),
        Err("retention_unavailable"));
    assert!(!path.exists());
    assert_eq!(store.replace_with_host(&revision(false), &json!(true), true).unwrap(), expected(true));
    assert_eq!(fs::read(&path).unwrap(), canonical(true));
    assert_eq!(RetentionPreferencesStore::new(&path).unwrap().snapshot().unwrap(), expected(true));
    assert_eq!(store.replace_with_host(&revision(false), &json!(false), true), Err("revision_conflict"));
}

#[test]
fn disable_valid_cas_writes_exact_false_and_restart_reads_it() {
    let root = fixture(); let path = root.join(FILENAME);
    fs::write(&path, canonical(true)).unwrap();
    let sentinel = root.join("ordinary-sentinel.bin"); fs::write(&sentinel, b"OWNED-SENTINEL").unwrap();
    let store = RetentionPreferencesStore::new(&path).unwrap();
    assert_eq!(store.replace(&revision(true), &json!(false)).unwrap(), expected(false));
    assert_eq!(fs::read(&path).unwrap(), canonical(false));
    assert_eq!(store.replace(&revision(true), &json!(false)), Err("revision_conflict"));
    assert_eq!(fs::read(&path).unwrap(), canonical(false));
    assert_eq!(RetentionPreferencesStore::new(path).unwrap().snapshot().unwrap(), expected(false));
    assert_eq!(fs::read(sentinel).unwrap(), b"OWNED-SENTINEL");
    assert_eq!(fs::read_dir(root).unwrap().count(), 2);
}

#[test]
fn default_false_can_be_explicitly_saved_but_stale_cas_creates_nothing() {
    let root = fixture(); let path = root.join("profile").join(FILENAME);
    let store = RetentionPreferencesStore::new(&path).unwrap();
    assert_eq!(store.replace(&"f".repeat(64), &json!(false)), Err("revision_conflict"));
    assert!(!path.parent().unwrap().exists());
    assert_eq!(store.replace(&revision(false), &json!(false)).unwrap(), expected(false));
    assert_eq!(fs::read(path).unwrap(), canonical(false));
}

#[test]
fn malformed_preferences_fail_closed_without_rewriting_any_bytes() {
    let root = fixture(); let path = root.join(FILENAME);
    let store = RetentionPreferencesStore::new(&path).unwrap();
    let cases: &[&[u8]] = &[
        b"", b"[]", b"null", b"{\"schema\":1}", b"{\"enabled\":false}",
        b"{\"schema\":1,\"enabled\":true,\"enabled\":false}",
        b"{\"schema\":true,\"enabled\":false}", b"{\"schema\":1.0,\"enabled\":false}",
        b"{\"schema\":2,\"enabled\":false}", b"{\"schema\":1,\"enabled\":0}",
        b"{\"schema\":1,\"enabled\":\"false\"}", b"{\"schema\":1,\"enabled\":null}",
        b"{\"schema\":1,\"enabled\":NaN}", b"{\"schema\":1,\"enabled\":false,\"extra\":null}",
        b"{\"schema\":1,\"enabled\":false} trailing", b"\xff"
    ];
    for bytes in cases {
        fs::write(&path, bytes).unwrap();
        assert_eq!(store.snapshot(), Err("invalid_store"));
        assert_eq!(store.replace(&revision(false), &json!(false)), Err("invalid_store"));
        assert_eq!(fs::read(&path).unwrap(), *bytes);
    }
    let excessive = vec![b' '; MAX_STORE_BYTES + 1]; fs::write(&path, &excessive).unwrap();
    assert_eq!(store.snapshot(), Err("invalid_store"));
    assert_eq!(fs::read(path).unwrap(), excessive);
}

#[test]
fn malformed_revision_or_non_boolean_setting_creates_nothing() {
    let root = fixture(); let store = RetentionPreferencesStore::new(root.join(FILENAME)).unwrap();
    for value in [json!(0), json!("false"), json!(null), json!({"enabled":false})] {
        assert_eq!(store.replace(&revision(false), &value), Err("invalid_settings"));
    }
    for value in [String::new(), "a".repeat(63), "A".repeat(64), "g".repeat(64)] {
        assert_eq!(store.replace(&value, &json!(false)), Err("invalid_revision"));
    }
    assert_eq!(fs::read_dir(root).unwrap().count(), 0);
}

#[test]
fn hardlinked_destination_does_not_modify_the_original() {
    let root = fixture(); let source = root.join("original.json"); let path = root.join(FILENAME);
    fs::write(&source, canonical(true)).unwrap(); fs::hard_link(&source, &path).unwrap();
    let store = RetentionPreferencesStore::new(path).unwrap();
    assert_eq!(store.snapshot(), Err("unsafe_path"));
    assert_eq!(store.replace(&revision(true), &json!(false)), Err("unsafe_path"));
    assert_eq!(fs::read(source).unwrap(), canonical(true));
}

#[test]
fn reparse_ancestor_is_rejected_without_following_it_for_read_or_disable() {
    let root = fixture(); let target = root.join("owned-target"); let junction = root.join("owned-junction");
    fs::create_dir(&target).unwrap(); fs::write(target.join(FILENAME), canonical(true)).unwrap();
    assert!(target.is_absolute() && junction.is_absolute() && target.starts_with(&root) && junction.starts_with(&root));
    let powershell = PathBuf::from(std::env::var_os("SystemRoot").unwrap())
        .join("System32/WindowsPowerShell/v1.0/powershell.exe");
    // Fixed native PowerShell command; no path is interpolated into shell code.
    let script = "$ownedRoot=[IO.Path]::GetFullPath($env:VALMAX_RETENTION_FIXTURE_ROOT); \
        New-Item -ItemType Junction -Path ([IO.Path]::Combine($ownedRoot,'owned-junction')) \
        -Target ([IO.Path]::Combine($ownedRoot,'owned-target')) -ErrorAction Stop | Out-Null";
    let output = std::process::Command::new(powershell).env("VALMAX_RETENTION_FIXTURE_ROOT", &root)
        .args(["-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script])
        .output().unwrap();
    assert!(output.status.success(), "owned junction fixture creation failed: {} {}",
        String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
    let store = RetentionPreferencesStore::new(junction.join(FILENAME)).unwrap();
    assert_eq!(store.snapshot(), Err("unsafe_path"));
    assert_eq!(store.replace(&revision(true), &json!(false)), Err("unsafe_path"));
    assert_eq!(store.replace(&revision(true), &json!(true)), Err("retention_unavailable"));
    assert_eq!(fs::read(target.join(FILENAME)).unwrap(), canonical(true));
}

fn empty() -> CoreBuffer { CoreBuffer { data: ptr::null_mut(), len: 0, capacity: 0 } }
unsafe fn output_value(output: &mut CoreBuffer) -> Value {
    let value = serde_json::from_slice(unsafe { slice::from_raw_parts(output.data, output.len) }).unwrap();
    unsafe { valmax_core_buffer_free(output); }
    value
}
struct NativeContext(*mut Runtime);
impl NativeContext {
    fn open(root: &std::path::Path) -> Self {
        let config = serde_json::to_vec(&json!({"profileDirectory":root.join("profile"),
            "runtimeDirectory":root.join("runtime"),"existingClient":true})).unwrap();
        let mut context = ptr::null_mut(); let mut output = empty();
        unsafe {
            assert_eq!(valmax_runtime_open(config.as_ptr(), config.len(), &mut context, &mut output), 0);
            assert_eq!(output_value(&mut output)["ok"], true);
        }
        assert!(!context.is_null()); Self(context)
    }
    fn raw(&self, input: &[u8]) -> Value {
        let mut output = empty();
        unsafe {
            assert_eq!(valmax_runtime_dispatch(self.0, input.as_ptr(), input.len(), &mut output), 0);
            output_value(&mut output)
        }
    }
    fn call(&self, request: Value) -> Value { self.raw(&serde_json::to_vec(&request).unwrap()) }
}
impl Drop for NativeContext { fn drop(&mut self) { unsafe { valmax_runtime_close(&mut self.0); } } }

#[test]
fn real_runtime_ffi_get_disable_restart_and_selected_lookup() {
    let root = fixture();
    {
        let context = NativeContext::open(&root);
        assert_eq!(context.call(json!({"operation":"retention.get"})), json!({"schema":1,"ok":true,"result":expected_runtime(false)}));
        assert_eq!(context.call(json!({"operation":"retention.set","revision":revision(false),"enabled":true}))["error"], "retention_unavailable");
        for value in [json!(null), json!(false), json!({"schema":"untrusted","path":"SYNTHETIC-NOT-A-PATH"})] {
            let result = context.call(json!({"operation":"retention.lookup","value":value}));
            assert_eq!(result, json!({"schema":1,"ok":false,"error":"invalid_request"}));
        }
        let selected = context.call(json!({"operation":"retention.lookup","value":{
            "schema":1,"accountId":"11","chatId":"-22","messageIds":["31"]}}));
        assert_eq!(selected["ok"], true);
        assert_eq!(selected["result"]["deletedIds"], json!([]));
        assert_eq!(selected["result"]["accountId"], "11");
        assert_eq!(selected["result"]["chatId"], "-22");
        assert!(!root.join("profile").exists());
        let runtime = root.join("runtime");
        assert_eq!(fs::read(runtime.join(crate::optimization_store::ACTIVE_FILENAME)).unwrap(),
            crate::optimization_store::Settings::default().canonical());
        assert_eq!(fs::read_dir(runtime).unwrap().count(), 1);
        assert_eq!(context.call(json!({"operation":"retention.set","revision":revision(false),"enabled":false}))["result"], expected_runtime(false));
    }
    let context = NativeContext::open(&root);
    assert_eq!(context.call(json!({"operation":"retention.get"}))["result"], expected_runtime(false));
    assert_eq!(fs::read(root.join("profile").join(FILENAME)).unwrap(), canonical(false));
    assert_eq!(fs::read_dir(root.join("profile")).unwrap().count(), 1);
}

#[test]
fn real_runtime_ffi_reads_true_and_lookup_survives_corrupt_preferences() {
    let root = fixture(); fs::create_dir(root.join("profile")).unwrap();
    let path = root.join("profile").join(FILENAME); fs::write(&path, canonical(true)).unwrap();
    let context = NativeContext::open(&root);
    assert_eq!(context.call(json!({"operation":"retention.get"}))["result"], expected_runtime(true));
    let selected = json!({"operation":"retention.lookup","value":{
        "schema":1,"accountId":"11","chatId":"-22","messageIds":["31"]}});
    assert_eq!(context.call(selected.clone())["result"]["deletedIds"], json!([]));
    fs::write(&path, b"SYNTHETIC_CORRUPT_PREFERENCE").unwrap();
    assert_eq!(context.call(json!({"operation":"retention.lookup","value":null}))["error"], "invalid_request");
    let lookup = context.call(selected);
    assert_eq!(lookup["ok"], true);
    assert_eq!(lookup["result"]["deletedIds"], json!([]));
    assert_eq!(lookup["result"]["enabled"], false);
    assert_eq!(context.call(json!({"operation":"retention.get"}))["error"], "invalid_store");
    assert_eq!(context.call(json!({"operation":"retention.set","revision":revision(true),"enabled":false}))["error"], "invalid_store");
    assert_eq!(fs::read(path).unwrap(), b"SYNTHETIC_CORRUPT_PREFERENCE");
}

#[test]
fn real_runtime_ffi_rejects_extra_keys_types_and_duplicate_operations() {
    let root = fixture(); let context = NativeContext::open(&root);
    for request in [json!({"operation":"retention.get","extra":null}),
        json!({"operation":"retention.lookup"}),
        json!({"operation":"retention.lookup","value":null,"extra":true}),
        json!({"operation":"retention.set","revision":revision(false),"enabled":false,"extra":null})] {
        assert_eq!(context.call(request)["error"], "invalid_request");
    }
    assert_eq!(context.call(json!({"operation":"retention.set","revision":0,"enabled":false}))["error"], "invalid_revision");
    assert_eq!(context.call(json!({"operation":"retention.set","revision":revision(false),"enabled":0}))["error"], "invalid_settings");
    assert_eq!(context.raw(br#"{"operation":"retention.get","operation":"retention.get"}"#)["error"], "invalid_request");
    assert!(!root.join("profile").exists());
    let runtime = root.join("runtime");
    assert_eq!(fs::read(runtime.join(crate::optimization_store::ACTIVE_FILENAME)).unwrap(),
        crate::optimization_store::Settings::default().canonical());
    assert_eq!(fs::read_dir(runtime).unwrap().count(), 1);
}
