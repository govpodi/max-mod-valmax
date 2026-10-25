"""Owned no-window process-service lifecycle, exact Qt/Rust; no network requests."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import uuid

ROOT=Path(__file__).resolve().parents[4]
BUILD=Path('C:/ValMAXBuild/build/media-runtime-host')
QT=Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
CORE=Path('C:/ValMAXBuild/build/all-feature-inputs-6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54/valmax_core.dll')
CORE_HASH='6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54'
FLOW=ROOT/'native/tests/media/flow/proof.py'
JOB=ROOT/'native/tests/qt/run_webview_blank_native.py'
spec=importlib.util.spec_from_file_location('owned_media_runtime_process',FLOW)
flow=importlib.util.module_from_spec(spec);spec.loader.exec_module(flow)

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(ok,message):
    if not ok:raise RuntimeError(message)

def main():
    media=ROOT/'native/media'
    sources=sorted({*media.glob('*.cpp'),*media.glob('*.h'),*media.glob('*.cmake'),media/'CMakeLists.txt',
        media/'html_entities.inc',ROOT/'native/core/valmax_core.h',*Path(__file__).parent.glob('*.*'),FLOW,JOB})
    sources=[p for p in sources if p.is_file()]
    before={str(p.relative_to(ROOT)):sha(p) for p in sources}
    exe=BUILD/'valmax_media_runtime_host_test.exe'
    require(exe.stat().st_mtime_ns>=max(p.stat().st_mtime_ns for p in sources if p.suffix in ('.h','.cpp','.inc')),'rebuild required')
    require(sha(CORE)==CORE_HASH,'frozen core mismatch')
    fixture=Path('C:/ValMAXBuild/build/media-runtime-host-fixtures')/uuid.uuid4().hex
    fixture.mkdir(parents=True)
    copied={}
    for source in QT.glob('*.dll'):
        if source.name.lower().startswith(('qt6','msvcp','vcruntime','concrt','api-ms-','ucrt')):
            os.link(source,fixture/source.name);copied[source.name]=sha(source)
    require(copied.get('Qt6Core.dll')=='22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa','exact Qt required')
    shutil.copy2(CORE,fixture/CORE.name);shutil.copy2(exe,fixture/exe.name)
    exe_hash=sha(exe);require(sha(fixture/exe.name)==exe_hash,'copied executable mismatch')
    for name in ('home','local','roaming','temp'):(fixture/name).mkdir()
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','SystemDrive','COMSPEC','PATHEXT') if k in os.environ}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'),QML_DISABLE_DISK_CACHE='1',
        USERPROFILE=str(fixture/'home'),LOCALAPPDATA=str(fixture/'local'),APPDATA=str(fixture/'roaming'),TEMP=str(fixture/'temp'),TMP=str(fixture/'temp'))
    results=[]
    for mode in ('ready','quit-pending','quit-ready','exit-ready','destroy-pending'):
        profile=fixture/('profile-'+mode);profile.mkdir()
        runtime=fixture/('runtime-'+mode);runtime.mkdir()
        config=json.dumps({'profileDirectory':str(profile),'runtimeDirectory':str(runtime),'existingClient':False})
        process=flow.run(fixture,{**env,'VALMAX_SYNTHETIC_RUNTIME_CONFIG':config,'VALMAX_SYNTHETIC_HOST_MODE':mode},exe.name)
        stdout=(fixture/'stdout.log').read_text('utf8');stderr=(fixture/'stderr.log').read_text('utf8')
        (fixture/(mode+'.stdout.log')).write_text(stdout,encoding='utf8');(fixture/(mode+'.stderr.log')).write_text(stderr,encoding='utf8')
        (BUILD/'last-run.json').write_text(json.dumps({'fixture':str(fixture),'mode':mode,'process':process,'stdout':stdout,'stderr':stderr},indent=2),encoding='utf8')
        require(process['failure'] is None and not process['timedOut'] and process['exitCode']==0 and process['jobProcessesAfterCleanup']==0,'host case failed: '+mode+'; '+str(BUILD/'last-run.json'))
        require(not stderr.strip(),'unexpected stderr: '+mode)
        result=json.loads(stdout.strip().splitlines()[-1]);require(result['passed'] and result['allThreadsDrained'],'host not drained')
        require(sorted(p.name for p in profile.iterdir())==['inline-media-native-v1'],'unexpected profile write')
        require(not any(runtime.iterdir()),'runtime settings changed')
        results.append({'result':result,'process':process})
    require(before=={str(p.relative_to(ROOT)):sha(p) for p in sources},'sources changed during proof')
    require(sha(exe)==exe_hash==sha(fixture/exe.name),'executable changed')
    require(sha(CORE)==CORE_HASH==sha(fixture/CORE.name),'core changed')
    proof={'schema':1,'ok':True,'scope':'process media owner startup/quit/Qt destruction with actual frozen core preparation and idle native service',
        'checks':sum(p['result']['checks'] for p in results),'cases':results,'fixture':str(fixture),'sourceSha256':before,
        'testExecutable':{'path':str(exe),'sha256':exe_hash},'core':{'path':str(CORE),'sha256':CORE_HASH},'qt':copied,
        'limitations':['No live network, active transfer, actual MAX presenter or installed profile','Preparation validates an already loaded core; not initial-load security','Final shutdown pumps Qt events while OS/PAC/I/O complete; no hard latency guarantee']}
    output=ROOT/'analysis/native-migration-v1/media-runtime-host-proof.json';output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'checks':proof['checks'],'proof':str(output),'sha256':sha(output)}))
if __name__=='__main__':main()
