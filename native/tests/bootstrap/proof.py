"""Owned QGenericPlugin ordering/failure fixtures, never MAX or real profiles."""
from __future__ import annotations
import ctypes as c
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import pefile

ROOT=Path(__file__).resolve().parents[3]
BUILD=Path('C:/ValMAXBuild/build/bootstrap')
MAX_QT=Path('C:/ValMAXBuild/build/max-engine-probes/probe-2335f7ff38f4437d8a23497fc19f704d/runtime')
JOB_SOURCE=ROOT/'native/tests/qt/run_webview_blank_native.py'
# Reuse only reviewed Win32 ctypes declarations. Import does not run its main,
# load a browser, create profiles or start any process.
spec=importlib.util.spec_from_file_location('owned_job_api',JOB_SOURCE)
jobapi=importlib.util.module_from_spec(spec);spec.loader.exec_module(jobapi)
w=jobapi.w

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def copy_qt(fixture):
    pins={'Qt6Core.dll':'22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa',
          'Qt6Gui.dll':'19265a4e76efc12ef5f5bcf59be6c6a5b9b59ec4c89660797738bd2da7f719cc'}
    for name,digest in pins.items():
        if sha(MAX_QT/name)!=digest:raise RuntimeError('immutable_qt_changed')
    pending=[MAX_QT/name for name in pins]+[MAX_QT/'plugins/platforms/qwindows.dll']
    copied={}
    while pending:
        source=pending.pop()
        relative=source.relative_to(MAX_QT)
        if str(relative) in copied:continue
        target=fixture/relative;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target);copied[str(relative)]=sha(target)
        pe=pefile.PE(str(source),fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        for item in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]):
            dependency=MAX_QT/item.dll.decode()
            if dependency.is_file():pending.append(dependency)
        pe.close()
    # Copy only existing dependencies reachable from Qt Core/Gui/qwindows; no
    # MAX executable, network module, user files or browser is copied.
    if any(Path(name).name.lower() in ('max.exe','core.dll','network.dll','notifications.dll') for name in copied):
        raise RuntimeError('unexpected_max_dependency')
    return copied

def run_case(fixture,mode):
    case=fixture/('case-'+mode);case.mkdir()
    for name in ('home','local','roaming','temp'):(case/name).mkdir()
    key='valmax_synthetic_startup_v1'
    target=fixture/'plugins/generic/valmaxbootstrapplugin.dll'
    if target.exists():
        if target.parent.resolve()!=fixture/'plugins/generic':raise RuntimeError('target_redirected')
        target.unlink() # one owned copied entry, never source/runtime DLL
    if mode=='invalid-image':target.write_bytes(b'MZsynthetic-invalid-plugin-no-executable-code')
    elif mode!='missing':shutil.copy2(BUILD/'valmaxbootstrapplugin.dll',target)
    if mode in ('reject','hang'):key+=':'+mode
    if mode=='wrong-key':key='valmax_synthetic_missing_key'
    environment={name:os.environ[name] for name in ('SystemRoot','WINDIR','SystemDrive','COMSPEC','PATHEXT') if name in os.environ}
    environment.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32'),
        QT_QPA_PLATFORM='windows',QT_QPA_GENERIC_PLUGINS=key,
        APPDATA=str(case/'roaming'),LOCALAPPDATA=str(case/'local'),USERPROFILE=str(case/'home'),
        TEMP=str(case/'temp'),TMP=str(case/'temp'))
    block=c.create_unicode_buffer('\0'.join(f'{k}={v}' for k,v in sorted(environment.items(),key=lambda x:x[0].upper()))+'\0\0')
    executable=fixture/'valmax_bootstrap_harness.exe'
    job=None;process=jobapi.PROCESS();handles=[];assigned=False;remaining=None;timed_out=False;failure=None
    code=w.DWORD(259);start=time.monotonic();assigned_count=0
    error_mode=jobapi.bind('SetErrorMode',w.UINT,[w.UINT])
    old_mode=error_mode(0x8003)
    try:
        job=jobapi.create_job(None,None);jobapi.require(job,'create_job')
        limits=jobapi.LIMITS();limits.basic.flags=0x2008;limits.basic.processes=1
        jobapi.require(jobapi.set_job(job,9,c.byref(limits),c.sizeof(limits)),'set_job')
        security=jobapi.SA(c.sizeof(jobapi.SA),None,True)
        for path in ('NUL',str(case/'stdout.log'),str(case/'stderr.log')):
            handle=jobapi.create_file(path,0x80000000 if path=='NUL' else 0x40000000,1,c.byref(security),3 if path=='NUL' else 2,0x80,None)
            jobapi.require(handle and handle!=c.c_void_p(-1).value,'output_handle');handles.append(handle)
        startup=jobapi.STARTUP();startup.cb=c.sizeof(startup);startup.flags=0x101;startup.show=0
        startup.stdin,startup.stdout,startup.stderr=handles
        command=c.create_unicode_buffer('"'+str(executable)+'"')
        jobapi.require(jobapi.create_process(str(executable),command,None,None,True,0x08000404,block,str(fixture),c.byref(startup),c.byref(process)),'create_owned_suspended')
        actual=c.create_unicode_buffer(32768);length=w.DWORD(32768)
        jobapi.require(jobapi.query_path(process.process,0,actual,c.byref(length)),'owned_path')
        if os.path.normcase(actual.value)!=os.path.normcase(str(executable)):raise RuntimeError('owned_path_mismatch')
        jobapi.require(jobapi.assign(job,process.process),'job_before_resume');assigned=True
        jobapi.require(jobapi.resume(process.thread)!=0xffffffff,'resume')
        wait_result=jobapi.wait(process.process,1500 if mode=='hang' else 8000)
        if wait_result not in (0,258):raise RuntimeError('wait_failed')
        timed_out=wait_result==258
        jobapi.require(jobapi.exit_code(process.process,c.byref(code)),'exit_code')
    except Exception as error:failure=str(error)
    finally:
        if job:
            jobapi.terminate_job(job,91)
            for _ in range(50):
                pids=jobapi.PIDS()
                if jobapi.query_job(job,3,c.byref(pids),c.sizeof(pids),None):
                    remaining=pids.listed;assigned_count=max(assigned_count,pids.assigned)
                    if remaining==0:break
                time.sleep(.02)
        if process.process and not assigned:
            terminate=jobapi.bind('TerminateProcess',w.BOOL,[w.HANDLE,w.UINT]);terminate(process.process,91)
        for handle in (process.thread,process.process,*handles,job):
            if handle:jobapi.close(handle)
        error_mode(old_mode)
    markers=[json.loads(line) for line in (case/'stdout.log').read_text().splitlines()]
    names=[item['marker'] for item in markers]
    expected_prefix=['executable_static_constructor','main_entered','before_gui_constructor','qt_core_startup_routine']
    order=[names.index(name) for name in expected_prefix]
    checks=[failure is None,assigned,remaining==0,order==sorted(order),len({item['thread'] for item in markers})==1,
        all(a['tick']<=b['tick'] for a,b in zip(markers,markers[1:])),not any(name in names for name in ('qt_fatal','platform_preflight_failed'))]
    if mode=='hang':checks += [timed_out,code.value==259,'generic_hang_entered' in names,'gui_constructor_returned' not in names]
    else:
        checks += [not timed_out,code.value==0,'gui_constructor_returned' in names,'synthetic_client_continuation' in names,'gui_destructor_returned' in names]
        if mode=='good':checks += ['synthetic_ack_only' in names,names.index('qt_core_startup_routine')<names.index('generic_create_entered')<names.index('synthetic_ack_only')<names.index('gui_constructor_returned'),names.index('plugin_object_destroyed')<names.index('gui_destructor_returned')]
        else:
            checks += ['synthetic_ack_only' not in names,'qt_warning' in names]
            checks += [('generic_create_rejected' in names) if mode=='reject' else ('generic_create_entered' not in names)]
    return {'mode':mode,'ok':all(checks),'checks':len(checks),'markers':markers,'elapsedSeconds':round(time.monotonic()-start,3),
        'exitCodeBeforeCleanup':code.value,'timeoutExpected':mode=='hang','timedOut':timed_out,'jobAssignedBeforeResume':assigned,
        'activeProcessLimit':1,'jobProcessesAfterCleanup':remaining,'failure':failure,
        'unprotectedSyntheticContinuation':mode not in ('good','hang') and 'synthetic_client_continuation' in names,
        'caseDirectory':str(case),'stderrSha256':sha(case/'stderr.log')}

def main():
    fixture=Path('C:/ValMAXBuild/build/bootstrap-fixtures')/('run-'+uuid.uuid4().hex)
    fixture.mkdir(parents=True);(fixture/'plugins/generic').mkdir(parents=True)
    qt=copy_qt(fixture);shutil.copy2(BUILD/'valmax_bootstrap_harness.exe',fixture/'valmax_bootstrap_harness.exe')
    results=[run_case(fixture,mode) for mode in ('good','missing','invalid-image','wrong-key','reject','hang')]
    if any(sha(fixture/name)!=digest for name,digest in qt.items()):raise RuntimeError('qt_copy_changed')
    sources=[Path(__file__),JOB_SOURCE,*Path(__file__).parent.glob('*.cpp'),Path(__file__).with_name('markers.h'),Path(__file__).with_name('plugin.json'),Path(__file__).with_name('CMakeLists.txt')]
    report={'schema':1,'ok':all(result['ok'] for result in results),'scope':'synthetic public Qt generic-plugin loading/order only',
        'qtVersion':'6.11.2','qtSourceRoot':str(MAX_QT),'qtCopiedFileSha256':qt,'fixture':str(fixture),
        'sources':{str(path.relative_to(ROOT)):sha(path) for path in sources},
        'artifacts':{name:sha(BUILD/name) for name in ('valmax_bootstrap_harness.exe','valmaxbootstrapplugin.dll')},
        'cases':results,'checks':sum(result['checks'] for result in results),
        'observations':['Executable static constructor and Qt core startup routine both precede generic plugin create',
            'Good plugin create and synthetic ACK occur synchronously on the main thread before QGuiApplication constructor returns',
            'Missing/invalid/wrong-key/rejected plugin allows the synthetic client continuation without ACK',
            'A hung plugin prevents constructor return; only the external owned Job deadline stops it',
            'Good plugin object is destroyed by QGuiApplication teardown; process-lifetime ownership still needs explicit design'],
        'limits':['No MAX executable, native protection hooks, Rust Runtime, real accounts/profile or retention are used',
            'Synthetic ACK is only a marker and is never a protection or pre-login claim',
            'Public Qt loading order does not establish MAX code/static-constructor behavior before QGuiApplication',
            'No visible window is created; valid qwindows is preflighted and inherited error mode suppresses loader fault dialogs; not a universal guarantee for arbitrary Qt/plugin failures',
            'No event loop is entered, no browser/network code is invoked; OS-level network absence is not independently proven',
            'Owned child environment is isolated, but this is not an OS filesystem sandbox or a complete production DLL closure proof'],
        'productionEntryChanged':False,'maxStarted':False,'userInstallationChanged':False}
    output=ROOT/'analysis/native-migration-v1/bootstrap-generic-plugin-proof.json'
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':report['ok'],'checks':report['checks'],'cases':[{k:r[k] for k in ('mode','ok','timedOut','unprotectedSyntheticContinuation')} for r in results],'proof':str(output)}))
    return 0 if report['ok'] else 1

if __name__=='__main__':raise SystemExit(main())
