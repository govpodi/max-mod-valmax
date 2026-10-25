use serde_json::{Value,json};
use valmax_core::export_render as r;
use std::{collections::BTreeMap,cell::Cell};
#[derive(Default)]
struct Sink {files:BTreeMap<String,String>,cancel:bool,fail_write:bool,bad_asset:bool,discarded:u64}
impl r::RenderSink for Sink {
    fn begin_file(&mut self,name:&str)->r::Result<()> {self.files.insert(name.into(),String::new());Ok(())}
    fn write_text(&mut self,name:&str,text:&str)->r::Result<()> {if self.fail_write{return Err(r::Error::IoError);}self.files.get_mut(name).unwrap().push_str(text);Ok(())}
    fn verify_asset(&mut self,_:&str,_:u64)->r::Result<r::VerifiedAsset> {if self.bad_asset{Ok(r::VerifiedAsset{bytes:0,sha256:"invalid".into(),head:vec![]})}else{Err(r::Error::SourceChanged)}}
    fn discard_asset(&mut self,_:&str)->r::Result<()> {self.discarded+=1;Ok(())}
    fn check_cancelled(&mut self)->r::Result<()> {if self.cancel{Err(r::Error::Cancelled)}else{Ok(())}}
}
fn chat()->Value{json!({"id":"-9007199254740993","title":"Synthetic <script>","timezoneOffsetMinutes":180})}
fn completion()->Value{json!({"historyComplete":true,"stopReason":"end_of_history","pagesFetched":1})}
fn message(id:u64)->Value{json!({"id":id.to_string(),"author":{"id":"101","name":"Test"},"timestampMs":null,"text":"<script>\"'&</script>","outgoing":false,"reply":null,"attachments":[]})}
fn attachment()->Value{json!({"id":"1","type":"image","name":"file.svg","mime":"image/png","status":"ready","reason":null,"source":"media/a.png"})}
fn render(sink:&mut Sink,rows:Vec<Value>)->r::Result<Value>{r::render_into(sink,&chat(),rows.into_iter().map(Ok),&completion(),r::RenderOptions::default(),None)}
#[test]fn offline_escaping_and_empty_page(){let mut sink=Sink::default();let out=render(&mut sink,vec![]).unwrap();assert_eq!(out["htmlPages"],1);let html=&sink.files["index.html"];assert!(html.contains("script-src 'none'")&&html.contains("Synthetic &lt;script&gt;")&&!html.contains("<script>"));assert!(sink.files["assets.jsonl"].is_empty());let mut sink=Sink::default();render(&mut sink,vec![message(1)]).unwrap();assert!(sink.files["index.html"].contains("&lt;script&gt;&quot;&#x27;&amp;&lt;/script&gt;"));}
#[test]fn source_paths_and_active_extensions(){for source in ["https://example.invalid/a.png","media/a.svg","media/NUL.png","media/a.png?x","media/../a.png","media/a.png:ads","media/a.JPG","media/a.b.png"]{assert!(!r::safe_source(source));}assert!(r::safe_source("media/A_-1.png"));for ext in ["svg","html","exe","js","lnk"]{assert_eq!(r::safe_download_name(&format!("../test.{ext}"),"image/png",false),"test.bin");}assert_eq!(r::safe_download_name("CON.jpg","image/png",true),"Вложение.png");}
#[test]fn signatures_do_not_embed_claimed_active_media(){assert_eq!(r::inline_kind("image",b"<svg onload='x'>"),(None,None));assert_eq!(r::inline_kind("image",b"\x89PNG\r\n\x1a\n"),(Some("image"),Some("image/png")));assert_eq!(r::inline_kind("voice",b"\0\0\0\x18ftypisom"),(Some("audio"),Some("audio/mp4")));assert_eq!(r::inline_kind("file",b"ID3test"),(None,None));}
#[test]fn validation_and_scalar_bounds(){let mut row=message(1);row["text"]="💬".repeat(r::MAX_TEXT).into();r::validate_message(&row,false).unwrap();row["text"]="💬".repeat(r::MAX_TEXT+1).into();assert_eq!(r::validate_message(&row,false).unwrap_err(),r::Error::InvalidMessage);let mut row=message(1);row["attachments"]=json!([attachment(),attachment()]);assert!(r::validate_message(&row,false).is_err());let mut complete=completion();complete["pagesFetched"]=true.into();assert!(r::validate_completion(&complete).is_err());}
#[test]fn missing_assets_and_invalid_proofs_are_not_published(){let mut row=message(1);row["attachments"]=json!([attachment()]);let mut sink=Sink::default();let out=render(&mut sink,vec![row.clone()]).unwrap();assert_eq!(out["counts"]["mediaMissing"],1);assert_eq!(sink.discarded,1);assert!(sink.files["omissions.jsonl"].contains("source_changed"));let mut sink=Sink{bad_asset:true,..Default::default()};assert_eq!(render(&mut sink,vec![row]).unwrap_err(),r::Error::InvalidAsset);assert!(!sink.files.contains_key("manifest.json"));}
#[test]fn duplicate_cancel_and_sink_error_propagate(){let mut sink=Sink::default();assert_eq!(render(&mut sink,vec![message(1),message(1)]).unwrap_err(),r::Error::InvalidMessage);let mut sink=Sink{cancel:true,..Default::default()};assert_eq!(render(&mut sink,vec![message(1)]).unwrap_err(),r::Error::Cancelled);let mut sink=Sink{fail_write:true,..Default::default()};assert_eq!(render(&mut sink,vec![]).unwrap_err(),r::Error::IoError);}
#[test]fn pagination_and_one_row_lookahead(){for total in [1000,1001,2003]{let consumed=Cell::new(0u64);let rows=(0..total).map(|n|{consumed.set(consumed.get()+1);Ok(message(n))});let mut callback=|counts:r::Counts|assert!(consumed.get()<=counts.messages+1);let mut sink=Sink::default();let out=r::render_into(&mut sink,&chat(),rows,&completion(),r::RenderOptions{deduplicated:true},Some(&mut callback)).unwrap();let pages=(total+999)/1000;assert_eq!(out["htmlPages"],pages);assert_eq!(consumed.get(),total);assert_eq!(sink.files.values().map(|v|v.matches("<article ").count()).sum::<usize>(),total as usize);for page in 1..=pages{let html=&sink.files[&r::page_file(page)];assert_eq!(html.contains("rel=\"next\""),page<pages);assert_eq!(html.contains("rel=\"prev\""),page>1);}}}
#[test]fn timestamp_boundaries_are_supported(){assert_eq!(r::date(Some(-62135546400000),-840).unwrap().0.as_deref(),Some("01.01.0001"));assert_eq!(r::date(Some(253402250399999),840).unwrap().2,"31.12.9999 23:59:59");assert_eq!(r::date(Some(-1),0).unwrap().2,"31.12.1969 23:59:59");assert!(r::date(Some(i64::MAX),0).is_err());}

struct NullSink {manifest_started:bool}
impl r::RenderSink for NullSink {
    fn begin_file(&mut self,name:&str)->r::Result<()>{if name=="manifest.json"{self.manifest_started=true;}Ok(())}
    fn write_text(&mut self,_:&str,_:&str)->r::Result<()>{Ok(())}
    fn verify_asset(&mut self,_:&str,_:u64)->r::Result<r::VerifiedAsset>{Err(r::Error::SourceUnavailable)}
    fn discard_asset(&mut self,_:&str)->r::Result<()>{Ok(())}
    fn check_cancelled(&mut self)->r::Result<()>{Ok(())}
}
#[test]fn million_message_and_attachment_limits_stream_without_output_accumulation(){
    let mut sink=NullSink{manifest_started:false};let mut reached=0;
    let mut progress=|c:r::Counts|reached=c.messages;
    let result=r::render_into(&mut sink,&chat(),(0..=r::MAX_MESSAGES).map(|id|Ok(message(id))),&completion(),r::RenderOptions{deduplicated:true},Some(&mut progress));
    assert_eq!(result.unwrap_err(),r::Error::LimitExceeded);assert_eq!(reached,r::MAX_MESSAGES);assert!(!sink.manifest_started);
    let attachments:Vec<_>=(0..1000).map(|id|json!({"id":id.to_string(),"type":"file","name":"x","mime":"","status":"unavailable","reason":"not_available"})).collect();
    let mut row=message(1);row["attachments"]=json!(attachments);let mut sink=NullSink{manifest_started:false};
    let rows=(0..=1000).map(|_|Ok(row.clone()));
    let result=r::render_into(&mut sink,&chat(),rows,&completion(),r::RenderOptions{deduplicated:true},None);
    assert_eq!(result.unwrap_err(),r::Error::LimitExceeded);assert!(!sink.manifest_started);
}
