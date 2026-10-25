use super::*;
use std::{
    fs,
    fs::OpenOptions,
    io::Write,
    os::windows::fs::OpenOptionsExt,
    path::{Path, PathBuf},
};

struct Fixture {
    root: PathBuf,
    service: ExportService,
    start: Value,
}
impl Fixture {
    fn new() -> Self {
        let base = Path::new("D:/ValMAXBuild/fixtures/export-native-media");
        fs::create_dir_all(base).unwrap();
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let root = base.join(encode_hex(&nonce));
        fs::create_dir(&root).unwrap();
        let service = ExportService::default();
        let request = json!({"schema":1,"accountId":"99","chat":{"id":"42","title":"Synthetic native media","timezoneOffsetMinutes":0},
            "directory":format!("file:///{}",root.to_string_lossy().replace('\\',"/"))});
        let start = service.execute("start", &request).unwrap();
        let row = json!({"id":"1","author":{"id":"123","name":"Synthetic"},"timestampMs":1,"text":"Synthetic only","outgoing":false,"reply":null,
            "attachments":[{"id":"file","name":"synthetic.png","mime":"image/png","type":"image","status":"pending","reason":null}]});
        let page = json!({"schema":1,"jobId":start["jobId"],"jobToken":start["jobToken"],"sequence":0,"messages":[row]});
        service.execute("page", &page).unwrap();
        Self {
            root,
            service,
            start,
        }
    }
    fn body(&self) -> Value {
        json!({"schema":1,"jobId":self.start["jobId"],"jobToken":self.start["jobToken"]})
    }
    fn media(&self) -> Value {
        let mut value = self.body();
        value["messageId"] = json!("1");
        value["attachmentId"] = json!("file");
        value
    }
    fn lease(&self) -> PathBuf {
        let value = self.service.execute("media-start", &self.media()).unwrap();
        let url = value["nativePathFileURL"].as_str().unwrap();
        assert!(url.starts_with("file:///D:/ValMAXBuild/fixtures/export-native-media/"));
        let path = PathBuf::from(url.strip_prefix("file:///").unwrap());
        assert_eq!(path.parent().unwrap().parent().unwrap(), self.root);
        assert!(
            path.parent()
                .unwrap()
                .file_name()
                .unwrap()
                .to_str()
                .unwrap()
                .starts_with(".valmax-download-")
        );
        path
    }
    fn finish_media(&self, status: &str, reason: Option<&str>) -> Result<Value> {
        let mut value = self.media();
        value["status"] = json!(status);
        value["reason"] = json!(reason);
        self.service.execute("media", &value)
    }
    fn publish(&self) -> Value {
        let mut body = self.body();
        body["completion"] =
            json!({"historyComplete":false,"stopReason":"unsupported_history","pagesFetched":1});
        self.service.execute("finish", &body).unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.service.execute("cancel", &self.body()).ok();
        // Drop the service's pinned handles before removing this exact random
        // synthetic root. Unknown real profiles/paths never enter this fixture.
        self.service = ExportService::default();
        assert_eq!(
            self.root.parent(),
            Some(Path::new("D:/ValMAXBuild/fixtures/export-native-media"))
        );
        assert!(hex::<16>(self.root.file_name().unwrap().to_str().unwrap()).is_some());
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
fn sdk_file_is_imported_to_owned_asset_and_lease_removed() {
    let f = Fixture::new();
    let path = f.lease();
    assert_eq!(f.lease(), path); // admission retry returns the exact same lease.
    let bytes = b"\x89PNG\r\n\x1a\nsynthetic-only";
    fs::write(&path, bytes).unwrap();
    let receipt = f.finish_media("ready", None).unwrap();
    assert_eq!(receipt["counts"]["ready"], 1);
    assert_eq!(receipt["downloadsPending"], false);
    assert!(!path.parent().unwrap().exists());
    let done = f.publish();
    assert_eq!(done["state"], "partial");
    let html = PathBuf::from(done["htmlPath"].as_str().unwrap());
    let media = fs::read_dir(html.parent().unwrap().join("media"))
        .unwrap()
        .next()
        .unwrap()
        .unwrap()
        .path();
    assert_eq!(fs::read(media).unwrap(), bytes);
    assert!(
        !fs::read_to_string(html)
            .unwrap()
            .contains("valmax-download")
    );
}

#[test]
fn cancellation_keeps_native_destination_until_terminal_ack() {
    let f = Fixture::new();
    let path = f.lease();
    let cancelled = f.service.execute("cancel", &f.body()).unwrap();
    assert_eq!(cancelled["state"], "cancelled");
    assert_eq!(cancelled["downloadsPending"], true);
    assert!(path.parent().unwrap().exists());
    // SDK may only create its file after cancellation; keeping the lease stops
    // stale asynchronous writes from racing a published/reused destination.
    fs::write(&path, b"synthetic late download").unwrap();
    assert!(
        !f.service
            .slot
            .lock()
            .unwrap()
            .current
            .as_ref()
            .unwrap()
            .terminal
            .load(Ordering::Acquire)
    );
    let receipt = f.finish_media("ready", None).unwrap();
    assert_eq!(receipt["state"], "cancelled");
    assert_eq!(receipt["downloadsPending"], false);
    assert!(!path.parent().unwrap().exists());
    assert!(
        f.service
            .slot
            .lock()
            .unwrap()
            .current
            .as_ref()
            .unwrap()
            .terminal
            .load(Ordering::Acquire)
    );
}

#[test]
fn unstarted_cancelled_lease_can_settle_without_a_file() {
    let f = Fixture::new();
    let path = f.lease();
    f.finish_media("cancelled", Some("cancelled")).unwrap();
    assert!(!path.parent().unwrap().exists());
    assert_eq!(f.publish()["counts"]["unavailable"], 1);
}

#[test]
fn arbitrary_paths_tokens_and_unknown_attachments_are_rejected() {
    let f = Fixture::new();
    for (key, value) in [
        ("path", json!("C:/unselected")),
        ("source", json!("https://unselected.invalid")),
    ] {
        let mut body = f.media();
        body[key] = value;
        assert_eq!(f.service.execute("media-start", &body), Err(invalid()));
    }
    let mut bad = f.media();
    bad["jobToken"] = json!("0".repeat(64));
    assert_eq!(
        f.service.execute("media-start", &bad),
        Err(ExportServiceError::NotFound)
    );
    bad = f.media();
    bad["attachmentId"] = json!("unknown");
    assert_eq!(
        f.service.execute("media-start", &bad),
        Err(export_job::Error::InvalidRequest.into())
    );
    let path = f.lease();
    let mut chunk = f.media();
    chunk["offset"] = json!(0);
    chunk["data"] = json!("T2dnUw==");
    assert_eq!(
        f.service.execute("media-chunk", &chunk),
        Err(export_job::Error::InvalidState.into())
    );
    f.finish_media("error", Some("download_failed")).unwrap();
    assert!(!path.parent().unwrap().exists());
}

#[test]
fn active_native_writer_blocks_import_and_cleanup_until_closed() {
    let f = Fixture::new();
    let path = f.lease();
    let mut writer = OpenOptions::new()
        .write(true)
        .create_new(true)
        .share_mode(3)
        .open(&path)
        .unwrap();
    writer.write_all(b"\x89PNG\r\n\x1a\nsynthetic").unwrap();
    assert!(f.finish_media("ready", None).is_err());
    assert!(path.exists());
    assert_eq!(
        f.service.execute("status", &f.body()).unwrap()["downloadsPending"],
        true
    );
    drop(writer);
    let receipt = f.finish_media("error", Some("download_failed")).unwrap();
    assert_eq!(receipt["downloadsPending"], false);
    assert!(!path.parent().unwrap().exists());
}

#[test]
fn hardlinked_download_is_never_imported_or_deleted() {
    let f = Fixture::new();
    let path = f.lease();
    let original = f.root.join("unrelated.bin");
    fs::write(&original, b"synthetic unrelated").unwrap();
    fs::hard_link(&original, &path).unwrap();
    assert!(f.finish_media("ready", None).is_err());
    assert_eq!(fs::read(&original).unwrap(), b"synthetic unrelated");
    assert!(path.exists());
    fs::remove_file(&path).unwrap(); // test fixture removes the alias it created.
    f.finish_media("error", Some("download_failed")).unwrap();
    assert_eq!(fs::read(&original).unwrap(), b"synthetic unrelated");
}

#[test]
fn native_media_stream_can_exceed_chunked_audio_limit() {
    let f = Fixture::new();
    let path = f.lease();
    let length = 65 * 1024 * 1024;
    let mut file = fs::File::create(&path).unwrap();
    file.write_all(b"\x89PNG\r\n\x1a\n").unwrap();
    file.set_len(length).unwrap();
    drop(file);
    let receipt = f.finish_media("ready", None).unwrap();
    assert_eq!(receipt["counts"]["mediaBytes"], length);
    assert_eq!(f.publish()["state"], "partial");
}
