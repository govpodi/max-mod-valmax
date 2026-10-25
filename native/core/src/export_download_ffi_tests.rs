use super::*;
use serde_json::{Value, json};
use std::{
    fs,
    path::{Path, PathBuf},
};
struct Fixture {
    root: PathBuf,
    runtime: Option<Runtime>,
    start: Value,
}
impl Fixture {
    fn new() -> Self {
        let base = Path::new("D:/ValMAXBuild/fixtures/export-download-ffi");
        fs::create_dir_all(base).unwrap();
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let name = nonce.iter().map(|b| format!("{b:02x}")).collect::<String>();
        let root = base.join(name);
        fs::create_dir(&root).unwrap();
        let runtime=Runtime::open(&json!({"profileDirectory":root.join("profile"),"runtimeDirectory":root.join("runtime"),"existingClient":true})).unwrap();
        let start=runtime.call(&json!({"operation":"export.start","value":{"schema":1,"accountId":"99","chat":{"id":"42","title":"Synthetic download","timezoneOffsetMinutes":0},"directory":format!("file:///{}",root.to_string_lossy().replace('\\',"/"))}})).unwrap();
        let row = json!({"id":"1","author":{"id":"123","name":"Synthetic"},"timestampMs":1,"text":"Synthetic only","outgoing":false,"reply":null,
            "attachments":[{"id":"file","type":"image","name":"sample.png","mime":"image/png","status":"pending","reason":null}]});
        let fixture = Self {
            root,
            runtime: Some(runtime),
            start,
        };
        fixture
            .command("export.page", json!({"sequence":0,"messages":[row]}))
            .unwrap();
        fixture
    }
    fn command(&self, operation: &str, extra: Value) -> std::result::Result<Value, &'static str> {
        let mut value =
            json!({"schema":1,"jobId":self.start["jobId"],"jobToken":self.start["jobToken"]});
        for (key, item) in extra.as_object().unwrap() {
            value[key] = item.clone();
        }
        self.runtime
            .as_ref()
            .unwrap()
            .call(&json!({"operation":operation,"value":value}))
    }
    fn request(&self) -> Value {
        json!({"jobId":self.start["jobId"],"jobToken":self.start["jobToken"],"messageId":"1","attachmentId":"file"})
    }
    fn lease(&self) {
        self.command(
            "export.media-start",
            json!({"messageId":"1","attachmentId":"file"}),
        )
        .unwrap();
    }
    fn open(&self, request: &Value, out: &mut *mut NativeDownload) -> u32 {
        let bytes = serde_json::to_vec(request).unwrap();
        unsafe {
            valmax_export_download_open(
                self.runtime.as_ref().unwrap(),
                bytes.as_ptr(),
                bytes.len(),
                out,
            )
        }
    }
    fn finish_media(&self, status: &str, reason: Option<&str>) -> Value {
        self.command(
            "export.media",
            json!({"messageId":"1","attachmentId":"file","status":status,"reason":reason}),
        )
        .unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        if self.runtime.is_some() {
            self.command("export.cancel", json!({})).ok();
        }
        self.runtime.take();
        assert_eq!(
            self.root.parent(),
            Some(Path::new("D:/ValMAXBuild/fixtures/export-download-ffi"))
        );
        assert_eq!(self.root.file_name().unwrap().to_str().unwrap().len(), 32);
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
fn open_requires_existing_selected_lease_and_accepts_no_path() {
    let f = Fixture::new();
    let mut sink = ptr::null_mut();
    assert_eq!(f.open(&f.request(), &mut sink), 2);
    assert!(sink.is_null());
    f.lease();
    let mut request = f.request();
    request["path"] = json!("C:/unselected");
    assert_eq!(f.open(&request, &mut sink), 1);
    request = f.request();
    request["jobToken"] = json!("0".repeat(64));
    assert_eq!(f.open(&request, &mut sink), 2);
    assert_eq!(f.open(&f.request(), &mut sink), 0);
    let mut duplicate = ptr::null_mut();
    assert_ne!(f.open(&f.request(), &mut duplicate), 0);
    assert!(duplicate.is_null());
    unsafe {
        assert_eq!(valmax_export_download_close(&mut sink), 0);
        assert_eq!(valmax_export_download_close(&mut sink), 0);
    }
    f.finish_media("error", Some("download_failed"));
}
#[test]
fn constant_memory_native_sink_closes_before_media_terminal_import() {
    let f = Fixture::new();
    f.lease();
    let mut sink = ptr::null_mut();
    assert_eq!(f.open(&f.request(), &mut sink), 0);
    let head = b"\x89PNG\r\n\x1a\n";
    let tail = b"synthetic-native-network-bytes";
    unsafe {
        assert_eq!(
            valmax_export_download_write(sink, head.as_ptr(), head.len()),
            0
        );
        assert_eq!(
            valmax_export_download_write(sink, tail.as_ptr(), tail.len()),
            0
        );
        assert_eq!(valmax_export_download_close(&mut sink), 0);
    }
    assert!(sink.is_null());
    let receipt = f.finish_media("ready", None);
    assert_eq!(receipt["counts"]["ready"], 1);
    assert_eq!(receipt["downloadsPending"], false);
    assert_eq!(receipt["counts"]["mediaBytes"], head.len() + tail.len());
}
#[test]
fn cancellation_is_observed_by_writer_without_deleting_live_lease() {
    let f = Fixture::new();
    f.lease();
    let mut sink = ptr::null_mut();
    assert_eq!(f.open(&f.request(), &mut sink), 0);
    let bytes = b"synthetic";
    let cancelled = f.command("export.cancel", json!({})).unwrap();
    assert_eq!(cancelled["downloadsPending"], true);
    unsafe {
        assert_eq!(
            valmax_export_download_write(sink, bytes.as_ptr(), bytes.len()),
            4
        );
        assert_eq!(valmax_export_download_close(&mut sink), 0);
    }
    let done = f.finish_media("cancelled", Some("cancelled"));
    assert_eq!(done["state"], "cancelled");
    assert_eq!(done["downloadsPending"], false);
}
#[test]
fn strict_abi_bounds_and_runtime_shutdown_keep_sink_lifetime_safe() {
    let mut f = Fixture::new();
    f.lease();
    let mut sink = ptr::null_mut();
    assert_eq!(f.open(&f.request(), &mut sink), 0);
    let bytes = b"synthetic";
    unsafe {
        assert_eq!(valmax_export_download_write(sink, bytes.as_ptr(), 0), 1);
        assert_eq!(
            valmax_export_download_write(sink, bytes.as_ptr(), 1024 * 1024 + 1),
            1
        );
        assert_eq!(valmax_export_download_write(sink, ptr::null(), 1), 1);
    }
    f.runtime.take(); // ExportService Drop signals cancellation; sink owns Arc<Job>.
    unsafe {
        assert_eq!(
            valmax_export_download_write(sink, bytes.as_ptr(), bytes.len()),
            4
        );
        assert_eq!(valmax_export_download_close(&mut sink), 0);
        assert!(sink.is_null());
    }
}
