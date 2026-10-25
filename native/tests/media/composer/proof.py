"""Owned Qt frontend end-to-end fixture. Fake HTTP, real codecs and Rust I/O."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import uuid

ROOT=Path(__file__).resolve().parents[4]
BUILD=Path('C:/ValMAXBuild/build/media-composer')
QT=Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
CORE=Path('C:/ValMAXBuild/target-media-staging/release/deps/valmax_core.dll')
CORE_HASH='6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54'
spec=importlib.util.spec_from_file_location('owned_composer_process',ROOT/'native/tests/media/flow/proof.py')
flow=importlib.util.module_from_spec(spec);spec.loader.exec_module(flow)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(value,message):
    if not value:raise RuntimeError(message)
def main():
    names=('protocol','scheduler','transport','result_cache','controller','thumbnail','decode_queue','preview_provider','engine_context','staging_client','service_host','draft_dispatcher','composer_frontend')
    sources=[ROOT/'native/media'/f'{name}.{ext}' for name in names for ext in ('cpp','h')]
    sources += [ROOT/'native/media/service_generation.h',ROOT/'native/core/valmax_core.h',ROOT/'native/media/html_entities.inc',Path(__file__),Path(__file__).with_name('composer_test.cpp'),Path(__file__).with_name('CMakeLists.txt')]
    before={str(p.relative_to(ROOT)):sha(p) for p in sources}
    protected=[Path(p) for p in ('C:/ValMAXBuild/target/release/valmax_core.dll','C:/ValMAXBuild/target/release/deps/valmax_core.dll','C:/ValMAXBuild/build/all-feature-inputs-core-startup/valmax_core.dll','C:/ValMAXBuild/build/all-feature-inputs/valmax_core.dll')]
    frozen={str(p):sha(p) for p in protected}
    require(sha(CORE)==CORE_HASH,'frozen staging DLL mismatch')
    exe=BUILD/'valmax_media_composer_test.exe'
    require(exe.stat().st_mtime_ns>=max(p.stat().st_mtime_ns for p in sources if p.suffix in ('.h','.cpp')),'rebuild required')
    fixture=Path('C:/ValMAXBuild/build/media-composer-fixtures')/uuid.uuid4().hex
    fixture.mkdir(parents=True)
    copied={}
    for source in QT.glob('*.dll'):
        if source.name.lower().startswith(('qt6','msvcp','vcruntime','concrt','api-ms-','ucrt')):
            os.link(source,fixture/source.name);copied[source.name]=sha(source)
    for name in ('plugins','qml'):
        shutil.copytree(QT/name,fixture/name,copy_function=os.link,ignore=lambda path,names:[n for n in names if n=='ValMAX'])
    shutil.copy2(QT/'qt.conf',fixture/'qt.conf');shutil.copy2(CORE,fixture/'valmax_core.dll');shutil.copy2(exe,fixture/exe.name)
    require(copied.get('Qt6Core.dll')=='22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa','exact MAX Qt required')
    for name in ('home','local','roaming','temp','public-media'):(fixture/name).mkdir()
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','SystemDrive','COMSPEC','PATHEXT') if k in os.environ}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'),QT_QPA_PLATFORM='windows',QT_QUICK_BACKEND='software',QML_DISABLE_DISK_CACHE='1',QML_IMPORT_PATH=str(fixture/'qml'),QML2_IMPORT_PATH=str(fixture/'qml'),USERPROFILE=str(fixture/'home'),LOCALAPPDATA=str(fixture/'local'),APPDATA=str(fixture/'roaming'),TEMP=str(fixture/'temp'),TMP=str(fixture/'temp'),VALMAX_SYNTHETIC_STAGING_ROOT=str(fixture/'public-media'))
    process=flow.run(fixture,env,exe.name)
    stdout=(fixture/'stdout.log').read_text('utf8');stderr=(fixture/'stderr.log').read_text('utf8')
    last=BUILD/'last-run.json';last.write_text(json.dumps({'fixture':str(fixture),'process':process,'stdout':stdout,'stderr':stderr},indent=2),encoding='utf8')
    require(process['failure'] is None and not process['timedOut'] and process['exitCode']==0,f'composer test failed; {last}')
    result=json.loads(stdout.strip().splitlines()[-1]);require(result['passed'] and result['allThreadsDrained'],'test failed')
    for name,path in result['loadedModules'].items():
        require(Path(path).resolve()==(fixture/name).resolve(),'loaded module outside exact fixture: '+name)
        require(sha(path)==(CORE_HASH if name=='valmax_core.dll' else copied[name]),'loaded module hash mismatch: '+name)
    require(before=={str(p.relative_to(ROOT)):sha(p) for p in sources},'sources changed during proof')
    require(frozen=={str(p):sha(p) for p in protected},'older DLL changed')
    require(sha(CORE)==CORE_HASH,'staging DLL changed')
    proof={'schema':1,'ok':True,'scope':'native GUI composer frontend through actual ServiceHost, fake Commons replies, real PNG codec and frozen Rust staging into exact mock upload method',
           'result':result,'sourceSha256':before,'core':{'path':str(CORE),'sha256':CORE_HASH},'qt':copied,'testExecutable':{'path':str(exe),'sha256':sha(exe)},'fixture':str(fixture),'process':process,'frozenDllsUnchanged':frozen,'stdoutSha256':sha(fixture/'stdout.log'),'stderrSha256':sha(fixture/'stderr.log'),
           'cases':['six public rows and six decoded provider sources; genuine QQmlEngine Image.Ready cache:false','opaque selection; actual retained file exact byte read by mock upload; plain attribution caption','HTTP and storage errors, attachment limit, busy-gate release and successful retry','reentrant changed during bind and stillCurrent replacement preserve newer host','reentrant context invalidation during search cannot start old query on new account','getter and void upload reentry cannot clobber newer search UI or leak old selected gate','stale search, late decoded preview and retain completion suppressed after generation/account changes','frontend and native sender destruction during original download; all worker/reply/codecs drained'],
           'limitations':['Synthetic presenter only: no actual MAX attachment acceptance or send; void dispatch reports Dispatched only','No live HTTP, real accounts, installed client or bootstrap changes','Image.Ready uses software QtQuick; not a GPU performance result','Retained test media remains inside its new owned fixture; unrelated rejected cleanup not retried','Caller event loop and verified Rust DLL remain alive through asynchronous service drain']}
    output=ROOT/'analysis/native-migration-v1/media-composer-proof.json';output.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'checks':result['checks'],'proof':str(output),'sha256':sha(output)}))
if __name__=='__main__':main()
