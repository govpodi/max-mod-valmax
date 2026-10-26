use serde_json::{Value,json};
use std::{fs,path::{Path,PathBuf},sync::{Arc,Barrier}};
use valmax_core::quick_reply_store::{self as store,Error,QuickReplyStore};

fn row(n:u32)->Value{json!({"id":format!("{n:032x}"),"command":format!("ответ{n}"),"title":"Искусственная заготовка","text":"Искусственный текст.\nВторая строка."})}
fn with(n:u32,changes:Value)->Value{let mut row=row(n);for (key,value) in changes.as_object().unwrap(){row[key]=value.clone();}row}
fn test_root()->PathBuf{
    let root=std::env::var_os("VALMAX_TEST_TEMP_ROOT").map(PathBuf::from).unwrap_or_else(std::env::temp_dir).join("quick-reply-store");
    fs::create_dir_all(&root).unwrap();root
}
fn scratch()->PathBuf{let mut random=[0u8;8];getrandom::getrandom(&mut random).unwrap();test_root().join(format!("quick-reply-{:x}",u64::from_le_bytes(random)))}
fn cleanup(path:&Path){if path.exists(){let actual=fs::canonicalize(path).unwrap();let parent=fs::canonicalize(test_root()).unwrap();assert!(actual.starts_with(&parent)&&actual!=parent);fs::remove_dir_all(actual).unwrap();}}
fn save(store:&QuickReplyStore,replies:&Value)->Value{store.replace(store.snapshot().unwrap()["revision"].as_str().unwrap(),replies).unwrap()}

#[test]
fn absent_reads_are_lazy_and_revision_matches_python(){
    let root=scratch();let store=QuickReplyStore::new(root.join("settings/quick-replies.json")).unwrap();
    assert!(!root.exists());let first=store.snapshot().unwrap();assert!(!root.exists());
    assert_eq!(first["replies"],json!([]));
    assert_eq!(first["revision"],valmax_core::settings_io::sha256(b"{\"replies\":[],\"schema\":1}"));assert_eq!(first,store.snapshot().unwrap());
}

#[test]
fn legacy_and_extended_unicode_roundtrip_preserve_optional_keys_and_order(){
    let root=scratch();let path=root.join("quick-replies.json");let store=QuickReplyStore::new(&path).unwrap();
    let attachment=json!({"url":"file:///Z:/does-not-exist/пример%20😀.txt","kind":"file"});
    let extended=with(2,json!({"accountId":"9223372036854775807","text":"","attachments":[attachment],
        "parts":[{"text":"Продолжение"},{"text":"","attachments":[{"url":"file:///D:/synthetic/photo.jpg","kind":"media"}]}]}));
    let replies=json!([extended,row(1),with(3,json!({"accountId":""}))]);let saved=save(&store,&replies);
    assert_eq!(saved["replies"],replies);assert_eq!(store.snapshot().unwrap(),saved);let raw=fs::read(&path).unwrap();
    assert_eq!(store::parse(&raw).unwrap(),replies);assert_eq!(saved["revision"],valmax_core::settings_io::sha256(&raw));
    assert!(saved["replies"][1].get("accountId").is_none());assert_eq!(saved["replies"][2]["accountId"],"");
    fs::write(&path,serde_json::to_vec_pretty(&json!({"schema":1,"replies":replies})).unwrap()).unwrap();assert_eq!(store.snapshot().unwrap(),saved);
    cleanup(&root);
}

#[test]
fn scope_isolation_and_personal_command_shadowing_match_composer(){
    let common=with(1,json!({"command":"away"}));let a=with(2,json!({"command":"away","accountId":"101"}));
    let b=with(3,json!({"command":"away","accountId":"202"}));let other=with(4,json!({"command":"another"}));
    let replies=json!([common,a,b,other]);assert_eq!(store::validate_replies(&replies).unwrap(),replies);
    assert_eq!(store::composer_replies(&replies,"101","a").unwrap(),json!([a,other]));
    assert_eq!(store::composer_replies(&replies,"202","away").unwrap(),json!([b]));
    assert_eq!(store::composer_replies(&replies,"303","").unwrap(),json!([common,other]));
    assert_eq!(store::composer_replies(&replies,"","").unwrap(),json!([common,other]));
    for account in ["0","01","+101","9223372036854775808"]{assert_eq!(store::composer_replies(&replies,account,""),Err(Error::InvalidReplies));}
    let mut duplicate=replies.as_array().unwrap().clone();duplicate.push(with(5,json!({"command":"away","accountId":""})));
    assert_eq!(store::validate_replies(&json!(duplicate)),Err(Error::InvalidReplies));
    duplicate.pop();duplicate.push(with(5,json!({"command":"away","accountId":"101"})));
    assert_eq!(store::validate_replies(&json!(duplicate)),Err(Error::InvalidReplies));
    assert_eq!(store::validate_replies(&json!([row(1),with(1,json!({"accountId":"101"}))])),Err(Error::InvalidReplies));
}

#[test]
fn strict_field_shapes_types_commands_and_unicode_scalar_limits(){
    for changes in [json!({"id":"A".repeat(32)}),json!({"id":"0".repeat(31)}),json!({"command":"Ответ"}),json!({"command":"1answer"}),
        json!({"command":"answer-now"}),json!({"command":"/answer"}),json!({"command":"a".repeat(33)}),json!({"command":"a\n"}),
        json!({"title":"😀".repeat(81)}),json!({"text":"😀".repeat(5001)}),json!({"title":null}),json!({"text":0}),json!({"text":"\0"}),
        json!({"text":""}),json!({"accountId":true}),json!({"accountId":"0"}),json!({"accountId":"9223372036854775808"}),json!({"autoSend":true})]{
        assert_eq!(store::validate_replies(&json!([with(1,changes)])),Err(Error::InvalidReplies));
    }
    let maximum=with(1,json!({"command":format!("ё{}","я".repeat(31)),"title":"😀".repeat(80),"text":"😀".repeat(5000)}));
    assert_eq!(store::validate_replies(&json!([maximum])).unwrap(),json!([maximum]));
    for input in [json!(null),json!({}),json!([null]),json!([{}])]{assert_eq!(store::validate_replies(&input),Err(Error::InvalidReplies));}
    let mut missing=row(1);missing.as_object_mut().unwrap().remove("text");assert_eq!(store::validate_replies(&json!([missing])),Err(Error::InvalidReplies));
    assert!(store::validate_replies(&json!([with(1,json!({"text":" \t\n"}))])).is_ok());
}

#[test]
fn local_attachment_url_contract_includes_raw_delimiters_and_unicode(){
    for url in ["file:///D:/missing/report%20final.pdf","file:///D:/пример.txt","file:///D:/emoji😀.txt","file:///D:/a?","file:///D:/a#","file:///D:/a?#",
        "file:///D:/a%23.txt","file:///D:/ leading.txt","file:///D:/COM¹.txt","file:///D:/CONIN$","file:///D:/a\u{85}b"]{assert!(store::local_file_url(url).is_ok(),"{url}");}
    for url in ["https://example.org/a","file://server/share/a","file://localhost/D:/a.txt","file:////server/share/a","FILE:///D:/a.txt","file:///tmp/a","D:/a.txt","file:///D:/",
        "file:///D:/../a","file:///D:/%2e%2e/a","file:///D:/a\\b","file:///D:/a%5cb","file:///D:/a.txt:stream","file:///D:/CON.txt","file:///D:/a/./b",
        "file:///D:/a?query","file:///D:/a#fragment","file:///D:/a%00.txt","file:///D:/a%0A.txt","file:///D:/%ff.txt","file:///D:/a%.txt","file:///D:/trailing.","file:///D:/a%20","file:///D://a.txt"]{
        assert_eq!(store::local_file_url(url),Err(Error::InvalidReplies),"{url}");
    }
    assert_eq!(store::local_file_url("file:///D:/Stra%C3%9Fe.txt").unwrap(),store::local_file_url("file:///d:/STRASSE.txt").unwrap());
    assert_eq!(store::local_file_url("file:///D:/ς.txt").unwrap(),store::local_file_url("file:///D:/Σ.txt").unwrap());
}

#[test]
fn attachments_and_parts_limits_enforce_metadata_only_manual_send(){
    let files:Vec<_>=(0..10).map(|i|json!({"url":format!("file:///D:/synthetic/{i}.txt"),"kind":"file"})).collect();
    assert!(store::validate_replies(&json!([with(1,json!({"text":"","attachments":files,"parts":(0..9).map(|_|json!({"text":"x"})).collect::<Vec<_>>()}))])).is_ok());
    for attachments in [json!([]),json!([{"url":"file:///D:/a","kind":"url"}]),json!([{"url":"file:///D:/a","kind":"file","content":"not metadata"}]),
        json!([{"url":"file:///D:/a%20b.txt","kind":"file"},{"url":"file:///d:/A b.txt","kind":"media"}]),json!([{"url":"file:///D:/Straße","kind":"file"},{"url":"file:///D:/STRASSE","kind":"file"}]),
        json!((0..11).map(|i|json!({"url":format!("file:///D:/{i}"),"kind":"file"})).collect::<Vec<_>>())]{
        assert_eq!(store::validate_replies(&json!([with(1,json!({"attachments":attachments}))])),Err(Error::InvalidReplies));
    }
    for parts in [json!([]),json!([{"text":""}]),json!([{"text":"x","autoSend":true}]),json!([null]),json!("text"),json!((0..10).map(|_|json!({"text":"x"})).collect::<Vec<_>>())]{
        assert_eq!(store::validate_replies(&json!([with(1,json!({"parts":parts}))])),Err(Error::InvalidReplies));
    }
}

#[test]
fn total_utf8_size_and_exact_store_boundary(){
    let hundred:Vec<_>=(0..100).map(|i|with(i,json!({"text":"Тест"}))).collect();assert!(store::validate_replies(&json!(hundred)).is_ok());
    let too_many:Vec<_>=(0..101).map(row).collect();assert_eq!(store::validate_replies(&json!(too_many)),Err(Error::InvalidReplies));
    let too_big:Vec<_>=(0..30).map(|i|with(i,json!({"text":"я".repeat(5000)}))).collect();assert_eq!(store::validate_replies(&json!(too_big)),Err(Error::InvalidReplies));
    // Construct exactly 256 KiB using bounded rows, then exceed by one byte.
    let mut rows:Vec<_>=(0..51).map(|i|with(i,json!({"text":"a".repeat(5000)}))).collect();rows.push(with(51,json!({"text":"x"})));
    let size=store::canonical(&json!(rows)).unwrap().len();let remaining=store::MAX_STORE_BYTES-size;
    assert!(remaining<5000);rows[51]["text"]=json!("x".repeat(1+remaining));
    let clean=store::validate_replies(&json!(rows)).unwrap();assert_eq!(store::canonical(&clean).unwrap().len(),store::MAX_STORE_BYTES);
    rows[51]["text"]=json!("x".repeat(2+remaining));assert_eq!(store::validate_replies(&json!(rows)),Err(Error::InvalidReplies));
}

#[test]
fn malformed_store_is_never_overwritten_and_errors_are_finite(){
    let root=scratch();fs::create_dir_all(&root).unwrap();let path=root.join("quick-replies.json");let store=QuickReplyStore::new(&path).unwrap();
    for bytes in [b"".to_vec(),b"{".to_vec(),vec![0xff],br#"{"schema":NaN,"replies":[]}"#.to_vec(),br#"{"schema":1,"schema":1,"replies":[]}"#.to_vec(),
        br#"{"schema":true,"replies":[]}"#.to_vec(),br#"{"schema":1.0,"replies":[]}"#.to_vec(),br#"{"schema":1,"replies":[],"other":1}"#.to_vec(),
        br#"{"schema":1,"replies":[]} trailing"#.to_vec(),vec![b' ';store::MAX_STORE_BYTES+1],br#"{"schema":1,"replies":[{"text":"\ud800"}]}"#.to_vec()]{
        fs::write(&path,&bytes).unwrap();assert_eq!(store.snapshot(),Err(Error::InvalidStore));assert_eq!(store.replace(&"0".repeat(64),&json!([])),Err(Error::InvalidStore));assert_eq!(fs::read(&path).unwrap(),bytes);
    }
    assert_eq!(Error::InvalidStore.to_string(),"invalid_store");cleanup(&root);
}

#[test]
fn invalid_input_stale_revision_and_clear_preserve_expected_file(){
    let root=scratch();let path=root.join("settings/quick-replies.json");let store=QuickReplyStore::new(&path).unwrap();
    for revision in ["","x",&"G".repeat(64),&"0".repeat(63)]{assert_eq!(store.replace(revision,&json!([])),Err(Error::InvalidRevision));}assert!(!root.exists());
    let before=store.snapshot().unwrap();assert_eq!(store.replace(before["revision"].as_str().unwrap(),&json!([with(1,json!({"autoSend":true}))])),Err(Error::InvalidReplies));assert!(!root.exists());
    let saved=save(&store,&json!([row(1)]));let bytes=fs::read(&path).unwrap();assert_eq!(store.replace(before["revision"].as_str().unwrap(),&json!([row(2)])),Err(Error::RevisionConflict));assert_eq!(fs::read(&path).unwrap(),bytes);
    assert_eq!(store.replace(saved["revision"].as_str().unwrap(),&json!([])).unwrap(),before);cleanup(&root);
}

#[test]
fn concurrent_store_instances_allow_one_revision_winner(){
    let root=scratch();let path=root.join("quick-replies.json");let store=QuickReplyStore::new(&path).unwrap();let revision=save(&store,&json!([]))["revision"].as_str().unwrap().to_owned();
    let barrier=Arc::new(Barrier::new(2));let mut threads=Vec::new();
    for n in 1..=2{let path=path.clone();let revision=revision.clone();let barrier=barrier.clone();threads.push(std::thread::spawn(move||{barrier.wait();QuickReplyStore::new(path).unwrap().replace(&revision,&json!([row(n)]))}));}
    let outcomes:Vec<_>=threads.into_iter().map(|thread|thread.join().unwrap()).collect();assert_eq!(outcomes.iter().filter(|r|r.is_ok()).count(),1);assert_eq!(outcomes.iter().filter(|r|**r==Err(Error::RevisionConflict)).count(),1);cleanup(&root);
}

#[test]
fn hardlinks_directories_and_unsafe_store_paths_are_rejected(){
    for path in ["relative.json","C:/ValMAXBuild/../escape.json","C:/ValMAXBuild/./x.json","C:/ValMAXBuild/x.json:stream","C:/ValMAXBuild/NUL","//localhost/share/x.json"]{assert!(matches!(QuickReplyStore::new(path),Err(Error::UnsafePath)));}
    let root=scratch();fs::create_dir_all(&root).unwrap();let path=root.join("quick-replies.json");fs::create_dir(&path).unwrap();let store=QuickReplyStore::new(&path).unwrap();assert_eq!(store.snapshot(),Err(Error::UnsafePath));fs::remove_dir(&path).unwrap();
    let target=root.join("target.json");fs::write(&target,b"SYNTHETIC-PROTECTED-FILE").unwrap();fs::hard_link(&target,&path).unwrap();assert_eq!(store.snapshot(),Err(Error::UnsafePath));assert_eq!(store.replace(&"0".repeat(64),&json!([])),Err(Error::UnsafePath));assert_eq!(fs::read(&target).unwrap(),b"SYNTHETIC-PROTECTED-FILE");cleanup(&root);
}
