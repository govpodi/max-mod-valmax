"""Exact Qt GUI dispatch against owned mocks; no MAX, network or real draft."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import shutil
import uuid

ROOT=Path(__file__).resolve().parents[4]
BUILD=Path('C:/ValMAXBuild/build/media-draft-dispatcher')
QT=Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
RUNNER=ROOT/'native/tests/media/flow/proof.py'
spec=importlib.util.spec_from_file_location('owned_dispatch_job',RUNNER)
runner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
require=runner.require
sha=runner.sha

def main():
    media=ROOT/'native/media'
    files=[media/name for name in ('draft_dispatcher.cpp','draft_dispatcher.h','draft_dispatcher.cmake',
        'service_generation.h','engine_context.cpp','engine_context.h','engine_context.cmake',
        'preview_provider.cpp','preview_provider.h','preview_provider.cmake','protocol.h','thumbnail.h','CMakeLists.txt')]
    files += [Path(__file__).with_name('dispatcher_test.cpp'),Path(__file__).with_name('CMakeLists.txt'),
        Path(__file__),RUNNER,runner.JOB_SOURCE,ROOT/'docs/native-media-draft-contract.md']
    exe=BUILD/'valmax_draft_dispatcher_test.exe'
    require(exe.is_file() and exe.stat().st_mtime_ns>=max(p.stat().st_mtime_ns for p in files if p.suffix in ('.cpp','.h')), 'rebuild_required')
    before={str(p.relative_to(ROOT)):sha(p) for p in files}
    fixture=Path('C:/ValMAXBuild/fixtures/media-draft-dispatcher')/uuid.uuid4().hex
    fixture.mkdir(parents=True)
    for source in QT.glob('*.dll'):
        if source.name.lower().startswith(('qt6','msvcp','vcruntime','concrt','api-ms-','ucrt')):
            os.link(source,fixture/source.name)
    for directory in ('plugins','qml'):
        shutil.copytree(QT/directory,fixture/directory,copy_function=os.link,
            ignore=lambda path,names:[n for n in names if n=='ValMAX'])
    shutil.copy2(QT/'qt.conf',fixture/'qt.conf')
    pins={'Qt6Core.dll':'22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa',
        'Qt6Gui.dll':'19265a4e76efc12ef5f5bcf59be6c6a5b9b59ec4c89660797738bd2da7f719cc',
        'plugins/platforms/qwindows.dll':'d94c584a18829d11fbf3aefd07e7b1e189e0cf063a0515826960037efa7465df'}
    for name,digest in pins.items():require(sha(fixture/name)==digest,'pinned_qt_changed')
    require(not any(p.name.lower() in ('max.exe','core.dll','valmax_core.dll','network.dll','notifications.dll')
        for p in fixture.rglob('*')),'unexpected_client_binary')
    shutil.copy2(exe,fixture/exe.name)
    for directory in ('home','local','roaming','temp'):(fixture/directory).mkdir()
    env={key:os.environ[key] for key in ('SystemRoot','WINDIR','SystemDrive','COMSPEC','PATHEXT') if key in os.environ}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'),
        QT_QPA_PLATFORM='windows',QT_QUICK_BACKEND='software',QT_DEBUG_PLUGINS='1',QML_DISABLE_DISK_CACHE='1',
        QML_IMPORT_PATH=str(fixture/'qml'),QML2_IMPORT_PATH=str(fixture/'qml'),
        USERPROFILE=str(fixture/'home'),LOCALAPPDATA=str(fixture/'local'),APPDATA=str(fixture/'roaming'),
        TEMP=str(fixture/'temp'),TMP=str(fixture/'temp'))
    process=runner.run(fixture,env,exe.name)
    stdout=(fixture/'stdout.log').read_text(encoding='utf8')
    stderr=(fixture/'stderr.log').read_text(encoding='utf8')
    require(process['failure'] is None and not process['timedOut'] and process['exitCode']==0,
        f'dispatch_failed: {process}\n{stdout}\n{stderr[-5000:]}')
    require(process['jobAssignedBeforeResume'] and process['jobProcessesAfterCleanup']==0,'job_cleanup_failed')
    native=json.loads(stdout)
    require(native['passed'] and native['qt']=='6.11.2','native_result_invalid')
    require(native['retainedFileUnchanged'] and not native['actualMaxInvoked'] and not native['attachmentAcceptanceProven'],'scope_mismatch')
    for name,path in native['loadedModules'].items():
        require(Path(path).resolve()==(fixture/name).resolve(),'loaded_path_mismatch')
        require(sha(Path(path))==sha(QT/name),'loaded_qt_mismatch')
    require(before=={str(p.relative_to(ROOT)):sha(p) for p in files},'source_changed_during_proof')
    report=dict(schema=1,verified=True,sources=before,fixture=str(fixture),qtSourceRoot=str(QT),
        native=native,process=process,executableSha256=sha(exe),platformPins=pins,
        runtimeModules={name:sha(fixture/name) for name in native['loadedModules']},
        libraries={str(p.relative_to(BUILD)):sha(p) for p in [BUILD/'valmax_media_draft_dispatcher.lib',
            BUILD/'media/valmax_media_engine.lib',BUILD/'media/valmax_media_preview.lib']},
        retainedSyntheticFileSha256=sha(fixture/'owned-dispatch-fixture.png'),stderrSha256=sha(fixture/'stderr.log'),
        installedClientModified=False,productionPluginWired=False,networkUsed=False,messagesSent=False,
        coverage=[
            'Real QMetaMethod DirectConnection receives exactly one owned fixture URL on GUI thread',
            'Strict public void QList<QUrl> method/slot; private/protected/signal/wrong return/argument rejected',
            'Exact qsizetype attachmentsCount; no double/string/int32/dynamic property coercion; negative invalid and count>=10 limit',
            'Engine lease and search capability identity/generation; old search, revoked context and other engine rejected',
            'Wrong thread rejects before binding reflection; sender/upload/model/context destruction is finite',
            'Reentrant count getter can revoke generation, detach/delete engine/model/sender, or delete caller argument containers',
            'Reentrant upload can delete composer/context/arguments; successful call remains Dispatched without later QObject dereference',
            'Local absolute bounded URL only; every outcome leaves the synthetic caller-retained file unchanged'],
        limitations=[
            'Exact Qt 6.11.2 with synthetic QObject mocks; no installed MAX or actual draft attachment invoked',
            'File is caller-retained synthetic input; Rust staging and service/GUI queue integration are tested separately',
            'Dispatched means method invoked, never attachment acceptance or sending',
            'Trusted host supplies and refreshes object binding, generation identity and already-retained file; QML cannot choose these',
            'Host must provide one selected-operation delivery; helper has no process-wide replay ledger or selected-operation queue',
            'Properties are read on GUI thread, then scope/lifetimes rechecked; no filesystem check or row rollback is performed',
            'Fixture Job limits child processes and guarantees cleanup; not an OS filesystem/network sandbox'])
    output=ROOT/'analysis/native-migration-v1/media-draft-dispatcher-proof.json'
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(dict(verified=True,checks=native['checks'],proof=str(output),fixture=str(fixture))))

if __name__=='__main__':main()
