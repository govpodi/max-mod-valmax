"""Synthetic, in-memory Python/Rust renderer compatibility; no real chat/media.

HTML compared byte-for-byte; JSONL/manifest compared as structured JSON because
key order/whitespace is not part of their contract. No file publisher is invoked.
"""
from __future__ import annotations
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
import base64
import copy
import hashlib
import io
import json
import random
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'modkit'))
import chat_export_render as py
from chat_export_storage import ChatExportStorageError
import launch_mod
EXE=Path('C:/ValMAXBuild/target/release/examples/export_render_interop.exe')
OUT=Path('C:/ValMAXBuild/fixtures/export-render-v1')
CHAT={'id':'-9007199254740993','title':'Синтетический <чат>','timezoneOffsetMinutes':180}
DONE={'historyComplete':True,'stopReason':'end_of_history','pagesFetched':3}
def msg(n=1,**changes):return dict(id=str(n),author={'id':'101','name':'Автор "<&\''},timestampMs=1700000000123,text='Text\n<&"\' Привет 💬',outgoing=False,reply=None,attachments=[])|changes
def att(n=1,**changes):return dict(id=str(n),type='image',name='Изображение.png',mime='image/png',status='ready',reason=None,source='media/a'+str(n)+'.png')|changes
def blob(value):return base64.b64encode(value).decode()

class MemoryWorkspace:
    staging=Path('C:/ValMAXBuild/synthetic-not-created')
    def __init__(self,media,cancel_at=0):self.media=media;self.files={};self.discarded=[];self.checks=0;self.cancel_at=cancel_at
    @contextmanager
    def open_text(self,path):
        stream=io.StringIO()
        try:yield stream
        finally:self.files[path]=stream.getvalue()
    def data(self,path):
        value=self.media.get(path)
        if value is None:raise ChatExportStorageError('source_unavailable')
        if isinstance(value,dict):raise ChatExportStorageError(value['error'])
        return base64.b64decode(value)
    def verify_asset(self,path,max_bytes):
        data=self.data(path)
        if len(data)>max_bytes:raise ChatExportStorageError('asset_too_large')
        return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
    def discard_asset(self,path):self.discarded.append(path)
    def check_cancelled(self):
        self.checks+=1
        if self.cancel_at and self.checks>=self.cancel_at:raise ChatExportStorageError('cancelled')

def invoke_python(action):
    op=action['operation']
    if op=='chat':return py.validate_chat(action['value'])
    if op=='message':return py.validate_message(action['value'],allow_pending=action.get('allowPending',False))
    if op=='completion':return py.validate_completion(action['value'])
    if op=='escape':return py.escape(action['value'])
    if op=='filename':return py._safe_download_name(action['name'],action['mime'],detected=action.get('detected',False))
    if op=='date':return list(py._date(action['stamp'],action['offset']))
    if op=='media':
        w=MemoryWorkspace({'media/a.bin':action['data']})
        with patch.object(launch_mod,'_config_stream',side_effect=lambda *a,**kw:io.BytesIO(w.data('media/a.bin'))):return list(py._inline_kind(w,{'source':'media/a.bin','type':action['kind']}))
    if op=='render':
        w=MemoryWorkspace(action['media'],action.get('cancelAt',0));progress=[]
        with patch.object(launch_mod,'_config_stream',side_effect=lambda path,**kw:io.BytesIO(w.data(path.relative_to(w.staging).as_posix()))):
            summary=py.render_into(w,action['chat'],iter(action['messages']),action['completion'],progress=progress.append,deduplicated=action.get('deduplicated',False))
        return {'summary':summary,'files':w.files,'discarded':w.discarded,'progress':progress}
    raise AssertionError(op)
def expected(action):
    try:return {'ok':True,'result':invoke_python(action)}
    except (py.ChatExportError,ChatExportStorageError) as error:return {'ok':False,'error':error.code}
def normalized(reply,operation):
    value=copy.deepcopy(reply)
    if operation=='render' and value['ok']:
        files=value['result']['files']
        for name in files:
            if name.endswith('.json'):files[name]=json.loads(files[name])
            elif name.endswith('.jsonl'):files[name]=[json.loads(line) for line in files[name].splitlines()]
    return value

class Html(HTMLParser):
    def __init__(self):super().__init__();self.tags=[];self.urls=[]
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag)
        self.urls.extend(v for k,v in attrs if k in ('href','src'))

def main():
    actions=[];names=[]
    def add(case,op,**kw):names.append(case);actions.append({'operation':op,**kw})
    add('valid chat','chat',value=CHAT)
    for key,values in {'id':[None,3,'',' bad','x'*129,'я'],'title':[False,'\0','x'*4097],'timezoneOffsetMinutes':[True,0.5,-841,841,None]}.items():
        for n,value in enumerate(values):add(f'chat {key}/{n}','chat',value=CHAT|{key:value})
    for key,values in {'historyComplete':[None,1,False],'stopReason':['unknown','timeout',None],'pagesFetched':[True,-1,20001,1.5]}.items():
        for n,value in enumerate(values):add(f'completion {key}/{n}','completion',value=DONE|{key:value})
    for reason in sorted(py.STOP_REASONS):add('completion '+reason,'completion',value={'historyComplete':reason=='end_of_history','stopReason':reason,'pagesFetched':2})
    add('default kind','message',value=msg())
    add('ignored proof metadata','message',value=msg(attachments=[att(bytes={'ignored':'value'},sha256=False)]))
    for key,values in {'timestampMs':[None,True,1.25,-62135546400001,253402250400000],'outgoing':[0,1,None],'text':['\0',None,'💬'*2000],'author':[None,{'id':'1','name':'a','extra':1}], 'kind':['service','unsupported','unknown',None]}.items():
        for n,value in enumerate(values):add(f'message {key}/{n}','message',value=msg(**{key:value}))
    for source in ['media/a.png','media/COM1.png','media/CON.bin','media/NUL.png','media/LPT9.mp3','media/LPT0.mp3','media/COM10.mp3','media/a.b.png','media/a.SVG','media/a.svg','media/a%20b.png','https://evil.invalid/token','../data','media/a.png\n','media/a.png?token','media/'+('x'*96)+'.png','media/'+('x'*97)+'.png']:
        add('source '+source[:35],'message',value=msg(attachments=[att(source=source)]))
    for status in ['pending','ready','unavailable','cancelled','error']:
        for pending in [False,True]:add(f'status {status}/{pending}','message',value=msg(attachments=[att(status=status,source='media/a1.png' if status=='ready' else None)]),allowPending=pending)
    for mime in [' IMAGE/PNG ','\u001cIMAGE/PNG\u001f','\timage/png\n','image/png\0','image/K','image/é','image/png\x7f','x'*256,'x'*257]:add('MIME '+repr(mime[:20]),'message',value=msg(attachments=[att(mime=mime)]))
    for name in ['../CON.exe','x.svg','x.html','x.js','x.pdf','x.docx','x.mKv','x.ogg','x.oga','x','a.b.c','\\path\\file.mp3','LPT9.txt','...',' .CON .txt','\u202efile.exe','👩'*125+'.png','a\x7fb.png','x\u2066y.txt']:
        for detected in [False,True]:
            for mime in ['application/octet-stream','image/png','audio/ogg']:add('filename '+repr(name[:16])+str(detected)+mime,'filename',name=name,mime=mime,detected=detected)
    for char in ['<&"\'','\r\n\t','Русский 💬','\u2028\u2029']:add('escape '+repr(char),'escape',value=char)
    rng=random.Random(20261016)
    stamps=[None,-62135546400000,253402250399999,-1,0,1,1709164800000]+[rng.randrange(-62135546400000,253402250400000) for _ in range(40)]
    for stamp in stamps:
        for offset in [-840,-210,0,180,840]:add(f'date {stamp}/{offset}','date',stamp=stamp,offset=offset)
    media=[b'',b'<svg onload="evil()">',b'\x89PNG\r\n\x1a\n',b'\xff\xd8\xff',b'GIF89a',b'RIFF0000WEBP',b'\0\0\0\x18ftypisom',b'\x1aE\xdf\xa3',b'OggS',b'RIFF0000WAVE',b'fLaC',b'ID3',b'\xff\xe0',b'no header']
    for n,data in enumerate(media):
        for kind in ['image','video','audio','voice','file','unsupported']:add(f'media {n}/{kind}','media',data=blob(data),kind=kind)
    for total in [0,1,50,1000,1001,2003]:add(f'render {total} ordered','render',chat=CHAT,completion=DONE,messages=[msg(i) for i in range(total)],media={})
    items=[];data={}
    for n,(kind,header) in enumerate(zip(['image','image','image','image','video','audio','voice','file'],[media[2],media[3],media[4],media[5],media[6],media[11],media[8],media[1]])):
        item=att(n,type=kind,name='../CON.exe');items.append(item);data[item['source']]=blob(header+b'Synthetic')
    for n,reason in enumerate(sorted(py.REASONS),100):items.append(att(n,type='unsupported',status='unavailable',source=None,reason=reason))
    add('all media and omissions','render',chat=CHAT,completion=DONE,messages=[msg(1,attachments=items,reply={'id':'0','authorName':'<script>','text':'quote <&'}),msg(2,kind='service'),msg(3,kind='unsupported',timestampMs=None)],media=data)
    for error in ['source_unavailable','source_changed','asset_too_large','cancelled','io_error','invalid_path','exists','unsafe_path','cleanup_failed','unsupported_platform','no_space']:add('asset '+error,'render',chat=CHAT,completion=DONE,messages=[msg(attachments=[att()])],media={'media/a1.png':{'error':error}})
    add('duplicate ids','render',chat=CHAT,completion=DONE,messages=[msg(),msg()],media={})
    add('duplicate sources','render',chat=CHAT,completion=DONE,messages=[msg(1,attachments=[att()]),msg(2,attachments=[att()])],media={'media/a1.png':blob(media[2])})
    add('cancel before row','render',chat=CHAT,completion=DONE,messages=[msg()],media={},cancelAt=1)
    add('partial history','render',chat=CHAT,completion={'historyComplete':False,'stopReason':'timeout','pagesFetched':1},messages=[msg(timestampMs=None)],media={})
    checks=[];golden=None
    for first in range(0,len(actions),80):
        batch=actions[first:first+80]
        process=subprocess.run([str(EXE)],input=json.dumps({'actions':batch},ensure_ascii=False).encode(),capture_output=True,timeout=45)
        assert process.returncode==0,process.stderr[:200]
        actual=json.loads(process.stdout);assert len(actual)==len(batch)
        for index,(action,reply) in enumerate(zip(batch,actual),first):
            wanted=expected(action);equal=normalized(reply,action['operation'])==normalized(wanted,action['operation'])
            checks.append({'name':names[index],'passed':equal})
            if not equal:
                OUT.mkdir(parents=True,exist_ok=True);(OUT/'failure.json').write_text(json.dumps({'case':names[index],'request':action,'expected':wanted,'actual':reply},ensure_ascii=False,indent=2),encoding='utf8')
                raise AssertionError(names[index])
            if names[index]=='all media and omissions':golden=reply['result']
    parsed=Html();parsed.feed(golden['files']['index.html']);assert 'script' not in parsed.tags
    assert all(url.startswith('media/') and '..' not in url and ':' not in url for url in parsed.urls)
    OUT.mkdir(parents=True,exist_ok=True)
    for name,text in golden['files'].items():(OUT/name).write_text(text,encoding='utf8')
    proof={'schema':1,'passed':len(checks),'failed':0,'checks':checks,'htmlByteEquivalent':True,'jsonMetadataEquivalent':True,'realProfilesAccessed':False,'networkAccess':False,'filesPublished':False,'gateG11Complete':False,'sources':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in ['modkit/chat_export_render.py','native/core/src/export_render.rs','native/core/src/export_render_assets.rs','native/core/tests/export_render_contract.rs','native/core/tests/export_render_interop.py','native/core/examples/export_render_interop.rs']},'fixture':str(OUT)}
    (OUT/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf8')
    print(f'Export renderer: {len(checks)} passed, 0 failed; {OUT}')

if __name__=='__main__':main()
