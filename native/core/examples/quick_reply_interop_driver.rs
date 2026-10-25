//! Synthetic compatibility harness, never shipped as a runtime service.
use serde_json::{Value,json};
use std::io::{self,Read};
use valmax_core::{quick_reply_store as store,session_archive::parse_json};
fn run(request:&Value)->store::Result<Value>{
    let operation=request["operation"].as_str().ok_or(store::Error::InvalidReplies)?;
    match operation {
        "validate"=>{let clean=store::validate_replies(&request["replies"])?;Ok(json!({"snapshot":store::snapshot_value(&clean)?,"canonical":String::from_utf8(store::canonical(&clean)?).unwrap()}))},
        "parse"=>{let text=request["text"].as_str().ok_or(store::Error::InvalidStore)?;store::snapshot_value(&store::parse(text.as_bytes())?)},
        "url"=>Ok(json!({"identity":store::local_file_url(request["url"].as_str().ok_or(store::Error::InvalidReplies)?)?})),
        "select"=>store::composer_replies(&request["replies"],request["account"].as_str().ok_or(store::Error::InvalidReplies)?,request["prefix"].as_str().ok_or(store::Error::InvalidReplies)?),
        "snapshot"=>store::QuickReplyStore::new(request["path"].as_str().ok_or(store::Error::UnsafePath)?)?.snapshot(),
        "replace"=>store::QuickReplyStore::new(request["path"].as_str().ok_or(store::Error::UnsafePath)?)?.replace(request["revision"].as_str().ok_or(store::Error::InvalidRevision)?,&request["replies"]),
        _=>Err(store::Error::InvalidReplies),
    }
}
fn response(request:&Value)->Value{match run(request){Ok(result)=>json!({"ok":true,"result":result}),Err(error)=>json!({"ok":false,"error":error.code()})}}
fn main(){
    let mut input=Vec::new();io::stdin().take(16*1024*1024+1).read_to_end(&mut input).unwrap();
    let output=match parse_json(&input,"invalid_payload") {
        Ok(value)=>if let Some(actions)=value.get("actions").and_then(Value::as_array){Value::Array(actions.iter().map(response).collect())}else{response(&value)},
        Err(_)=>json!({"ok":false,"error":"invalid_replies"}),
    };
    serde_json::to_writer(io::stdout().lock(),&output).unwrap();
}
