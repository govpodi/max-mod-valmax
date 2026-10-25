use std::collections::HashMap;
use serde_json::{Value, json};
use zeroize::Zeroizing;
use valmax_core::export_render::{self as render, Error, LocalDeletedRow, RenderOptions, RenderSink, VerifiedAsset};

#[derive(Default)]
struct Sink { files: HashMap<String, String> }
impl RenderSink for Sink {
    fn begin_file(&mut self, name: &str) -> render::Result<()> {
        if self.files.contains_key(name) { return Err(Error::Exists); }
        self.files.insert(name.into(), String::new()); Ok(())
    }
    fn write_text(&mut self, name: &str, text: &str) -> render::Result<()> {
        self.files.get_mut(name).ok_or(Error::SourceUnavailable)?.push_str(text); Ok(())
    }
    fn verify_asset(&mut self, _: &str, _: u64) -> render::Result<VerifiedAsset> { Err(Error::SourceUnavailable) }
    fn discard_asset(&mut self, _: &str) -> render::Result<()> { Ok(()) }
    fn check_cancelled(&mut self) -> render::Result<()> { Ok(()) }
}

fn chat() -> Value { json!({"id":"synthetic-chat","title":"Synthetic <chat>","timezoneOffsetMinutes":180}) }
fn completion() -> Value {
    json!({"historyComplete":false,"stopReason":"unsupported_history","pagesFetched":1,
        "retainedLocal":true,"retainedLocalCount":1})
}

#[test]
fn selected_local_section_is_separate_escaped_and_labelled_without_wire_ids() {
    let mut sink = Sink::default();
    let row = LocalDeletedRow { observed_deleted_at_ms: 1_700_000_000_000,
        text: Zeroizing::new("Synthetic <b>retained</b> & text".into()) };
    assert_eq!(render::render_local_deleted_into(&mut sink, &chat(), [Ok(row)]).unwrap(), 1);
    let html = &sink.files[render::LOCAL_DELETED_FILE];
    assert!(html.contains("Synthetic &lt;b&gt;retained&lt;/b&gt; &amp; text"));
    assert!(html.contains("Synthetic &lt;chat&gt;"));
    assert!(!html.contains("<b>retained</b>"));
    assert!(html.contains("Наблюдаемое удаление"));
    assert!(html.contains("Исходное время отправки и полный порядок переписки не подтверждены"));
    assert!(!html.contains("messageId") && !html.contains("accountId"));

    let result = render::render_into(&mut sink, &chat(), std::iter::empty(),
        &completion(), RenderOptions::default(), None).unwrap();
    assert_eq!(result["counts"]["messages"], 0);
    assert!(sink.files["index.html"].contains("href=\"local-deleted.html\""));
    assert!(sink.files["index.html"].contains("Бета: чат может быть неполным."));
    assert!(sink.files["index.html"].contains("полученные после включения функции"));
    let manifest: Value = serde_json::from_str(&sink.files["manifest.json"]).unwrap();
    assert_eq!(manifest["localDeletedFile"], render::LOCAL_DELETED_FILE);
    assert_eq!(manifest["localDeletedCount"], 1);
}

#[test]
fn empty_local_section_is_explicit_and_invalid_completion_or_content_rejects() {
    let mut sink = Sink::default();
    assert_eq!(render::render_local_deleted_into(&mut sink, &chat(),
        std::iter::empty()).unwrap(), 0);
    assert!(sink.files[render::LOCAL_DELETED_FILE].contains("нет локально сохранённых удалений"));
    let invalid = LocalDeletedRow { observed_deleted_at_ms: 1_700_000_000_000,
        text: Zeroizing::new("Synthetic\0bad".into()) };
    let mut rejected = Sink::default();
    assert_eq!(render::render_local_deleted_into(&mut rejected, &chat(), [Ok(invalid)]),
        Err(Error::InvalidMessage));
    for invalid in [
        json!({"historyComplete":false,"stopReason":"unsupported_history","pagesFetched":1,"retainedLocal":true}),
        json!({"historyComplete":false,"stopReason":"unsupported_history","pagesFetched":1,"retainedLocal":false,"retainedLocalCount":0}),
        json!({"historyComplete":false,"stopReason":"unsupported_history","pagesFetched":1,"retainedLocal":true,"retainedLocalCount":65_537}),
    ] { assert_eq!(render::validate_completion(&invalid), Err(Error::InvalidCompletion)); }
}
