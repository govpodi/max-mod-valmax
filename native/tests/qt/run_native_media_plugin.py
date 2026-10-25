"""Combined production-plugin proof in owned MAX-Qt fixture; no MAX process."""
from __future__ import annotations
import ctypes as c
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import pefile

ROOT=Path(__file__).resolve().parents[3]
BUILD_PROOF=ROOT/'analysis/native-migration-v1/qt-all-features-native-media-build.json'
REPORT=ROOT/'analysis/native-migration-v1/qt-all-features-native-media-proof.json'
QT=Path('C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb')
spec=importlib.util.spec_from_file_location('owned_native_media_job',Path(__file__).with_name('run_webview_blank_native.py'))
job=importlib.util.module_from_spec(spec);spec.loader.exec_module(job)
w=job.w
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(ok,why):
    if not ok:raise RuntimeError(why)
def run(fixture,exe,arguments,environment,label):
    block=c.create_unicode_buffer('\0'.join(f'{k}={v}' for k,v in sorted(environment.items(),key=lambda item:item[0].upper()))+'\0\0')
    command=c.create_unicode_buffer(subprocess.list2cmdline([str(exe),*map(str,arguments)]))
    process=job.PROCESS();handle=None;outputs=[];assigned=False;remaining=None;timed=False;failure=None;code=w.DWORD(259);started=time.monotonic()
    try:
        handle=job.create_job(None,None);job.require(handle,'job_create')
        limits=job.LIMITS();limits.basic.flags=0x2008;limits.basic.processes=1;job.require(job.set_job(handle,9,c.byref(limits),c.sizeof(limits)),'job_limit')
        security=job.SA(c.sizeof(job.SA),None,True)
        for path in ('NUL',str(fixture/(label+'.stdout.log')),str(fixture/(label+'.stderr.log'))):
            output=job.create_file(path,0x80000000 if path=='NUL' else 0x40000000,1,c.byref(security),3 if path=='NUL' else 2,0x80,None)
            job.require(output and output!=c.c_void_p(-1).value,'output_handle');outputs.append(output)
        startup=job.STARTUP();startup.cb=c.sizeof(startup);startup.flags=0x101;startup.show=0;startup.stdin,startup.stdout,startup.stderr=outputs
        job.require(job.create_process(str(exe),command,None,None,True,0x08000404,block,str(fixture),c.byref(startup),c.byref(process)),'suspended_create')
        actual=c.create_unicode_buffer(32768);length=w.DWORD(32768);job.require(job.query_path(process.process,0,actual,c.byref(length)),'owned_path')
        require(Path(actual.value).resolve()==exe.resolve(),'unexpected_executable')
        job.require(job.assign(handle,process.process),'assign_before_resume');assigned=True
        job.require(job.resume(process.thread)!=0xffffffff,'resume')
        waited=job.wait(process.process,30000);require(waited in (0,258),'wait_error');timed=waited==258
        job.require(job.exit_code(process.process,c.byref(code)),'exit_code')
    except Exception as error:failure=str(error)
    finally:
        if handle:
            job.terminate_job(handle,91)
            for _ in range(50):
                pids=job.PIDS()
                if job.query_job(handle,3,c.byref(pids),c.sizeof(pids),None):
                    remaining=pids.listed
                    if remaining==0:break
                time.sleep(.02)
        if process.process and not assigned:job.bind('TerminateProcess',w.BOOL,[w.HANDLE,w.UINT])(process.process,91)
        for item in (process.thread,process.process,*outputs,handle):
            if item:job.close(item)
    proof={'exitCode':code.value,'failure':failure,'timedOut':timed,'assignedBeforeResume':assigned,'activeProcessLimit':1,'remainingProcesses':remaining,'elapsedSeconds':round(time.monotonic()-started,3)}
    (fixture/(label+'.process.json')).write_text(json.dumps(proof,indent=2),encoding='utf8')
    require(failure is None and not timed and code.value==0 and remaining==0,'test_failed: '+label+'; '+str(fixture/(label+'.stderr.log')))
    result=json.loads((fixture/(label+'.stdout.log')).read_text('utf8'))
    require(result.get('ok') is True,'missing success: '+label)
    return {'result':result,'process':proof,'stdoutSha256':sha(fixture/(label+'.stdout.log')),'stderrSha256':sha(fixture/(label+'.stderr.log'))}

def closure(entries,fixture):
    system=Path(os.environ['SystemRoot'])/'System32';seen={};pending=list(entries)
    while pending:
        path=pending.pop().resolve()
        if str(path).lower() in seen:continue
        binary=pefile.PE(str(path));imports=[]
        for directory in ('DIRECTORY_ENTRY_IMPORT','DIRECTORY_ENTRY_DELAY_IMPORT'):
            for entry in getattr(binary,directory,[]):
                name=entry.dll.decode('ascii').lower()
                require(not name.startswith(('python','frida-agent')),'unexpected runtime dependency: '+name)
                if name.startswith(('api-ms-','ext-ms-')):
                    imports.append({'name':name,'source':'Windows API set'});continue
                candidates=(path.parent/name,fixture/name,system/name)
                found=next((candidate for candidate in candidates if candidate.is_file()),None)
                require(found is not None,'unresolved dependency: '+name)
                item={'name':name,'path':str(found.resolve()),'sha256':sha(found),'source':'system' if found.parent.resolve()==system.resolve() else 'fixture'}
                imports.append(item)
                # System DLLs/ApiSet forwarding are the Windows platform boundary.
                if item['source']=='fixture':pending.append(found)
        seen[str(path).lower()]={'path':str(path),'sha256':sha(path),'imports':imports}
    return list(seen.values())

def main():
    require(not REPORT.exists(),'new output proof already exists')
    c.windll.kernel32.SetErrorMode(0x8003)
    build=json.loads(BUILD_PROOF.read_text(encoding='utf8'));directory=Path(build['qtBuild']);hooks=Path(build['hookBuild'])
    for path,digest in build['sourceHashes'].items():require(sha(ROOT/path)==digest,'source changed: '+path)
    for path,digest in build['artifacts'].items():require(sha(path)==digest,'artifact changed: '+Path(path).name)
    core=next(Path(path) for path in build['artifacts'] if Path(path).name=='valmax_core.dll')
    require(sha(core)=='6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54','wrong core')
    fixture=Path('C:/ValMAXBuild/build')/('qt-all-features-native-media-fixture-'+uuid.uuid4().hex);fixture.mkdir()
    for source in QT.glob('*.dll'):
        if source.name.lower().startswith(('qt6','msvcp','vcruntime','concrt','api-ms-','ucrt')):os.link(source,fixture/source.name)
    for name in ('plugins','qml'):shutil.copytree(QT/name,fixture/name,copy_function=os.link,ignore=lambda path,names:[n for n in names if n=='ValMAX'])
    shutil.copy2(QT/'qt.conf',fixture/'qt.conf')
    module=fixture/'qml/ValMAX/Native';module.mkdir(parents=True)
    for name in ('qmldir','SettingsTransport.qml','ChatLockTransport.qml','SupportTransport.qml','valmaxnativeplugin.dll'):shutil.copy2(directory/'qml/ValMAX/Native'/name,module/name)
    shutil.copy2(core,fixture/core.name)
    for name in ('valmax_ghost.dll','valmax_notifications.dll','valmax_privacy.dll'):shutil.copy2(hooks/name,fixture/name)
    for source in hooks.glob('valmax-private-notification-*.png'):shutil.copy2(source,fixture/source.name)
    for source in directory.glob('*.exe'):shutil.copy2(source,fixture/source.name)
    generator=json.loads((ROOT/'analysis/native-migration-v1/qt-generator-proof.json').read_text(encoding='utf8'))
    generated=Path(generator['fixture'])/'native/BaseSendPanel.qml'
    native_proof=json.loads(generated.with_name('native-qml-proof.json').read_text(encoding='utf8'))
    require(sha(generated)==native_proof['sourceHashes'][':/qt/qml/OneMeUi/BaseSendPanel.qml'],'generated composer changed')
    declaration=(ROOT/'native/qt/InlineMediaComposer.qml').read_text(encoding='utf8').replace('\r\n','\n')
    require(generated.read_text(encoding='utf8').count(declaration)==1,'generated declaration mismatch')
    wrapper='''import QtQuick
import ValMAX.Native 1.0 as ValMAXNative
Item {
 id: root
 property bool textInputVisible: true
 property var modQuickReplySelected: null
 function setText(value, entities) { chatInput.text = value; }
 Item { id: chatInput; objectName: "synthetic-chat-input"; property string text: "@pic fixture"; function moveCursorToEnd() {} }
'''+declaration+'\n Component.onDestruction: modInlineReset(false)\n}\n'
    (fixture/'inline_native_fragment.qml').write_text(wrapper,encoding='utf8')
    for name in ('home','local','roaming','temp','synthetic-profile','synthetic-runtime'):(fixture/name).mkdir()
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','SystemDrive','COMSPEC','PATHEXT') if k in os.environ}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'),QT_QPA_PLATFORM='windows',QT_QUICK_BACKEND='software',QT_DEBUG_PLUGINS='1',QML_DISABLE_DISK_CACHE='1',QML_IMPORT_PATH=str(fixture/'qml'),QML2_IMPORT_PATH=str(fixture/'qml'),USERPROFILE=str(fixture/'home'),LOCALAPPDATA=str(fixture/'local'),APPDATA=str(fixture/'roaming'),TEMP=str(fixture/'temp'),TMP=str(fixture/'temp'))
    dependencies=closure([module/'valmaxnativeplugin.dll',fixture/'plugins/platforms/qwindows.dll',fixture/'qml/QtQuick/qtquick2plugin.dll'],fixture)
    pe=pefile.PE(str(module/'valmaxnativeplugin.dll'))
    imports={entry.dll.decode('ascii').lower() for entry in pe.DIRECTORY_ENTRY_IMPORT}
    require({'valmax_core.dll','valmax_ghost.dll','valmax_notifications.dll','valmax_privacy.dll','qt6network.dll'}<=imports,'partial combined plugin')
    exported={item.name.decode('ascii') for item in pefile.PE(str(fixture/'valmax_core.dll')).DIRECTORY_ENTRY_EXPORT.symbols if item.name}
    requested={item.name.decode('ascii') for entry in pe.DIRECTORY_ENTRY_IMPORT if entry.dll.lower()==b'valmax_core.dll' for item in entry.imports if item.name}
    require(requested<=exported,'missing Rust export')
    results={}
    for name in ('valmax_privacy_host_test','valmax_privacy_delivery_test','valmax_protection_delivery_test','valmax_ghost_host_test','valmax_notification_host_test'):
        args=[fixture/(name+'-synthetic')] if name in ('valmax_ghost_host_test','valmax_notification_host_test') else []
        results[name]=run(fixture,fixture/(name+'.exe'),args,env,name)
    shutil.copy2(Path(__file__).with_name('all_features.qml'),fixture/'all_features.qml')
    config=json.dumps({'profileDirectory':str(fixture/'synthetic-profile'),'runtimeDirectory':str(fixture/'synthetic-runtime'),'existingClient':False})
    lifecycle=run(fixture,fixture/'valmax_all_features_test.exe',[fixture/'qml',fixture/'all_features.qml'],{**env,'VALMAX_NATIVE_RUNTIME_CONFIG':config},'all_features')
    require(len(lifecycle['result']['cycles'])==3 and lifecycle['result']['mediaEngineChecks']==44 and lifecycle['result']['mediaComposerChecks']==51,'missing combined lifecycle checks')
    for name,path in lifecycle['result']['loadedModules'].items():
        expected=module/name if name=='valmaxnativeplugin.dll' else fixture/name
        require(Path(path).resolve()==expected.resolve() and sha(path)==sha(expected),'loaded native module mismatch')
    negative={}
    for label,value,state in (('unconfigured',None,'unconfigured'),('malformed','{','error')):
        qml=fixture/(label+'.qml');qml.write_text(Path(__file__).with_name('settings_unavailable.qml').read_text().replace('@STATE@',state))
        configenv=dict(env)
        if value is not None:configenv['VALMAX_NATIVE_RUNTIME_CONFIG']=value
        negative[label]=run(fixture,fixture/'valmax_qml_bridge_test.exe',[fixture/'qml',qml],configenv,label)
    for path,digest in build['sourceHashes'].items():require(sha(ROOT/path)==digest,'source changed during proof: '+path)
    for path,digest in build['artifacts'].items():require(sha(path)==digest,'artifact changed during proof')
    fixture_hashes={str(path.relative_to(fixture)):sha(path) for path in fixture.rglob('*') if path.is_file() and path.suffix in ('.dll','.exe','.qml','.conf')}
    proof={'schema':1,'verified':True,'fixture':str(fixture),'buildProof':str(BUILD_PROOF),'buildProofSha256':sha(BUILD_PROOF),'pluginSha256':sha(module/'valmaxnativeplugin.dll'),'allFeatureImports':sorted(imports),'dependencyClosure':dependencies,'closureBoundary':'normal/delay imports of app-local binaries; Windows system DLL/ApiSet forwarding is platform boundary; no Python runtime imports','coreImportSymbols':sorted(requested),'coreSymbolsResolved':True,'hostChecks':results,'lifecycle':lifecycle,'negativeConfiguration':negative,'fixtureFilesSha256':fixture_hashes,'generatedComposer':str(generated),'generatedComposerSha256':sha(generated),'fragmentSha256':sha(ROOT/'native/qt/InlineMediaComposer.qml'),'earlyBootstrapGateVerified':False,'actualMaxStarted':False,'installedClientChanged':False,'retentionBuilt':False,'scope':'Actual registered plugin MediaComposer and generated declaration compile/smoke, combined Rust/hooks/media linkage, unavailable null composer, repeated QQmlEngine lifetime. No real MAX presenter or HTTP search.'}
    REPORT.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    total=sum(value['result']['checks'] for value in results.values())+sum(item['checks'] for item in lifecycle['result']['cycles'])+sum(value['result']['checks'] for value in negative.values())+lifecycle['result']['mediaEngineChecks']+lifecycle['result']['mediaComposerChecks']
    print(json.dumps({'verified':True,'checks':total,'proof':str(REPORT),'pluginSha256':proof['pluginSha256']}))
if __name__=='__main__':main()
