//! Synthetic test harness only. This example is not part of the runtime ABI or
//! installer. In-memory/base64 adapters intentionally bound input to 16 MiB.
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{cell::RefCell, collections::BTreeMap, io::{self, Cursor, Read, Write}, rc::Rc};
use valmax_core::{mdata_archive::*, session_archive::{ArchivePolicy, parse_json}};

struct Sink(Rc<RefCell<Vec<u8>>>);
impl Write for Sink {
    fn write(&mut self, bytes:&[u8])->io::Result<usize>{self.0.borrow_mut().extend_from_slice(bytes);Ok(bytes.len())}
    fn flush(&mut self)->io::Result<()>{Ok(())}
}
fn kind(value:&Value)->Result<ArchiveKind,MDataError>{match value.as_str(){Some("profile")=>Ok(ArchiveKind::Profile),Some("login")=>Ok(ArchiveKind::Login),_=>Err(MDataError::PolicyViolation)}}
fn run(request:&Value)->Result<Value,MDataError>{
    let action=request["action"].as_str().ok_or(MDataError::InvalidPayload)?;
    if action=="casefold" {return Ok(json!({"text":path_comparison_key(request["text"].as_str().ok_or(MDataError::InvalidPayload)?),"unicodeVersion":UNICODE_VERSION}));}
    if action=="paths" {return Ok(json!({"valid":request["paths"].as_array().ok_or(MDataError::InvalidPayload)?.iter().map(|s|s.as_str().is_some_and(valid_path)).collect::<Vec<_>>()}));}
    let credentials:ArchivePolicy=serde_json::from_value(request["credentialPolicy"].clone()).map_err(|_|MDataError::PolicyViolation)?;
    let allowed=request["allowedPaths"].as_array().ok_or(MDataError::PolicyViolation)?;
    let validator=|path:&str|allowed.iter().any(|item|item.as_str()==Some(path));
    let policy=MDataPolicy{credential_policy:&credentials,file_path_validator:&validator,credential_name_validator:None};
    if action=="validate" {return Ok(json!({"manifest":validate_manifest(&request["manifest"],&policy)?}));}
    let password=request["password"].as_str().ok_or(MDataError::InvalidPassword)?;
    if action=="encrypt" {
        let contents=request["files"].as_object().ok_or(MDataError::InvalidPayload)?;let mut archive=Vec::new();
        let mut source=|path:&str|{let encoded=contents.get(path).and_then(Value::as_str).ok_or(MDataError::IoError)?;let bytes=STANDARD.decode(encoded).map_err(|_|MDataError::InvalidPayload)?;Ok(Box::new(Cursor::new(bytes)) as Box<dyn Read>)};
        let summary=encrypt_stream(&request["manifest"],password,&mut source,&mut archive,&policy,kind(&request["kind"])?)?;
        Ok(json!({"archive":STANDARD.encode(archive),"summary":summary}))
    } else if action=="decrypt" {
        let archive=STANDARD.decode(request["archive"].as_str().ok_or(MDataError::InvalidArchive)?).map_err(|_|MDataError::InvalidArchive)?;
        let mut outputs=BTreeMap::new();let mut sink=|path:&str,_|{let bytes=Rc::new(RefCell::new(Vec::new()));outputs.insert(path.to_owned(),bytes.clone());Ok(Box::new(Sink(bytes)) as Box<dyn Write>)};
        let expected=request.get("expectedKind").filter(|v|!v.is_null()).map(kind).transpose()?;
        let manifest=decrypt_stream(&mut Cursor::new(archive),password,&mut sink,&policy,None,expected)?;
        let files:BTreeMap<_,_>=outputs.into_iter().map(|(path,bytes)|(path,STANDARD.encode(&*bytes.borrow()))).collect();
        Ok(json!({"manifest":manifest,"files":files}))
    } else {Err(MDataError::InvalidPayload)}
}
fn main(){
    let mut input=Vec::new();let result=io::stdin().take(16*1024*1024+1).read_to_end(&mut input)
        .map_err(|_|MDataError::IoError).and_then(|_|parse_json(&input,"invalid_payload").map_err(MDataError::from)).and_then(|request|run(&request));
    let response=match result {Ok(value)=>json!({"ok":true,"result":value}),Err(error)=>json!({"ok":false,"error":error.code()})};
    serde_json::to_writer(io::stdout().lock(),&response).unwrap();
}
