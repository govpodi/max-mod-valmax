"""Synthetic process-wide Qt service plus frozen Rust staging; no live HTTP."""
from __future__ import annotations
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

ROOT=Path(__file__).resolve().parents[4]
BUILD=Path('C:/ValMAXBuild/build/media-service')
QT=Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
CORE=Path('C:/ValMAXBuild/target-media-staging/release/deps/valmax_core.dll')
CORE_HASH='6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54'
FLOW=ROOT/'native/tests/media/flow/proof.py'
spec=importlib.util.spec_from_file_location('owned_service_process',FLOW)
flow=importlib.util.module_from_spec(spec);spec.loader.exec_module(flow)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(value,message):
    if not value:raise RuntimeError(message)
def main():
    deps=('protocol','scheduler','transport','result_cache','controller','thumbnail','decode_queue','preview_provider','engine_context','staging_client','service_host')
    files=[ROOT/'native/media'/f'{name}.{ext}' for name in deps for ext in ('cpp','h')]
    files += [ROOT/'native/media/service_generation.h', ROOT/'native/media/service_host.cmake', ROOT/'native/core/valmax_core.h', ROOT/'native/media/html_entities.inc',Path(__file__),Path(__file__).with_name('service_test.cpp'),Path(__file__).with_name('CMakeLists.txt')]
    before={str(p.relative_to(ROOT)):sha(p) for p in files}
    frozen={str(p):sha(p) for p in map(Path,('C:/ValMAXBuild/target/release/valmax_core.dll','C:/ValMAXBuild/target/release/deps/valmax_core.dll','C:/ValMAXBuild/build/all-feature-inputs-core-startup/valmax_core.dll','C:/ValMAXBuild/build/all-feature-inputs/valmax_core.dll'))}
    require(sha(CORE)==CORE_HASH,'frozen staging core mismatch')
    exe=BUILD/'valmax_media_service_test.exe'
    require(exe.stat().st_mtime_ns>=max(p.stat().st_mtime_ns for p in files if p.suffix in ('.cpp','.h')),'rebuild required')
    fixture=Path('C:/ValMAXBuild/build/media-service-fixtures')/uuid.uuid4().hex
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
    output=BUILD/'last-run.json';output.write_text(json.dumps({'fixture':str(fixture),'process':process,'stdout':stdout,'stderr':stderr},indent=2),encoding='utf8')
    require(process['failure'] is None and not process['timedOut'] and process['exitCode']==0,f'service test failed; {output}')
    result=json.loads(stdout.strip().splitlines()[-1]);require(result['passed'] and result['allThreadsDrained'],'test failed')
    require(before=={str(p.relative_to(ROOT)):sha(p) for p in files},'sources changed during run')
    require(frozen=={p:sha(p) for p in frozen},'older core changed')
    require(sha(CORE)==CORE_HASH,'staging core changed')
    proof={'schema':1,'ok':True,'scope':'process-wide native media service with fake HTTP, genuine Qt codecs/engine, real frozen Rust staging','result':result,'sourceSha256':before,'core':{'path':str(CORE),'sha256':CORE_HASH},'qt':copied,'testExecutable':{'path':str(exe),'sha256':sha(exe)},'fixture':str(fixture),'process':process,'frozenDllsUnchanged':frozen,'stdoutSha256':sha(fixture/'stdout.log'),'stderrSha256':sha(fixture/'stderr.log'),'limitations':['No live HTTP or real MAX presenter dispatch in this fixture','Caller-owned event loop and verified DLL must outlive asynchronous drain','OS/PAC lookup and filesystem syscalls are not hard-time-bounded; no GUI wait/join','Trusted callback may retain image copies outside the two queue credits','No installer or installed client changes']}
    report=ROOT/'analysis/native-migration-v1/media-service-proof.json';report.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8');print(json.dumps({'ok':True,'checks':result['checks'],'report':str(report)}))
if __name__=='__main__':main()
