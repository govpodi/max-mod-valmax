"""Differential quick-reply tests: synthetic text/URLs, never attachment access.

Build the quick_reply_interop_driver example first. Temp profiles and proof live
under VALMAX_TEST_TEMP_ROOT when set; neither Python nor Rust opens the saved
attachment URLs.
"""
from pathlib import Path
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'modkit'))
import quick_reply_store as py

DRIVER=Path(os.environ.get('CARGO_TARGET_DIR', str(ROOT/'target')))/'release/examples/quick_reply_interop_driver.exe'
ARTIFACTS=Path(os.environ.get('VALMAX_TEST_TEMP_ROOT', tempfile.gettempdir()))/'quick-reply-v1'
CHECKS=[]

def row(n=1,**changes):
    return dict(id=f'{n:032x}',command=f'ответ{n}',title='Искусственная заготовка',text='Тестовая строка.\nНовая строка. 😀',**{})|changes

def check(name,condition):
    CHECKS.append(dict(name=name,passed=bool(condition)))
    if not condition:raise AssertionError(name)

def native(request):
    result=subprocess.run([str(DRIVER)],input=json.dumps(request,ensure_ascii=False).encode(),capture_output=True,check=True,timeout=15)
    return json.loads(result.stdout)

def oracle(request):
    try:
        action=request['operation']
        if action=='validate':
            clean=py._validated_replies(request['replies'],'invalid_replies')
            result={'snapshot':py._snapshot(clean,'invalid_replies'),'canonical':py._canonical(clean,'invalid_replies').decode()}
        elif action=='url':result={'identity':py._local_file_url(request['url'],'invalid_replies')[1]}
        elif action=='parse':result=py._snapshot(py._parse(request['text'].encode()))
        elif action=='select':
            account=request['account'];prefix=request['prefix'];py._account(account,'invalid_replies')
            rows=py._validated_replies(request['replies'],'invalid_replies');specific={r['command'] for r in rows if r.get('accountId') and r['accountId']==account}
            result=[r for r in rows if (not r.get('accountId') and r['command'] not in specific or r.get('accountId') and r['accountId']==account) and r['command'].startswith(prefix)]
        else:raise AssertionError(action)
        return {'ok':True,'result':result}
    except py.QuickReplyStoreError as error:return {'ok':False,'error':error.code}

def main():
    ARTIFACTS.mkdir(parents=True,exist_ok=True)
    cases=[]
    def add(name,operation,**kwargs):cases.append((name,dict(operation=operation,**kwargs)))
    extended=row(2,accountId='9223372036854775807',text='',attachments=[{'url':'file:///Z:/nonexistent/пример%20😀.txt','kind':'file'}],
                 parts=[{'text':'Следующий текст'},{'text':'','attachments':[{'url':'file:///D:/synthetic/photo.jpg','kind':'media'}]}])
    fixture=[row(),extended,row(3,accountId='')]
    add('legacy and extended UTF-8 canonical','validate',replies=fixture)
    add('empty canonical','validate',replies=[])
    for n,changes in enumerate([{'id':'A'*32},{'id':'g'*32},{'id':'0'*31},{'command':'Ответ'},{'command':'/answer'},{'command':'answer-now'},
        {'command':'1answer'},{'command':'a'*33},{'command':'x\n'},{'title':'😀'*81},{'text':'я'*5001},{'title':None},{'text':12},{'text':'\0'},
        {'text':''},{'accountId':False},{'accountId':101},{'accountId':'0'},{'accountId':'01'},{'accountId':'-1'},{'accountId':'+1'},
        {'accountId':' 1'},{'accountId':'1\n'},{'accountId':'9223372036854775808'},{'unknown':'disallowed'}]):
        add(f'invalid row {n}','validate',replies=[row(**changes)])
    for n,text in enumerate(['😀'*5000,'a'*5000,'я'*5000,' \t\n','\b\f\r\u0001','é','e\u0301','\u0085','\u2028','\u2066']):
        add(f'valid Unicode/control text {n}','validate',replies=[row(text=text)])
    for n,command in enumerate(['a','ё','я_0','ё'+'я'*31,'é','і','ї','Ā','а'*33]):add(f'command alphabet {n}','validate',replies=[row(command=command)])
    for n,replies in enumerate([None,{},[None],[{}],[row(),row(2,id=row()['id'])],[row(),row(2,command=row()['command'])],
        [row(n,text='small') for n in range(100)],[row(n,text='small') for n in range(101)],
        [row(n,text='я'*5000) for n in range(30)]]):add(f'row-list shape/limits {n}','validate',replies=replies)
    files=[{'url':f'file:///D:/synthetic/{n}.txt','kind':'file'} for n in range(10)]
    for n,attachments in enumerate([files,[],files+[{'url':'file:///D:/extra','kind':'file'}],[{'url':'file:///D:/file','kind':'url'}],
        [{'url':'file:///D:/file','kind':'file','content':'disallowed'}],
        [{'url':'file:///D:/a%20b','kind':'file'},{'url':'file:///d:/A b','kind':'media'}],
        [{'url':'file:///D:/Straße','kind':'file'},{'url':'file:///D:/STRASSE','kind':'file'}],
        [{'url':'file:///D:/ς','kind':'file'},{'url':'file:///D:/Σ','kind':'file'}],
        [{'url':'file:///D:/İ','kind':'file'},{'url':'file:///D:/i\u0307','kind':'file'}]]):
        add(f'attachment shape/casefold {n}','validate',replies=[row(text='',attachments=attachments)])
    for n,parts in enumerate([[],[{'text':'x'}]*9,[{'text':'x'}]*10,[{'text':''}],[{'text':'x'*5001}],
        [{'text':'ok','autoSend':True}],[None],'text',[{'text':'','attachments':files}]]):add(f'parts limits {n}','validate',replies=[row(parts=parts)])
    scopes=[row(1,command='away'),row(2,command='away',accountId='101'),row(3,command='away',accountId='202'),row(4,command='another')]
    for account in ('','101','202','303'):
        for prefix in ('','a','away','another','no'):add(f'scope {account or "common"}/{prefix}','select',replies=scopes,account=account,prefix=prefix)
    for scope in ('','101'):
        add(f'duplicate scope {scope}','validate',replies=scopes+[row(5,command='away',accountId=scope)])

    urls=['https://example.org/a','file://server/share/a','file://localhost/D:/a.txt','file:////server/share/a','file:///tmp/a','D:/a.txt','file:///D:/',
          'file:///D:/../a','file:///D:/%2e%2e/a','file:///D:/a\\b','file:///D:/a%5cb','file:///D:/a.txt:stream','file:///D:/CON.txt','file:///D:/a/./b',
          'file:///D:/a?query','file:///D:/a#fragment','file:///D:/a%00.txt','file:///D:/a%0A.txt','file:///D:/%ff.txt','file:///D:/a%.txt',
          'file:///D:/trailing.','file:///D:/a%20','file:///D://a.txt','file:///D:/a?','file:///D:/a#','file:///D:/a?#','file:///D:/a#?',
          'file:///D:/a%23','file:///D:/COM¹','file:///D:/CONIN$','file:///D:/ leading','file:///D:/PRN .txt','file:///D:/a\u0085b']
    urls += [f'file:///D:/a%{n:02x}b' for n in range(256)]
    urls += [f'file:///D:/a{chr(n)}b' for n in [1,8,9,10,13,31,32,126,127,128,133,159,160,0x130,0x3c2,0x2028,0x202e,0x2066,0xfeff,0x1f680]]
    urls += ['file:///D:/'+'я'*n for n in (4085,4086,4087,4096)]
    for n,url in enumerate(urls):add(f'URL spelling {n}','url',url=url)
    raw_cases=['','{','[]','{"schema":NaN,"replies":[]}','{"schema":1,"schema":1,"replies":[]}',
        '{"schema":true,"replies":[]}','{"schema":1.0,"replies":[]}','{"schema":1,"replies":[],"other":1}',
        '{"schema":1,"replies":[]} trailing','{"schema":1,"replies":[{"text":"\\ud800"}]}',
        '['*3000+']'*3000,'{"schema":'+'9'*5000+',"replies":[]}',json.dumps({'schema':1,'replies':fixture},ensure_ascii=False,indent=2)]
    for n,text in enumerate(raw_cases):add(f'persisted malformed/pretty {n}','parse',text=text)
    # Chunk batches so the test harness keeps its 16 MiB request bound.
    for start in range(0,len(cases),100):
        batch=cases[start:start+100];outputs=native({'actions':[request for _,request in batch]})
        for (name,request),actual in zip(batch,outputs):check(name,actual==oracle(request))

    scratch=ARTIFACTS/('stores-'+uuid.uuid4().hex);path=scratch/'quick-replies.json';store=py.QuickReplyStore(path)
    check('Python/Rust absent snapshot',native({'operation':'snapshot','path':str(path)})=={'ok':True,'result':store.snapshot()} and not scratch.exists())
    old=store.snapshot()['revision'];python_saved=store.replace(old,fixture)
    check('Python store to native snapshot',native({'operation':'snapshot','path':str(path)})=={'ok':True,'result':python_saved})
    changed=fixture+[row(4,command='новый',accountId='202')]
    response=native({'operation':'replace','path':str(path),'revision':python_saved['revision'],'replies':changed})
    check('Native store to Python snapshot',response=={'ok':True,'result':store.snapshot()} and response['result']['replies']==changed)
    before=path.read_bytes();response=native({'operation':'replace','path':str(path),'revision':old,'replies':[]})
    check('Cross-language stale edit preserves bytes',response=={'ok':False,'error':'revision_conflict'} and path.read_bytes()==before)
    empty=store.replace(store.snapshot()['revision'],[])
    request=dict(operation='replace',path=str(path),revision=empty['revision'],replies=[row(6)])
    outcomes=[]
    def python_writer():
        try:store.replace(empty['revision'],[row(7)]);outcomes.append('saved')
        except py.QuickReplyStoreError as error:outcomes.append(error.code)
    with py._writer_lock(path):
        process=subprocess.Popen([str(DRIVER)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        process.stdin.write(json.dumps(request).encode());process.stdin.close();process.stdin=None
        thread=threading.Thread(target=python_writer);thread.start()
    stdout,stderr=process.communicate(timeout=10);thread.join(timeout=10)
    check('Race workers completed',process.returncode==0 and not thread.is_alive() and not stderr)
    outcome=json.loads(stdout);outcomes.append('saved' if outcome['ok'] else outcome['error'])
    check('Python/native mutex CAS one winner',sorted(outcomes)==['revision_conflict','saved'])
    with py._writer_lock(path):
        response=native({'operation':'replace','path':str(path),'revision':store.snapshot()['revision'],'replies':[row(8)]})
    check('Native obeys Python writer mutex timeout',response=={'ok':False,'error':'store_busy'})

    proof={'schema':1,'scope':'Quick-reply store/service-free layer; synthetic templates and metadata only','checks':CHECKS,
           'passed':sum(item['passed'] for item in CHECKS),'failed':sum(not item['passed'] for item in CHECKS),
           'realProfilesAccessed':False,'attachmentTargetsOpened':False,'messagesSent':False,'fullMigrationOrInstallerVerified':False,
           'sources':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
               ['modkit/quick_reply_store.py','modkit/quick_replies_composer.qml','native/core/src/quick_reply_store.rs',
                'native/core/src/settings_io.rs','native/core/tests/quick_reply_store_contract.rs','native/core/tests/quick_reply_interop.py',
                'native/core/examples/quick_reply_interop_driver.rs']},
           'remainingIntegration':['runtime/FFI get/set routing','Qt settings and scoped composer refresh','explicit picker and draft-only attachment staging','manual-send UI reset/next-part behavior'],
           'artifacts':str(scratch)}
    (ARTIFACTS/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2),encoding='utf8')
    (ARTIFACTS/'golden.json').write_text(json.dumps({'replies':fixture,'expected':oracle({'operation':'validate','replies':fixture})},ensure_ascii=False,indent=2),encoding='utf8')
    print(f"Quick-reply interop: {proof['passed']} passed, {proof['failed']} failed; {ARTIFACTS/'proof.json'}")

if __name__=='__main__':main()
