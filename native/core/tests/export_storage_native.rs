#![cfg(windows)]
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs,
    path::{Path, PathBuf},
};
use valmax_core::export_render::{Error, RenderOptions, RenderSink};
use valmax_core::export_storage::{Cancellation, ExportWorkspace, absolute_path};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let id: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let path = Path::new("C:/ValMAXBuild/fixtures/export-storage").join(id);
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn output(&self) -> PathBuf {
        self.0.join("export")
    }
    fn workspace(&self) -> ExportWorkspace {
        ExportWorkspace::new(self.output(), Cancellation::default()).unwrap()
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize("C:/ValMAXBuild/fixtures/export-storage").unwrap();
        let path = fs::canonicalize(&self.0).unwrap();
        assert!(path.starts_with(&base) && path != base);
        fs::remove_dir_all(path).unwrap();
    }
}
fn chat() -> Value {
    json!({"id":"synthetic-chat","title":"Synthetic <export>","timezoneOffsetMinutes":180})
}
fn completion() -> Value {
    json!({"historyComplete":true,"stopReason":"end_of_history","pagesFetched":1})
}
fn message(id: u64) -> Value {
    json!({"id":id.to_string(),"author":{"id":"synthetic-author","name":"Synthetic & name"},
    "timestampMs":0,"text":"Synthetic <body>\nsecond line","outgoing":false,"reply":null,"attachments":[]})
}
fn render_empty(work: &mut ExportWorkspace) {
    work.render(
        &chat(),
        Vec::<Result<Value, Error>>::new(),
        &completion(),
        RenderOptions::default(),
        None,
    )
    .unwrap();
}
fn media(work: &mut ExportWorkspace) -> Vec<u8> {
    let bytes = b"\x89PNG\r\n\x1a\nSYNTHETIC-MEDIA".to_vec();
    work.reserve_asset("media/synthetic.png").unwrap();
    assert_eq!(
        work.write_asset_chunk("media/synthetic.png", 0, &bytes[..8], 1024)
            .unwrap(),
        8
    );
    assert_eq!(
        work.write_asset_chunk("media/synthetic.png", 8, &bytes[8..], 1024)
            .unwrap(),
        bytes.len() as u64
    );
    bytes
}

#[test]
fn renders_and_atomically_publishes_owned_media() {
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    let stage = work.staging_path().to_owned();
    let bytes = media(&mut work);
    let verified = work.verify_asset("media/synthetic.png", 1024).unwrap();
    assert_eq!(verified.bytes, bytes.len() as u64);
    assert_eq!(verified.head, bytes);
    assert_eq!(verified.sha256, format!("{:x}", Sha256::digest(&bytes)));
    let mut row = message(1);
    row["attachments"] = json!([{"id":"1","type":"image","name":"synthetic.png","mime":"image/png","status":"ready","reason":null,"source":"media/synthetic.png"}]);
    let report = work
        .render(
            &chat(),
            [Ok(row)],
            &completion(),
            RenderOptions::default(),
            None,
        )
        .unwrap();
    assert_eq!(report["counts"]["messages"], 1);
    assert!(!fixture.output().exists());
    assert_eq!(
        work.publish().unwrap(),
        absolute_path(&fixture.output()).unwrap()
    );
    assert!(!stage.exists());
    assert_eq!(
        fs::read(fixture.output().join("media/synthetic.png")).unwrap(),
        bytes
    );
    let html = fs::read_to_string(fixture.output().join("index.html")).unwrap();
    assert!(html.contains("Synthetic &lt;body&gt;"));
    assert!(html.contains("script-src 'none'"));
    assert!(html.contains("media/synthetic.png"));
    work.abort().unwrap();
    assert!(fixture.output().exists());
}

#[test]
fn paged_render_flushes_buffered_utf8_and_preserves_navigation() {
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    work.render(
        &chat(),
        (0..2003).map(|i| {
            let mut m = message(i);
            m["text"] = json!("Проверка 🦀 <&>".repeat(7));
            Ok(m)
        }),
        &completion(),
        RenderOptions::default(),
        None,
    )
    .unwrap();
    work.publish().unwrap();
    for name in [
        "index.html",
        "messages-2.html",
        "messages-3.html",
        "manifest.json",
        "assets.jsonl",
        "omissions.jsonl",
    ] {
        assert!(fixture.output().join(name).is_file());
    }
    let final_page = fs::read_to_string(fixture.output().join("messages-3.html")).unwrap();
    assert!(final_page.contains("Всего в копии: 2003"));
    assert!(final_page.contains("Проверка 🦀 &lt;&amp;&gt;"));
}

#[test]
fn existing_and_racing_destinations_are_never_replaced() {
    let fixture = Fixture::new();
    fs::create_dir(fixture.output()).unwrap();
    assert!(matches!(
        ExportWorkspace::new(fixture.output(), Cancellation::default()),
        Err(Error::Exists)
    ));
    fs::remove_dir(fixture.output()).unwrap();
    let mut work = fixture.workspace();
    media(&mut work);
    render_empty(&mut work);
    let stage = work.staging_path().to_owned();
    fs::create_dir(fixture.output()).unwrap();
    fs::write(fixture.output().join("keep.txt"), b"SYNTHETIC-OTHER-EXPORT").unwrap();
    assert_eq!(work.publish(), Err(Error::Exists));
    work.abort().unwrap();
    assert!(!stage.exists());
    assert_eq!(
        fs::read(fixture.output().join("keep.txt")).unwrap(),
        b"SYNTHETIC-OTHER-EXPORT"
    );
}

#[test]
fn failed_publish_cleans_empty_owned_media_directory() {
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    media(&mut work);
    work.discard_asset("media/synthetic.png").unwrap();
    render_empty(&mut work);
    let stage = work.staging_path().to_owned();
    fs::create_dir(fixture.output()).unwrap();
    assert_eq!(work.publish(), Err(Error::Exists));
    work.abort().unwrap();
    assert!(!stage.exists());
}

#[test]
fn cancel_and_iterator_errors_poison_publication_and_cleanup_owned_files() {
    for mode in 0..3 {
        let fixture = Fixture::new();
        let mut work = fixture.workspace();
        let stage = work.staging_path().to_owned();
        if mode == 0 {
            work.cancellation().cancel();
            assert_eq!(
                work.render(
                    &chat(),
                    [Ok(message(1))],
                    &completion(),
                    RenderOptions::default(),
                    None
                ),
                Err(Error::Cancelled)
            );
        }
        if mode == 1 {
            assert_eq!(
                work.render(
                    &chat(),
                    [Ok(message(1)), Err(Error::IoError)],
                    &completion(),
                    RenderOptions::default(),
                    None
                ),
                Err(Error::IoError)
            );
        }
        if mode == 2 {
            render_empty(&mut work);
            work.cancellation().cancel();
        }
        assert!(work.publish().is_err());
        work.abort().unwrap();
        work.abort().unwrap();
        assert!(!stage.exists());
        assert!(!fixture.output().exists());
    }
}

#[test]
fn missing_and_oversize_owned_media_can_be_discarded() {
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    media(&mut work);
    assert!(matches!(
        work.verify_asset("media/synthetic.png", 1),
        Err(Error::AssetTooLarge)
    ));
    work.discard_asset("media/synthetic.png").unwrap();
    assert!(!work.staging_path().join("media/synthetic.png").exists());
    media(&mut work);
    fs::remove_file(work.staging_path().join("media/synthetic.png")).unwrap();
    assert!(matches!(
        work.verify_asset("media/synthetic.png", 1024),
        Err(Error::SourceUnavailable)
    ));
    work.discard_asset("media/synthetic.png").unwrap();
    render_empty(&mut work);
    work.publish().unwrap();
}

#[test]
fn offsets_reservations_and_generated_paths_are_strict() {
    let cases = [
        (0, 0usize, 1024),
        (0, 49153, 1024 * 1024),
        (0, 1, 0),
        (0, 1, 64 * 1024 * 1024 + 1),
        (u64::MAX, 1, 1024),
        (1, 1, 1024),
    ];
    for (offset, len, max) in cases {
        let fixture = Fixture::new();
        let mut work = fixture.workspace();
        work.reserve_asset("media/x.bin").unwrap();
        assert!(
            work.write_asset_chunk("media/x.bin", offset, &vec![0; len], max)
                .is_err()
        );
        assert!(work.publish().is_err());
        work.abort().unwrap();
    }
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    assert_eq!(work.begin_file("../index.html"), Err(Error::InvalidPath));
    work.abort().unwrap();
    let invalid = [
        "relative",
        "C:/",
        "C:/test/../other",
        "C:/test//other",
        "C:/test/NUL.txt",
        "C:/test/thing:stream",
        "C:/test/trailing.",
        "C:/test/trailing ",
        "//server/share/name",
        "\\\\?\\C:\\test",
        "C:/test/\u{7f}name",
    ];
    for path in invalid {
        assert!(absolute_path(Path::new(path)).is_err(), "{path}");
    }
    assert!(absolute_path(Path::new("C:/тест/Сохранённый чат")).is_ok());
}

#[test]
fn unknown_entries_and_hardlinks_do_not_get_deleted() {
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    let stage = work.staging_path().to_owned();
    fs::write(stage.join("unexpected.txt"), b"SYNTHETIC-UNOWNED").unwrap();
    render_empty(&mut work);
    assert_eq!(work.publish(), Err(Error::UnsafePath));
    assert_eq!(work.abort(), Err(Error::CleanupFailed));
    assert_eq!(work.abort(), Err(Error::CleanupFailed));
    assert_eq!(
        fs::read(stage.join("unexpected.txt")).unwrap(),
        b"SYNTHETIC-UNOWNED"
    );
    let mut work = ExportWorkspace::new(fixture.0.join("second"), Cancellation::default()).unwrap();
    media(&mut work);
    let stage2 = work.staging_path().to_owned();
    let outside = fixture.0.join("outside.bin");
    fs::write(&outside, b"SYNTHETIC-KEEP").unwrap();
    fs::remove_file(stage2.join("media/synthetic.png")).unwrap();
    fs::hard_link(&outside, stage2.join("media/synthetic.png")).unwrap();
    assert!(matches!(
        work.verify_asset("media/synthetic.png", 1024),
        Err(Error::UnsafePath)
    ));
    assert_eq!(work.abort(), Err(Error::CleanupFailed));
    assert_eq!(fs::read(&outside).unwrap(), b"SYNTHETIC-KEEP");
}

#[test]
fn pins_prevent_parent_and_stage_replacement() {
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    let stage = work.staging_path().to_owned();
    assert!(fs::rename(&stage, fixture.0.join("replaced")).is_err());
    assert!(fs::rename(&fixture.0, fixture.0.with_extension("replacement")).is_err());
    work.abort().unwrap();
    assert!(!stage.exists());
}

#[test]
fn reparse_ancestor_is_rejected_without_touching_its_target() {
    use std::{os::windows::process::CommandExt, process::Command};
    let fixture = Fixture::new();
    let target = fixture.0.join("target");
    let junction = fixture.0.join("junction");
    fs::create_dir(&target).unwrap();
    fs::write(target.join("keep.txt"), b"SYNTHETIC-TARGET").unwrap();
    let output = Command::new("cmd.exe")
        .args(["/D", "/C", "mklink", "/J"])
        .arg(absolute_path(&junction).unwrap())
        .arg(absolute_path(&target).unwrap())
        .creation_flags(0x08000000)
        .output()
        .unwrap();
    assert!(
        output.status.success(),
        "could not create own synthetic junction"
    );
    assert!(matches!(
        ExportWorkspace::new(junction.join("export"), Cancellation::default()),
        Err(Error::UnsafePath)
    ));
    assert_eq!(
        fs::read(target.join("keep.txt")).unwrap(),
        b"SYNTHETIC-TARGET"
    );
    assert!(!target.join("export").exists());
    // Remove only the junction entry, before the fixture's bounded cleanup.
    fs::remove_dir(&junction).unwrap();
}

#[test]
fn raw_writes_cannot_authorize_publish_and_changed_owned_asset_is_an_omission() {
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    work.begin_file("index.html").unwrap();
    work.write_text("index.html", "not-a-complete-export")
        .unwrap();
    assert_eq!(work.publish(), Err(Error::UnsafePath));
    work.abort().unwrap();
    let mut work = fixture.workspace();
    media(&mut work);
    fs::write(
        work.staging_path().join("media/synthetic.png"),
        b"SYNTHETIC-CHANGED",
    )
    .unwrap();
    assert!(matches!(
        work.verify_asset("media/synthetic.png", 1024),
        Err(Error::SourceChanged)
    ));
    work.discard_asset("media/synthetic.png").unwrap();
    render_empty(&mut work);
    work.publish().unwrap();
}

#[test]
fn sequential_media_does_not_retain_one_handle_per_attachment() {
    use std::ffi::c_void;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetCurrentProcess() -> *mut c_void;
        fn GetProcessHandleCount(process: *mut c_void, count: *mut u32) -> i32;
    }
    fn count() -> u32 {
        let mut result = 0;
        assert_ne!(
            unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut result) },
            0
        );
        result
    }
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    let baseline = count();
    for n in 0..384 {
        let name = format!("media/asset-{n}.bin");
        work.reserve_asset(&name).unwrap();
        work.write_asset_chunk(&name, 0, b"SYNTHETIC", 1024)
            .unwrap();
    }
    // The workspace retains directory handles and metadata, not 384 file handles.
    // Tolerance covers other parallel test threads in ordinary cargo test runs.
    assert!(count() < baseline + 128);
    work.abort().unwrap();
}

#[test]
fn failed_delete_remains_a_reported_cleanup_failure() {
    use std::os::windows::fs::OpenOptionsExt;
    let fixture = Fixture::new();
    let mut work = fixture.workspace();
    let stage = work.staging_path().to_owned();
    work.render(
        &chat(),
        (0..1001).map(|id| Ok(message(id))),
        &completion(),
        RenderOptions::default(),
        None,
    )
    .unwrap();
    // Page one was sealed; hold it without delete sharing to force the real OS
    // cleanup failure, without mocking a successful filesystem operation.
    let held = fs::OpenOptions::new()
        .read(true)
        .share_mode(1)
        .open(stage.join("index.html"))
        .unwrap();
    assert_eq!(work.abort(), Err(Error::CleanupFailed));
    assert_eq!(work.abort(), Err(Error::CleanupFailed));
    assert!(stage.join("index.html").exists());
    assert!(!fixture.output().exists());
    drop(held);
}
