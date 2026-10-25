use super::*;
use std::fs;

struct Fixture {
    root: PathBuf,
    job: Option<ExportJob>,
}
impl Fixture {
    fn new() -> Self {
        let base = PathBuf::from("D:/ValMAXBuild/fixtures/export-job");
        fs::create_dir_all(&base).unwrap();
        let root = base.join(nonce().unwrap());
        fs::create_dir(&root).unwrap();
        let job = ExportJob::new(
            &root.join("archive"),
            &json!({"id":"42","title":"Synthetic chat","timezoneOffsetMinutes":180}),
            "99",
        )
        .unwrap();
        Self {
            root,
            job: Some(job),
        }
    }
    fn job(&mut self) -> &mut ExportJob {
        self.job.as_mut().unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        self.job.take();
        // The random root was created above for this test only. Never follow a
        // caller path or touch another fixture/profile.
        assert!(self.root.parent().unwrap() == Path::new("D:/ValMAXBuild/fixtures/export-job"));
        assert!(self.root.file_name().unwrap().to_str().unwrap().len() == 32);
        let _ = fs::remove_dir_all(&self.root);
    }
}
fn message(id: &str) -> Value {
    json!({"id":id,"author":{"id":"123","name":"Synthetic sender"},"timestampMs":1,"text":format!("Message{id}"),"outgoing":false,"reply":null,"attachments":[]})
}
fn photo(id: &str) -> Value {
    let mut v = message(id);
    v["attachments"] = json!([{"id":"photo","name":"sample.png","mime":"image/png","type":"image","status":"pending","reason":null}]);
    v
}
fn completion() -> Value {
    json!({"historyComplete":true,"stopReason":"end_of_history","pagesFetched":1})
}

#[test]
fn many_pages_stream_to_paginated_html() {
    let mut f = Fixture::new();
    for page in 0..6 {
        let rows: Vec<_> = (page * 200..((page + 1) * 200).min(1001))
            .map(|id| message(&id.to_string()))
            .collect();
        f.job().page(page, &rows).unwrap();
    }
    f.job().bind_history(11).unwrap();
    f.job()
        .observe_history(11, "center", 1001, true, false, false)
        .unwrap();
    let done = f.job().finish(&completion()).unwrap();
    assert_eq!(done["state"], "partial");
    let first = fs::read_to_string(f.root.join("archive/index.html")).unwrap();
    let second = fs::read_to_string(f.root.join("archive/messages-2.html")).unwrap();
    assert_eq!(first.matches("<article ").count(), 1000);
    assert_eq!(second.matches("<article ").count(), 1);
    assert!(first.contains("Сохранена часть доступной истории"));
    assert!(first.contains("messages-2.html"));
    assert!(second.contains("Message1000"));
    let manifest: Value =
        serde_json::from_slice(&fs::read(f.root.join("archive/manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "unsupported_history");
    assert_eq!(manifest["counts"]["messages"], 1001);
    assert_eq!(f.job().cancel()["state"], "partial");
}

#[test]
fn unsupported_content_is_visible_in_partial_archive() {
    let mut f = Fixture::new();
    let mut row = message("1");
    row["kind"] = json!("unsupported");
    f.job().page(0, &[row]).unwrap();
    f.job().bind_history(12).unwrap();
    f.job()
        .observe_history(12, "center", 1, true, false, false)
        .unwrap();
    assert_eq!(f.job().finish(&completion()).unwrap()["state"], "partial");
    let html = fs::read_to_string(f.root.join("archive/index.html")).unwrap();
    assert!(html.contains("Message1"));
    assert!(html.contains("Содержимое этого сообщения поддерживается не полностью"));
    let manifest: Value =
        serde_json::from_slice(&fs::read(f.root.join("archive/manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["counts"]["unsupported"], 1);
    assert_eq!(manifest["counts"]["mediaMissing"], 0);
}

#[test]
fn page_retry_overlap_and_ordering() {
    let mut f = Fixture::new();
    let first = f.job().page(0, &[message("10"), message("2")]).unwrap();
    assert_eq!(
        f.job().page(0, &[message("10"), message("2")]).unwrap(),
        first
    );
    assert_eq!(
        f.job().page(0, &[message("3")]),
        Err(Error::SequenceConflict)
    );
    let mut changed = message("2");
    changed["text"] = json!("changed duplicate");
    f.job().page(1, &[changed, message("3")]).unwrap();
    assert_eq!(f.job().snapshot()["counts"]["messages"], 3);
    assert_eq!(
        f.job().page(2, &[message("4"), message("4")]),
        Err(Error::InvalidRequest)
    );
    assert_eq!(f.job().snapshot()["counts"]["messages"], 3);
    let done = f.job().finish(&completion()).unwrap();
    assert_eq!(done["state"], "partial");
    let html = fs::read_to_string(f.root.join("archive/index.html")).unwrap();
    assert!(html.find("Message2").unwrap() < html.find("Message3").unwrap());
    assert!(html.find("Message3").unwrap() < html.find("Message10").unwrap());
    assert!(!html.contains("changed duplicate"));
    let manifest: Value =
        serde_json::from_slice(&fs::read(f.root.join("archive/manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["stopReason"], "unsupported_history");
    assert_eq!(manifest["pagesFetched"], 2);
    assert_eq!(f.job().finish(&completion()).unwrap(), done);
}
#[test]
fn ingress_limits_and_paths_rejected() {
    let mut f = Fixture::new();
    for id in ["0", "-1", "01", "arbitrary", "9223372036854775808"] {
        assert!(matches!(
            ExportJob::new(
                &f.root.join("invalid-account"),
                &json!({"id":"42","title":"Synthetic","timezoneOffsetMinutes":0}),
                id
            ),
            Err(Error::InvalidRequest)
        ));
        assert!(!f.root.join("invalid-account").exists());
    }
    let mut row = photo("1");
    row["attachments"][0]["source"] = json!("file:///C:/private");
    assert_eq!(f.job().page(0, &[row]), Err(Error::InvalidRequest));
    let mut row = photo("1");
    row["attachments"][0]["status"] = json!("ready");
    assert_eq!(f.job().page(0, &[row]), Err(Error::InvalidRequest));
    let mut row = message("1");
    row["author"]["name"] = json!("a".repeat(513));
    assert_eq!(f.job().page(0, &[row]), Err(Error::InvalidRequest));
    let mut row = message("1");
    row["text"] = json!("a".repeat(65537));
    assert_eq!(f.job().page(0, &[row]), Err(Error::InvalidRequest));
    let mut row = photo("1");
    row["attachments"][0]["mime"] = json!("image/png/evil");
    assert_eq!(f.job().page(0, &[row]), Err(Error::InvalidRequest));
    assert_eq!(f.job().page(0, &[]), Err(Error::InvalidRequest));
    assert_eq!(
        f.job().page(MAX_PAGES, &[message("1")]),
        Err(Error::InvalidRequest)
    );
    let mut row = message("1");
    row["text"] = json!("a".repeat(65536));
    assert_eq!(f.job().page(0, &vec![row; 33]), Err(Error::LimitReached));
    assert_eq!(f.job().state(), State::Exporting);
}
#[test]
fn media_chunk_retries_and_pending_finish() {
    let mut f = Fixture::new();
    f.job().page(0, &[photo("1")]).unwrap();
    assert_eq!(f.job().finish(&completion()), Err(Error::InvalidState));
    let data = b"\x89PNG\r\n\x1a\nsynthetic";
    assert_eq!(
        f.job().write_chunk("1", "photo", 0, data).unwrap(),
        data.len() as u64
    );
    assert_eq!(
        f.job().write_chunk("1", "photo", 0, data).unwrap(),
        data.len() as u64
    );
    assert_eq!(
        f.job().write_chunk("1", "photo", 0, b"changed"),
        Err(Error::SequenceConflict)
    );
    let ready = f.job().complete_media("1", "photo", "ready", None).unwrap();
    assert_eq!(
        f.job().complete_media("1", "photo", "ready", None).unwrap(),
        ready
    );
    assert_eq!(ready["counts"]["ready"], 1);
    assert_eq!(ready["counts"]["mediaBytes"], data.len());
    assert_eq!(
        f.job()
            .write_chunk("1", "photo", data.len() as u64, b"late"),
        Err(Error::InvalidState)
    );
    f.job().bind_history(7).unwrap();
    assert_eq!(
        f.job()
            .observe_history(7, "center", 100001, true, false, false),
        Err(Error::InvalidRequest)
    );
    f.job()
        .observe_history(7, "center", 1, true, false, false)
        .unwrap();
    let done = f.job().finish(&completion()).unwrap();
    assert_eq!(done["state"], "partial");
    assert_eq!(done["counts"]["ready"], 1);
    let html = fs::read_to_string(f.root.join("archive/index.html")).unwrap();
    assert!(html.contains("Message1"));
    assert!(html.contains("Сохранена часть доступной истории"));
    let manifest: Value =
        serde_json::from_slice(&fs::read(f.root.join("archive/manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "unsupported_history");
    assert_eq!(manifest["counts"]["mediaReady"], 1);
    assert_eq!(manifest["counts"]["mediaMissing"], 0);
}
#[test]
fn missing_media_is_partial_and_cannot_change_after_ack() {
    let mut f = Fixture::new();
    f.job().page(0, &[photo("1")]).unwrap();
    assert_eq!(
        f.job().complete_media("1", "photo", "error", None),
        Err(Error::InvalidRequest)
    );
    f.job().write_chunk("1", "photo", 0, b"incomplete").unwrap();
    f.job()
        .complete_media("1", "photo", "error", Some("download_failed"))
        .unwrap();
    assert_eq!(
        f.job().complete_media("1", "photo", "ready", None),
        Err(Error::InvalidState)
    );
    f.job().bind_history(9).unwrap();
    f.job()
        .observe_history(9, "center", 1, true, false, false)
        .unwrap();
    let done = f.job().finish(&completion()).unwrap();
    assert_eq!(done["state"], "partial");
    assert_eq!(done["counts"]["unavailable"], 1);
    assert_eq!(
        fs::read_dir(f.root.join("archive/media")).unwrap().count(),
        0
    );
}
#[test]
fn history_requires_matching_native_view_and_current_end() {
    let mut f = Fixture::new();
    f.job().page(0, &[message("1")]).unwrap();
    assert_eq!(
        f.job().observe_history(7, "center", 1, true, false, false),
        Err(Error::InvalidRequest)
    );
    f.job().bind_history(7).unwrap();
    assert_eq!(f.job().bind_history(8), Err(Error::InvalidState));
    assert_eq!(
        f.job().observe_history(8, "center", 1, true, false, false),
        Err(Error::InvalidRequest)
    );
    f.job()
        .observe_history(7, "center", 1, true, false, false)
        .unwrap();
    f.job()
        .observe_history(7, "backward", 1, true, true, false)
        .unwrap();
    assert_eq!(f.job().finish(&completion()).unwrap()["state"], "partial");
}
#[test]
fn cancellation_cleans_staging_and_prevents_publication() {
    let mut f = Fixture::new();
    f.job().page(0, &[photo("1")]).unwrap();
    f.job().write_chunk("1", "photo", 0, b"synthetic").unwrap();
    let flag = f.job().cancellation();
    std::thread::spawn(move || flag.cancel()).join().unwrap();
    assert_eq!(f.job().page(1, &[message("2")]), Err(Error::Cancelled));
    assert_eq!(f.job().state(), State::Cancelled);
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
    assert_eq!(f.job().cancel()["state"], "cancelled");
}
#[test]
fn ready_without_bytes_fails_without_archive() {
    let mut f = Fixture::new();
    f.job().page(0, &[photo("1")]).unwrap();
    assert_eq!(
        f.job().complete_media("1", "photo", "ready", None),
        Err(Error::IoError)
    );
    assert_eq!(f.job().state(), State::Failed);
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

#[test]
fn sqlite_failure_during_finish_aborts_the_job() {
    let mut f = Fixture::new();
    f.job().page(0, &[message("1")]).unwrap();
    f.job()
        .index
        .as_ref()
        .unwrap()
        .connection()
        .execute_batch("DROP TABLE assets")
        .unwrap();
    assert_eq!(f.job().finish(&completion()), Err(Error::IoError));
    assert_eq!(f.job().state(), State::Failed);
    assert!(f.job().index.is_none());
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

#[test]
fn interrupted_renderer_query_is_cancelled() {
    let mut f = Fixture::new();
    let rows: Vec<_> = (0..200).map(|n| message(&n.to_string())).collect();
    f.job().page(0, &rows).unwrap();
    // Pending-count has few opcodes; interrupt once rendering walks its rows.
    let observed = f.job().cancellation();
    f.job()
        .index
        .as_ref()
        .unwrap()
        .connection()
        .progress_handler(
            150,
            Some(move || {
                observed.cancel();
                true
            }),
        )
        .unwrap();
    assert_eq!(f.job().finish(&completion()), Err(Error::Cancelled));
    assert_eq!(f.job().state(), State::Cancelled);
    assert!(f.job().index.is_none());
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

#[test]
fn locked_asset_reports_cleanup_failure_without_publishing() {
    use std::os::windows::fs::OpenOptionsExt;
    let mut f = Fixture::new();
    let receipt = f.job().page(0, &[photo("1")]).unwrap();
    f.job()
        .write_chunk("1", "photo", 0, b"owned synthetic bytes")
        .unwrap();
    let path = f
        .job()
        .workspace
        .staging_path()
        .join(receipt["media"][0]["source"].as_str().unwrap());
    let held = fs::OpenOptions::new()
        .read(true)
        .share_mode(1)
        .open(&path)
        .unwrap();
    assert_eq!(f.job().cancel()["error"], "cleanup_failed");
    assert!(!f.root.join("archive").exists());
    assert!(path.exists());
    drop(held);
}

#[test]
fn locked_index_prevents_archive_publication() {
    use std::os::windows::fs::OpenOptionsExt;
    let mut f = Fixture::new();
    f.job().page(0, &[message("1")]).unwrap();
    let directory = fs::read_dir(&f.root)
        .unwrap()
        .map(|e| e.unwrap().path())
        .find(|p| {
            p.file_name()
                .unwrap()
                .to_string_lossy()
                .starts_with(".valmax-index-")
        })
        .unwrap();
    let path = directory.join("index.sqlite");
    let held = fs::OpenOptions::new()
        .read(true)
        .share_mode(3)
        .open(&path)
        .unwrap();
    assert_eq!(f.job().finish(&completion()), Err(Error::CleanupFailed));
    assert_eq!(f.job().state(), State::Failed);
    assert!(!f.root.join("archive").exists());
    assert!(path.exists());
    drop(held);
}
#[test]
fn existing_destination_is_never_replaced() {
    let mut f = Fixture::new();
    f.job().page(0, &[message("1")]).unwrap();
    let archive = f.root.join("archive");
    fs::create_dir(&archive).unwrap();
    fs::write(archive.join("owned.txt"), b"keep").unwrap();
    assert!(f.job().finish(&completion()).is_err());
    assert_eq!(fs::read(archive.join("owned.txt")).unwrap(), b"keep");
    assert!(!archive.join("index.html").exists());
    assert_eq!(f.job().state(), State::Failed);
}
