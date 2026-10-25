use aes_gcm::{Aes256Gcm, KeyInit, Nonce, aead::{Aead, Payload}};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{cell::RefCell, collections::BTreeMap, io::{self, Cursor, Read, Write}, rc::Rc, sync::OnceLock};
use valmax_core::{mdata_archive::*, session_archive::ArchivePolicy};

const PASSWORD: &str = "Synthetic-MData-Password-123";
fn credentials() -> ArchivePolicy { ArchivePolicy { credential_names:vec!["access_token_42@MXMOD".into()], ..Default::default() } }
fn policy<'a>(credentials: &'a ArchivePolicy) -> MDataPolicy<'a> { MDataPolicy { credential_policy:credentials, file_path_validator:&|_| true, credential_name_validator:None } }
fn manifest(files: &[(&str, usize)]) -> Value {
    json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1",
        "credentials":[{"name":"access_token_42@MXMOD","blob":STANDARD.encode(b"SYNTHETIC-NOT-REAL")}],
        "files":files.iter().map(|(path,size)|json!({"path":path,"size":size})).collect::<Vec<_>>()})
}
struct SmallReader { cursor:Cursor<Vec<u8>>, max:usize }
impl Read for SmallReader {
    fn read(&mut self, out:&mut [u8]) -> io::Result<usize> { let limit=out.len().min(self.max); self.cursor.read(&mut out[..limit]) }
}
#[derive(Clone)]
struct Staged { bytes:Rc<RefCell<Vec<u8>>>, max:usize }
impl Write for Staged {
    fn write(&mut self, bytes:&[u8]) -> io::Result<usize> { let count=bytes.len().min(self.max); self.bytes.borrow_mut().extend_from_slice(&bytes[..count]); Ok(count) }
    fn flush(&mut self) -> io::Result<()> { Ok(()) }
}
type Outputs = BTreeMap<String, Rc<RefCell<Vec<u8>>>>;
fn decrypt(bytes:&[u8], password:&str) -> Result<(Value,Outputs),MDataError> {
    let credentials=credentials(); let mut output=Outputs::new();
    let mut sink=|path:&str,_| { let bytes=Rc::new(RefCell::new(Vec::new())); output.insert(path.to_owned(),bytes.clone()); Ok(Box::new(Staged{bytes,max:13}) as Box<dyn Write>) };
    let value=decrypt_stream(&mut SmallReader {cursor:Cursor::new(bytes.to_vec()),max:11},password,&mut sink,&policy(&credentials),None,None)?;
    Ok((value,output))
}
fn encode(input:&Value, kind:ArchiveKind, contents:&BTreeMap<String,Vec<u8>>) -> Vec<u8> {
    let credentials=credentials(); let mut output=Vec::new();
    let summary=encrypt_stream(input,PASSWORD,&mut |path|Ok(Box::new(SmallReader{cursor:Cursor::new(contents[path].clone()),max:7001}) as Box<dyn Read>),&mut output,&policy(&credentials),kind).unwrap();
    assert_eq!(summary.archive_bytes,output.len() as u64); assert_eq!(summary.schema,1); output
}
fn base() -> &'static Vec<u8> {
    static BASE:OnceLock<Vec<u8>>=OnceLock::new(); BASE.get_or_init(||encode(&manifest(&[("user42/История.db",2*CHUNK_BYTES+71),("settings/привет%.ini",4),("empty.txt",0)]),ArchiveKind::Profile,
        &BTreeMap::from([("user42/История.db".into(),vec![0x5a;2*CHUNK_BYTES+71]),("settings/привет%.ini".into(),vec![1,2,3,4]),("empty.txt".into(),vec![])])))
}
fn frames(bytes:&[u8]) -> (Vec<u8>,Vec<Vec<u8>>) {
    let offset=10+u16::from_be_bytes(bytes[8..10].try_into().unwrap()) as usize;
    let mut position=offset; let mut frames=Vec::new();
    while position<bytes.len() { let length=u32::from_be_bytes(bytes[position..position+4].try_into().unwrap()) as usize; let end=position+5+length+16; frames.push(bytes[position..end].to_vec()); position=end; }
    (bytes[..offset].to_vec(),frames)
}
fn join(header:&[u8], frames:&[Vec<u8>]) -> Vec<u8> { [header.to_vec(),frames.concat()].concat() }
fn edit_header(bytes:&[u8], edit:impl FnOnce(&mut Value)) -> Vec<u8> {
    let (header,frames)=frames(bytes); let mut value:Value=serde_json::from_slice(&header[10..]).unwrap(); edit(&mut value);
    let raw=serde_json::to_vec(&value).unwrap(); let mut result=header[..8].to_vec(); result.extend_from_slice(&(raw.len() as u16).to_be_bytes()); result.extend(raw); result.extend(frames.concat()); result
}

#[test]
fn roundtrip_both_magics_multi_chunk_unicode_empty_and_partial_io() {
    for kind in [ArchiveKind::Profile,ArchiveKind::Login] {
        let mut archive=base().clone();
        if kind==ArchiveKind::Login {
            archive=encode(&manifest(&[("user42/История.db",2*CHUNK_BYTES+71),("settings/привет%.ini",4),("empty.txt",0)]),kind,
                &BTreeMap::from([("user42/История.db".into(),vec![0x5a;2*CHUNK_BYTES+71]),("settings/привет%.ini".into(),vec![1,2,3,4]),("empty.txt".into(),vec![])]));
        }
        assert_eq!(archive_kind(&archive[..8]),Ok(kind));
        let (value,files)=decrypt(&archive,PASSWORD).unwrap(); assert_eq!(value["files"].as_array().unwrap().len(),3);
        assert_eq!(*files["user42/История.db"].borrow(),vec![0x5a;2*CHUNK_BYTES+71]);
        assert_eq!(*files["settings/привет%.ini"].borrow(),[1,2,3,4]); assert!(files["empty.txt"].borrow().is_empty());
        assert!(!archive.windows(18).any(|w|w==b"SYNTHETIC-NOT-REAL"));
    }
}

#[test]
fn nonce_randomness_and_empty_archive_require_terminal() {
    let first=encode(&manifest(&[]),ArchiveKind::Profile,&BTreeMap::new()); let second=encode(&manifest(&[]),ArchiveKind::Profile,&BTreeMap::new()); assert_ne!(first,second);
    assert_eq!(decrypt(&first,PASSWORD).unwrap().0,manifest(&[]));
    let (header,mut chunks)=frames(&first); chunks.pop(); assert_eq!(decrypt(&join(&header,&chunks),PASSWORD).err(),Some(MDataError::InvalidArchive));
}

#[test]
fn wrong_password_kind_and_magic_tamper_never_open_sinks() {
    let credentials=credentials(); let mut calls=0; let mut preflight=|_,_|{calls+=1;Ok(())};
    let mut sink=|_:&str,_|->Result<Box<dyn Write>,MDataError>{panic!("sink must not open")};
    assert_eq!(decrypt_stream(&mut Cursor::new(base()),"wrong-password-valid-length",&mut sink,&policy(&credentials),Some(&mut preflight),None),Err(MDataError::AuthenticationFailed));
    assert_eq!(decrypt_stream(&mut Cursor::new(base()),PASSWORD,&mut sink,&policy(&credentials),None,Some(ArchiveKind::Login)),Err(MDataError::InvalidArchive));
    let mut changed=base().clone(); changed[7]=2;
    assert_eq!(decrypt_stream(&mut Cursor::new(changed),PASSWORD,&mut sink,&policy(&credentials),Some(&mut preflight),None),Err(MDataError::AuthenticationFailed));
    assert_eq!(calls,0);
}

#[test]
fn altered_reordered_duplicated_spliced_frames_fail_authentication() {
    let (header,original)=frames(base());
    let mut corrupted=original.clone(); corrupted[0][6]^=1;
    let mut reversed=original.clone(); reversed.swap(0,1);
    let mut duplicated=original.clone(); duplicated.insert(1,duplicated[0].clone());
    let other=encode(&manifest(&[("other",CHUNK_BYTES*2)]),ArchiveKind::Profile,&BTreeMap::from([("other".into(),vec![0x5a;CHUNK_BYTES*2])]));
    let mut spliced=original.clone(); spliced[1]=frames(&other).1[1].clone();
    for chunks in [corrupted,reversed,duplicated,spliced] { assert_eq!(decrypt(&join(&header,&chunks),PASSWORD).err(),Some(MDataError::AuthenticationFailed)); }
}

#[test]
fn malformed_frame_headers_truncation_and_trailing_data_rejected() {
    let (header,original)=frames(base());
    for (length,flag) in [(CHUNK_BYTES as u32+1,0),(1,2),(1,1),(0,0)] {
        let mut chunks=original.clone(); chunks[0][..4].copy_from_slice(&length.to_be_bytes()); chunks[0][4]=flag;
        assert_eq!(decrypt(&join(&header,&chunks),PASSWORD).err(),Some(MDataError::InvalidArchive));
    }
    for end in [0,7,9,10,header.len()-1,header.len(),base().len()-1,base().len()-21] {
        assert_eq!(decrypt(&base()[..end],PASSWORD).err(),Some(MDataError::InvalidArchive));
    }
    let mut appended=base().clone(); appended.push(0); assert_eq!(decrypt(&appended,PASSWORD).err(),Some(MDataError::InvalidArchive));
}

#[test]
fn terminal_error_leaves_only_discardable_staging_and_never_commits() {
    let mut archive=base().clone(); *archive.last_mut().unwrap()^=1;
    let credentials=credentials(); let mut staged=Outputs::new(); let mut committed=false;
    let result=decrypt_stream(&mut Cursor::new(archive),PASSWORD,&mut |path,_|{let bytes=Rc::new(RefCell::new(Vec::new()));staged.insert(path.to_owned(),bytes.clone());Ok(Box::new(Staged{bytes,max:CHUNK_BYTES}) as Box<dyn Write>)},&policy(&credentials),None,None);
    if result.is_ok() {committed=true;} assert!(!committed); assert_eq!(result,Err(MDataError::AuthenticationFailed));
    assert_eq!(staged["user42/История.db"].borrow().len(),2*CHUNK_BYTES+71); staged.clear();
}

#[test]
fn hostile_header_parameters_rejected_before_expensive_kdf_or_callbacks() {
    for (field,value) in [("schema",json!(2)),("schema",json!(true)),("chunkBytes",json!(true)),("chunkBytes",json!(1)),("plaintextBytes",json!(MAX_PLAINTEXT_BYTES+1)),("plaintextBytes",json!(true)),("plaintextBytes",json!(0)),("cipher",json!("AES-128-GCM")),("extra",json!(0))] {
        let archive=edit_header(base(),|header|header[field]=value);
        assert_eq!(decrypt(&archive,PASSWORD).err(),Some(MDataError::InvalidArchive));
    }
    for (field,value) in [("n",json!(1u64<<40)),("n",json!(true)),("r",json!(4096)),("p",json!(2048)),("name",json!("other")),("extra",json!(0))] {
        let archive=edit_header(base(),|header|header["kdf"][field]=value); assert_eq!(decrypt(&archive,PASSWORD).err(),Some(MDataError::InvalidArchive));
    }
    assert_eq!(decrypt(&edit_header(base(),|h|h["sourceVersion"]=json!("other")),PASSWORD).err(),Some(MDataError::UnsupportedVersion));
    assert_eq!(decrypt(&edit_header(base(),|h|h["noncePrefix"]=json!(STANDARD.encode([7u8;8]))),PASSWORD).err(),Some(MDataError::AuthenticationFailed));
}

#[test]
fn unicode_paths_casefold_and_windows_aliases() {
    for path in ["профиль/История.db","settings/привет%.ini"," leading/file","emoji/😀.png","%2e%2e/file"] {assert!(valid_path(path),"{path}");}
    for path in ["", "/absolute", "a//b", "a/../b", "a\\b", "a:b", "a?b", "a. ", "a/CON", "a/con.txt", "com¹.ini", "LPT²", "CONıN$", "CLOCK$", "a/PRN .txt", "NUL", "a\0b", "a\u{85}b"] {assert!(!valid_path(path),"{path}");}
    assert!(!valid_path(&"😀".repeat(128))); assert!(valid_path(&"😀".repeat(127))); assert!(!valid_path(&"a".repeat(256)));
    let credentials=credentials();
    for (a,b) in [("Straße.txt","STRASSE.txt"),("ς.txt","Σ.txt"),("ﬃ.txt","FFI.txt"),("İ.txt","i\u{307}.txt"),("k.txt","K.txt")] {
        assert_eq!(path_comparison_key(a),path_comparison_key(b)); assert_eq!(validate_manifest(&manifest(&[(a,0),(b,0)]),&policy(&credentials)),Err(MDataError::InvalidPayload));
    }
    assert_ne!(path_comparison_key("é"),path_comparison_key("e\u{301}"));
}

#[test]
fn exact_shapes_file_count_and_aggregate_limits_without_allocating_gigabytes() {
    let credentials=credentials();
    let mut input=manifest(&[("file",0)]); input["files"][0]["size"]=json!(MAX_FILE_BYTES); assert!(validate_manifest(&input,&policy(&credentials)).is_ok());
    input["files"][0]["size"]=json!(MAX_FILE_BYTES+1); assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::TooLarge));
    for bad in [json!(true),json!(-1),json!(1.5),json!("1")] {input["files"][0]["size"]=bad;assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::InvalidPayload));}
    for bad in [json!({"path":"a","size":0,"extra":true}),json!({"path":"a"}),json!({"path":"../a","size":0})] {input["files"]=json!([bad]);assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::InvalidPayload));}
    input["files"]=json!((0..MAX_FILES+1).map(|n|json!({"path":n.to_string(),"size":0})).collect::<Vec<_>>()); assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::TooLarge));
    let long="я".repeat(200); input["files"]=json!((0..8000).map(|n|json!({"path":format!("{n}/{long}/{long}/{long}"),"size":0})).collect::<Vec<_>>()); assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::TooLarge));
}

#[test]
fn explicit_policy_deny_callbacks_and_non_ascii_credentials() {
    let credentials=credentials(); let deny=|_:&str|false;
    let denied=MDataPolicy{credential_policy:&credentials,file_path_validator:&deny,credential_name_validator:None};
    assert_eq!(validate_manifest(&manifest(&[("file",0)]),&denied),Err(MDataError::PolicyViolation));
    let empty=ArchivePolicy::default(); let allow=|name:&str|name=="access_token_42@MXMOD";
    let allow_policy=MDataPolicy{credential_policy:&empty,file_path_validator:&|_|true,credential_name_validator:Some(&allow)};
    assert!(validate_manifest(&manifest(&[]),&allow_policy).is_ok());
    let mut input=manifest(&[]); input["credentials"][0]["name"]=json!("токен"); assert_eq!(validate_manifest(&input,&allow_policy),Err(MDataError::UnsupportedCredential));
    input["credentials"][0]["name"]=json!("different"); assert_eq!(validate_manifest(&input,&allow_policy),Err(MDataError::PolicyViolation));
}

#[test]
fn short_growing_and_failed_sources_cannot_finish_archive() {
    let credentials=credentials(); let input=manifest(&[("file",3)]);
    for bytes in [vec![1u8,2],vec![1u8,2,3,4]] {
        let mut output=Vec::new(); assert_eq!(encrypt_stream(&input,PASSWORD,&mut |_|Ok(Box::new(Cursor::new(bytes.clone())) as Box<dyn Read>),&mut output,&policy(&credentials),ArchiveKind::Profile),Err(MDataError::SourceChanged));
        assert!(decrypt(&output,PASSWORD).is_err());
    }
    assert_eq!(encrypt_stream(&input,PASSWORD,&mut |_|Err(MDataError::UnsafePath),&mut Vec::new(),&policy(&credentials),ArchiveKind::Profile),Err(MDataError::UnsafePath));
}

#[test]
fn preflight_verified_counts_precede_sinks_and_may_abort() {
    let credentials=credentials(); let called=Rc::new(RefCell::new(false)); let seen=called.clone(); let mut files=0;
    let mut preflight=|bytes,count| {assert_eq!(bytes,2*CHUNK_BYTES as u64+75);assert_eq!(count,3);*seen.borrow_mut()=true;Ok(())};
    let mut sink=|_:&str,_|{assert!(*called.borrow());files+=1;Ok(Box::new(io::sink()) as Box<dyn Write>)};
    assert!(decrypt_stream(&mut Cursor::new(base()),PASSWORD,&mut sink,&policy(&credentials),Some(&mut preflight),None).is_ok()); assert_eq!(files,3);
    let mut reject=|_,_|Err(MDataError::InsufficientSpace);
    assert_eq!(decrypt_stream(&mut Cursor::new(base()),PASSWORD,&mut |_,_|panic!("must not open sink"),&policy(&credentials),Some(&mut reject),None),Err(MDataError::InsufficientSpace));
}

struct BrokenWriter { mode:u8 }
impl Write for BrokenWriter {
    fn write(&mut self,bytes:&[u8])->io::Result<usize>{match self.mode {0=>Ok(0),1=>Ok(bytes.len()+1),_=>Err(io::Error::new(io::ErrorKind::StorageFull,"SYNTHETIC-PRIVATE-ERROR"))}}
    fn flush(&mut self)->io::Result<()>{Ok(())}
}
#[test]
fn write_failures_have_only_finite_error_codes() {
    let credentials=credentials();
    for (mode,expected) in [(0,MDataError::IoError),(1,MDataError::IoError),(2,MDataError::InsufficientSpace)] {
        assert_eq!(encrypt_stream(&manifest(&[]),PASSWORD,&mut |_|panic!("no files"),&mut BrokenWriter{mode},&policy(&credentials),ArchiveKind::Profile),Err(expected));
        assert_eq!(decrypt_stream(&mut Cursor::new(base()),PASSWORD,&mut |_,_|Ok(Box::new(BrokenWriter{mode}) as Box<dyn Write>),&policy(&credentials),None,None),Err(expected));
        assert!(!expected.to_string().contains("SYNTHETIC"));
    }
}

struct BrokenReader { overflow: bool }
impl Read for BrokenReader {
    fn read(&mut self,bytes:&mut [u8])->io::Result<usize>{
        if self.overflow {Ok(bytes.len()+1)} else {Err(io::Error::new(io::ErrorKind::Other,"SYNTHETIC-PRIVATE-ERROR"))}
    }
}
#[test]
fn invalid_read_counts_and_source_io_errors_are_contained() {
    let credentials=credentials();
    for overflow in [false,true] {
        assert_eq!(decrypt_stream(&mut BrokenReader{overflow},PASSWORD,&mut |_,_|panic!("no sink"),&policy(&credentials),None,None),Err(MDataError::IoError));
        assert_eq!(encrypt_stream(&manifest(&[("file",1)]),PASSWORD,&mut |_|Ok(Box::new(BrokenReader{overflow}) as Box<dyn Read>),&mut Vec::new(),&policy(&credentials),ArchiveKind::Profile),Err(MDataError::IoError));
    }
}

#[test]
fn credential_bounds_are_checked_before_clean_manifest_clone() {
    let credentials=credentials(); let mut input=manifest(&[]);
    input["credentials"][0]["blob"]=json!(STANDARD.encode(vec![0u8;4*1024*1024+1]));
    assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::TooLarge));
    input["credentials"][0]["extra"]=json!(true);
    assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::InvalidPayload));
    input["credentials"][0].as_object_mut().unwrap().remove("extra");
    input["credentials"][0]["name"]=json!("unapproved");
    assert_eq!(validate_manifest(&input,&policy(&credentials)),Err(MDataError::PolicyViolation));
}

// A synthetic independently sealed plaintext lets tests reach authenticated
// malformed manifest paths instead of only testing GCM rejection.
fn seal(plaintext:&[u8], expected:u64) -> Vec<u8> {
    let salt=[9u8;16]; let prefix=[8u8;8]; let mut key=[0u8;32]; scrypt::scrypt(PASSWORD.as_bytes(),&salt,&scrypt::Params::new(15,8,1,32).unwrap(),&mut key).unwrap();
    let header=serde_json::to_vec(&json!({"schema":1,"sourceClient":"MAX Desktop","sourceVersion":"26.35.1","cipher":"AES-256-GCM","chunkBytes":CHUNK_BYTES,"plaintextBytes":expected,"noncePrefix":STANDARD.encode(prefix),"kdf":{"name":"scrypt","n":32768,"r":8,"p":1,"salt":STANDARD.encode(salt)}})).unwrap();
    let mut archive=MAGIC.to_vec(); archive.extend_from_slice(&(header.len() as u16).to_be_bytes()); archive.extend(header); let aad=archive.clone();
    let cipher=Aes256Gcm::new_from_slice(&key).unwrap(); let chunks:Vec<&[u8]>=plaintext.chunks(CHUNK_BYTES).chain(std::iter::once(&[][..])).collect();
    for (index,bytes) in chunks.iter().enumerate() {let counter=(index as u32).to_be_bytes();let mut frame=(bytes.len() as u32).to_be_bytes().to_vec();frame.push(u8::from(bytes.is_empty()));let mut nonce=prefix.to_vec();nonce.extend(counter);let mut frame_aad=aad.clone();frame_aad.extend(counter);frame_aad.extend(&frame);let encrypted=cipher.encrypt(Nonce::from_slice(&nonce),Payload{msg:bytes,aad:&frame_aad}).unwrap();archive.extend(frame);archive.extend(encrypted);}
    archive
}
#[test]
fn authenticated_invalid_json_canonical_form_and_manifest_lengths() {
    for raw in [br#"{"schema":1,"schema":1}"#.to_vec(),b"NaN".to_vec(),vec![b'"',0xff,b'"']] {
        let mut plain=(raw.len() as u32).to_be_bytes().to_vec();plain.extend(raw);let archive=seal(&plain,plain.len() as u64);assert_eq!(decrypt(&archive,PASSWORD).err(),Some(MDataError::InvalidPayload));
    }
    for (length,expected) in [(1,MDataError::InvalidPayload),(MAX_MANIFEST_BYTES as u32+1,MDataError::TooLarge)] {
        let archive=seal(&length.to_be_bytes(),6);assert_eq!(decrypt(&archive,PASSWORD).err(),Some(expected));
    }
    let raw=serde_json::to_vec_pretty(&manifest(&[])).unwrap();let mut plain=(raw.len() as u32).to_be_bytes().to_vec();plain.extend(raw);assert_eq!(decrypt(&seal(&plain,plain.len() as u64),PASSWORD).err(),Some(MDataError::InvalidArchive));
    let raw=serde_json::to_vec(&manifest(&[])).unwrap();let mut plain=(raw.len() as u32).to_be_bytes().to_vec();plain.extend(raw);assert_eq!(decrypt(&seal(&plain,plain.len() as u64+1),PASSWORD).err(),Some(MDataError::InvalidArchive));
}
