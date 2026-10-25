#![cfg(windows)]
use std::{fs, path::PathBuf, ptr, slice};
use serde_json::{Value,json};
use valmax_core::{ffi::*, runtime::Runtime};
fn root() -> PathBuf {
    let mut random = [0u8;8]; getrandom::getrandom(&mut random).unwrap();
    PathBuf::from("C:/ValMAXBuild/test-data").join(format!("runtime-{:x}",u64::from_le_bytes(random)))
}
fn empty() -> CoreBuffer { CoreBuffer {data:ptr::null_mut(),len:0,capacity:0} }
fn cleanup(dir:PathBuf) {
    let target=dir.canonicalize().unwrap();let base=PathBuf::from("C:/ValMAXBuild/test-data").canonicalize().unwrap();
    assert!(target.starts_with(&base)&&target!=base&&dir.file_name().unwrap().to_str().unwrap().starts_with("runtime-"));
    fs::remove_dir_all(target).unwrap();
}
unsafe fn value(output: &mut CoreBuffer) -> Value {
    let value = serde_json::from_slice(unsafe {slice::from_raw_parts(output.data,output.len)}).unwrap();
    unsafe {valmax_core_buffer_free(output)}; value
}
fn open(config:&Value) -> (*mut Runtime,Value) {
    let data = serde_json::to_vec(config).unwrap(); let mut context=ptr::null_mut(); let mut output=empty();
    unsafe {
        assert_eq!(valmax_runtime_open(data.as_ptr(),data.len(),&mut context,&mut output),0);
        (context,value(&mut output))
    }
}
fn call(context:*mut Runtime,request:&Value) -> Value {
    let data=serde_json::to_vec(request).unwrap(); let mut output=empty();
    unsafe {assert_eq!(valmax_runtime_dispatch(context,data.as_ptr(),data.len(),&mut output),0);value(&mut output)}
}

#[test]
fn context_restart_file_scoping_and_no_false_ghost_ack() {
    let dir = root(); let profile = dir.join("profile"); let runtime = dir.join("runtime");
    let config=json!({"profileDirectory":profile,"runtimeDirectory":runtime,"existingClient":false});
    let (mut context,result)=open(&config); assert_eq!(result["ok"],true); assert!(!context.is_null());
    let current=call(context,&json!({"operation":"optimization.get"}))["result"].clone();
    let mut flags=current["settings"].clone(); flags["smoothWheel"]=false.into();
    let changed=call(context,&json!({"operation":"optimization.set","revision":current["revision"],"settings":flags}));
    assert_eq!(changed["result"]["restartRequired"],true);
    assert_eq!(call(context,&json!({"operation":"optimization.set","revision":current["revision"],"settings":flags}))["error"],"revision_conflict");
    let ghost=call(context,&json!({"operation":"ghost.get"}))["result"].clone();
    let mut selection=ghost["settings"].clone(); selection["enabled"]=true.into();
    let ghost=call(context,&json!({"operation":"ghost.set","revision":ghost["revision"],"settings":selection}));
    assert_eq!(ghost["result"]["runtime"]["applied"],false);
    assert_eq!(ghost["result"]["runtime"]["state"],"unavailable");
    let accounts=call(context,&json!({"operation":"accounts.get"}))["result"].clone();
    let row=valmax_core::account_preferences::defaults("7").unwrap();
    assert_eq!(call(context,&json!({"operation":"accounts.set","revision":accounts["revision"],"accounts":[row]}))["ok"],true);
    unsafe {valmax_runtime_close(&mut context);valmax_runtime_close(&mut context)}; assert!(context.is_null());
    let (mut context,result)=open(&config); assert_eq!(result["ok"],true);
    assert_eq!(call(context,&json!({"operation":"optimization.get"}))["result"]["restartRequired"],false);
    assert_eq!(call(context,&json!({"operation":"accounts.get"}))["result"]["accounts"][0]["accountId"],"7");
    assert_eq!(call(context,&json!({"operation":"version"}))["result"]["abi"],1);
    let mut names:Vec<_>=fs::read_dir(&profile).unwrap().map(|f|f.unwrap().file_name().into_string().unwrap()).collect(); names.sort();
    assert_eq!(names,["account-settings.json","ghost-settings.json","optimization-settings.json"]);
    unsafe {valmax_runtime_close(&mut context)};
    cleanup(dir);
}

#[test]
fn quick_reply_ffi_preserves_scopes_parts_and_revision_after_restart() {
    let dir=root();let config=json!({"profileDirectory":dir.join("profile"),"runtimeDirectory":dir.join("runtime"),"existingClient":true});
    let (mut first,opened)=open(&config);assert_eq!(opened["ok"],true);
    let empty=call(first,&json!({"operation":"quick_replies.get"}))["result"].clone();assert!(!dir.exists());
    let rows=json!([
        {"id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","command":"hi","title":"Общий","text":"Общий ответ"},
        {"id":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","command":"hi","title":"Личный","text":"Текст","accountId":"42","attachments":[{"kind":"media","url":"file:///C:/synthetic/photo.jpg"}],"parts":[{"text":"Продолжение"}]},
        {"id":"cccccccccccccccccccccccccccccccc","command":"hello","title":"Другой аккаунт","text":"Не выдавать","accountId":"43"}
    ]);
    let saved=call(first,&json!({"operation":"quick_replies.set","revision":empty["revision"],"replies":rows}));assert_eq!(saved["ok"],true);
    let selected=call(first,&json!({"operation":"quick_replies.for_account","accountId":"42","prefix":"h"}));
    assert_eq!(selected["result"]["revision"],saved["result"]["revision"]);assert_eq!(selected["result"]["replies"],json!([rows[1]]));
    assert_eq!(call(first,&json!({"operation":"quick_replies.for_account","accountId":"44","prefix":"h"}))["result"]["replies"],json!([rows[0]]));
    unsafe {valmax_runtime_close(&mut first)};
    let (mut second,result)=open(&config);assert_eq!(result["ok"],true);
    assert_eq!(call(second,&json!({"operation":"quick_replies.get"}))["result"],saved["result"]);
    assert_eq!(call(second,&json!({"operation":"quick_replies.set","revision":empty["revision"],"replies":[]}))["error"],"revision_conflict");
    assert_eq!(call(second,&json!({"operation":"quick_replies.for_account","accountId":"042","prefix":"h"}))["error"],"invalid_replies");
    assert_eq!(call(second,&json!({"operation":"quick_replies.get","path":"C:/Windows"}))["error"],"invalid_request");
    unsafe {valmax_runtime_close(&mut second)};cleanup(dir);
}

#[test]
fn malformed_config_requests_and_duplicate_keys_are_finite() {
    let (context,result)=open(&json!({"profileDirectory":"../no","runtimeDirectory":"C:/ValMAXBuild/unused","existingClient":false}));
    assert!(context.is_null()); assert_eq!(result["error"],"unsafe_path");
    let dir=root(); let (mut context,result)=open(&json!({"profileDirectory":dir.join("profile"),"runtimeDirectory":dir.join("runtime"),"existingClient":true}));
    assert_eq!(result["ok"],true); assert!(!dir.exists());
    for request in [json!({"operation":"accounts.get","path":"C:/Windows"}), json!({"operation":"accounts.get","extra":null})] {
        assert_eq!(call(context,&request)["error"],"invalid_request");
    }
    assert_eq!(call(context,&json!({"operation":"unknown"}))["error"],"unsupported_operation");
    let request=br#"{"operation":"accounts.get","operation":"accounts.get"}"#;let mut output=empty();
    unsafe {
        assert_eq!(valmax_runtime_dispatch(context,request.as_ptr(),request.len(),&mut output),0);
        assert_eq!(value(&mut output)["error"],"invalid_request");
        valmax_runtime_close(&mut context);
        assert_eq!(valmax_runtime_dispatch(context,request.as_ptr(),request.len(),&mut output),1);
    }
    assert!(output.data.is_null()); assert!(!dir.exists());
}

#[test]
fn support_preview_is_read_only_and_export_uses_explicit_new_file() {
    let dir=root(); let profile=dir.join("profile");
    let (mut context,opened)=open(&json!({"profileDirectory":profile,"runtimeDirectory":dir.join("runtime"),"existingClient":true}));
    assert_eq!(opened["ok"],true);assert!(!dir.exists());
    assert_eq!(call(context,&json!({"operation":"version"}))["result"]["features"],opened["result"]["features"]);
    assert!(opened["result"]["features"].as_array().unwrap().contains(&json!("support_diagnostics")));
    let source=json!({"versions":{"qt":"6.11.2","valmax":"8.8.4","path":"SYNTHETIC-SECRET"},
        "startup":{"state":"private","operationId":"SYNTHETIC-SECRET"},
        "privacy":{"verified":true,"token":"SYNTHETIC-SECRET","accountId":"SYNTHETIC-SECRET"}});
    let preview=call(context,&json!({"operation":"support.preview","context":source}));
    assert_eq!(preview["ok"],true);assert_eq!(preview["result"]["report"]["schema"],2);
    assert!(!preview.to_string().contains("SYNTHETIC-SECRET"));assert!(!dir.exists());
    let mut wrong=source.clone();wrong["profileDirectory"]=json!("SYNTHETIC-SECRET");
    assert_eq!(call(context,&json!({"operation":"support.preview","context":wrong}))["error"],"invalid_request");
    fs::create_dir_all(&dir).unwrap();let destination=dir.join("report.json");
    let url=format!("file:///{}",destination.to_str().unwrap().replace('\\',"/"));
    let request=json!({"operation":"support.export","context":source,"file":url});
    assert_eq!(call(context,&request)["result"],json!({"schema":1,"saved":true}));
    let bytes=fs::read(&destination).unwrap();assert_eq!(serde_json::from_slice::<Value>(&bytes).unwrap(),preview["result"]["report"]);
    assert_eq!(call(context,&request)["error"],"file_exists");assert_eq!(fs::read(&destination).unwrap(),bytes);
    assert!(!profile.exists());unsafe {valmax_runtime_close(&mut context)};cleanup(dir);
}
