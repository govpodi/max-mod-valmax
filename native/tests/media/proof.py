"""Synthetic differential policy tests. No HTTP, accounts, credentials or staging.

Uses the real legacy search() method on a memory-only object (constructor and
all disk APIs are intentionally never invoked); transport is an in-memory fake.
"""
from __future__ import annotations
import base64
from collections import OrderedDict
from copy import deepcopy
from email.utils import formatdate
import hashlib
import html.entities
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import threading
import unittest
import uuid
import pefile

ROOT=Path(__file__).resolve().parents[3]
BUILD=Path('C:/ValMAXBuild/build/media-protocol')
QT=Path('C:/ValMAXBuild/build/max-engine-probes/probe-2335f7ff38f4437d8a23497fc19f704d/runtime')
sys.path.insert(0,str(ROOT/'modkit'))
import inline_media as old
import test_inline_media as fixtures
from test_inline_media_transport import RetryDelayTests

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def b64(b):return base64.b64encode(b).decode()
def raw(value):return json.dumps(value,ensure_ascii=True,separators=(',',':')).encode()
def legacy_search(payload,body=None,mime='application/json',body_bytes=None):
    service=object.__new__(old.InlineMediaService)
    service.clock=lambda:100.
    service._last_search=-float('inf')
    service._lock=threading.RLock()
    service._entries=OrderedDict()
    calls=[]
    def fake(url,limit,kind):
        assert kind=='api' and limit==old.MAX_API_BYTES
        calls.append(url)
        return body_bytes if body_bytes is not None else raw(body if body is not None else {}),mime
    service.transport=fake
    result=service.search(payload)
    for row in result['results']:row.pop('id')
    return result,calls

def copy_runtime(fixture):
    expected='22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa'
    assert sha(QT/'Qt6Core.dll')==expected
    pending=[QT/'Qt6Core.dll'];files={}
    while pending:
        src=pending.pop()
        if src.name in files:continue
        shutil.copy2(src,fixture/src.name);files[src.name]=sha(src)
        pe=pefile.PE(str(src),fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        for entry in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]):
            child=QT/entry.dll.decode()
            if child.is_file():pending.append(child)
        pe.close()
    assert not any(n.lower().startswith(('max','network','core.','notifications','qt6gui','qt6network')) for n in files)
    shutil.copy2(BUILD/'valmax_media_protocol_test.exe',fixture/'test.exe')
    return files

def main():
    cases=[];categories={};strict=[]
    def add(category,request,expected):
        categories[category]=categories.get(category,0)+1
        cases.append((category,request,expected))
    def url(text,kind='media',canonical=False):
        try:value=old.canonical_media_url(text) if canonical else old.safe_url(text,kind);expected={'ok':True,'value':value}
        except (old.InlineMediaError,ValueError):expected={'ok':False,'error':'invalid_remote_url','status':502,'retry':None}
        add('url',{'op':'canonical' if canonical else 'url','text':text,'kind':kind},expected)
    def query(payload):
        try:
            result,calls=legacy_search(payload);expected={'ok':True,'query':payload['query'].strip(),'url':calls[0]}
        except old.InlineMediaError as e:expected={'ok':False,'error':e.code,'status':e.status,'retry':None}
        add('request',{'op':'request','json':b64(raw(payload))},expected)
    def response(body=None,mode='pic',mime='application/json',body_bytes=None):
        data=body_bytes if body_bytes is not None else raw(body)
        try:r,_=legacy_search({'mode':mode,'query':'synthetic'},body,mime,data);expected={'ok':True,'results':r['results']}
        except old.InlineMediaError as e:expected={'ok':False,'error':e.code,'status':e.status,'retry':None}
        add('response',{'op':'response','json':b64(data),'mime':mime,'mode':mode},expected)
    def stricter(name,request):
        strict.append(name);add('intentional-stricter',request,{'reject':True})

    for value in ('cat',' кот ','🙂'*2,'🙂'*160,'🙂'*161,'x'*159,'x'*160,'x'*161,'猫 '*80,'a\nb',' a\t ','\\" filetype:video','\x1c cat \x1f','a\x7fb','a\u0085b','a\u2028b'):
        for mode in ('pic','vid'):query({'mode':mode,'query':value})
    for payload in ({},[],None,{'mode':[],'query':'abc'},{'mode':'pic','query':None},{'mode':'bad','query':'xx'},{'mode':'pic','query':'xx','url':'no'}):query(payload)
    rng=random.Random(0x56414c)
    for _ in range(250):
        query({'mode':rng.choice(['pic','vid']),'query':''.join(rng.choice(' абвxyz猫🙂\\"&<>+~') for _ in range(rng.randrange(0,170)))})
    bases=[fixtures.URL,fixtures.THUMB,fixtures.THUMB.replace('upload.','thumb.')]
    for base in bases:
        url(base)
        for tail in ('?utm_source=x','?utm_campaign=','?utm_content=x&utm_source=y','?%75tm_source=x','?utm_source=x&&utm_content=y','?utm_term=x','?auth=x','?utm_source=x&token=x','?'+ '&'.join(['utm_source=x']*9)):
            url(base+tail,canonical=True)
        url(base+'?%EF%BB%BFutm_source=x',canonical=True)
        url(base+'?utm_source=%EF%BB%BFx',canonical=True)
        for path in ('/../a','/%2e%2e/a','/%2e/a','/a/%2f../b','/%5c','?token=x','#abc'):
            url(base+path)
        for origin in ('http://upload.wikimedia.org','https://127.0.0.1','https://upload.wikimedia.org.attacker.test','https://name@upload.wikimedia.org','https://upload.wikimedia.org:444'):
            url(origin+'/wikipedia/commons/a.png')
    url(old.API,'api');url(old.API+'?action=query&x=1','api');url(fixtures.SOURCE,'source')
    for tail in ('#','?','/%00','/%1f','/%7f','/%ff','/%','/%q1','/%C2','/%E2%82','/%F0%9F'):
        stricter('raw-url-empty-delimiter-or-control-'+repr(tail),{'op':'url','text':fixtures.URL+tail,'kind':'media'})
    stricter('tracking-invalid-utf8',{'op':'canonical','text':fixtures.URL+'?utm_source=%ff'})
    for value in ('https://@upload.wikimedia.org/wikipedia/commons/a.png','https://upload.wikimedia.org./wikipedia/commons/a.png'):
        url(value)
    for host in ('ｕpload.wikimedia.org','upload.wikimedia。org','ｃommons.wikimedia.org','UPLOAD.WIKIMEDIA.ORG'):
        url('https://'+host+'/wikipedia/commons/a.png')
    url(fixtures.URL+'/%F0%9F%99%82.png')

    texts=['<b>Test author</b>','Name &amp; author','<a href="https://invalid.test">A &amp; B</a>','A<br>B','a<!--comment-->b','<img src="file:///not-opened">Name','\u202eA\u2067B','a\t\nb','&#128; &#0; &#xD800; &#1114112;','<b title="a > b">x</b>','<span>🙂'*3,'&amp notanentity;','<b>unclosed but complete tag','a < b','&am<!-- -->p;']
    for text in texts:add('plain',{'op':'plain','text':text},{'value':old.plain(text)})
    for name in html.entities.html5:
        text='&'+name
        add('html5-entity',{'op':'plain','text':text},{'value':old.plain(text)})
    for cp in list(range(160))+[0x202e,0x2066,0xfdd0,0xffff,0x10ffff,0x1f642]:
        text='a&#'+str(cp)+';b';add('numeric-entity',{'op':'plain','text':text},{'value':old.plain(text)})
    for text in ('A<script>x</script>','<style>color:red</style>A','A<b','A<?instruction>','A<![CDATA[x]]>'):
        strict.append('conservative-metadata-'+str(len(strict)));add('intentional-stricter',{'op':'plain','text':text},{'value':''})

    response(fixtures.response());response(fixtures.response('vid'),'vid')
    for key,values in {'size':[True,False,None,0,-1,1,17,17.0,17.5,old.MAX_FILE_BYTES,old.MAX_FILE_BYTES+1,'17',[]],
                       'width':[1,16000,16001,0,True,320.0,320.5,None], 'height':[1,16000,16001,0,240.0],
                       'mime':['image/png','video/webm','text/html',[],None],
                       'thumburl':['',None,False,0,0.0,1.0,True,'http://bad','https://upload.wikimedia.org/wikipedia/commons/a.png?utm_source=x'],
                       'extmetadata':[None,[],{}, {'Artist':{'value':'<b>A</b>'},'LicenseShortName':{'value':'CC0'}}]}.items():
        for value in values:
            body=fixtures.response();body['query']['pages'][0]['imageinfo'][0][key]=value;response(body)
    for value in ([],{},None,{'error':{}},{'query':[]},{'query':{'pages':None}},{'query':{'pages':[1]*7}}):response(value)
    response(fixtures.response(),mime='text/html');response(fixtures.response(),mime='application/json; charset=utf-8')
    response(body_bytes=b'\xff');response(body_bytes=b'{"query":');response(body_bytes=b'x'*(old.MAX_API_BYTES+1))
    for token in ('17.0','17e0','17.000','1.7e1','true','false','null','"17"'):
        data=raw(fixtures.response()).replace(b'"size":17',b'"size":'+token.encode());response(body_bytes=data)
    response(body_bytes=b'{"query":{},"query":{"pages":[]}}') # explicit last-wins compatibility
    stricter('nonfinite-json-ignored-field',{'op':'response','json':b64(b'{"unused":NaN}'),'mime':'application/json','mode':'pic'})
    stricter('invalid-scalar-json',{'op':'request','json':b64(b'{"mode":"pic","query":"a\\ud800"}')})

    signatures=[('image/png',fixtures.PNG),('video/webm',fixtures.WEBM),('image/jpeg',b'\xff\xd8\xffx'),('image/gif',b'GIF87ax'),('image/gif',b'GIF89ax'),('image/webp',b'RIFF0000WEBP'),('video/mp4',b'0000ftypisom'),('video/mp4',b'0000ftypM4V ') ]
    for mime,data in signatures:
        for value in [data,*[data[:i] for i in range(len(data))],b'MZ',b'<html>',b'']:
            add('signature',{'op':'signature','data':b64(value),'mime':mime},{'value':old.valid_content(value,mime)})
    wall=1700000000.
    for header in ['','0','1','5','10','100000','999999999999','-1','NaN','1.5','x'*129,'Sun Nov  6 08:49:37 2094','Sunday, 06-Nov-94 08:49:37 GMT','Sun, 06 Nov 2094 08:49:37 GMT','06 Nov 2094 08:49:37 +0300','06 Nov 2094 08:49:37','06 Nov 2094 08:49:37 EST',*[formatdate(wall+delay,usegmt=True) for delay in (-60,0,1,5,37,86400,100000,1234567)]]:
        add('retry',{'op':'retry','text':header,'wall':wall},{'value':old._retry_delay(header,wall)})

    fixture=Path('C:/ValMAXBuild/build/media-protocol-fixtures')/uuid.uuid4().hex
    fixture.mkdir(parents=True)
    copied=copy_runtime(fixture)
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC') if k in os.environ}
    env['PATH']=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32')
    flags=subprocess.CREATE_NO_WINDOW
    run=subprocess.run([str(fixture/'test.exe')],cwd=fixture,env=env,creationflags=flags,capture_output=True,timeout=20)
    if run.returncode:raise RuntimeError(run.stderr.decode(errors='replace'))
    unit=json.loads(run.stdout)
    data=b''.join(raw(request)+b'\n' for _,request,_ in cases)
    result=subprocess.run([str(fixture/'test.exe'),'--json-lines'],input=data,cwd=fixture,env=env,creationflags=flags,capture_output=True,timeout=90)
    if result.returncode:raise RuntimeError('native differential process failed '+str(result.returncode))
    actual=[json.loads(line) for line in result.stdout.splitlines()]
    assert len(actual)==len(cases)
    failures=[]
    for index,((category,request,expected),got) in enumerate(zip(cases,actual)):
        passed=(got.get('ok') is False) if expected=={'reject':True} else got==expected
        if not passed:failures.append({'index':index,'category':category,'request':request,'expected':expected,'actual':got})
    if failures:
        # Synthetic cases only, not copied to public proof.
        (fixture/'failures.json').write_text(json.dumps(failures,ensure_ascii=True,indent=2),encoding='utf-8')
        print(json.dumps({'passed':False,'failures':len(failures),'fixture':str(fixture),'first':failures[:4]}));return 1
    suite=unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(fixtures.URLTests),unittest.defaultTestLoader.loadTestsFromTestCase(RetryDelayTests)])
    legacy=unittest.TextTestRunner(verbosity=0).run(suite)
    assert legacy.wasSuccessful()
    sources=[ROOT/'native/media'/n for n in ('protocol.h','protocol.cpp','CMakeLists.txt','html_entities.inc','html-entities-provenance.json')]+[Path(__file__),Path(__file__).with_name('protocol_test.cpp'),ROOT/'modkit/inline_media.py']
    proof={'schema':1,'passed':True,'scope':'pure native protocol and policy; synthetic bytes only; no HTTP, disk staging, accounts, hooks or UI',
        'nativeChecks':unit['checks'],'differentialChecks':len(cases),'categories':categories,'legacyTests':legacy.testsRun,
        'intentionalStricterCases':strict,'fixture':str(fixture),'runtimeFiles':copied,
        'librarySha256':sha(BUILD/'valmax_media_protocol.lib'),'testExeSha256':sha(fixture/'test.exe'),
        'sources':{str(p.relative_to(ROOT)):sha(p) for p in sources},
        'limitations':['Metadata parser is a bounded conservative subset, not a full HTMLParser replacement; script/style/PI/incomplete markup rejects rather than extracting text.',
            'JSON duplicate keys retain last value as in Python. Nonfinite JSON, malformed UTF-8/scalars and depth over64 reject.',
            'Raw empty URL delimiters, malformed percent escapes, decoded controls and invalid percent UTF-8 reject more strictly.',
            'Header Content-Length is ASCII decimal only and finite bounded; response MIME header syntax is stricter than email.message.',
            'Signature validation is not a full image/video decode. Dimensions come from bounded provider metadata and remain untrusted for future decoder.',
            'Retry parser covers all3 HTTP-date forms and common RFC2822 zones; not all permissive email date comments/extensions.',
            'No media cache, ticket service, HTTP transport, file staging or native draft integration is included.']}
    output=ROOT/'analysis/native-migration-v1/media-protocol-proof.json'
    output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'native':unit['checks'],'differential':len(cases),'legacy':legacy.testsRun,'proof':str(output)}));return 0
if __name__=='__main__':raise SystemExit(main())
