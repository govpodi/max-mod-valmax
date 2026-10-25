use super::*;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::Barrier,
    thread,
};

struct Fixture {
    root: PathBuf,
}

#[test]
fn video_source_rebind_retries_only_one_transient_failure_and_honors_cancel() {
    use std::cell::Cell;

    let attempts = Cell::new(0);
    let waits = Cell::new(0);
    let recovered = retry_video_resolution(
        || {
            attempts.set(attempts.get() + 1);
            if attempts.get() == 1 {
                Ok(Err(MediaError::Timeout))
            } else {
                Ok(Ok(7_u8))
            }
        },
        || {
            waits.set(waits.get() + 1);
            Ok(())
        },
    );
    assert_eq!(recovered, Ok(Ok(7)));
    assert_eq!((attempts.get(), waits.get()), (2, 1));

    attempts.set(0);
    waits.set(0);
    let still_offline = retry_video_resolution(
        || {
            attempts.set(attempts.get() + 1);
            Ok::<_, ExportServiceError>(Err::<u8, _>(MediaError::Network))
        },
        || {
            waits.set(waits.get() + 1);
            Ok(())
        },
    );
    assert_eq!(still_offline, Ok(Err(MediaError::Network)));
    assert_eq!((attempts.get(), waits.get()), (2, 1));

    attempts.set(0);
    waits.set(0);
    let denied = retry_video_resolution(
        || {
            attempts.set(attempts.get() + 1);
            Ok::<_, ExportServiceError>(Err::<u8, _>(MediaError::PermissionDenied))
        },
        || {
            waits.set(waits.get() + 1);
            Ok(())
        },
    );
    assert_eq!(denied, Ok(Err(MediaError::PermissionDenied)));
    assert_eq!((attempts.get(), waits.get()), (1, 0));

    attempts.set(0);
    let cancelled_during_wait = retry_video_resolution(
        || {
            attempts.set(attempts.get() + 1);
            Ok::<_, ExportServiceError>(Err::<u8, _>(MediaError::Timeout))
        },
        || Err(cancelled()),
    );
    assert_eq!(cancelled_during_wait, Err(cancelled()));
    assert_eq!(attempts.get(), 1);
}

#[test]
fn video_variant_fallback_rewinds_once_and_keeps_only_lower_bytes() {
    use std::cell::Cell;

    let sources = [
        Zeroizing::new("https://high.okcdn.ru/high".to_owned()),
        Zeroizing::new("https://low.okcdn.ru/low".to_owned()),
    ];
    let attempts = Cell::new(0);
    let resets = Cell::new(0);
    let pauses = Cell::new(0);
    let mut output: Vec<u8> = Vec::new();
    let result = download_video_with_fallback(
        &sources,
        &mut output,
        |url, target| {
            attempts.set(attempts.get() + 1);
            if url == sources[0].as_str() {
                target.extend_from_slice(b"partial-high");
                Err(MediaError::TooLarge)
            } else {
                assert_eq!(url, sources[1].as_str());
                target.extend_from_slice(b"complete-low");
                Ok(target.len() as u64)
            }
        },
        |target| {
            resets.set(resets.get() + 1);
            target.clear();
            Ok(())
        },
        || {
            pauses.set(pauses.get() + 1);
            Ok(())
        },
    );
    assert_eq!(result, Ok(b"complete-low".len() as u64));
    assert_eq!(output, b"complete-low");
    assert_eq!((attempts.get(), resets.get(), pauses.get()), (2, 1, 1));
}

#[test]
fn video_variant_fallback_skips_duplicate_high_url() {
    let sources = [
        Zeroizing::new("high".to_owned()),
        Zeroizing::new("middle".to_owned()),
        Zeroizing::new("high".to_owned()),
    ];
    let mut attempted = Vec::new();
    let mut output = Vec::new();
    let result = download_video_with_fallback(
        &sources,
        &mut output,
        |url, target| {
            attempted.push(url.to_owned());
            if url == "high" {
                target.extend_from_slice(b"high-part");
                Err(MediaError::Network)
            } else {
                target.extend_from_slice(b"middle-complete");
                Ok(target.len() as u64)
            }
        },
        |target| { target.clear(); Ok(()) },
        || Ok(()),
    );
    assert_eq!(result, Ok(b"middle-complete".len() as u64));
    assert_eq!(attempted, ["high", "middle"]);
    assert_eq!(output, b"middle-complete");
}

#[test]
fn video_variant_fallback_stops_on_denial_and_on_cancelled_pause() {
    use std::cell::Cell;

    let sources = [Zeroizing::new("high".to_owned()), Zeroizing::new("low".to_owned())];
    let attempts = Cell::new(0);
    let mut output: Vec<u8> = Vec::new();
    let denied = download_video_with_fallback(
        &sources,
        &mut output,
        |_, _| {
            attempts.set(attempts.get() + 1);
            Err(MediaError::PermissionDenied)
        },
        |_| panic!("denied variant must not reset the writer"),
        || panic!("denied variant must not wait for fallback"),
    );
    assert_eq!(denied, Err(MediaError::PermissionDenied));
    assert_eq!(attempts.get(), 1);

    attempts.set(0);
    let cancelled = download_video_with_fallback(
        &sources,
        &mut output,
        |_, _| {
            attempts.set(attempts.get() + 1);
            Err(MediaError::Timeout)
        },
        |_| panic!("cancelled fallback must not reset the writer"),
        || Err(MediaError::Cancelled),
    );
    assert_eq!(cancelled, Err(MediaError::Cancelled));
    assert_eq!(attempts.get(), 1);
}
impl Fixture {
    fn new() -> Self {
        let base = Path::new("E:/ValMAXBuild/fixtures/media-interleave-export-service");
        fs::create_dir_all(base).unwrap();
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let root = base.join(encode_hex(&nonce));
        fs::create_dir(&root).unwrap();
        Self { root }
    }
    fn request(&self) -> Value {
        json!({"schema":1,"accountId":"99","chat":{"id":"42","title":"Synthetic chat","timezoneOffsetMinutes":180},
            "directory":format!("file:///{}",self.root.to_str().unwrap().replace('\\',"/"))})
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        // Only this newly created random fixture is owned by this test. Never
        // accept a path from the tested request or follow external test roots.
        assert_eq!(
            self.root.parent(),
            Some(Path::new("E:/ValMAXBuild/fixtures/media-interleave-export-service"))
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
fn body(start: &Value) -> Value {
    json!({"schema":1,"jobId":start["jobId"],"jobToken":start["jobToken"]})
}
fn page(start: &Value, messages: Vec<Value>) -> Value {
    let mut v = body(start);
    v["sequence"] = json!(0);
    v["messages"] = json!(messages);
    v
}
fn message() -> Value {
    json!({"id":"1","author":{"id":"123","name":"Synthetic sender"},"timestampMs":1,"text":"Synthetic message",
        "outgoing":false,"reply":null,"attachments":[]})
}
fn completion(start: &Value) -> Value {
    let mut v = body(start);
    v["completion"] =
        json!({"historyComplete":true,"stopReason":"end_of_history","pagesFetched":1});
    v
}
fn handle(service: &ExportService, start: &Value) -> Arc<JobHandle> {
    service
        .lookup(
            start["jobId"].as_str().unwrap(),
            start["jobToken"].as_str().unwrap(),
        )
        .unwrap()
}

#[test]
fn strict_commands_authentication_and_failed_start_release_reservation() {
    let f = Fixture::new();
    let service = ExportService::default();
    for action in ["bind-history", "observe-history", "pending", "", "START"] {
        assert_eq!(
            service.execute(action, &json!({})),
            Err(ExportServiceError::UnsupportedOperation)
        );
    }
    for schema in [json!(true), json!(1.0), json!("1"), json!(2)] {
        let mut request = f.request();
        request["schema"] = schema;
        assert_eq!(service.execute("start", &request), Err(invalid()));
    }
    let mut extra = f.request();
    extra["path"] = json!("C:/unselected");
    assert_eq!(service.execute("start", &extra), Err(invalid()));
    let mut missing = f.request();
    missing["directory"] = json!(format!("{}/absent", missing["directory"].as_str().unwrap()));
    assert!(service.execute("start", &missing).is_err());
    assert!(!service.slot.lock().unwrap().starting);
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
    let start = service.execute("start", &f.request()).unwrap();
    assert!(hex::<16>(start["jobId"].as_str().unwrap()).is_some());
    assert!(hex::<32>(start["jobToken"].as_str().unwrap()).is_some());
    assert_eq!(start["limits"]["maxChunkBytes"], 49_152);
    for field in ["jobId", "jobToken"] {
        let mut bad = body(&start);
        let mut bytes = bad[field].as_str().unwrap().as_bytes().to_vec();
        bytes[0] = if bytes[0] == b'0' { b'1' } else { b'0' };
        bad[field] = json!(String::from_utf8(bytes).unwrap());
        assert_eq!(
            service.execute("status", &bad),
            Err(ExportServiceError::NotFound)
        );
        bad[field] = json!("A".repeat(if field == "jobId" { 32 } else { 64 }));
        assert_eq!(
            service.execute("status", &bad),
            Err(ExportServiceError::NotFound)
        );
    }
    assert!(
        service
            .execute("status", &body(&start))
            .unwrap()
            .get("jobToken")
            .is_none()
    );
    assert_eq!(
        service.execute("start", &f.request()),
        Err(ExportServiceError::Busy)
    );
    service.execute("cancel", &body(&start)).unwrap();
}

#[test]
fn history_revocation_signals_while_worker_holds_job_mutex() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let id = start["jobId"].as_str().unwrap();
    let token = start["jobToken"].as_str().unwrap();
    service.bind_history(id, token, 17).unwrap();
    service
        .observe_history(id, token, 17, "center", 1, true, false, false)
        .unwrap();
    let current = handle(&service, &start);
    let guard = current.job.lock().unwrap();
    assert!(service.invalidate_history(id, token, 18).is_err());
    assert!(current.cancel.check().is_ok());
    service.invalidate_history(id, token, 17).unwrap();
    assert!(current.cancel.check().is_err());
    drop(guard);
    assert!(service.execute("finish", &completion(&start)).is_err());
    assert_eq!(
        service.execute("status", &body(&start)).unwrap()["state"],
        "cancelled"
    );
}

#[test]
fn chunked_export_stays_selected_and_publishes_partial_archive() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let mut row = message();
    row["attachments"] = json!([{"id":"photo","name":"example.png","mime":"image/png","type":"image","status":"pending","reason":null}]);
    let request = page(&start, vec![row]);
    let receipt = service.execute("page", &request).unwrap();
    assert_eq!(service.execute("page", &request).unwrap(), receipt);
    let bytes = b"\x89PNG\r\n\x1a\nsynthetic-only";
    let mut chunk = body(&start);
    chunk["messageId"] = json!("1");
    chunk["attachmentId"] = json!("photo");
    chunk["offset"] = json!(0);
    chunk["data"] = json!(STANDARD.encode(bytes));
    let next = service.execute("media-chunk", &chunk).unwrap();
    assert_eq!(
        next,
        json!({"schema":1,"jobId":start["jobId"],"offset":bytes.len()})
    );
    assert_eq!(service.execute("media-chunk", &chunk).unwrap(), next);
    let mut media = body(&start);
    media["messageId"] = json!("1");
    media["attachmentId"] = json!("photo");
    media["status"] = json!("ready");
    media["reason"] = Value::Null;
    service.execute("media", &media).unwrap();
    let history = service.execute("history", &body(&start)).unwrap();
    assert_eq!(
        history["history"],
        json!({"ready":false,"backward":false,"forward":false})
    );
    let done = service.execute("finish", &completion(&start)).unwrap();
    assert_eq!(done["state"], "partial");
    assert!(done.get("jobToken").is_none());
    let path = PathBuf::from(done["htmlPath"].as_str().unwrap());
    assert_eq!(path.parent().unwrap().parent().unwrap(), f.root);
    assert!(
        path.parent()
            .unwrap()
            .file_name()
            .unwrap()
            .to_str()
            .unwrap()
            .starts_with("ValMAX-Chat-")
    );
    assert_eq!(
        crate::selected_path::selected_file_url(done["htmlFileURL"].as_str().unwrap()).unwrap(),
        path
    );
    let html = fs::read_to_string(&path).unwrap();
    assert!(html.contains("Synthetic message"));
    assert!(html.contains("Сохранена часть доступной истории"));
    let manifest: Value =
        serde_json::from_slice(&fs::read(path.parent().unwrap().join("manifest.json")).unwrap())
            .unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "unsupported_history");
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 1); // published archive; no spool remains
    assert_eq!(
        service.execute("finish", &completion(&start)).unwrap(),
        done
    );
    let second = service.execute("start", &f.request()).unwrap();
    assert_ne!(second["jobId"], start["jobId"]);
    assert_eq!(
        service.execute("status", &body(&start)),
        Err(ExportServiceError::NotFound)
    );
    service.execute("cancel", &body(&second)).unwrap();
    assert!(path.is_file()); // later job cleanup never removes a published result
}

#[test]
fn host_history_exact_identity_cannot_prove_server_completion() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    service
        .execute("page", &page(&start, vec![message()]))
        .unwrap();
    let id = start["jobId"].as_str().unwrap();
    let token = start["jobToken"].as_str().unwrap();
    assert_eq!(
        service.bind_history(id, &"0".repeat(64), 8),
        Err(ExportServiceError::NotFound)
    );
    service.bind_history(id, token, 8).unwrap();
    assert!(
        service
            .observe_history(id, token, 9, "center", 1, true, false, false)
            .is_err()
    );
    service
        .observe_history(id, token, 8, "center", 1, true, false, false)
        .unwrap();
    let before = service.execute("history", &body(&start)).unwrap();
    assert_eq!(
        before["history"],
        json!({"ready":true,"backward":true,"forward":true})
    );
    let mut forged = body(&start);
    forged["history"] = json!({"ready":true});
    assert_eq!(service.execute("history", &forged), Err(invalid()));
    assert_eq!(service.execute("history", &body(&start)).unwrap(), before);
    let done = service.execute("finish", &completion(&start)).unwrap();
    assert_eq!(done["state"], "partial");
    let html_path = Path::new(done["htmlPath"].as_str().unwrap());
    let html = fs::read_to_string(html_path).unwrap();
    assert!(html.contains("Synthetic message"));
    assert!(html.contains("Сохранена часть доступной истории"));
    let manifest: Value = serde_json::from_slice(
        &fs::read(html_path.parent().unwrap().join("manifest.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "unsupported_history");
    assert_eq!(manifest["counts"]["messages"], 1);
}

#[test]
fn bounded_page_chunk_and_canonical_base64_reject_before_writes() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    assert_eq!(
        service.execute("page", &page(&start, vec![message(); 201])),
        Err(invalid())
    );
    let mut large = message();
    large["text"] = json!("a".repeat(65_536));
    assert_eq!(
        service.execute("page", &page(&start, vec![large; 33])),
        Err(export_job::Error::LimitReached.into())
    );
    let mut chunk = body(&start);
    chunk["messageId"] = json!("1");
    chunk["attachmentId"] = json!("file");
    chunk["offset"] = json!(0);
    for encoded in ["", "YQ", "YQ=", "YQ===", "YR==", "YQ==\n", "____", "Y Q=="] {
        chunk["data"] = json!(encoded);
        assert_eq!(service.execute("media-chunk", &chunk), Err(invalid()));
    }
    chunk["data"] = json!("YQ==");
    for offset in [
        json!(-1),
        json!(0.0),
        json!(true),
        json!(MAX_CHUNKED_ASSET_BYTES),
    ] {
        chunk["offset"] = offset;
        assert_eq!(service.execute("media-chunk", &chunk), Err(invalid()));
    }
    chunk["offset"] = json!(MAX_CHUNKED_ASSET_BYTES - 1);
    chunk["data"] = json!("YWE=");
    assert_eq!(
        service.execute("media-chunk", &chunk),
        Err(export_job::Error::LimitReached.into())
    );
    assert_eq!(
        service.execute("status", &body(&start)).unwrap()["counts"]["messages"],
        0
    );
    service.execute("cancel", &body(&start)).unwrap();
}

#[test]
fn cancellation_signals_before_busy_job_and_history_is_read_only() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let job = handle(&service, &start);
    let guard = job.job.lock().unwrap();
    assert_eq!(
        service.execute("status", &body(&start)),
        Err(ExportServiceError::Busy)
    );
    assert_eq!(
        service.execute("cancel", &body(&start)),
        Err(ExportServiceError::Busy)
    );
    assert!(job.cancel.check().is_err());
    assert!(service.slot.try_lock().is_ok()); // blocking job operation never owns the slot
    drop(guard);
    let files = fs::read_dir(&f.root).unwrap().count();
    assert_eq!(files, 2);
    service.execute("history", &body(&start)).unwrap();
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), files);
    assert_eq!(job.job.lock().unwrap().state(), State::Exporting);
    assert_eq!(
        service.execute("status", &body(&start)).unwrap()["state"],
        "cancelled"
    );
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

#[test]
fn concurrent_admission_has_one_job_and_shutdown_is_irreversible() {
    let f = Fixture::new();
    let service = Arc::new(ExportService::default());
    let gate = Arc::new(Barrier::new(3));
    let workers: Vec<_> = (0..2)
        .map(|_| {
            let service = service.clone();
            let gate = gate.clone();
            let request = f.request();
            thread::spawn(move || {
                gate.wait();
                service.execute("start", &request)
            })
        })
        .collect();
    gate.wait();
    let outcomes: Vec<_> = workers.into_iter().map(|w| w.join().unwrap()).collect();
    assert_eq!(outcomes.iter().filter(|r| r.is_ok()).count(), 1);
    assert_eq!(
        outcomes
            .iter()
            .filter(|r| **r == Err(ExportServiceError::Busy))
            .count(),
        1
    );
    let start = outcomes.into_iter().find_map(|r| r.ok()).unwrap();
    let job = handle(&service, &start);
    let guard = job.job.lock().unwrap();
    service.cancel_pending();
    assert!(job.cancel.check().is_err());
    assert!(service.slot.lock().unwrap().closed);
    assert_eq!(service.execute("start", &f.request()), Err(cancelled()));
    drop(guard);
    service.execute("cancel", &body(&start)).unwrap();
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

#[test]
fn shutdown_during_start_reservation_cannot_publish_new_job() {
    let f = Fixture::new();
    let service = ExportService::default();
    let reservation = service.reserve_start().unwrap();
    let job = ExportJob::new(
        &f.root.join("ValMAX-Chat-owned"),
        &f.request()["chat"],
        "99",
    )
    .unwrap();
    let id = hex(job.snapshot()["jobId"].as_str().unwrap()).unwrap();
    let handle = Arc::new(JobHandle {
        id,
        token: Zeroizing::new([1; 32]),
        cancel: job.cancellation(),
        terminal: AtomicBool::new(false),
        history_source: AtomicU64::new(0),
        api2_active: AtomicBool::new(false),
        api2_cancel: HistoryCancel::new(),
        api2: Mutex::new(Api2Progress::new().unwrap()),
        job: Mutex::new(job),
    });
    service.cancel_pending();
    assert_eq!(reservation.commit(handle.clone()), Err(cancelled()));
    assert!(service.slot.lock().unwrap().current.is_none());
    assert!(!service.slot.lock().unwrap().starting);
    drop(handle);
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
    assert_eq!(service.execute("start", &f.request()), Err(cancelled()));
}

#[test]
fn drop_cancels_active_job_and_releases_owned_files() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let cancellation = handle(&service, &start).cancel.clone();
    service
        .execute("page", &page(&start, vec![message()]))
        .unwrap();
    drop(service);
    assert!(cancellation.check().is_err());
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

struct NoCredentials;
impl LoginCredentials for NoCredentials {
    fn owned_token_names(&self) -> crate::login_credentials::Result<Vec<String>> {
        panic!("unexpected credential inventory")
    }
    fn read(&self, _name: &str) -> crate::login_credentials::Result<Option<Zeroizing<Vec<u8>>>> {
        panic!("unexpected credential read")
    }
    fn set_owned(
        &self,
        _name: &str,
        _blob: &[u8],
        _expected: Option<&[u8]>,
    ) -> crate::login_credentials::Result<()> {
        panic!("unexpected credential write")
    }
    fn delete_owned(&self, _name: &str, _expected: &[u8]) -> crate::login_credentials::Result<()> {
        panic!("unexpected credential delete")
    }
}

fn synthetic_api2_message(
    id: HistoryMessageId,
    time_ms: i64,
    text: &str,
    attachments: Vec<HistoryAttachmentKind>,
    unsupported: bool,
) -> HistoryMessage {
    HistoryMessage {
        id,
        time_ms,
        sender_id: 99,
        kind: HistoryMessageKind::User,
        text: Zeroizing::new(text.to_owned()),
        attachments,
        media_sources: Vec::new(),
        rich_content_present: unsupported,
        unsupported_visible_content: unsupported,
    }
}

fn synthetic_api2_page(from_ms: i64, messages: Vec<HistoryMessage>) -> HistoryPage {
    let evidence = if messages.is_empty() {
        PageEvidence::EmptyWindow
    } else {
        let oldest = messages
            .iter()
            .map(|message| message.time_ms)
            .min()
            .unwrap();
        if oldest == from_ms {
            PageEvidence::NoTimeProgress {
                count: messages.len(),
            }
        } else {
            PageEvidence::AdvanceCandidate {
                next_from_ms: oldest,
                count: messages.len(),
            }
        }
    };
    HistoryPage { messages, evidence }
}

fn bind_synthetic_api2(service: &ExportService, start: &Value, from_ms: i64) -> Arc<JobHandle> {
    let id = start["jobId"].as_str().unwrap();
    let token = start["jobToken"].as_str().unwrap();
    service.bind_history(id, token, 71).unwrap();
    let current = handle(service, start);
    let (_, own_id) = service.claim_api2(&current).unwrap();
    let mut progress = current.api2.lock().unwrap();
    progress.own_id = Some(own_id);
    progress.next_from_ms = Some(from_ms);
    drop(progress);
    current
}

#[test]
fn api2_next_requires_live_host_view_and_refuses_mixed_host_ingress() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = handle(&service, &start);
    let mut extra = body(&start);
    extra["chatId"] = json!("42");
    assert_eq!(service.api2_next(&extra, &NoCredentials), Err(invalid()));
    assert_eq!(
        service.api2_next(&body(&start), &NoCredentials),
        Err(export_job::Error::InvalidState.into())
    );
    assert!(!current.api2_active.load(Ordering::Acquire));
    let (_, own_id) = {
        service
            .bind_history(
                start["jobId"].as_str().unwrap(),
                start["jobToken"].as_str().unwrap(),
                71,
            )
            .unwrap();
        service.claim_api2(&current).unwrap()
    };
    assert_eq!(own_id, 99);
    assert!(current.api2_active.load(Ordering::Acquire));
    assert_eq!(
        service.execute("page", &page(&start, vec![message()])),
        Err(export_job::Error::InvalidState.into())
    );
    assert_eq!(
        service.execute("finish", &completion(&start)),
        Err(export_job::Error::InvalidState.into())
    );
    let cancelled = service.execute("cancel", &body(&start)).unwrap();
    assert_eq!(cancelled["state"], "cancelled");
    assert!(cancelled.get("htmlFileURL").is_none());
    assert!(current.cancel.check().is_err());
}

#[test]
fn api2_rebind_scope_keeps_the_claimed_account_and_saved_cursor() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 5_000);
    assert!(service.active_api2_scope(&current, 99).is_ok());
    assert!(service.active_api2_scope(&current, 98).is_err());
    assert_eq!(current.api2.lock().unwrap().next_from_ms, Some(5_000));
    service
        .invalidate_history(
            start["jobId"].as_str().unwrap(),
            start["jobToken"].as_str().unwrap(),
            71,
        )
        .unwrap();
    assert!(service.active_api2_scope(&current, 99).is_err());
    assert!(current.api2_cancel.is_cancelled());
}

#[test]
fn api2_reconnect_pause_exits_immediately_after_cancellation() {
    let cancel = HistoryCancel::new();
    cancel.cancel();
    let start = Instant::now();
    assert_eq!(wait_api2_history_retry(&cancel), Err(TransportError::Cancelled));
    assert!(start.elapsed() < API2_HISTORY_RETRY_SPACING);
}

#[test]
fn api2_typed_ids_ties_unavailable_media_and_partial_publication() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 5_000);
    let mut progress = current.api2.lock().unwrap();
    let first = synthetic_api2_page(
        5_000,
        vec![
            synthetic_api2_message(
                HistoryMessageId::Text(Zeroizing::new("7".into())),
                4_000,
                "first synthetic text",
                vec![HistoryAttachmentKind::Photo],
                false,
            ),
            synthetic_api2_message(
                HistoryMessageId::Unsigned(7),
                4_000,
                "second synthetic text",
                vec![HistoryAttachmentKind::Other],
                true,
            ),
        ],
    );
    let first_receipt = service
        .ingest_api2_page(&current, &mut progress, &first, 5_000)
        .unwrap();
    assert_eq!(first_receipt["state"], "exporting");
    assert_eq!(first_receipt["counts"]["messages"], 2);
    assert_eq!(first_receipt["api2"]["windowsFetched"], 1);
    assert_eq!(first_receipt["api2"]["done"], false);
    assert!(first_receipt.get("htmlFileURL").is_none());
    assert_ne!(
        api2_message_key(&progress.id_salt, &first.messages[0].id).unwrap(),
        api2_message_key(&progress.id_salt, &first.messages[1].id).unwrap()
    );
    let second = synthetic_api2_page(
        4_000,
        vec![
            synthetic_api2_message(
                HistoryMessageId::Unsigned(6),
                3_000,
                "older synthetic text",
                Vec::new(),
                false,
            ),
            synthetic_api2_message(
                HistoryMessageId::Text(Zeroizing::new("7".into())),
                4_000,
                "first synthetic text",
                vec![HistoryAttachmentKind::Photo],
                false,
            ),
            synthetic_api2_message(
                HistoryMessageId::Unsigned(7),
                4_000,
                "second synthetic text",
                vec![HistoryAttachmentKind::Other],
                true,
            ),
        ],
    );
    let second_receipt = service
        .ingest_api2_page(&current, &mut progress, &second, 4_000)
        .unwrap();
    assert_eq!(second_receipt["counts"]["messages"], 3);
    let last = synthetic_api2_page(
        3_000,
        vec![synthetic_api2_message(
            HistoryMessageId::Unsigned(6),
            3_000,
            "older synthetic text",
            Vec::new(),
            false,
        )],
    );
    let final_receipt = service
        .ingest_api2_page(&current, &mut progress, &last, 3_000)
        .unwrap();
    assert_eq!(final_receipt["state"], "partial");
    assert_eq!(
        final_receipt["api2"],
        json!({"windowsFetched":3,"mediaProcessed":0,"mediaTotal":0,"phase":"done","done":true})
    );
    assert_eq!(final_receipt["counts"]["messages"], 3);
    assert_eq!(final_receipt["counts"]["attachments"], 2);
    assert_eq!(final_receipt["counts"]["ready"], 0);
    assert_eq!(final_receipt["counts"]["unavailable"], 2);
    assert_eq!(final_receipt["downloadsPending"], false);
    assert!(
        final_receipt["htmlFileURL"]
            .as_str()
            .unwrap()
            .starts_with("file:///")
    );
    let html_path = Path::new(final_receipt["htmlPath"].as_str().unwrap());
    let html = fs::read_to_string(html_path).unwrap();
    for text in [
        "first synthetic text",
        "second synthetic text",
        "older synthetic text",
    ] {
        assert!(html.contains(text));
    }
    assert!(html.contains("Полнота не подтверждена"));
    assert!(html.contains("Бета: чат может быть неполным."));
    assert!(html.contains("Восстановление уже удалённых сообщений не подтверждено"));
    assert!(html.contains("файлы вложений могут отсутствовать"));
    let manifest: Value =
        serde_json::from_slice(&fs::read(html_path.with_file_name("manifest.json")).unwrap())
            .unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "no_progress");
    assert_eq!(manifest["pagesFetched"], 3);
    assert_eq!(manifest["counts"]["unsupported"], 2);
    assert_eq!(manifest["counts"]["mediaMissing"], 2);
}

#[test]
fn api2_media_queue_keeps_wire_urls_inside_core_and_waits_to_publish() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 1_000);
    let mut message = synthetic_api2_message(
        HistoryMessageId::Unsigned(1),
        1_000,
        "synthetic voice",
        vec![HistoryAttachmentKind::Audio, HistoryAttachmentKind::Photo, HistoryAttachmentKind::Video],
        false,
    );
    let synthetic_url = "https://a.oneme.ru/fixture?token=synthetic-secret";
    let synthetic_photo_url = "https://i.oneme.ru/fixture?token=synthetic-photo-secret";
    let synthetic_video_token = "synthetic-video-secret";
    message.media_sources = vec![
        Some(HistoryMediaSource::AudioUrl(Zeroizing::new(synthetic_url.to_owned()))),
        Some(HistoryMediaSource::PhotoUrl(Zeroizing::new(synthetic_photo_url.to_owned()))),
        Some(HistoryMediaSource::VideoRef(api2_history_transport::HistoryVideoRef {
            message_id: HistoryMessageId::Unsigned(1),
            video_id: 9_007_199_254_740_993,
            token: Zeroizing::new(synthetic_video_token.to_owned()),
        })),
    ];
    let page = synthetic_api2_page(1_000, vec![message]);
    let mut progress = current.api2.lock().unwrap();
    let receipt = service
        .ingest_api2_page(&current, &mut progress, &page, 1_000)
        .unwrap();
    assert_eq!(receipt["state"], "exporting");
    assert_eq!(receipt["counts"]["attachments"], 3);
    assert_eq!(receipt["counts"]["unavailable"], 0);
    let queued_video = &page.messages[0];
    let prepared_video = prepare_api2_row(queued_video, 99, &[0; 32]).unwrap();
    assert_eq!(prepared_video.value["attachments"][2]["mime"], "video/mp4");
    assert_eq!(receipt["api2"], json!({"windowsFetched":1,
        "mediaProcessed":0,"mediaTotal":3,"phase":"media","done":false}));
    assert_eq!(progress.media_queue.len(), 3);
    assert!(!receipt.to_string().contains(synthetic_url));
    assert!(!receipt.to_string().contains("synthetic-secret"));
    assert!(!receipt.to_string().contains(synthetic_photo_url));
    assert!(!receipt.to_string().contains(synthetic_video_token));
    drop(progress);
    let cancelled = service.execute("cancel", &body(&start)).unwrap();
    assert_eq!(cancelled["state"], "cancelled");
    assert!(cancelled.get("htmlFileURL").is_none());
}

#[test]
fn api2_direct_signed_urls_precede_resolvable_video_refs_within_each_window() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 2_000);
    let mut message = synthetic_api2_message(
        HistoryMessageId::Unsigned(1),
        1_000,
        "synthetic mixed media",
        vec![HistoryAttachmentKind::Video, HistoryAttachmentKind::Photo, HistoryAttachmentKind::Audio],
        false,
    );
    message.media_sources = vec![
        Some(HistoryMediaSource::VideoRef(api2_history_transport::HistoryVideoRef {
            message_id: HistoryMessageId::Unsigned(1),
            video_id: 9_007_199_254_740_993,
            token: Zeroizing::new("synthetic-video-reference".to_owned()),
        })),
        Some(HistoryMediaSource::PhotoUrl(Zeroizing::new(
            "https://i.oneme.ru/fixture?token=synthetic-photo".to_owned(),
        ))),
        Some(HistoryMediaSource::AudioUrl(Zeroizing::new(
            "https://a.oneme.ru/fixture?token=synthetic-voice".to_owned(),
        ))),
    ];
    let mut progress = current.api2.lock().unwrap();
    let receipt = service
        .ingest_api2_page(&current, &mut progress, &synthetic_api2_page(2_000, vec![message]), 2_000)
        .unwrap();
    assert_eq!(receipt["api2"]["mediaTotal"], 3);
    assert_eq!(progress.media_queue.len(), 3);
    assert!(matches!(&progress.media_queue[0].source, HistoryMediaSource::PhotoUrl(_)));
    assert!(matches!(&progress.media_queue[1].source, HistoryMediaSource::AudioUrl(_)));
    assert!(matches!(&progress.media_queue[2].source, HistoryMediaSource::VideoRef(_)));
    assert!(!receipt.to_string().contains("synthetic-photo"));
    assert!(!receipt.to_string().contains("synthetic-voice"));
    assert!(!receipt.to_string().contains("synthetic-video-reference"));
    drop(progress);
    service.execute("cancel", &body(&start)).unwrap();
}

#[test]
fn api2_media_is_settled_before_the_next_history_window() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 5_000);
    let mut message = synthetic_api2_message(
        HistoryMessageId::Unsigned(1),
        4_000,
        "synthetic media page",
        vec![HistoryAttachmentKind::Audio],
        false,
    );
    message.media_sources = vec![Some(HistoryMediaSource::AudioUrl(Zeroizing::new(
        "https://a.oneme.ru/fixture?token=synthetic-only".to_owned(),
    )))];
    let mut progress = current.api2.lock().unwrap();
    let first = service
        .ingest_api2_page(&current, &mut progress, &synthetic_api2_page(5_000, vec![message]), 5_000)
        .unwrap();
    assert_eq!(first["api2"]["phase"], "history");
    assert_eq!(first["api2"]["mediaTotal"], 1);
    assert_eq!(first["api2"]["mediaProcessed"], 0);
    assert_eq!(progress.next_from_ms, Some(4_000));
    assert_eq!(progress.media_queue.len(), 1);
    assert_eq!(
        service.ingest_api2_page(&current, &mut progress, &synthetic_api2_page(4_000, Vec::new()), 4_000),
        Err(export_job::Error::InvalidState.into()),
    );
    assert_eq!(progress.windows_fetched, 1);
    // A malformed synthetic source settles locally without network access.
    // The real downloader's pinned-origin tests cover accepted sources.
    progress.media_queue.front_mut().unwrap().source =
        HistoryMediaSource::AudioUrl(Zeroizing::new("invalid-synthetic-source".to_owned()));
    drop(progress);
    let settled = service.api2_next(&body(&start), &NoCredentials).unwrap();
    assert_eq!(settled["state"], "exporting");
    assert_eq!(settled["api2"]["phase"], "history");
    assert_eq!(settled["api2"]["windowsFetched"], 1);
    assert_eq!(settled["api2"]["mediaProcessed"], 1);
    assert_eq!(settled["counts"]["unavailable"], 1);
    assert!(settled.get("htmlPath").is_none());
    let mut progress = current.api2.lock().unwrap();
    assert!(progress.media_queue.is_empty());
    let final_receipt = service
        .ingest_api2_page(&current, &mut progress, &synthetic_api2_page(4_000, Vec::new()), 4_000)
        .unwrap();
    assert_eq!(final_receipt["state"], "partial");
    assert_eq!(final_receipt["api2"]["mediaTotal"], 1);
    assert_eq!(final_receipt["api2"]["mediaProcessed"], 1);
    assert_eq!(final_receipt["counts"]["attachments"], 1);
    assert_eq!(final_receipt["counts"]["unavailable"], 1);
    assert_eq!(final_receipt["downloadsPending"], false);
}

#[test]
fn api2_timeout_drains_pending_media_before_partial_publication() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 5_000);
    let mut message = synthetic_api2_message(
        HistoryMessageId::Unsigned(1),
        4_000,
        "synthetic timeout page",
        vec![HistoryAttachmentKind::Photo],
        false,
    );
    message.media_sources = vec![Some(HistoryMediaSource::PhotoUrl(Zeroizing::new(
        "https://i.oneme.ru/fixture?token=synthetic-only".to_owned(),
    )))];
    let mut progress = current.api2.lock().unwrap();
    service
        .ingest_api2_page(&current, &mut progress, &synthetic_api2_page(5_000, vec![message]), 5_000)
        .unwrap();
    let waiting = service
        .transport_error_api2(&current, &mut progress, TransportError::TimedOut)
        .unwrap();
    assert_eq!(waiting["state"], "exporting");
    assert_eq!(waiting["api2"]["phase"], "media");
    assert!(waiting.get("htmlPath").is_none());
    progress.media_queue.front_mut().unwrap().source =
        HistoryMediaSource::PhotoUrl(Zeroizing::new("invalid-synthetic-source".to_owned()));
    drop(progress);
    let final_receipt = service.api2_next(&body(&start), &NoCredentials).unwrap();
    assert_eq!(final_receipt["state"], "partial");
    assert_eq!(final_receipt["api2"]["phase"], "done");
    assert_eq!(final_receipt["api2"]["mediaTotal"], 1);
    assert_eq!(final_receipt["api2"]["mediaProcessed"], 1);
    assert_eq!(final_receipt["counts"]["attachments"], 1);
    assert_eq!(final_receipt["counts"]["unavailable"], 1);
    assert_eq!(final_receipt["downloadsPending"], false);
    let path = Path::new(final_receipt["htmlPath"].as_str().unwrap());
    let manifest: Value =
        serde_json::from_slice(&fs::read(path.with_file_name("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["stopReason"], "timeout");
    assert_eq!(manifest["counts"]["mediaMissing"], 1);
}

#[test]
fn api2_drained_media_count_does_not_cap_later_pages() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 5_000);
    let mut progress = current.api2.lock().unwrap();
    service
        .ingest_api2_page(
            &current,
            &mut progress,
            &synthetic_api2_page(
                5_000,
                vec![synthetic_api2_message(
                    HistoryMessageId::Unsigned(1), 4_000, "synthetic prefix", Vec::new(), false,
                )],
            ),
            5_000,
        )
        .unwrap();
    progress.media_total = API2_MAX_MEDIA_QUEUED_TASKS as u32;
    progress.media_processed = API2_MAX_MEDIA_QUEUED_TASKS as u32;
    let mut later = synthetic_api2_message(
        HistoryMessageId::Unsigned(2), 3_000, "later window", vec![HistoryAttachmentKind::Audio], false,
    );
    later.media_sources = vec![Some(HistoryMediaSource::AudioUrl(Zeroizing::new(
        "https://a.oneme.ru/fixture?token=synthetic-only".to_owned(),
    )))];
    let overlap = synthetic_api2_message(
        HistoryMessageId::Unsigned(1), 4_000, "synthetic prefix", Vec::new(), false,
    );
    let receipt = service
        .ingest_api2_page(&current, &mut progress, &synthetic_api2_page(4_000, vec![later, overlap]), 4_000)
        .unwrap();
    assert_eq!(receipt["state"], "exporting");
    assert_eq!(receipt["counts"]["messages"], 2);
    assert_eq!(receipt["counts"]["attachments"], 1);
    assert_eq!(receipt["api2"]["mediaTotal"], 20_001);
    assert_eq!(receipt["api2"]["mediaProcessed"], 20_000);
    assert!(receipt.get("htmlPath").is_none());
    assert_eq!(progress.media_queue.len(), 1);
    progress.media_queue.front_mut().unwrap().source =
        HistoryMediaSource::AudioUrl(Zeroizing::new("invalid-synthetic-source".to_owned()));
    drop(progress);
    let settled = service.api2_next(&body(&start), &NoCredentials).unwrap();
    assert_eq!(settled["state"], "exporting");
    assert_eq!(settled["api2"]["mediaTotal"], 20_001);
    assert_eq!(settled["api2"]["mediaProcessed"], 20_001);
    assert_eq!(settled["counts"]["unavailable"], 1);
}

#[test]
fn api2_signed_url_queue_is_bounded_to_one_history_window() {
    const WINDOWS: u32 = 8;
    const PER_WINDOW: u32 = 3;
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let mut from_ms = 25_000;
    let current = bind_synthetic_api2(&service, &start, from_ms);
    let mut next_id = WINDOWS as u64 * PER_WINDOW as u64;
    let mut boundary: Option<u64> = None;
    let url = "https://a.oneme.ru/fixture?token=synthetic-window";
    for window in 0..WINDOWS {
        let mut ids = Vec::new();
        for _ in 0..PER_WINDOW {
            ids.push(next_id);
            next_id -= 1;
        }
        if let Some(value) = boundary {
            ids.push(value);
        }
        ids.sort_unstable();
        let messages: Vec<_> = ids
            .iter()
            .map(|id| {
                let mut message = synthetic_api2_message(
                    HistoryMessageId::Unsigned(*id),
                    *id as i64 * 1_000,
                    "synthetic bounded media",
                    vec![HistoryAttachmentKind::Audio],
                    false,
                );
                message.media_sources = vec![Some(HistoryMediaSource::AudioUrl(Zeroizing::new(
                    url.to_owned(),
                )))];
                message
            })
            .collect();
        let mut progress = current.api2.lock().unwrap();
        let receipt = service
            .ingest_api2_page(&current, &mut progress, &synthetic_api2_page(from_ms, messages), from_ms)
            .unwrap();
        assert_eq!(receipt["state"], "exporting");
        assert_eq!(receipt["api2"]["phase"], "history");
        assert_eq!(progress.media_queue.len(), PER_WINDOW as usize);
        assert_eq!(progress.media_url_bytes, PER_WINDOW as usize * url.len());
        assert_eq!(progress.media_total, (window + 1) * PER_WINDOW);
        boundary = Some(ids[0]);
        from_ms = ids[0] as i64 * 1_000;
        drop(progress);
        for _ in 0..PER_WINDOW {
            let mut progress = current.api2.lock().unwrap();
            // Pacing is tested separately; this fixture checks queue lifetime
            // and accounting without waiting 250 ms per synthetic omission.
            progress.last_media_completed = None;
            progress.media_queue.front_mut().unwrap().source =
                HistoryMediaSource::AudioUrl(Zeroizing::new("x".repeat(url.len())));
            drop(progress);
            let settled = service.api2_next(&body(&start), &NoCredentials).unwrap();
            assert_eq!(settled["state"], "exporting");
            assert_eq!(settled["api2"]["phase"], "history");
        }
        let progress = current.api2.lock().unwrap();
        assert!(progress.media_queue.is_empty());
        assert_eq!(progress.media_url_bytes, 0);
        assert_eq!(progress.media_processed, progress.media_total);
    }
    let last = synthetic_api2_message(
        HistoryMessageId::Unsigned(boundary.unwrap()),
        from_ms,
        "synthetic bounded media",
        vec![HistoryAttachmentKind::Audio],
        false,
    );
    let mut last = last;
    last.media_sources = vec![Some(HistoryMediaSource::AudioUrl(Zeroizing::new(
        url.to_owned(),
    )))];
    let mut progress = current.api2.lock().unwrap();
    let done = service
        .ingest_api2_page(&current, &mut progress, &synthetic_api2_page(from_ms, vec![last]), from_ms)
        .unwrap();
    assert_eq!(done["state"], "partial");
    assert_eq!(done["counts"]["attachments"], WINDOWS * PER_WINDOW);
    assert_eq!(done["counts"]["unavailable"], WINDOWS * PER_WINDOW);
    assert_eq!(done["api2"]["mediaTotal"], WINDOWS * PER_WINDOW);
    assert_eq!(done["api2"]["mediaProcessed"], WINDOWS * PER_WINDOW);
    assert_eq!(done["downloadsPending"], false);
}

#[test]
fn api2_video_reference_must_match_the_validated_message_identity() {
    let mut message = synthetic_api2_message(
        HistoryMessageId::Unsigned(1),
        1_000,
        "synthetic video",
        vec![HistoryAttachmentKind::Video],
        false,
    );
    message.media_sources = vec![Some(HistoryMediaSource::VideoRef(
        api2_history_transport::HistoryVideoRef {
            message_id: HistoryMessageId::Unsigned(2),
            video_id: 9_007_199_254_740_993,
            token: Zeroizing::new("synthetic-token".to_owned()),
        },
    ))];
    let prepared = prepare_api2_row(&message, 99, &[0; 32]).unwrap();
    assert!(prepared.media.is_empty());
    assert_eq!(prepared.value["attachments"][0]["status"], "unavailable");
}

#[test]
fn api2_untrusted_audio_origin_cannot_become_pending() {
    let mut message = synthetic_api2_message(
        HistoryMessageId::Unsigned(1),
        1_000,
        "",
        vec![HistoryAttachmentKind::Audio],
        false,
    );
    message.media_sources = vec![Some(HistoryMediaSource::AudioUrl(Zeroizing::new(
        "https://private.example.invalid/fixture".to_owned(),
    )))];
    let prepared = prepare_api2_row(&message, 99, &[0; 32]).unwrap();
    assert!(prepared.media.is_empty());
    assert_eq!(prepared.value["attachments"][0]["status"], "unavailable");
}

#[test]
fn api2_disordered_or_edited_overlap_stops_on_previous_partial_prefix() {
    for edited in [false, true] {
        let f = Fixture::new();
        let service = ExportService::default();
        let start = service.execute("start", &f.request()).unwrap();
        let current = bind_synthetic_api2(&service, &start, 6_000);
        let mut progress = current.api2.lock().unwrap();
        let first = synthetic_api2_page(
            6_000,
            vec![
                synthetic_api2_message(
                    HistoryMessageId::Unsigned(1),
                    4_000,
                    "kept first",
                    Vec::new(),
                    false,
                ),
                synthetic_api2_message(
                    HistoryMessageId::Unsigned(2),
                    5_000,
                    "kept second",
                    Vec::new(),
                    false,
                ),
            ],
        );
        service
            .ingest_api2_page(&current, &mut progress, &first, 6_000)
            .unwrap();
        let suspicious = if edited {
            synthetic_api2_page(
                4_000,
                vec![
                    synthetic_api2_message(
                        HistoryMessageId::Unsigned(3),
                        3_000,
                        "must not appear",
                        Vec::new(),
                        false,
                    ),
                    synthetic_api2_message(
                        HistoryMessageId::Unsigned(1),
                        4_000,
                        "changed boundary",
                        Vec::new(),
                        false,
                    ),
                ],
            )
        } else {
            synthetic_api2_page(
                4_000,
                vec![
                    synthetic_api2_message(
                        HistoryMessageId::Unsigned(1),
                        4_000,
                        "kept first",
                        Vec::new(),
                        false,
                    ),
                    synthetic_api2_message(
                        HistoryMessageId::Unsigned(3),
                        3_000,
                        "must not appear",
                        Vec::new(),
                        false,
                    ),
                ],
            )
        };
        let result = service
            .ingest_api2_page(&current, &mut progress, &suspicious, 4_000)
            .unwrap();
        assert_eq!(result["state"], "partial");
        assert_eq!(result["api2"], json!({"windowsFetched":2,"mediaProcessed":0,"mediaTotal":0,"phase":"done","done":true}));
        assert_eq!(result["counts"]["messages"], 2);
        let html_path = Path::new(result["htmlPath"].as_str().unwrap());
        let html = fs::read_to_string(html_path).unwrap();
        assert!(html.contains("kept first"));
        assert!(!html.contains("must not appear"));
        assert!(!html.contains("changed boundary"));
        let manifest: Value =
            serde_json::from_slice(&fs::read(html_path.with_file_name("manifest.json")).unwrap())
                .unwrap();
        assert_eq!(manifest["stopReason"], "history_error");
        assert_eq!(manifest["historyComplete"], false);
    }
}

#[test]
fn api2_timeout_keeps_partial_prefix_but_scope_or_tls_failure_never_publish() {
    for failure in [
        TransportError::TimedOut,
        TransportError::HistoryScope,
        TransportError::Tls,
    ] {
        let f = Fixture::new();
        let service = ExportService::default();
        let start = service.execute("start", &f.request()).unwrap();
        let current = bind_synthetic_api2(&service, &start, 2_000);
        let mut progress = current.api2.lock().unwrap();
        let first = synthetic_api2_page(
            2_000,
            vec![synthetic_api2_message(
                HistoryMessageId::Unsigned(1),
                1_000,
                "synthetic prefix",
                Vec::new(),
                false,
            )],
        );
        service
            .ingest_api2_page(&current, &mut progress, &first, 2_000)
            .unwrap();
        let result = service.transport_error_api2(&current, &mut progress, failure);
        match failure {
            TransportError::TimedOut => {
                let receipt = result.unwrap();
                assert_eq!(receipt["state"], "partial");
                assert_eq!(receipt["api2"], json!({"windowsFetched":1,"mediaProcessed":0,"mediaTotal":0,"phase":"done","done":true}));
                let path = Path::new(receipt["htmlPath"].as_str().unwrap());
                let manifest: Value = serde_json::from_slice(
                    &fs::read(path.with_file_name("manifest.json")).unwrap(),
                )
                .unwrap();
                assert_eq!(manifest["stopReason"], "timeout");
                assert_eq!(manifest["historyComplete"], false);
            }
            TransportError::HistoryScope | TransportError::Tls => {
                assert!(result.is_err());
                let status = service.execute("status", &body(&start)).unwrap();
                assert_eq!(status["state"], "cancelled");
                assert!(status.get("htmlFileURL").is_none());
                assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
            }
            _ => unreachable!(),
        }
    }
}

#[test]
fn api2_cancel_racing_with_terminal_timeout_never_sets_done_or_publishes() {
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 2_000);
    let mut progress = current.api2.lock().unwrap();
    let first = synthetic_api2_page(
        2_000,
        vec![synthetic_api2_message(
            HistoryMessageId::Unsigned(1),
            1_000,
            "synthetic prefix",
            Vec::new(),
            false,
        )],
    );
    service
        .ingest_api2_page(&current, &mut progress, &first, 2_000)
        .unwrap();
    let cancelled = service.execute("cancel", &body(&start)).unwrap();
    assert_eq!(cancelled["state"], "cancelled");
    assert!(
        service
            .transport_error_api2(&current, &mut progress, TransportError::TimedOut)
            .is_err()
    );
    assert!(!progress.done);
    assert_eq!(fs::read_dir(&f.root).unwrap().count(), 0);
}

#[test]
fn api2_explicit_empty_page_is_partial_but_predata_credential_error_has_no_url() {
    let empty_fixture = Fixture::new();
    let empty_service = ExportService::default();
    let empty_start = empty_service
        .execute("start", &empty_fixture.request())
        .unwrap();
    let empty_handle = bind_synthetic_api2(&empty_service, &empty_start, 2_000);
    let mut empty_progress = empty_handle.api2.lock().unwrap();
    let empty = synthetic_api2_page(2_000, Vec::new());
    let result = empty_service
        .ingest_api2_page(&empty_handle, &mut empty_progress, &empty, 2_000)
        .unwrap();
    assert_eq!(result["state"], "partial");
    assert_eq!(result["counts"]["messages"], 0);
    assert_eq!(result["api2"], json!({"windowsFetched":1,"mediaProcessed":0,"mediaTotal":0,"phase":"done","done":true}));
    assert!(
        result["htmlFileURL"]
            .as_str()
            .unwrap()
            .starts_with("file:///")
    );
    let index = Path::new(result["htmlPath"].as_str().unwrap());
    let manifest: Value =
        serde_json::from_slice(&fs::read(index.with_file_name("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "unsupported_history");

    let error_fixture = Fixture::new();
    let error_service = ExportService::default();
    let error_start = error_service
        .execute("start", &error_fixture.request())
        .unwrap();
    let error_handle = bind_synthetic_api2(&error_service, &error_start, 2_000);
    let mut error_progress = error_handle.api2.lock().unwrap();
    assert!(
        error_service
            .transport_error_api2(
                &error_handle,
                &mut error_progress,
                TransportError::Credential
            )
            .is_err()
    );
    let status = error_service
        .execute("status", &body(&error_start))
        .unwrap();
    assert_eq!(status["state"], "cancelled");
    assert!(status.get("htmlFileURL").is_none());
    assert_eq!(fs::read_dir(&error_fixture.root).unwrap().count(), 0);
}

#[test]
fn selected_api2_export_publishes_durable_local_text_in_separate_page() {
    struct RetentionFixture(PathBuf);
    impl RetentionFixture {
        fn new() -> Self {
            let base = Path::new("E:/ValMAXBuild/fixtures/synthetic/export-retention-p11");
            fs::create_dir_all(base).unwrap();
            let mut nonce = [0u8; 16];
            getrandom::getrandom(&mut nonce).unwrap();
            let root = base.join(encode_hex(&nonce));
            fs::create_dir(&root).unwrap();
            fs::create_dir(root.join("profile")).unwrap();
            Self(root)
        }
        fn profile(&self) -> PathBuf { self.0.join("profile") }
        fn request(&self) -> Value {
            json!({"schema":1,"accountId":"99","chat":{"id":"42","title":"Synthetic selected chat","timezoneOffsetMinutes":0},
                "directory":format!("file:///{}",self.0.to_str().unwrap().replace('\\',"/"))})
        }
    }
    impl Drop for RetentionFixture {
        fn drop(&mut self) {
            assert_eq!(self.0.parent(), Some(Path::new("E:/ValMAXBuild/fixtures/synthetic/export-retention-p11")));
            assert!(hex::<16>(self.0.file_name().unwrap().to_str().unwrap()).is_some());
            assert!(!fs::symlink_metadata(&self.0).unwrap().file_type().is_symlink());
            fs::remove_dir_all(&self.0).unwrap();
        }
    }

    let fixture = RetentionFixture::new();
    let store = crate::retention_store::RetentionStore::new(fixture.profile()).unwrap();
    store.capture_text(99, 42, 7, 1_000, "Synthetic locally retained body").unwrap();
    store.mark_received_delete(99, 42, &[7], 2_000).unwrap();
    store.capture_text(88, 42, 7, 1_100, "Other account synthetic body").unwrap();
    store.mark_received_delete(88, 42, &[7], 2_100).unwrap();
    let service = ExportService::with_retention_profile(&fixture.profile());
    let start = service.execute("start", &fixture.request()).unwrap();
    let current = bind_synthetic_api2(&service, &start, 3_000);
    let mut progress = current.api2.lock().unwrap();
    let page = synthetic_api2_page(3_000, vec![synthetic_api2_message(
        HistoryMessageId::Unsigned(7), 1_500, "Synthetic API2 body", Vec::new(), false,
    )]);
    service.ingest_api2_page(&current, &mut progress, &page, 3_000).unwrap();
    let receipt = service.finish_api2(&current, &mut progress, "no_progress").unwrap();
    assert_eq!(receipt["state"], "partial");
    assert!(!receipt.to_string().contains("Synthetic locally retained body"));
    let html_path = Path::new(receipt["htmlPath"].as_str().unwrap());
    let main = fs::read_to_string(html_path).unwrap();
    let local = fs::read_to_string(html_path.with_file_name("local-deleted.html")).unwrap();
    let manifest: Value = serde_json::from_slice(&fs::read(html_path.with_file_name("manifest.json")).unwrap()).unwrap();
    assert!(main.contains("local-deleted.html"));
    assert!(main.contains("Synthetic API2 body"));
    assert!(!main.contains("Synthetic locally retained body"));
    assert!(local.contains("Synthetic locally retained body"));
    assert!(!local.contains("Other account synthetic body"));
    assert!(!local.contains("Synthetic API2 body"));
    assert_eq!(manifest["localDeletedCount"], 1);
    assert_eq!(manifest["localDeletedFile"], "local-deleted.html");
}

#[test]
#[ignore = "manual synthetic selected-chat scale check"]
fn api2_synthetic_37019_messages_3061_media_placeholders_render_partial() {
    const TOTAL: u64 = 37_019;
    const ATTACHMENTS: u64 = 3_061;
    let f = Fixture::new();
    let service = ExportService::default();
    let start = service.execute("start", &f.request()).unwrap();
    let mut from_ms = (TOTAL as i64 + 1) * 1_000;
    let current = bind_synthetic_api2(&service, &start, from_ms);
    let mut progress = current.api2.lock().unwrap();
    let total_started = std::time::Instant::now();
    let mut remaining = TOTAL;
    let mut boundary: Option<u64> = None;
    while remaining > 0 {
        let mut ids = Vec::with_capacity(100);
        for _ in 0..if boundary.is_some() { 99 } else { 100 } {
            if remaining == 0 {
                break;
            }
            ids.push(remaining);
            remaining -= 1;
        }
        if let Some(value) = boundary {
            ids.push(value);
        }
        ids.sort_unstable();
        let messages = ids
            .iter()
            .map(|id| {
                synthetic_api2_message(
                    HistoryMessageId::Unsigned(*id),
                    *id as i64 * 1_000,
                    "synthetic scale text",
                    if *id <= ATTACHMENTS {
                        vec![HistoryAttachmentKind::Photo]
                    } else {
                        Vec::new()
                    },
                    false,
                )
            })
            .collect();
        let page = synthetic_api2_page(from_ms, messages);
        let receipt = service
            .ingest_api2_page(&current, &mut progress, &page, from_ms)
            .unwrap();
        assert_eq!(receipt["state"], "exporting");
        boundary = Some(ids[0]);
        from_ms = ids[0] as i64 * 1_000;
    }
    assert_eq!(progress.seen.len(), TOTAL as usize);
    let ingest_elapsed = total_started.elapsed();
    let last = synthetic_api2_page(
        from_ms,
        vec![synthetic_api2_message(
            HistoryMessageId::Unsigned(boundary.unwrap()),
            from_ms,
            "synthetic scale text",
            vec![HistoryAttachmentKind::Photo],
            false,
        )],
    );
    let render_started = std::time::Instant::now();
    let result = service
        .ingest_api2_page(&current, &mut progress, &last, from_ms)
        .unwrap();
    let render_elapsed = render_started.elapsed();
    eprintln!(
        "synthetic API2: ingest_ms={}, final_render_ms={}, windows={}",
        ingest_elapsed.as_millis(),
        render_elapsed.as_millis(),
        progress.windows_fetched
    );
    assert_eq!(result["state"], "partial");
    assert_eq!(result["counts"]["messages"], TOTAL);
    assert_eq!(result["counts"]["attachments"], ATTACHMENTS);
    assert_eq!(result["counts"]["ready"], 0);
    assert_eq!(result["counts"]["unavailable"], ATTACHMENTS);
    assert_eq!(result["api2"]["windowsFetched"], 375);
    assert_eq!(result["api2"]["done"], true);
    let index = Path::new(result["htmlPath"].as_str().unwrap());
    assert!(index.is_file());
    assert!(index.with_file_name("messages-38.html").is_file());
    let manifest: Value =
        serde_json::from_slice(&fs::read(index.with_file_name("manifest.json")).unwrap()).unwrap();
    assert_eq!(manifest["historyComplete"], false);
    assert_eq!(manifest["stopReason"], "no_progress");
    assert_eq!(manifest["pagesFetched"], 375);
    assert_eq!(manifest["htmlPages"], 38);
    assert_eq!(manifest["counts"]["mediaMissing"], ATTACHMENTS);
}
