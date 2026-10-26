use std::{fs, os::windows::fs::MetadataExt, ops::Deref, path::{Path, PathBuf}};
use serde_json::{Value, json};
use valmax_core::{retention_store::{IncomingText, RetentionStore}, runtime::Runtime};
use rusqlite::Connection;

fn fixture_base() -> PathBuf {
    let root = std::env::var_os("VALMAX_TEST_TEMP_ROOT")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("E:/ValMAXBuild/fixtures/synthetic"));
    assert!(root.is_absolute(), "synthetic fixture root must be absolute");
    root.join("retention-runtime-p11")
}
struct Fixture(PathBuf, PathBuf);
impl Deref for Fixture {
    type Target = Path;
    fn deref(&self) -> &Path { &self.0 }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let base = self.1.as_path();
        let ordinary_base = base.ancestors().take_while(|path| path.parent().is_some())
            .all(|path| fs::symlink_metadata(path).is_ok_and(|metadata|
                metadata.is_dir() && metadata.file_attributes() & 0x400 == 0));
        let resolved_inside_base = fs::canonicalize(base).ok().zip(fs::canonicalize(&self.0).ok())
            .is_some_and(|(resolved_base, resolved_child)|
                resolved_child.parent() == Some(resolved_base.as_path()));
        let safe = ordinary_base && resolved_inside_base
            && self.0.parent() == Some(base)
            && self.0.file_name().and_then(|value| value.to_str())
                .is_some_and(|value| value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit()))
            && fs::symlink_metadata(&self.0).is_ok_and(|metadata|
                metadata.is_dir() && metadata.file_attributes() & 0x400 == 0);
        if !safe {
            if !std::thread::panicking() { panic!("unsafe synthetic fixture path"); }
            eprintln!("synthetic fixture cleanup skipped: unsafe path");
            return;
        }
        if let Err(error) = fs::remove_dir_all(&self.0) {
            if !std::thread::panicking() { panic!("synthetic fixture cleanup failed: {error}"); }
            eprintln!("synthetic fixture cleanup failed: {error}");
        }
    }
}
fn fixture_root() -> Fixture {
    let base = fixture_base();
    fs::create_dir_all(&base).unwrap();
    let mut random = [0_u8; 16];
    getrandom::getrandom(&mut random).unwrap();
    let child = random.iter().map(|part| format!("{part:02x}")).collect::<String>();
    let path = base.join(child);
    fs::create_dir(&path).unwrap();
    let root = Fixture(path, base);
    fs::create_dir(root.join("profile")).unwrap();
    fs::create_dir(root.join("runtime")).unwrap();
    root
}

fn runtime(root: &Path) -> Runtime {
    Runtime::open(&json!({"profileDirectory":root.join("profile"),
        "runtimeDirectory":root.join("runtime"),"existingClient":true})).unwrap()
}
fn lookup(rt: &Runtime, owner: &str, chat: &str, ids: &[&str]) -> Value {
    rt.call(&json!({"operation":"retention.lookup","value":{
        "schema":1,"accountId":owner,"chatId":chat,"messageIds":ids}})).unwrap()
}
fn list(rt: &Runtime, owner: &str, chat: &str, cursor: Option<&str>, limit: u64) -> Value {
    rt.call(&json!({"operation":"retention.list","value":{
        "schema":1,"accountId":owner,"chatId":chat,"cursor":cursor,"limit":limit}})).unwrap()
}
fn inline(rt: &Runtime, owner: &str, chat: &str, cursor: Option<&str>, limit: u64) -> Value {
    inline_mode(rt, owner, chat, cursor, limit, false)
}
fn inline_mode(rt: &Runtime, owner: &str, chat: &str, cursor: Option<&str>, limit: u64,
    legacy_only: bool) -> Value {
    rt.call(&json!({"operation":"retention.inline","value":{
        "schema":1,"accountId":owner,"chatId":chat,"cursor":cursor,"limit":limit,
        "legacyOnly":legacy_only}})).unwrap()
}
fn range(rt: &Runtime, owner: &str, chat: &str, from: &str, to: &str,
    cursor: Option<&str>, limit: u64) -> Value {
    rt.call(&json!({"operation":"retention.range","value":{
        "schema":1,"accountId":owner,"chatId":chat,
        "fromOrderKey":from,"toOrderKey":to,"cursor":cursor,"limit":limit}})).unwrap()
}

#[test]
fn selected_chat_lookup_and_local_list_survive_restart_without_cross_account_rows() {
    let root = fixture_root();
    let store = RetentionStore::new(root.join("profile")).unwrap();
    store.capture_text(11, 22, 33, 9_000, "Synthetic first retention").unwrap();
    store.capture_text(11, 22, 34, 9_100, "Synthetic second retention").unwrap();
    store.mark_received_delete(11, 22, &[33], 10_000).unwrap();
    store.mark_received_delete(11, 22, &[34], 11_000).unwrap();
    store.capture_text(55, 22, 33, 9_200, "Synthetic other account").unwrap();
    store.mark_received_delete(55, 22, &[33], 12_000).unwrap();
    let rt = runtime(&root);
    let found = lookup(&rt, "11", "22", &["33", "34"]);
    assert_eq!(found["deletedIds"], json!(["33", "34"]));
    assert_eq!(found["ready"], true);
    assert!(found.get("text").is_none());
    let first = list(&rt, "11", "22", None, 1);
    assert_eq!(first["items"].as_array().unwrap().len(), 1);
    assert_eq!(first["items"][0]["text"], "Synthetic second retention");
    assert!(first["items"][0].get("messageId").is_none());
    let first_inline = inline(&rt, "11", "22", None, 1);
    assert_eq!(first_inline["items"][0], json!({
        "messageId":"34", "orderKey":null, "sentAtMs":null, "receivedAtMs":9_100,
        "deletedAtMs":11_000, "text":"Synthetic second retention"}));
    assert_eq!(first_inline["revision"], first["revision"]);
    let inline_cursor = first_inline["nextCursor"].as_str().unwrap();
    assert_eq!(inline(&rt, "11", "22", Some(inline_cursor), 1)["items"][0]["messageId"], "33");
    assert_eq!(rt.call(&json!({"operation":"retention.inline","value":{
        "schema":1,"accountId":"55","chatId":"22","cursor":inline_cursor,"limit":1,
        "legacyOnly":false}})),
        Err("invalid_request"));
    let cursor = first["nextCursor"].as_str().unwrap();
    let second = list(&rt, "11", "22", Some(cursor), 1);
    assert_eq!(second["items"][0]["text"], "Synthetic first retention");
    assert_eq!(second["nextCursor"], Value::Null);
    let other = list(&rt, "55", "22", None, 20);
    assert_eq!(other["items"].as_array().unwrap().len(), 1);
    assert_eq!(other["items"][0]["text"], "Synthetic other account");
    assert_eq!(list(&rt, "11", "66", None, 20)["items"], json!([]));
    assert_eq!(inline(&rt, "55", "22", None, 20)["items"][0]["text"], "Synthetic other account");
    assert_eq!(inline(&rt, "11", "66", None, 20)["items"], json!([]));
    drop(rt);
    let reopened = runtime(&root);
    assert_eq!(lookup(&reopened, "11", "22", &["33"])["deletedIds"], json!(["33"]));
    assert_eq!(list(&reopened, "11", "22", None, 20)["items"].as_array().unwrap().len(), 2);
    assert_eq!(inline(&reopened, "11", "22", None, 20)["items"].as_array().unwrap().len(), 2);
}

#[test]
fn revision_invalidates_cached_badges_and_stale_pages_without_fabricating_empty_success() {
    let root = fixture_root();
    let store = RetentionStore::new(root.join("profile")).unwrap();
    store.capture_text(11, 22, 33, 9_000, "Synthetic first").unwrap();
    store.capture_text(11, 22, 34, 9_100, "Synthetic second").unwrap();
    store.mark_received_delete(11, 22, &[33], 10_000).unwrap();
    store.mark_received_delete(11, 22, &[34], 11_000).unwrap();
    let rt = runtime(&root);
    let first = list(&rt, "11", "22", None, 1);
    let cursor = first["nextCursor"].as_str().unwrap().to_owned();
    let before = lookup(&rt, "11", "22", &["33", "34", "35"]);
    assert_eq!(before["deletedIds"], json!(["33", "34"]));
    // A second identical poll can use the revision cache. A new durable mark
    // must invalidate it even when visible IDs are unchanged.
    assert_eq!(lookup(&rt, "11", "22", &["33", "34", "35"])["deletedIds"], before["deletedIds"]);
    store.capture_text(11, 22, 35, 9_200, "Synthetic third").unwrap();
    store.mark_received_delete(11, 22, &[35], 12_000).unwrap();
    let after = lookup(&rt, "11", "22", &["33", "34", "35"]);
    assert_eq!(after["deletedIds"], json!(["33", "34", "35"]));
    assert_ne!(after["revision"], before["revision"]);
    let stale = rt.call(&json!({"operation":"retention.list","value":{
        "schema":1,"accountId":"11","chatId":"22","cursor":cursor,"limit":1}}));
    assert_eq!(stale, Err("stale_cursor"));
    let stale_inline = rt.call(&json!({"operation":"retention.inline","value":{
        "schema":1,"accountId":"11","chatId":"22","cursor":cursor,"limit":1,
        "legacyOnly":false}}));
    assert_eq!(stale_inline, Err("stale_cursor"));
    for bad in [
        json!({"schema":1,"accountId":"011","chatId":"22","messageIds":["33"]}),
        json!({"schema":1,"accountId":"11","chatId":"22","messageIds":["33","33"]}),
        json!({"schema":1,"accountId":"11","chatId":"22","messageIds":[33]}),
    ] {
        assert_eq!(rt.call(&json!({"operation":"retention.lookup","value":bad})),
            Err("invalid_request"));
    }
}

#[test]
fn inline_ids_remain_decimal_strings_and_input_is_exactly_scoped() {
    let root = fixture_root();
    let store = RetentionStore::new(root.join("profile")).unwrap();
    let id = 9_007_199_254_740_993_i64;
    store.capture_text(11, 22, id, 9_000, "Synthetic wide id").unwrap();
    store.mark_received_delete(11, 22, &[id], 10_000).unwrap();
    let rt = runtime(&root);
    let page = inline(&rt, "11", "22", None, 1);
    assert_eq!(page["items"][0]["messageId"], id.to_string());
    assert_eq!(page["items"][0]["sentAtMs"], Value::Null);
    assert_eq!(page["items"][0].as_object().unwrap().len(), 6);
    for value in [
        json!({"schema":1,"accountId":"011","chatId":"22","cursor":null,"limit":1,"legacyOnly":false}),
        json!({"schema":1,"accountId":"11","chatId":"22","cursor":null,"limit":101,"legacyOnly":false}),
        json!({"schema":1,"accountId":"11","chatId":"22","cursor":null,"limit":1,"legacyOnly":false,"extra":true}),
        json!({"schema":1,"accountId":"11","chatId":"22","cursor":42,"limit":1,"legacyOnly":false}),
        json!({"schema":1,"accountId":"11","chatId":"22","cursor":null,"limit":1}),
        json!({"schema":1,"accountId":"11","chatId":"22","cursor":null,"limit":1,"legacyOnly":1}),
    ] {
        assert_eq!(rt.call(&json!({"operation":"retention.inline","value":value})),
            Err("invalid_request"));
    }
}

#[test]
fn legacy_inline_pages_skip_newer_ordered_rows_without_losing_old_captures() {
    let root = fixture_root();
    let store = RetentionStore::new(root.join("profile")).unwrap();
    let keyed_ids = (100_i64..125).collect::<Vec<_>>();
    let keyed = keyed_ids.iter().map(|id| IncomingText {
        id: *id, received_at_ms: 1_700_000_000_000 + id,
        order_key: Some(1_700_000_000_000 + id), text: "synthetic ordered capture",
    }).collect::<Vec<_>>();
    store.capture_text_batch(11, 22, &keyed).unwrap();
    assert!(store.mark_received_delete(11, 22, &keyed_ids, 2_000).unwrap()
        .into_iter().all(|value| value));
    let legacy_ids = (200_i64..222).collect::<Vec<_>>();
    let legacy = legacy_ids.iter().map(|id| IncomingText {
        id: *id, received_at_ms: 100 + id, order_key: None,
        text: "synthetic old capture",
    }).collect::<Vec<_>>();
    store.capture_text_batch(11, 22, &legacy).unwrap();
    assert!(store.mark_received_delete(11, 22, &legacy_ids, 1_000).unwrap()
        .into_iter().all(|value| value));
    store.capture_text(55, 22, 200, 300, "synthetic other-account capture").unwrap();
    assert_eq!(store.mark_received_delete(55, 22, &[200], 1_000).unwrap(), [true]);

    let rt = runtime(&root);
    let mixed_page = inline(&rt, "11", "22", None, 20);
    assert_eq!(mixed_page["items"].as_array().unwrap().len(), 20);
    assert!(mixed_page["items"].as_array().unwrap().iter()
        .all(|row| !row["orderKey"].is_null()));
    let mixed_cursor = mixed_page["nextCursor"].as_str().unwrap();
    let first = inline_mode(&rt, "11", "22", None, 20, true);
    assert_eq!(first["items"].as_array().unwrap().len(), 20);
    assert_eq!(first["items"][0]["messageId"], "221");
    assert!(first["items"].as_array().unwrap().iter()
        .all(|row| row["orderKey"].is_null() && row["text"] == "synthetic old capture"));
    let legacy_cursor = first["nextCursor"].as_str().unwrap().to_owned();
    let second = inline_mode(&rt, "11", "22", Some(&legacy_cursor), 20, true);
    assert_eq!(second["items"].as_array().unwrap().len(), 2);
    assert_eq!(second["items"][0]["messageId"], "201");
    assert_eq!(second["items"][1]["messageId"], "200");
    assert_eq!(second["nextCursor"], Value::Null);
    assert_eq!(inline_mode(&rt, "55", "22", None, 20, true)["items"][0]["text"],
        "synthetic other-account capture");
    assert_eq!(rt.call(&json!({"operation":"retention.inline","value":{
        "schema":1,"accountId":"11","chatId":"22","cursor":mixed_cursor,
        "limit":20,"legacyOnly":true}})), Err("invalid_request"));
    assert_eq!(rt.call(&json!({"operation":"retention.inline","value":{
        "schema":1,"accountId":"11","chatId":"22","cursor":legacy_cursor,
        "limit":20,"legacyOnly":false}})), Err("invalid_request"));
    assert_eq!(rt.call(&json!({"operation":"retention.inline","value":{
        "schema":1,"accountId":"55","chatId":"22","cursor":legacy_cursor,
        "limit":20,"legacyOnly":true}})), Err("invalid_request"));
    drop(rt);
    drop(store);
    let reopened = runtime(&root);
    assert_eq!(inline_mode(&reopened, "11", "22", None, 20, true)["items"][0]["messageId"],
        "221");
    assert_eq!(inline_mode(&reopened, "55", "22", None, 20, true)["items"][0]["text"],
        "synthetic other-account capture");
    drop(reopened);
}

#[test]
fn legacy_marker_count_is_scoped_and_never_claims_missing_text() {
    let root = fixture_root();
    let profile = root.join("profile");
    let store = RetentionStore::new(&profile).unwrap();
    store.preflight().unwrap();
    store.capture_text(11, 22, 33, 9_000, "Synthetic durable body").unwrap();
    store.mark_received_delete(11, 22, &[33], 10_000).unwrap();
    let db = Connection::open(profile.join("retained-messages.sqlite3")).unwrap();
    db.execute("INSERT INTO deleted_messages VALUES('11','22','34',11000)", []).unwrap();
    db.execute("UPDATE retention_meta SET revision=revision+1 WHERE singleton=1", []).unwrap();
    drop(db);
    let rt = runtime(&root);
    let page = list(&rt, "11", "22", None, 20);
    assert_eq!(page["items"].as_array().unwrap().len(), 1);
    assert_eq!(inline(&rt, "11", "22", None, 20)["items"].as_array().unwrap().len(), 1);
    let summary = rt.call(&json!({"operation":"retention.marker-summary","value":{
        "schema":1,"accountId":"11","chatId":"22","revision":page["revision"]}})).unwrap();
    assert_eq!(summary["markerOnlyCount"], 1);
    assert_eq!(summary.as_object().unwrap().len(), 5);
    assert!(summary.get("items").is_none());
    let other = rt.call(&json!({"operation":"retention.marker-summary","value":{
        "schema":1,"accountId":"55","chatId":"22","revision":page["revision"]}})).unwrap();
    assert_eq!(other["markerOnlyCount"], 0);
    let wrong_chat = rt.call(&json!({"operation":"retention.marker-summary","value":{
        "schema":1,"accountId":"11","chatId":"66","revision":page["revision"]}})).unwrap();
    assert_eq!(wrong_chat["markerOnlyCount"], 0);
    store.capture_text(11, 22, 35, 9_100, "Synthetic later body").unwrap();
    store.mark_received_delete(11, 22, &[35], 12_000).unwrap();
    assert_eq!(rt.call(&json!({"operation":"retention.marker-summary","value":{
        "schema":1,"accountId":"11","chatId":"22","revision":page["revision"]}})),
        Err("stale_cursor"));
}

#[test]
fn range_uses_exact_order_strings_scope_cursor_and_restart() {
    let root = fixture_root();
    let store = RetentionStore::new(root.join("profile")).unwrap();
    let from = 9_007_199_254_740_993_i64;
    let to = from + 2;
    let rows = [
        IncomingText { id:33, received_at_ms:100, order_key:Some(from), text:"synthetic first" },
        IncomingText { id:34, received_at_ms:101, order_key:Some(from+1), text:"synthetic second" },
        IncomingText { id:35, received_at_ms:102, order_key:Some(from+1), text:"synthetic tied" },
    ];
    store.capture_text_batch(11, 22, &rows).unwrap();
    store.capture_text(11, 22, 36, 103, "synthetic legacy null").unwrap();
    store.capture_text_batch(55, 22, &[IncomingText { id:33, received_at_ms:104,
        order_key:Some(from), text:"synthetic other account" }]).unwrap();
    store.capture_text_batch(11, 23, &[IncomingText { id:33, received_at_ms:105,
        order_key:Some(from), text:"synthetic other chat" }]).unwrap();
    assert_eq!(store.mark_received_delete(11, 22, &[33,34,35,36], 200).unwrap(),
        [true,true,true,true]);
    store.mark_received_delete(55, 22, &[33], 201).unwrap();
    store.mark_received_delete(11, 23, &[33], 202).unwrap();
    let rt = runtime(&root);
    let first = range(&rt, "11", "22", &from.to_string(), &to.to_string(), None, 2);
    assert_eq!(first.as_object().unwrap().len(), 8);
    assert_eq!(first["items"].as_array().unwrap().len(), 2);
    assert_eq!(first["items"][0]["messageId"], "33");
    assert_eq!(first["items"][0]["orderKey"], from.to_string());
    assert_eq!(first["items"][0].as_object().unwrap().len(), 6);
    assert_eq!(first["items"][1]["messageId"], "34");
    let cursor = first["nextCursor"].as_str().unwrap().to_owned();
    let second = range(&rt, "11", "22", &from.to_string(), &to.to_string(), Some(&cursor), 2);
    assert_eq!(second["items"].as_array().unwrap().len(), 1);
    assert_eq!(second["items"][0]["messageId"], "35");
    assert_eq!(second["nextCursor"], Value::Null);
    assert_eq!(range(&rt, "55", "22", &from.to_string(), &to.to_string(), None, 10)
        ["items"][0]["text"], "synthetic other account");
    assert_eq!(range(&rt, "11", "23", &from.to_string(), &to.to_string(), None, 10)
        ["items"][0]["text"], "synthetic other chat");
    assert_eq!(inline(&rt, "11", "22", None, 10)["items"][0]["orderKey"], Value::Null);
    drop(rt);
    drop(store);
    let reopened = runtime(&root);
    assert_eq!(range(&reopened, "11", "22", &from.to_string(), &to.to_string(), None, 10)
        ["items"].as_array().unwrap().len(), 3);
    assert_eq!(reopened.call(&json!({"operation":"retention.range","value":{
        "schema":1,"accountId":"55","chatId":"22",
        "fromOrderKey":from.to_string(),"toOrderKey":to.to_string(),
        "cursor":cursor,"limit":2}})), Err("invalid_request"));
    for bad in [
        json!({"schema":1,"accountId":"11","chatId":"22","fromOrderKey":"0",
            "toOrderKey":to.to_string(),"cursor":null,"limit":2}),
        json!({"schema":1,"accountId":"11","chatId":"22","fromOrderKey":"01",
            "toOrderKey":to.to_string(),"cursor":null,"limit":2}),
        json!({"schema":1,"accountId":"11","chatId":"22","fromOrderKey":to.to_string(),
            "toOrderKey":from.to_string(),"cursor":null,"limit":2}),
        json!({"schema":1,"accountId":"11","chatId":"22","fromOrderKey":from.to_string(),
            "toOrderKey":to.to_string(),"cursor":null,"limit":101}),
        json!({"schema":1,"accountId":"11","chatId":"22","fromOrderKey":from.to_string(),
            "toOrderKey":to.to_string(),"cursor":null,"limit":2,"extra":true}),
    ] {
        assert_eq!(reopened.call(&json!({"operation":"retention.range","value":bad})),
            Err("invalid_request"));
    }
    let store = RetentionStore::new(root.join("profile")).unwrap();
    store.capture_text_batch(11, 22, &[IncomingText { id:37, received_at_ms:106,
        order_key:Some(to), text:"synthetic newer" }]).unwrap();
    store.mark_received_delete(11, 22, &[37], 203).unwrap();
    assert_eq!(reopened.call(&json!({"operation":"retention.range","value":{
        "schema":1,"accountId":"11","chatId":"22",
        "fromOrderKey":from.to_string(),"toOrderKey":to.to_string(),
        "cursor":cursor,"limit":2}})), Err("stale_cursor"));
}

#[test]
fn large_text_pages_keep_full_rows_and_fit_transport_budget() {
    let root = fixture_root();
    let store = RetentionStore::new(root.join("profile")).unwrap();
    let text = "\u{0}".repeat(64 * 1024);
    let ids = (100_i64..117).collect::<Vec<_>>();
    let rows = ids.iter().map(|&id| IncomingText { id, received_at_ms:100,
        order_key:Some(id), text:&text }).collect::<Vec<_>>();
    store.capture_text_batch(11, 22, &rows).unwrap();
    assert!(store.mark_received_delete(11, 22, &ids, 200).unwrap()
        .into_iter().all(|value| value));
    let rt = runtime(&root);
    let first_inline = inline(&rt, "11", "22", None, 100);
    assert!(first_inline["items"].as_array().unwrap().len() < ids.len());
    assert!(first_inline["nextCursor"].is_string());
    assert!(serde_json::to_vec(&first_inline).unwrap().len() < 8 * 1024 * 1024);
    let mut cursor = None::<String>;
    let mut found = 0;
    loop {
        let page = range(&rt, "11", "22", "100", "116", cursor.as_deref(), 100);
        assert!(serde_json::to_vec(&page).unwrap().len() < 8 * 1024 * 1024);
        let items = page["items"].as_array().unwrap();
        assert!(!items.is_empty());
        for item in items { assert!(item["text"].as_str().unwrap() == text); }
        found += items.len();
        cursor = page["nextCursor"].as_str().map(str::to_owned);
        if cursor.is_none() { break; }
        assert!(found < ids.len());
    }
    assert_eq!(found, ids.len());
}
