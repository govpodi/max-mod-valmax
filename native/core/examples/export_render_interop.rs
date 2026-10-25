//! Synthetic test driver only; never built into the installer/runtime.
use std::{collections::BTreeMap,io::{self,Read}};
use base64::{Engine as _,engine::general_purpose::STANDARD};
use serde_json::{Value,json};
use valmax_core::{export_render as r,settings_io::sha256};
struct Sink {files:BTreeMap<String,String>,media:Value,discarded:Vec<String>,checks:u64,cancel_at:u64}
impl r::RenderSink for Sink {
    fn begin_file(&mut self,name:&str)->r::Result<()> {if self.files.insert(name.into(),String::new()).is_some(){return Err(r::Error::IoError);}Ok(())}
    fn write_text(&mut self,name:&str,text:&str)->r::Result<()> {self.files.get_mut(name).ok_or(r::Error::IoError)?.push_str(text);Ok(())}
    fn verify_asset(&mut self,name:&str,max:u64)->r::Result<r::VerifiedAsset> {
        let value=&self.media[name];
        if value.is_null(){return Err(r::Error::SourceUnavailable);}
        if let Some(error)=value.get("error").and_then(Value::as_str){return Err(match error {"source_changed"=>r::Error::SourceChanged,"asset_too_large"=>r::Error::AssetTooLarge,"cancelled"=>r::Error::Cancelled,"io_error"=>r::Error::IoError,"invalid_path"=>r::Error::InvalidPath,"exists"=>r::Error::Exists,"unsafe_path"=>r::Error::UnsafePath,"cleanup_failed"=>r::Error::CleanupFailed,"unsupported_platform"=>r::Error::UnsupportedPlatform,"no_space"=>r::Error::NoSpace,_=>r::Error::SourceUnavailable});}
        let bytes=STANDARD.decode(value.as_str().ok_or(r::Error::InvalidAsset)?).map_err(|_|r::Error::InvalidAsset)?;
        if bytes.len() as u64>max{return Err(r::Error::AssetTooLarge);}
        Ok(r::VerifiedAsset{bytes:bytes.len() as u64,sha256:sha256(&bytes),head:bytes[..bytes.len().min(32)].into()})
    }
    fn discard_asset(&mut self,name:&str)->r::Result<()> {self.discarded.push(name.into());Ok(())}
    fn check_cancelled(&mut self)->r::Result<()> {self.checks+=1;if self.cancel_at>0&&self.checks>=self.cancel_at{Err(r::Error::Cancelled)}else{Ok(())}}
}
fn action(value:&Value)->r::Result<Value>{
    match value["operation"].as_str().unwrap_or(""){
        "chat"=>r::validate_chat(&value["value"]),
        "message"=>r::validate_message(&value["value"],value["allowPending"]==true),
        "completion"=>r::validate_completion(&value["value"]),
        "escape"=>Ok(json!(r::escape(value["value"].as_str().ok_or(r::Error::InvalidMessage)?))),
        "filename"=>Ok(json!(r::safe_download_name(value["name"].as_str().unwrap(),value["mime"].as_str().unwrap(),value["detected"]==true))),
        "date"=>{let (day,clock,full)=r::date(value["stamp"].as_i64(),value["offset"].as_i64().unwrap())?;Ok(json!([day,clock,full]))},
        "media"=>{let bytes=STANDARD.decode(value["data"].as_str().unwrap()).unwrap();let (kind,mime)=r::inline_kind(value["kind"].as_str().unwrap(),&bytes);Ok(json!([kind,mime]))},
        "render"=>{
            let mut sink=Sink{files:BTreeMap::new(),media:value["media"].clone(),discarded:Vec::new(),checks:0,cancel_at:value["cancelAt"].as_u64().unwrap_or(0)};
            let mut progress=Vec::new();let mut callback=|counts:r::Counts|progress.push(counts.value());
            let rows=value["messages"].as_array().ok_or(r::Error::InvalidMessage)?;
            let summary=r::render_into(&mut sink,&value["chat"],rows.iter().cloned().map(Ok),&value["completion"],r::RenderOptions{deduplicated:value["deduplicated"]==true},Some(&mut callback))?;
            Ok(json!({"summary":summary,"files":sink.files,"discarded":sink.discarded,"progress":progress}))
        },
        _=>Err(r::Error::InvalidMessage),
    }
}
fn reply(value:&Value)->Value{match action(value){Ok(result)=>json!({"ok":true,"result":result}),Err(error)=>json!({"ok":false,"error":error.code()})}}
fn main(){let mut bytes=Vec::new();io::stdin().take(16*1024*1024+1).read_to_end(&mut bytes).unwrap();let value=valmax_core::session_archive::parse_json(&bytes,"invalid_payload").unwrap();let out=if let Some(actions)=value["actions"].as_array(){Value::Array(actions.iter().map(reply).collect())}else{reply(&value)};println!("{}",serde_json::to_string(&out).unwrap());}
