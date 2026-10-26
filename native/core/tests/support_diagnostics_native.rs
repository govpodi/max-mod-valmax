#![cfg(windows)]
use serde_json::{Value, json};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Barrier},
};
use valmax_core::{settings_io::SafeStorePath, support_diagnostics::*};

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut nonce = [0u8; 16];
        getrandom::getrandom(&mut nonce).unwrap();
        let id: String = nonce.iter().map(|b| format!("{b:02x}")).collect();
        let path = fixture_root().join(id);
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }
    fn url(&self, name: &str) -> String {
        format!(
            "file:///{}",
            self.0.join(name).to_str().unwrap().replace('\\', "/")
        )
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = fs::canonicalize(fixture_root()).unwrap();
        let path = fs::canonicalize(&self.0).unwrap();
        assert!(path.starts_with(&base) && path != base);
        fs::remove_dir_all(path).unwrap();
    }
}
fn fixture_root() -> PathBuf {
    let root = std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(std::env::temp_dir)
        .join("support-diagnostics");
    fs::create_dir_all(&root).unwrap();
    root
}
fn sample() -> Value {
    build_report(
        &json!({"valmax":"8.8.4","max":"26.35.1.80662","windows":"10.0.26100","qt":"6.11.2","python":"3.14.6"}),
        &json!({"role":"launcher","stage":"complete","state":"ready","code":"none","operationId":"SYNTHETIC-SECRET"}),
        &json!({"verified":true,"observation":{"seconds":7,"active":true},"errors":{"hook_failed":2}}),
        &json!({"settings":{"enabled":true,"hideRead":true},"revision":"SYNTHETIC-SECRET"}),
        &json!({"settings":{"enabled":true,"smoothWheel":true}}),
    )
}
#[test]
fn strict_allowlist_never_serializes_broad_snapshot_extras() {
    let report = sample();
    let raw = serialize(&report).unwrap();
    assert_eq!(serde_json::from_slice::<Value>(&raw).unwrap(), report);
    assert!(raw.len() < MAX_BYTES);
    let text = String::from_utf8(raw).unwrap();
    for forbidden in [
        "SYNTHETIC-SECRET",
        "operationId",
        "revision",
        "python",
        "timestamp",
        "accountId",
    ] {
        assert!(!text.contains(forbidden));
    }
    assert_eq!(report["schema"], 2);
    assert_eq!(report["runtime"]["kind"], "native");
    assert_eq!(report["errors"]["hook_failed"], 2);
    assert_eq!(report["errors"]["attach_failed"], 0);
    assert!(report["counters"]["dashboard"]["observedBytes"].is_null());
    let secret = json!({"token":"SYNTHETIC-SECRET","accountId":"SYNTHETIC-SECRET","events":[{"text":"SYNTHETIC-SECRET"}],
        "versions":{"valmax":"SYNTHETIC-SECRET"},"settings":{"enabled":"SYNTHETIC-SECRET"},
        "observation":{"active":"SYNTHETIC-SECRET","seconds":"SYNTHETIC-SECRET"},"errors":{"SYNTHETIC-SECRET":42}});
    let report = build_report(&secret, &secret, &secret, &secret, &secret);
    assert!(
        !String::from_utf8(serialize(&report).unwrap())
            .unwrap()
            .contains("SYNTHETIC-SECRET")
    );
    assert!(report["startup"].is_null());
    assert!(report["flags"]["ghost"]["enabled"].is_null());
}
#[test]
fn invalid_scalars_and_unavailable_data_remain_null() {
    for value in [
        json!(-1),
        json!(true),
        json!(MAX_COUNT + 1),
        json!(4.0),
        json!("4"),
        json!([4]),
    ] {
        let report = build_report(
            &Value::Null,
            &Value::Null,
            &json!({"observation":{"seconds":value}}),
            &Value::Null,
            &Value::Null,
        );
        assert!(report["counters"]["observation"]["seconds"].is_null());
        serialize(&report).unwrap();
    }
    for value in [
        json!("8.8.4 Beta"),
        json!("1.2\n"),
        json!("1.2.3.4.5"),
        json!("999999.1"),
        json!("１.２"),
        json!(3),
    ] {
        let report = build_report(
            &json!({"valmax":value}),
            &Value::Null,
            &Value::Null,
            &Value::Null,
            &Value::Null,
        );
        assert!(report["versions"]["valmax"].is_null());
        serialize(&report).unwrap();
    }
    let report = build_report(
        &Value::Null,
        &Value::Null,
        &Value::Null,
        &Value::Null,
        &Value::Null,
    );
    assert!(
        report["errors"]
            .as_object()
            .unwrap()
            .values()
            .all(Value::is_null)
    );
}
#[test]
fn extra_fields_or_wrong_types_rejected_at_every_level() {
    for pointer in [
        "",
        "/versions",
        "/runtime",
        "/startup",
        "/flags",
        "/flags/ghost",
        "/flags/optimization",
        "/counters",
        "/counters/observation",
        "/counters/dashboard",
        "/errors",
    ] {
        let mut report = sample();
        report.pointer_mut(pointer).unwrap()["private"] = json!("SYNTHETIC-SECRET");
        assert_eq!(serialize(&report), Err(ReportError::InvalidReport));
    }
    for (pointer, value) in [
        ("/schema", json!(true)),
        ("/schema", json!(2.0)),
        ("/runtime/kind", json!("python")),
        ("/runtime/core", json!("private")),
        ("/startup/code", json!("private")),
        ("/flags/verified", json!(1)),
        ("/flags/ghost/enabled", json!(0)),
        ("/counters/observation/seconds", json!(7.0)),
    ] {
        let mut report = sample();
        *report.pointer_mut(pointer).unwrap() = value;
        assert_eq!(serialize(&report), Err(ReportError::InvalidReport));
    }
}
#[test]
fn legacy_schema_one_is_readable_without_mislabeling_runtime() {
    let mut report = sample();
    report.as_object_mut().unwrap().remove("runtime");
    report["schema"] = json!(1);
    report["versions"]["python"] = json!("3.13.5");
    assert_eq!(
        serde_json::from_slice::<Value>(&serialize(&report).unwrap()).unwrap(),
        report
    );
    report["versions"]["core"] = json!("0.1.0");
    assert_eq!(serialize(&report), Err(ReportError::InvalidReport));
}
#[test]
fn save_file_url_strict_decoding_no_authority_or_traversal() {
    for url in [
        "",
        "report.json",
        "https://example.invalid/report.json",
        "file://server/a.json",
        "file://localhost/C:/x.json",
        "file:////C:/x.json",
        "file:///C:/../x.json",
        "file:///C:/a//x.json",
        "file:///C:/x.json?token=private",
        "file:///C:/x.json#private",
        "file:///C:/x.json:stream",
        "file:///C:/NUL.json",
        "file:///C:/x.JSON.",
        "file:///C:/%00x.json",
        "file:///C:/%ff.json",
        "file:///C:/%zz.json",
        "file:///C:/x%2.json",
        "file:///C:/%2e%2e/x.json",
        "file:///C:/x.log",
    ] {
        assert_eq!(selected_file(url), Err(ReportError::UnsafePath), "{url}");
    }
    assert_eq!(
        selected_file("file:///C:/safe/%D0%BE%D1%82%D1%87%D1%91%D1%82%20%231.JSON").unwrap(),
        PathBuf::from("C:\\safe\\отчёт #1.JSON")
    );
    assert_eq!(
        selected_file("file:///C:/safe/%252e%252e.json").unwrap(),
        PathBuf::from("C:\\safe\\%2e%2e.json")
    );
}
#[test]
fn explicit_new_file_written_and_existing_file_never_overwritten() {
    let fixture = Fixture::new();
    let url = fixture.url("отчёт.json");
    let path = selected_file(&url).unwrap();
    assert_eq!(
        save_report(&url, &sample()).unwrap(),
        json!({"schema":1,"saved":true})
    );
    let original = fs::read(&path).unwrap();
    assert_eq!(
        serde_json::from_slice::<Value>(&original).unwrap(),
        sample()
    );
    assert_eq!(save_report(&url, &sample()), Err(ReportError::FileExists));
    assert_eq!(fs::read(&path).unwrap(), original);
    assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 1);
    let mut invalid = sample();
    invalid["private"] = json!("SYNTHETIC-SECRET");
    assert_eq!(
        save_report(&fixture.url("invalid.json"), &invalid),
        Err(ReportError::InvalidReport)
    );
    assert!(!fixture.0.join("invalid.json").exists());
    assert_eq!(
        save_report(&fixture.url("missing/report.json"), &sample()),
        Err(ReportError::UnsafePath)
    );
    assert!(!fixture.0.join("missing").exists());
}
#[test]
fn concurrent_publication_exactly_one_winner_and_no_partial_or_temporary_files() {
    let fixture = Fixture::new();
    let path = fixture.0.join("race.json");
    let barrier = Arc::new(Barrier::new(8));
    let workers: Vec<_> = (0..8)
        .map(|n| {
            let path = path.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                let guard = SafeStorePath::new(&path)
                    .unwrap()
                    .begin(false)
                    .unwrap()
                    .unwrap();
                let bytes = format!("{{\"winner\":{n}}}\n").into_bytes();
                barrier.wait();
                (n, guard.atomic_create(&bytes))
            })
        })
        .collect();
    let outcomes: Vec<_> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    let winners: Vec<_> = outcomes
        .iter()
        .filter(|(_, result)| result.is_ok())
        .collect();
    assert_eq!(winners.len(), 1);
    // A still-held winner file can cause sharing violation before losers see
    // name collision. Both failures must preserve its bytes and clean the temp.
    assert!(outcomes.iter().all(|(_, result)| result.is_ok()
        || matches!(
            result,
            Err(valmax_core::settings_io::StoreError::FileExists
                | valmax_core::settings_io::StoreError::StoreBusy)
        )));
    let written: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(written["winner"], winners[0].0);
    assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 1);
}
#[test]
fn parent_pins_and_existing_hardlink_preserve_foreign_content() {
    let fixture = Fixture::new();
    let parent = fixture.0.join("pinned");
    fs::create_dir(&parent).unwrap();
    let guard = SafeStorePath::new(parent.join("report.json"))
        .unwrap()
        .begin(false)
        .unwrap()
        .unwrap();
    assert!(fs::rename(&parent, fixture.0.join("moved")).is_err());
    guard.atomic_create(b"{}").unwrap();
    drop(guard);
    let foreign = fixture.0.join("foreign.json");
    fs::write(&foreign, b"SYNTHETIC-PRIVATE").unwrap();
    fs::hard_link(&foreign, fixture.0.join("alias.json")).unwrap();
    assert_eq!(
        save_report(&fixture.url("alias.json"), &sample()),
        Err(ReportError::FileExists)
    );
    assert_eq!(fs::read(foreign).unwrap(), b"SYNTHETIC-PRIVATE");
}

#[test]
fn reparse_parent_and_existing_directory_are_never_followed_or_replaced() {
    use std::{os::windows::process::CommandExt, process::Command};
    let fixture = Fixture::new();
    let target = fixture.0.join("target");
    let junction = fixture.0.join("junction");
    fs::create_dir(&target).unwrap();
    let absolute_target = target.to_str().unwrap().replace('/', "\\");
    let absolute_junction = junction.to_str().unwrap().replace('/', "\\");
    let made = Command::new("cmd.exe")
        .args([
            "/D",
            "/C",
            "mklink",
            "/J",
            &absolute_junction,
            &absolute_target,
        ])
        .creation_flags(0x08000000)
        .output()
        .unwrap();
    assert!(made.status.success(), "own test junction must be created");
    assert_eq!(
        save_report(&fixture.url("junction/report.json"), &sample()),
        Err(ReportError::UnsafePath)
    );
    assert_eq!(fs::read_dir(&target).unwrap().count(), 0);
    // Remove only the known directory-link itself before recursive fixture cleanup.
    fs::remove_dir(&junction).unwrap();
    let directory = fixture.0.join("directory.json");
    fs::create_dir(&directory).unwrap();
    fs::write(directory.join("sentinel"), b"PRESERVE").unwrap();
    assert!(save_report(&fixture.url("directory.json"), &sample()).is_err());
    assert_eq!(fs::read(directory.join("sentinel")).unwrap(), b"PRESERVE");
    assert_eq!(fs::read_dir(&fixture.0).unwrap().count(), 2);
}
