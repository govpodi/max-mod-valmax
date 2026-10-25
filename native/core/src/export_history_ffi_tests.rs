use super::*;
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
    ptr,
};
struct Fixture {
    root: PathBuf,
    runtime: Runtime,
    start: Value,
}
impl Fixture {
    fn new() -> Self {
        let base = Path::new("D:/ValMAXBuild/fixtures/export-history-ffi");
        fs::create_dir_all(base).unwrap();
        let mut bytes = [0u8; 16];
        getrandom::getrandom(&mut bytes).unwrap();
        let root = base.join(bytes.iter().map(|b| format!("{b:02x}")).collect::<String>());
        fs::create_dir(&root).unwrap();
        let runtime=Runtime::open(&json!({"profileDirectory":root.join("profile"),"runtimeDirectory":root.join("runtime"),"existingClient":true})).unwrap();
        let start=runtime.call(&json!({"operation":"export.start","value":{"schema":1,"accountId":"99","chat":{"id":"42","title":"Synthetic EOF","timezoneOffsetMinutes":0},"directory":format!("file:///{}",root.to_string_lossy().replace('\\',"/"))}})).unwrap();
        Self {
            root,
            runtime,
            start,
        }
    }
    fn binding(&self) -> HistoryBinding {
        let mut out = HistoryBinding {
            size: 112,
            version: 1,
            source: 17,
            job_id: [0; 32],
            job_token: [0; 64],
        };
        out.job_id
            .copy_from_slice(self.start["jobId"].as_str().unwrap().as_bytes());
        out.job_token
            .copy_from_slice(self.start["jobToken"].as_str().unwrap().as_bytes());
        out
    }
    fn command(&self, operation: &str, extra: Value) -> std::result::Result<Value, &'static str> {
        let mut value =
            json!({"schema":1,"jobId":self.start["jobId"],"jobToken":self.start["jobToken"]});
        for (key, item) in extra.as_object().unwrap() {
            value[key] = item.clone();
        }
        self.runtime
            .call(&json!({"operation":operation,"value":value}))
    }
    fn observation(&self) -> HistoryObservation {
        HistoryObservation {
            binding: self.binding(),
            direction: 1,
            count: 1,
            pending_done: 1,
            more_backward: 0,
            more_forward: 0,
            reserved: 0,
        }
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.command("export.cancel", json!({})).ok();
        assert_eq!(
            self.root.parent(),
            Some(Path::new("D:/ValMAXBuild/fixtures/export-history-ffi"))
        );
        assert_eq!(self.root.file_name().unwrap().to_str().unwrap().len(), 32);
        // Runtime remains alive but cancellation has closed its own files.
        assert!(
            !fs::symlink_metadata(&self.root)
                .unwrap()
                .file_type()
                .is_symlink()
        );
        fs::remove_dir_all(&self.root).unwrap();
    }
}
#[test]
fn null_alignment_and_versioned_metadata_rejected() {
    let f = Fixture::new();
    let mut value = f.binding();
    unsafe {
        assert_eq!(valmax_runtime_export_history_bind(ptr::null(), &value), 1);
        assert_eq!(
            valmax_runtime_export_history_bind(&f.runtime, ptr::null()),
            1
        );
        let raw = (&value as *const HistoryBinding).cast::<u8>();
        assert_eq!(
            valmax_runtime_export_history_bind(&f.runtime, raw.add(1).cast()),
            1
        );
        for size in [0, 111, 113] {
            value.size = size;
            assert_eq!(valmax_runtime_export_history_bind(&f.runtime, &value), 1);
        }
        value = f.binding();
        value.version = 2;
        assert_eq!(valmax_runtime_export_history_bind(&f.runtime, &value), 1);
        value = f.binding();
        value.source = 0;
        assert_eq!(valmax_runtime_export_history_bind(&f.runtime, &value), 1);
        value = f.binding();
        value.job_id[0] = b'A';
        assert_eq!(valmax_runtime_export_history_bind(&f.runtime, &value), 1);
        value = f.binding();
        value.job_token[63] = 0;
        assert_eq!(valmax_runtime_export_history_bind(&f.runtime, &value), 1);
    }
}
#[test]
fn exact_host_binding_and_terminal_observation_are_required() {
    let f = Fixture::new();
    let mut observation = f.observation();
    unsafe {
        assert_eq!(
            valmax_runtime_export_history_observe(&f.runtime, &observation),
            2
        );
        assert_eq!(
            valmax_runtime_export_history_bind(&f.runtime, &f.binding()),
            0
        );
        observation.binding.source += 1;
        assert_eq!(
            valmax_runtime_export_history_observe(&f.runtime, &observation),
            2
        );
        observation = f.observation();
        observation.pending_done = 0;
        assert_eq!(
            valmax_runtime_export_history_observe(&f.runtime, &observation),
            0
        );
        assert_eq!(
            f.command("export.history", json!({})).unwrap()["history"],
            json!({"ready":true,"backward":false,"forward":false})
        );
        observation.pending_done = 1;
        assert_eq!(
            valmax_runtime_export_history_observe(&f.runtime, &observation),
            0
        );
        assert_eq!(
            f.command("export.history", json!({})).unwrap()["history"],
            json!({"ready":true,"backward":true,"forward":true})
        );
    }
    assert_eq!(
        f.command("export.observe-history", json!({"source":17})),
        Err("unsupported_operation")
    );
}
#[test]
fn malformed_observation_cannot_grant_completeness() {
    let f = Fixture::new();
    unsafe {
        assert_eq!(
            valmax_runtime_export_history_bind(&f.runtime, &f.binding()),
            0
        );
    }
    for field in [
        "direction",
        "count",
        "pending",
        "backward",
        "forward",
        "reserved",
    ] {
        let mut value = f.observation();
        match field {
            "direction" => value.direction = 3,
            "count" => value.count = 100001,
            "pending" => value.pending_done = 2,
            "backward" => value.more_backward = 2,
            "forward" => value.more_forward = 2,
            _ => value.reserved = 1,
        }
        unsafe {
            assert_eq!(valmax_runtime_export_history_observe(&f.runtime, &value), 1);
        }
    }
    assert_eq!(
        f.command("export.history", json!({})).unwrap()["history"]["backward"],
        false
    );
}
#[test]
fn invalidation_is_exact_and_revokes_active_evidence() {
    let f = Fixture::new();
    unsafe {
        assert_eq!(
            valmax_runtime_export_history_bind(&f.runtime, &f.binding()),
            0
        );
        assert_eq!(
            valmax_runtime_export_history_observe(&f.runtime, &f.observation()),
            0
        );
        let mut wrong = f.binding();
        wrong.source += 1;
        assert_eq!(
            valmax_runtime_export_history_invalidate(&f.runtime, &wrong),
            2
        );
        assert_eq!(
            f.command("export.history", json!({})).unwrap()["history"]["ready"],
            true
        );
        assert_eq!(
            valmax_runtime_export_history_invalidate(&f.runtime, &f.binding()),
            0
        );
        assert_eq!(
            f.command("export.history", json!({})).unwrap()["history"],
            json!({"ready":false,"backward":false,"forward":false})
        );
        assert_eq!(
            f.command("export.status", json!({})).unwrap()["state"],
            "cancelled"
        );
    }
}
