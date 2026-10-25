//! Pure, streaming offline selected-chat renderer. No profile discovery, network,
//! SQLite, file picker, filesystem access or publication. The trusted sink owns
//! verified staging, cancellation and aborting every partially written output.
//! Caller supplies display order; unavailable history is never invented.
use serde_json::{Value, json};
use std::collections::{BTreeMap, HashSet};
use zeroize::Zeroizing;
#[path = "export_render_assets.rs"] mod assets;
use assets::*;
pub const MAX_MESSAGES: u64 = 1_000_000;
pub const MAX_ATTACHMENTS: u64 = 1_000_000;
pub const MESSAGES_PER_PAGE: u64 = 1000;
pub const MAX_TEXT: usize = 1_048_576;
pub const MAX_ASSET_BYTES: u64 = 16 * 1024 * 1024 * 1024;
pub const LOCAL_DELETED_FILE: &str = "local-deleted.html";
pub const MAX_LOCAL_DELETED_ROWS: u64 = 65_536;

/// The source is an exact selected-account/chat local store query. Original
/// send time and wire ID are deliberately absent: neither is proven here.
pub struct LocalDeletedRow {
    pub observed_deleted_at_ms: i64,
    pub text: Zeroizing<String>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Error { InvalidChat, InvalidMessage, InvalidCompletion, InvalidAsset, LimitExceeded, Cancelled, IoError, SourceUnavailable, AssetTooLarge, SourceChanged, InvalidPath, Exists, UnsafePath, CleanupFailed, UnsupportedPlatform, NoSpace }
impl Error { pub fn code(self)->&'static str { match self {
    Self::InvalidChat=>"invalid_chat",Self::InvalidMessage=>"invalid_message",Self::InvalidCompletion=>"invalid_completion",
    Self::InvalidAsset=>"invalid_asset",Self::LimitExceeded=>"limit_exceeded",Self::Cancelled=>"cancelled",Self::IoError=>"io_error",
    Self::SourceUnavailable=>"source_unavailable",Self::AssetTooLarge=>"asset_too_large",Self::SourceChanged=>"source_changed",
    Self::InvalidPath=>"invalid_path",Self::Exists=>"exists",Self::UnsafePath=>"unsafe_path",Self::CleanupFailed=>"cleanup_failed",Self::UnsupportedPlatform=>"unsupported_platform",Self::NoSpace=>"no_space",
} } }
impl std::fmt::Display for Error {fn fmt(&self,f:&mut std::fmt::Formatter<'_>)->std::fmt::Result {f.write_str(self.code())}}
impl std::error::Error for Error {}
pub type Result<T> = std::result::Result<T,Error>;
fn exact(value:&Value,required:&[&str],optional:&[&str])->bool {value.as_object().is_some_and(|map|required.iter().all(|k|map.contains_key(*k)) && map.keys().all(|k|required.contains(&k.as_str())||optional.contains(&k.as_str())))}
fn text(value:&Value,max:usize,empty:bool,error:Error)->Result<&str> {value.as_str().filter(|s|(empty||!s.is_empty()) && s.chars().count()<=max && !s.contains('\0')).ok_or(error)}
fn identifier(value:&Value,error:Error)->Result<&str> {text(value,128,false,error).and_then(|s|if s.bytes().all(|b|(33..=126).contains(&b)){Ok(s)}else{Err(error)})}
fn is_integer(value:&Value,min:i64,max:i64)->bool {value.as_i64().is_some_and(|n|(min..=max).contains(&n))}
fn member<'a>(value:&'a Value,choices:&[&str],error:Error)->Result<&'a str> {value.as_str().filter(|s|choices.contains(s)).ok_or(error)}
fn lookup<'a>(items:&[(&str,&'a str)],key:&str)->Option<&'a str>{items.iter().find_map(|(k,v)|(*k==key).then_some(*v))}
fn py_whitespace(c:char)->bool {c.is_whitespace()||('\u{1c}'..='\u{1f}').contains(&c)}
pub fn validate_chat(value:&Value)->Result<Value> {
    let error=Error::InvalidChat;
    if !exact(value,&["id","title","timezoneOffsetMinutes"],&[]) || !is_integer(&value["timezoneOffsetMinutes"],-840,840) {return Err(error);}
    Ok(json!({"id":identifier(&value["id"],error)?,"title":text(&value["title"],4096,true,error)?,"timezoneOffsetMinutes":value["timezoneOffsetMinutes"]}))
}
pub fn validate_completion(value:&Value)->Result<Value> {
    let error=Error::InvalidCompletion;
    if !exact(value,&["historyComplete","stopReason","pagesFetched"],&["retainedLocal","retainedLocalCount"]) || !value["historyComplete"].is_boolean()
        || value.get("retainedLocal").is_some() != value.get("retainedLocalCount").is_some()
        || value.get("retainedLocal").is_some_and(|item| item.as_bool() != Some(true))
        || value.get("retainedLocalCount").is_some_and(|item| !is_integer(item,0,MAX_LOCAL_DELETED_ROWS as i64))
        || !is_integer(&value["pagesFetched"],0,20000) {return Err(error);}
    let reason=member(&value["stopReason"],STOP_REASONS,error)?;
    if value["historyComplete"].as_bool()!=Some(reason=="end_of_history") {return Err(error);}
    Ok(value.clone())
}
pub fn safe_source(value:&str)->bool {
    let Some(file)=value.strip_prefix("media/") else{return false;};
    let Some((stem,ext))=file.split_once('.') else{return false;};
    if !(1..=96).contains(&stem.len()) || !stem.bytes().all(|b|b.is_ascii_alphanumeric()||b==b'_'||b==b'-') || !["jpg","jpeg","png","webp","gif","mp4","webm","ogg","opus","mp3","wav","m4a","flac","pdf","bin"].contains(&ext) {return false;}
    let device=stem.to_ascii_uppercase();
    !reserved_device(&device)
}
fn reserved_device(stem:&str)->bool {matches!(stem,"CON"|"NUL"|"PRN"|"AUX") || stem.len()==4 && (stem.starts_with("COM")||stem.starts_with("LPT")) && (b'1'..=b'9').contains(&stem.as_bytes()[3])}
pub fn validate_message(value:&Value,allow_pending:bool)->Result<Value> {
    let error=Error::InvalidMessage;
    if !exact(value,&["id","author","timestampMs","text","outgoing","reply","attachments"],&["kind"])
        || !exact(&value["author"],&["id","name"],&[]) || !value["outgoing"].is_boolean()
        || !value["timestampMs"].is_null() && !is_integer(&value["timestampMs"],-62135546400000,253402250399999) {return Err(error);}
    let kind=value.get("kind").map(|v|member(v,&["message","service","unsupported"],error)).transpose()?.unwrap_or("message");
    let reply=if value["reply"].is_null(){Value::Null}else{
        let row=&value["reply"];if !exact(row,&["id","authorName","text"],&[]){return Err(error);}
        json!({"id":identifier(&row["id"],error)?,"authorName":text(&row["authorName"],1024,true,error)?,"text":text(&row["text"],MAX_TEXT,true,error)?})
    };
    let attachments=value["attachments"].as_array().filter(|v|v.len()<=1000).ok_or(error)?;
    let mut seen=HashSet::new();let mut output=Vec::new();
    for item in attachments {
        if !exact(item,&["id","type","name","mime","status","reason"],&["source","bytes","sha256"]){return Err(error);}
        let id=identifier(&item["id"],error)?;if !seen.insert(id){return Err(error);}
        let kind=member(&item["type"],ATTACHMENT_TYPES,error)?;
        let status=member(&item["status"],if allow_pending{&["ready","unavailable","cancelled","error","pending"]}else{&["ready","unavailable","cancelled","error"]},error)?;
        if !item["reason"].is_null(){member(&item["reason"],REASONS,error)?;}
        let source=item.get("source").unwrap_or(&Value::Null);
        if status=="ready" {if !source.as_str().is_some_and(safe_source)||!item["reason"].is_null(){return Err(error);}}
        else if !source.is_null(){return Err(error);}
        if status=="pending"&&!item["reason"].is_null(){return Err(error);}
        let mime=text(&item["mime"],256,true,error)?.to_lowercase();let mime=mime.trim_matches(py_whitespace);
        if !mime.bytes().all(|b|(32..=126).contains(&b)){return Err(error);}
        output.push(json!({"id":id,"type":kind,"name":text(&item["name"],1024,true,error)?,"mime":mime,"status":status,"reason":item["reason"],"source":source}));
    }
    Ok(json!({"id":identifier(&value["id"],error)?,"author":{"id":identifier(&value["author"]["id"],error)?,"name":text(&value["author"]["name"],1024,true,error)?},
        "timestampMs":value["timestampMs"],"text":text(&value["text"],MAX_TEXT,true,error)?,"outgoing":value["outgoing"],"reply":reply,"attachments":output,"kind":kind}))
}
pub fn escape(value:&str)->String {
    let mut out=String::new();for c in value.chars(){match c{'&'=>out.push_str("&amp;"),'<'=>out.push_str("&lt;"),'>'=>out.push_str("&gt;"),'"'=>out.push_str("&quot;"),'\''=>out.push_str("&#x27;"),_=>out.push(c)}}out
}
pub fn safe_download_name(name:&str,mime:&str,detected:bool)->String {
    let basename=name.rsplit(['/', '\\']).next().unwrap_or("");
    let (stem,extension)=basename.rsplit_once('.').unwrap_or((basename,""));let extension=extension.to_lowercase();
    let clean:String=stem.chars().filter(|c|!"<>:\"|?*".contains(*c)&&u32::from(*c)>=32&&*c!='\u{7f}'&&!('\u{202a}'..='\u{202e}').contains(c)&&!('\u{2066}'..='\u{2069}').contains(c)).collect();
    let mut stem:String=clean.trim_matches([' ','.']).chars().take(120).collect();
    if stem.is_empty()||reserved_device(&stem.split('.').next().unwrap_or("").to_ascii_uppercase()){stem="Вложение".into();}
    let matches=DETECTED_EXTENSIONS.iter().find(|(key,_)|*key==mime).is_some_and(|(_,ext)|ext.contains(&extension.as_str()));
    let suffix=if ACTIVE_EXTENSIONS.contains(&extension.as_str()){"bin"}else if DOWNLOAD_EXTENSIONS.contains(&extension.as_str())&&(!detected||matches){&extension}else{lookup(MIME_EXT,mime).unwrap_or("bin")};
    format!("{stem}.{suffix}")
}
/// Classify only the supplied first 32 verified bytes; claimed MIME is not trusted.
pub fn inline_kind(kind:&str,head:&[u8])->(Option<&'static str>,Option<&'static str>) {
    let head=&head[..head.len().min(32)];
    let detected=if head.starts_with(b"\x89PNG\r\n\x1a\n"){Some("image/png")}else if head.starts_with(b"\xff\xd8\xff"){Some("image/jpeg")}
        else if head.starts_with(b"GIF87a")||head.starts_with(b"GIF89a"){Some("image/gif")}
        else if head.starts_with(b"RIFF")&&head.get(8..12)==Some(b"WEBP"){Some("image/webp")}else{None};
    if kind=="image"&&detected.is_some(){return(Some("image"),detected);}
    let mime=if head.get(4..8)==Some(b"ftyp"){Some(if kind=="video"{"video/mp4"}else{"audio/mp4"})}
        else if head.starts_with(b"\x1aE\xdf\xa3"){Some(if kind=="video"{"video/webm"}else{"audio/webm"})}
        else if head.starts_with(b"OggS"){Some(if kind=="video"{"video/ogg"}else{"audio/ogg"})}
        else if head.starts_with(b"RIFF")&&head.get(8..12)==Some(b"WAVE"){Some("audio/wav")}
        else if head.starts_with(b"fLaC"){Some("audio/flac")}
        else if head.starts_with(b"ID3")||head.len()>1&&head[0]==255&&head[1]&0xe0==0xe0{Some("audio/mpeg")}else{None};
    if mime.is_some()&&["video","audio","voice"].contains(&kind){(Some(if kind=="video"{"video"}else{"audio"}),mime)}else{(None,None)}
}
/// Validated timestamp interval reserves 14 hours at both calendar boundaries.
pub fn date(stamp:Option<i64>,offset:i64)->Result<(Option<String>,String,String)> {
    if !(-840..=840).contains(&offset){return Err(Error::InvalidChat);}
    let Some(stamp)=stamp else{return Ok((None,"Время не указано".into(),String::new()));};
    if !(-62135546400000..=253402250399999).contains(&stamp){return Err(Error::InvalidMessage);}
    let seconds=stamp.div_euclid(1000)+offset*60;let days=seconds.div_euclid(86400);let time=seconds.rem_euclid(86400);
    // Proleptic Gregorian civil date, with mathematical floor for negative eras.
    let z=days+719468;let era=z.div_euclid(146097);let doe=z-era*146097;
    let yoe=(doe-doe/1460+doe/36524-doe/146096)/365;let mut year=yoe+era*400;
    let doy=doe-(365*yoe+yoe/4-yoe/100);let mp=(5*doy+2)/153;let day=doy-(153*mp+2)/5+1;let month=mp+if mp<10{3}else{-9};year+=i64::from(month<=2);
    let date=format!("{day:02}.{month:02}.{year:04}");let clock=format!("{:02}:{:02}",time/3600,time/60%60);
    Ok((Some(date.clone()),clock.clone(),format!("{date} {clock}:{:02}",time%60)))
}
#[derive(Clone, Copy, Default, Debug)]
pub struct Counts {pub messages:u64,pub attachments:u64,pub media_ready:u64,pub media_missing:u64,pub unsupported:u64,pub bytes:u64}
impl Counts {pub fn value(self)->Value {json!({"messages":self.messages,"attachments":self.attachments,"mediaReady":self.media_ready,"mediaMissing":self.media_missing,"unsupported":self.unsupported,"bytes":self.bytes})}}
/// The sink must verify bytes/hash/header against its pinned staged file, never
/// merely echo metadata from a message. Failure must leave publication disabled.
pub struct VerifiedAsset {pub bytes:u64,pub sha256:String,pub head:Vec<u8>}
pub trait RenderSink {
    fn begin_file(&mut self,name:&str)->Result<()>;
    fn write_text(&mut self,name:&str,text:&str)->Result<()>;
    fn verify_asset(&mut self,source:&str,max_bytes:u64)->Result<VerifiedAsset>;
    fn discard_asset(&mut self,source:&str)->Result<()>;
    fn check_cancelled(&mut self)->Result<()>;
}
#[derive(Default,Clone,Copy)]
pub struct RenderOptions { /// Only a coordinator with a unique disk index may opt out.
    pub deduplicated:bool }
pub fn page_file(number:u64)->String {if number==1{"index.html".into()}else{format!("messages-{number}.html")}}
fn header(chat:&Value,completion:&Value)->String {
    let title=chat["title"].as_str().unwrap();let offset=chat["timezoneOffsetMinutes"].as_i64().unwrap();
    let mut out=format!("<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; connect-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; img-src 'self' file:; media-src 'self' file:; style-src 'unsafe-inline'\"><meta name=\"referrer\" content=\"no-referrer\"><title>{} · ValMAX</title><style>{CSS}</style></head><body><header><h1>{}</h1>",escape(title),escape(if title.is_empty(){"Чат"}else{title}));
    out.push_str(&format!("<div class=\"muted\">Экспорт ValMAX · локальная копия · время UTC{}{:02}:{:02}</div>",if offset>=0{"+"}else{"−"},offset.abs()/60,offset.abs()%60));
    let retained = completion["retainedLocal"] == true;
    out.push_str(if completion["historyComplete"]==true {
        if retained {"<div class=\"notice\"><strong>Бета: архив чата может быть неполным.</strong> Клиент достиг начала доступной истории. Отдельный раздел содержит доступные локально сохранённые удалённые входящие, полученные после включения функции. Ранее удалённые и не полученные клиентом сообщения восстановить нельзя; возможны повторы и пропуски. Файлы вложений могут отсутствовать.</div>"}
        else {"<div class=\"notice\"><strong>Бета: архив чата может быть неполным.</strong> Клиент достиг начала доступной истории. Удалённые и недоступные сообщения в копию не входят; файлы вложений могут отсутствовать.</div>"}
    } else if retained {
        "<div class=\"notice partial\"><strong>Бета: чат может быть неполным.</strong> Сохранена часть доступной истории. Полнота не подтверждена. Отдельный раздел содержит доступные локально сохранённые удалённые входящие, полученные после включения функции. Ранее удалённые и не полученные клиентом сообщения восстановить нельзя; возможны повторы и пропуски. Файлы вложений могут отсутствовать.</div>"
    } else {
        "<div class=\"notice partial\"><strong>Бета: чат может быть неполным.</strong> Сохранена часть доступной истории. Полнота не подтверждена. Восстановление уже удалённых сообщений не подтверждено; файлы вложений могут отсутствовать.</div>"
    });
    if retained {
        out.push_str("<nav><a href=\"local-deleted.html\">Локально сохранённые удаления</a></nav>");
    }
    out.push_str("</header><main>");out
}
fn footer(c:Counts,page:u64,start:Counts,next:bool)->String {
    let mut out="</main><footer>".to_owned();
    if page>1||next {
        out.push_str("<nav aria-label=\"Страницы переписки\" style=\"display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin-bottom:16px;font-size:14px\">");
        if page>1{out.push_str(&format!("<a rel=\"prev\" href=\"{}\">Предыдущая</a>",page_file(page-1)));}
        out.push_str(&format!("<span>Страница {page}</span>"));
        if next{out.push_str(&format!("<a rel=\"next\" href=\"{}\">Следующая</a>",page_file(page+1)));}
        out.push_str(&format!("</nav>На странице: {} сообщений · вложений сохранено: {} · не сохранено: {}.<br>",c.messages-start.messages,c.media_ready-start.media_ready,c.media_missing-start.media_missing));
        if !next{out.push_str(&format!("Всего в копии: {} сообщений · вложений сохранено: {} · не сохранено: {}.<br>",c.messages,c.media_ready,c.media_missing));}
    }else{out.push_str(&format!("Сообщений: {} · вложений сохранено: {} · не сохранено: {}.<br>",c.messages,c.media_ready,c.media_missing));}
    out.push_str("Часовой пояс зафиксирован при экспорте; исторические переходы на летнее время не пересчитываются. Копия открывается без подключения к сети.</footer></body></html>");out
}
fn label<'a>(value:&'a Value,key:&str,fallback:&'a str)->&'a str {let text=value[key].as_str().unwrap();if text.is_empty(){fallback}else{text}}
fn json_line(sink:&mut impl RenderSink,name:&str,value:&Value)->Result<()> {sink.write_text(name,&(serde_json::to_string(value).map_err(|_|Error::IoError)?+"\n"))}

/// A separate, bounded offline section. It cannot claim a server message
/// position or expose raw IDs; all rows are escaped and labelled as local.
pub fn render_local_deleted_into<S: RenderSink, I: IntoIterator<Item = Result<LocalDeletedRow>>>(
    sink: &mut S, chat: &Value, rows: I,
) -> Result<u64> {
    let chat = validate_chat(chat)?;
    let title = escape(chat["title"].as_str().ok_or(Error::InvalidChat)?);
    let offset = chat["timezoneOffsetMinutes"].as_i64().ok_or(Error::InvalidChat)?;
    sink.begin_file(LOCAL_DELETED_FILE)?;
    sink.write_text(LOCAL_DELETED_FILE, &format!(
        "<!doctype html><html lang=\"ru\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; connect-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'; style-src 'unsafe-inline'\"><meta name=\"referrer\" content=\"no-referrer\"><title>Локально сохранённые удаления · ValMAX</title><style>{CSS}</style></head><body><header><h1>Локально сохранённые удаления</h1><div class=\"muted\">{title}</div><p><a href=\"index.html\">К истории чата</a></p><div class=\"notice partial\">Здесь только сообщения, полученные этим аккаунтом и локально сохранённые при наблюдаемом удалении. Исходное время отправки и полный порядок переписки не подтверждены. Удаления до включения функции восстановить нельзя; возможны повторы и пропуски.</div></header><main>"
    ))?;
    let mut count = 0_u64;
    for row in rows {
        sink.check_cancelled()?;
        let row = row?;
        if row.text.is_empty() || row.text.len() > 64 * 1024 || row.text.contains('\0') {
            return Err(Error::InvalidMessage);
        }
        let (_, _, deleted_time) = date(Some(row.observed_deleted_at_ms), offset)?;
        count = count.checked_add(1).filter(|n| *n <= MAX_LOCAL_DELETED_ROWS)
            .ok_or(Error::LimitExceeded)?;
        sink.write_text(LOCAL_DELETED_FILE, &format!(
            "<article class=\"message\"><div class=\"bubble\"><div class=\"author\">Локально сохранено после удаления</div><div class=\"text\">{}</div><div class=\"time\">Наблюдаемое удаление: {}</div></div></article>",
            escape(&row.text), escape(&deleted_time)
        ))?;
    }
    if count == 0 {
        sink.write_text(LOCAL_DELETED_FILE, "<p>В этом чате нет локально сохранённых удалений.</p>")?;
    }
    sink.write_text(LOCAL_DELETED_FILE,
        "</main><footer><a href=\"index.html\">К истории чата</a></footer></body></html>")?;
    Ok(count)
}
/// Incremental output, at most current+one lookahead row. Any Err requires the
/// caller to abort/discard the complete staged export; no partial output is ready.
pub fn render_into<S:RenderSink,I:IntoIterator<Item=Result<Value>>>(sink:&mut S,chat:&Value,messages:I,completion:&Value,options:RenderOptions,mut progress:Option<&mut dyn FnMut(Counts)>)->Result<Value> {
    let chat=validate_chat(chat)?;let completion=validate_completion(completion)?;
    let mut counts=Counts::default();let mut omissions=BTreeMap::<String,u64>::new();let mut seen=HashSet::new();let mut sources=HashSet::new();
    let mut previous_day:Option<Option<String>>=None;let mut page=1;let mut start=counts;
    sink.begin_file("assets.jsonl")?;sink.begin_file("omissions.jsonl")?;
    let mut filename=page_file(page);sink.begin_file(&filename)?;sink.write_text(&filename,&header(&chat,&completion))?;
    let mut iterator=messages.into_iter();let mut current=iterator.next().transpose()?;
    while let Some(original)=current {
        let next=iterator.next().transpose()?;let has_next=next.is_some();
        sink.check_cancelled()?;let item=validate_message(&original,false)?;
        let identity=crate::settings_io::sha256(item["id"].as_str().unwrap().as_bytes());
        if !options.deduplicated&&!seen.insert(identity.clone()){return Err(Error::InvalidMessage);}
        counts.messages+=1;if counts.messages>MAX_MESSAGES{return Err(Error::LimitExceeded);}
        let (day,clock,fulltime)=date(item["timestampMs"].as_i64(),chat["timezoneOffsetMinutes"].as_i64().unwrap())?;
        if previous_day.as_ref()!=Some(&day){sink.write_text(&filename,&format!("<div class=\"day\">{}</div>",escape(day.as_deref().unwrap_or("Дата не указана"))))?;previous_day=Some(day);}
        sink.write_text(&filename,&format!("<article class=\"message{}\" id=\"m{identity}\"><div class=\"bubble\"><div class=\"author\">{}</div>",if item["outgoing"]==true{" outgoing"}else{""},escape(label(&item["author"],"name","Автор не указан"))))?;
        if !item["reply"].is_null(){let reply=&item["reply"];sink.write_text(&filename,&format!("<div class=\"reply\"><strong>{}</strong>{}</div>",escape(label(reply,"authorName","Ответ на сообщение")),escape(reply["text"].as_str().unwrap())))?;}
        if item["kind"]!="message"{counts.unsupported+=1;sink.write_text(&filename,if item["kind"]=="service"{"<div class=\"service\">Служебное сообщение</div>"}else{"<div class=\"service\">Содержимое этого сообщения поддерживается не полностью</div>"})?;}
        sink.write_text(&filename,&format!("<div class=\"text\">{}</div>",escape(item["text"].as_str().unwrap())))?;
        for attachment in item["attachments"].as_array().unwrap() {
            counts.attachments+=1;if counts.attachments>MAX_ATTACHMENTS{return Err(Error::LimitExceeded);}
            let kind=attachment["type"].as_str().unwrap();let mut reason=attachment["reason"].as_str().unwrap_or(if kind=="unsupported"{"unsupported"}else{"not_downloaded"});
            let mut proof=None;
            if attachment["status"]=="ready" {
                let source=attachment["source"].as_str().unwrap();if !options.deduplicated&&!sources.insert(source.to_owned()){return Err(Error::InvalidMessage);}
                match sink.verify_asset(source,MAX_ASSET_BYTES) {
                    Ok(value)=>{
                        if value.bytes>MAX_ASSET_BYTES{return Err(Error::InvalidAsset);}
                        if value.sha256.len()!=64||!value.sha256.bytes().all(|c|c.is_ascii_digit()||(b'a'..=b'f').contains(&c))||value.head.len()>32||value.head.len() as u64>value.bytes{return Err(Error::InvalidAsset);}
                        proof=Some(value);
                    },
                    Err(error @ (Error::SourceUnavailable|Error::AssetTooLarge|Error::SourceChanged))=>{reason=error.code();sink.discard_asset(source)?;},
                    Err(error)=>return Err(error),
                }
            }
            sink.write_text(&filename,"<div class=\"attachment\">")?;
            let label=label(attachment,"name",lookup(TYPE_TEXT,kind).unwrap());
            if let Some(proof)=proof {
                counts.media_ready+=1;counts.bytes=counts.bytes.checked_add(proof.bytes).ok_or(Error::LimitExceeded)?;
                let source=attachment["source"].as_str().unwrap();let (inline,detected)=inline_kind(kind,&proof.head);
                if inline==Some("image"){sink.write_text(&filename,&format!("<img loading=\"lazy\" src=\"{source}\" alt=\"{}\">",escape(label)))?;}
                else if let Some(tag)=inline{sink.write_text(&filename,&format!("<{tag} controls preload=\"none\" src=\"{source}\"></{tag}>"))?;}
                sink.write_text(&filename,&format!("<a href=\"{source}\" download=\"{}\">{}</a>",escape(&safe_download_name(label,detected.unwrap_or(attachment["mime"].as_str().unwrap()),detected.is_some())),escape(label)))?;
                json_line(sink,"assets.jsonl",&json!({"messageId":item["id"],"attachmentId":attachment["id"],"source":source,"name":label,"mime":attachment["mime"],"detectedMime":detected,"bytes":proof.bytes,"sha256":proof.sha256}))?;
            }else{
                counts.media_missing+=1;*omissions.entry(reason.into()).or_default()+=1;
                sink.write_text(&filename,&format!("<div class=\"missing\">{} · {}</div>",escape(label),lookup(REASON_TEXT,reason).unwrap()))?;
                json_line(sink,"omissions.jsonl",&json!({"messageId":item["id"],"attachmentId":attachment["id"],"type":kind,"reason":reason}))?;
            }
            if kind=="unsupported"{counts.unsupported+=1;}sink.write_text(&filename,"</div>")?;
        }
        sink.write_text(&filename,&format!("<div class=\"time\" title=\"{}\">{clock}</div></div></article>",escape(&fulltime)))?;
        if counts.messages%50==0{if let Some(ref mut callback)=progress{callback(counts);}}
        if has_next&&counts.messages-start.messages==MESSAGES_PER_PAGE {
            sink.write_text(&filename,&footer(counts,page,start,true))?;page+=1;start=counts;previous_day=None;
            filename=page_file(page);sink.begin_file(&filename)?;sink.write_text(&filename,&header(&chat,&completion))?;
        }
        current=next;
    }
    sink.write_text(&filename,&footer(counts,page,start,false))?;
    let summary=json!({"schema":1,"counts":counts.value(),"historyComplete":completion["historyComplete"],"stopReason":completion["stopReason"],"pagesFetched":completion["pagesFetched"],"htmlFile":"index.html","htmlPages":page});
    let mut manifest=summary.clone();manifest["chat"]=chat;manifest["omissions"]=json!(omissions);manifest["assetsFile"]="assets.jsonl".into();manifest["omissionsFile"]="omissions.jsonl".into();
    if completion["retainedLocal"] == true {
        manifest["localDeletedFile"] = json!(LOCAL_DELETED_FILE);
        manifest["localDeletedCount"] = completion["retainedLocalCount"].clone();
    }
    sink.begin_file("manifest.json")?;sink.write_text("manifest.json",&serde_json::to_string_pretty(&manifest).map_err(|_|Error::IoError)?)?;
    if let Some(callback)=progress{callback(counts);}Ok(summary)
}

#[cfg(test)]
mod beta_banner_tests {
    use super::*;

    #[test]
    fn beta_warning_remains_visible_when_the_client_reports_history_complete() {
        let chat = json!({"title":"Synthetic chat","timezoneOffsetMinutes":0});
        let complete = header(&chat, &json!({"historyComplete":true}));
        assert!(complete.contains("Бета: архив чата может быть неполным."));
        assert!(complete.contains("Клиент достиг начала доступной истории."));
        assert!(complete.contains("файлы вложений могут отсутствовать"));

        let partial = header(&chat, &json!({"historyComplete":false}));
        assert!(partial.contains("Бета: чат может быть неполным."));
        assert!(partial.contains("Полнота не подтверждена."));
        assert!(!partial.contains("Клиент достиг начала доступной истории."));
    }
}
