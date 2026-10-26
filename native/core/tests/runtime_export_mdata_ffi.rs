#![cfg(windows)]
//! Actual exported C ABI -> Runtime routes. Synthetic selected files only;
//! no MAX process, profile discovery, WinCred, network or native history proof.

use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Cursor, Read},
    path::{Path, PathBuf},
    ptr, slice,
    sync::OnceLock,
};
use valmax_core::{
    ffi::*,
    mdata_archive::{self as archive, ArchiveKind, MDataError, MDataPolicy},
    runtime::Runtime,
    session_archive::ArchivePolicy,
};

const BASE: &str = "C:/ValMAXBuild/fixtures/runtime-export-mdata";
fn fixture_base() -> PathBuf {
    std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(|root| PathBuf::from(root).join("runtime-export-mdata"))
        .unwrap_or_else(|| PathBuf::from(BASE))
}
const PASSWORD: &str = "synthetic-runtime-mdata-password";
const OPAQUE: &[u8] = b"SYNTHETIC-OPAQUE-CREDENTIAL";

fn buffer() -> CoreBuffer {
    CoreBuffer {
        data: ptr::null_mut(),
        len: 0,
        capacity: 0,
    }
}
unsafe fn take(output: &mut CoreBuffer) -> Value {
    assert!(!output.data.is_null() && output.len > 0 && output.len <= 4 * 1024 * 1024);
    let result =
        serde_json::from_slice(unsafe { slice::from_raw_parts(output.data, output.len) }).unwrap();
    unsafe {
        valmax_core_buffer_free(output);
    }
    assert!(output.data.is_null() && output.len == 0 && output.capacity == 0);
    result
}
fn exact(value: &Value, expected: &[&str]) {
    let object = value.as_object().unwrap();
    assert_eq!(object.len(), expected.len());
    for field in expected {
        assert!(object.contains_key(*field), "missing public field {field}");
    }
}
fn success(value: Value) -> Value {
    exact(&value, &["schema", "ok", "result"]);
    assert_eq!(value["schema"], 1);
    assert_eq!(value["ok"], true);
    value["result"].clone()
}
fn error(value: Value, code: &str) {
    exact(&value, &["schema", "ok", "error"]);
    assert_eq!(value["schema"], 1);
    assert_eq!(value["ok"], false);
    assert_eq!(value["error"], code);
}
fn file_url(path: &Path) -> String {
    let mut out = String::from("file:///");
    for byte in path.to_str().unwrap().replace('\\', "/").bytes() {
        if byte.is_ascii_alphanumeric() || b"-._~/:".contains(&byte) {
            out.push(byte as char);
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

struct Fixture {
    root: PathBuf,
    runtime: *mut Runtime,
}
impl Fixture {
    fn new() -> Self {
        let mut random = [0u8; 16];
        getrandom::getrandom(&mut random).unwrap();
        let name: String = random.iter().map(|b| format!("{b:02x}")).collect();
        let root = fixture_base().join(name);
        fs::create_dir_all(&root).unwrap();
        let config = serde_json::to_vec(&json!({"profileDirectory":root.join("profile"),
            "runtimeDirectory":root.join("runtime"),"existingClient":true}))
        .unwrap();
        let mut fixture = Self {
            root,
            runtime: ptr::null_mut(),
        };
        let mut output = buffer();
        unsafe {
            assert_eq!(
                valmax_runtime_open(
                    config.as_ptr(),
                    config.len(),
                    &mut fixture.runtime,
                    &mut output
                ),
                0
            );
            let opened = success(take(&mut output));
            assert_eq!(opened["abi"], 1);
            for feature in ["mdata_preview", "mdata_export", "chat_export_coordinator"] {
                assert!(
                    opened["features"]
                        .as_array()
                        .unwrap()
                        .contains(&json!(feature))
                );
            }
        }
        assert!(!fixture.runtime.is_null());
        assert!(!fixture.root.join("profile").exists());
        let runtime_directory = fixture.root.join("runtime");
        let active = runtime_directory.join(valmax_core::optimization_store::ACTIVE_FILENAME);
        let snapshot: Value = serde_json::from_slice(&fs::read(active).unwrap()).unwrap();
        assert_eq!(snapshot, json!({"schema":1,"settings":valmax_core::optimization_store::Settings::default().value()}));
        assert_eq!(names(&fixture.root), vec!["runtime".to_owned()]);
        assert_eq!(names(&runtime_directory), vec![valmax_core::optimization_store::ACTIVE_FILENAME.to_owned()]);
        fixture
    }
    fn raw(&self, input: &[u8]) -> Value {
        let mut output = buffer();
        unsafe {
            assert_eq!(
                valmax_runtime_dispatch(self.runtime, input.as_ptr(), input.len(), &mut output),
                0
            );
            take(&mut output)
        }
    }
    fn call(&self, value: Value) -> Value {
        self.raw(&serde_json::to_vec(&value).unwrap())
    }
    fn export(&self, action: &str, value: Value) -> Value {
        self.call(json!({"operation":format!("export.{action}"),"value":value}))
    }
    fn selected_directory(&self) -> PathBuf {
        let selected = self.root.join("selected-export");
        fs::create_dir(&selected).unwrap();
        selected
    }
    fn close(&mut self) {
        unsafe {
            valmax_runtime_close(&mut self.runtime);
            valmax_runtime_close(&mut self.runtime);
        }
        assert!(self.runtime.is_null());
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.close();
        let base = fs::canonicalize(fixture_base()).unwrap();
        let root = fs::canonicalize(&self.root).unwrap();
        assert!(root.starts_with(&base) && root != base && root.parent() == Some(base.as_path()));
        let own_name = root.file_name().unwrap().to_str().unwrap();
        assert!(own_name.len() == 32 && own_name.bytes().all(|b| b.is_ascii_hexdigit()));
        fs::remove_dir_all(root).unwrap();
    }
}

fn encrypted() -> &'static [u8] {
    static BYTES: OnceLock<Vec<u8>> = OnceLock::new();
    BYTES.get_or_init(|| {
        let auth = br#"{"first_name":"Alice","last_name":"Example","short_name":"AE","private":"SYNTHETIC-PRIVATE-AUTH"}"#;
        let manifest = json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
            "credentials":[{"name":"access_token_9007199254740993@MXMOD","blob":STANDARD.encode(OPAQUE)}],
            "files":[{"path":"auth_9007199254740993","size":auth.len()}]});
        let default = ArchivePolicy::default();
        let path = |p: &str| p == "auth_9007199254740993";
        let credential = |n: &str| n == "access_token_9007199254740993@MXMOD";
        let policy = MDataPolicy { credential_policy: &default, file_path_validator: &path,
            credential_name_validator: Some(&credential) };
        let mut factory = |_: &str| -> Result<Box<dyn Read>, MDataError> { Ok(Box::new(Cursor::new(auth.to_vec()))) };
        let mut output = Vec::new();
        archive::encrypt_stream(&manifest, PASSWORD, &mut factory, &mut output, &policy, ArchiveKind::Login).unwrap();
        output
    })
}
fn start(directory: &Path) -> Value {
    json!({"schema":1,"accountId":"9007199254740993",
        "chat":{"id":"-42","title":"Synthetic selected chat","timezoneOffsetMinutes":180},
        "directory":file_url(directory)})
}
fn auth(started: &Value) -> Value {
    json!({"schema":1,"jobId":started["jobId"],"jobToken":started["jobToken"]})
}
fn page(started: &Value, sequence: u32) -> Value {
    let mut body = auth(started);
    body["sequence"] = json!(sequence);
    body["messages"] = json!([{"id":"2","author":{"id":"7","name":"Synthetic sender"},
        "timestampMs":1,"text":"Synthetic export text <tag>","outgoing":false,"reply":null,"attachments":[]}]);
    body
}
fn finish(started: &Value) -> Value {
    let mut body = auth(started);
    body["completion"] =
        json!({"historyComplete":true,"stopReason":"end_of_history","pagesFetched":999});
    body
}
fn names(directory: &Path) -> Vec<String> {
    let mut entries: Vec<_> = fs::read_dir(directory)
        .unwrap()
        .map(|e| e.unwrap().file_name().into_string().unwrap())
        .collect();
    entries.sort();
    entries
}

#[test]
fn mdata_runtime_ffi_authenticates_before_projecting_only_public_metadata() {
    let f = Fixture::new();
    let archive = f.root.join("Архив # 100%.MDATA");
    fs::write(&archive, encrypted()).unwrap();
    let url = file_url(&archive);
    let result =
        success(f.call(json!({"operation":"mdata.inspect","file":url,"password":PASSWORD})));
    exact(
        &result,
        &["schema", "kind", "profiles", "accounts", "fingerprint"],
    );
    assert_eq!(result["schema"], 1);
    assert_eq!(result["kind"], "login");
    assert_eq!(result["profiles"], 1);
    assert_eq!(
        result["fingerprint"],
        format!("{:x}", Sha256::digest(encrypted()))
    );
    assert_eq!(
        result["accounts"],
        json!([{"accountId":"9007199254740993","displayName":"Alice Example","shortName":"AE"}])
    );
    let public = result.to_string();
    for forbidden in [
        PASSWORD,
        "SYNTHETIC-PRIVATE-AUTH",
        "SYNTHETIC-OPAQUE-CREDENTIAL",
        "access_token",
        "auth_",
        "file:///",
    ] {
        assert!(!public.contains(forbidden));
    }
    assert!(!public.contains(&STANDARD.encode(OPAQUE)));
    error(
        f.call(
            json!({"operation":"mdata.inspect","file":url,"password":"wrong synthetic password"}),
        ),
        "authentication_failed",
    );
    error(
        f.call(json!({"operation":"mdata.inspect","file":url,"password":true})),
        "invalid_request",
    );
    error(
        f.call(
            json!({"operation":"mdata.inspect","file":url,"password":PASSWORD,"path":"ignored"}),
        ),
        "invalid_request",
    );
    error(
        f.call(json!({"operation":"mdata.inspect","file":url})),
        "invalid_request",
    );
    error(f.raw(br#"{"operation":"mdata.inspect","operation":"mdata.inspect","file":"x","password":"synthetic password"}"#), "invalid_request");
    error(f.call(json!({"operation":"mdata.inspect","file":file_url(&f.root.join("missing.mdata")),"password":PASSWORD})), "io_error");
    for operation in ["mdata.import", "mdata.import-confirmed"] {
        error(
            f.call(json!({"operation":operation,"file":url,"password":PASSWORD})),
            "unsupported_operation",
        );
    }
    // Export validation must fail before a production credential API is called.
    error(
        f.call(json!({"operation":"mdata.export","file":url,"password":"short"})),
        "invalid_password",
    );
    error(
        f.call(json!({"operation":"mdata.export-selected","file":url,"password":PASSWORD})),
        "invalid_request",
    );
    error(f.call(json!({"operation":"mdata.export-selected","file":url,"password":PASSWORD,"accounts":[]})), "invalid_request");
    error(
        f.call(json!({"operation":"mdata.export","file":url,"password":PASSWORD})),
        "unsafe_path",
    );
    assert_eq!(fs::read(&archive).unwrap(), encrypted());
    let mut expected = vec!["runtime".to_owned(), archive.file_name().unwrap().to_string_lossy().into_owned()];
    expected.sort();
    assert_eq!(names(&f.root), expected);
}

#[test]
fn export_runtime_ffi_scopes_job_tokens_and_cannot_claim_native_history() {
    let f = Fixture::new();
    let directory = f.selected_directory();
    let body = start(&directory);
    error(
        f.call(json!({"operation":"export.start","value":body,"extra":0})),
        "invalid_request",
    );
    error(
        f.call(json!({"operation":"export.start"})),
        "invalid_request",
    );
    let started = success(f.export("start", body.clone()));
    assert_eq!(started["state"], "exporting");
    assert_eq!(started["jobId"].as_str().unwrap().len(), 32);
    assert_eq!(started["jobToken"].as_str().unwrap().len(), 64);
    assert_eq!(started["limits"]["maxPageMessages"], 200);
    error(f.export("start", body), "busy");
    let mut wrong = auth(&started);
    let token = wrong["jobToken"].as_str().unwrap();
    wrong["jobToken"] = json!(format!(
        "{}{}",
        if token.starts_with('0') { "1" } else { "0" },
        &token[1..]
    ));
    error(f.export("status", wrong), "not_found");
    let history = success(f.export("history", auth(&started)));
    assert_eq!(
        history["history"],
        json!({"ready":false,"backward":false,"forward":false})
    );
    for operation in ["export.bind_history", "export.observe_history"] {
        error(
            f.call(json!({"operation":operation,"value":auth(&started)})),
            "unsupported_operation",
        );
    }
    let mut forged = auth(&started);
    forged["source"] = json!(42);
    forged["historyComplete"] = json!(true);
    error(f.export("history", forged), "invalid_request");
    let accepted = success(f.export("page", page(&started, 0)));
    assert_eq!(success(f.export("page", page(&started, 0))), accepted);
    let mut extra = page(&started, 1);
    extra["nativeHistory"] = json!({"ready":true});
    error(f.export("page", extra), "invalid_request");
    let done = success(f.export("finish", finish(&started)));
    assert_eq!(done["state"], "partial");
    assert_eq!(done["counts"]["messages"], 1);
    assert!(
        done["htmlFileURL"]
            .as_str()
            .unwrap()
            .starts_with("file:///")
    );
    assert!(done.get("jobToken").is_none());
    assert_eq!(success(f.export("finish", finish(&started))), done);
    let published = names(&directory);
    assert_eq!(published.len(), 1);
    assert!(published[0].starts_with("ValMAX-Chat-"));
    let output = directory.join(&published[0]);
    let manifest: Value =
        serde_json::from_slice(&fs::read(output.join("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "unsupported_history");
    assert_eq!(manifest["pagesFetched"], 1);
    let html = fs::read_to_string(output.join("index.html")).unwrap();
    assert!(html.contains("Synthetic export text &lt;tag&gt;"));
    let next = success(f.export("start", start(&directory)));
    assert_ne!(next["jobId"], started["jobId"]);
    assert_ne!(next["jobToken"], started["jobToken"]);
    error(f.export("status", auth(&started)), "not_found");
    assert_eq!(
        success(f.export("cancel", auth(&next)))["state"],
        "cancelled"
    );
    assert_eq!(names(&directory), published);
    assert!(!f.root.join("profile").exists());
}

#[test]
fn host_cancellation_closes_admission_and_finishes_active_job_cleanup() {
    let mut f = Fixture::new();
    let directory = f.selected_directory();
    let started = success(f.export("start", start(&directory)));
    success(f.export("page", page(&started, 0)));
    assert!(
        names(&directory)
            .iter()
            .any(|name| name.starts_with(".valmax-index-"))
    );
    unsafe {
        assert_eq!(valmax_runtime_cancel_pending(ptr::null()), 1);
        assert_eq!(valmax_runtime_cancel_pending(f.runtime), 0);
        assert_eq!(valmax_runtime_cancel_pending(f.runtime), 0);
    }
    error(f.export("start", start(&directory)), "cancelled");
    error(f.call(json!({"operation":"mdata.export","file":file_url(&f.root.join("closed.mdata")),"password":PASSWORD})), "cancelled");
    // cancel_pending is deliberately a signal, not a blocking cleanup ACK.
    // The next worker operation observes the flag and performs owned cleanup.
    let status = success(f.export("status", auth(&started)));
    assert_eq!(status["state"], "cancelled");
    assert_eq!(status["error"], "cancelled");
    assert!(names(&directory).is_empty());
    error(f.export("start", start(&directory)), "cancelled");
    f.close();
    let bytes = br#"{"operation":"version"}"#;
    let mut output = buffer();
    unsafe {
        assert_eq!(valmax_runtime_cancel_pending(f.runtime), 1);
        assert_eq!(
            valmax_runtime_dispatch(f.runtime, bytes.as_ptr(), bytes.len(), &mut output),
            1
        );
        valmax_runtime_close(ptr::null_mut());
    }
    assert!(output.data.is_null() && output.len == 0 && output.capacity == 0);
    assert!(names(&directory).is_empty());
}

#[test]
fn runtime_close_alone_releases_owned_active_export_files() {
    let mut f = Fixture::new();
    let directory = f.selected_directory();
    let started = success(f.export("start", start(&directory)));
    success(f.export("page", page(&started, 0)));
    assert!(!names(&directory).is_empty());
    // No other worker retains this handle; close is allowed and owns cleanup.
    f.close();
    assert!(names(&directory).is_empty());
    assert!(!f.root.join("profile").exists());
}
