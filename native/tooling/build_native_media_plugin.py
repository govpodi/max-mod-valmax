"""Build a fresh combined native media plugin with immutable core/hook inputs."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[2]
BUILD=Path('C:/ValMAXBuild/build/qt-all-features-native-media')
CORE=Path('C:/ValMAXBuild/build/all-feature-inputs-6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54')
HOOKS=Path('C:/ValMAXBuild/build/hooks-all-features')
REPORT=ROOT/'analysis/native-migration-v1/qt-all-features-native-media-build.json'
TARGETS=('valmaxnativeplugin','valmax_all_features_test','valmax_qml_bridge_test','valmax_privacy_host_test','valmax_privacy_delivery_test','valmax_protection_delivery_test','valmax_ghost_host_test','valmax_notification_host_test')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def require(ok,why):
    if not ok:raise RuntimeError(why)
def main():
    require(not REPORT.exists(),'new build report already exists')
    pair=json.loads((CORE/'core-pair-proof.json').read_text(encoding='utf8'))
    require(pair['verified'] and pair['sourceSnapshotMatches'] and pair['importLibrarySymbolsMatch'],'unverified immutable core pair')
    require(sha(CORE/'valmax_core.dll')==pair['hashes']['dll']=='6d51da3b847411e72f38b101c78e40573342e636cfdbadb3594aa9957c689b54','wrong core')
    require(sha(CORE/'valmax_core.dll.lib')==pair['hashes']['importLibrary'],'wrong core importlib')
    previous=json.loads((ROOT/'analysis/native-migration-v1/qt-all-features-media-engine-build.json').read_text(encoding='utf8'))
    inputs={p:digest for p,digest in previous['artifacts'].items() if Path(p).parent.resolve() in (CORE.resolve(),HOOKS.resolve())}
    for path,digest in inputs.items():require(sha(path)==digest,'frozen input changed: '+Path(path).name)
    files=set()
    for folder in ('native/qt','native/media'):
        directory=ROOT/folder
        for pattern in ('*.cpp','*.h','*.qml','*.cmake','*.inc'):files.update(directory.glob(pattern))
        files.add(directory/'CMakeLists.txt')
    files.update(ROOT/'native/tests/qt'/name for name in ('all_features_harness.cpp','all_features.qml','bridge_harness.cpp','settings_unavailable.qml','privacy_host_test.cpp','privacy_delivery_test.cpp','protection_delivery_test.cpp','ghost_host_test.cpp','notification_host_test.cpp','run_native_media_plugin.py','run_webview_blank_native.py'))
    files.update(ROOT/path for path in ('native/tests/media/flow/proof.py','native/tooling/build_native_qml.py','native/tooling/native_env.ps1','native/tooling/msvc_env.ps1','native/core/valmax_core.h','native/hooks/privacy_api.h','native/hooks/ghost.h','native/hooks/notification_api.h','native/qt/qmldir'))
    files.add(Path(__file__))
    before={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in sorted(files)}
    BUILD.mkdir(parents=True,exist_ok=True)
    (BUILD/'source-before.json').write_text(json.dumps(before,indent=2),encoding='utf8')
    compiler=Path(shutil.which('cl') or '')
    require(compiler.is_file(),'invoke inside native_env.ps1 environment')
    command=['cmake','-S',str(ROOT/'native/qt'),'-B',str(BUILD),'-G','Ninja','-DCMAKE_BUILD_TYPE=Release','-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL','-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt','-DVALMAX_REQUIRE_ALL_FEATURES=ON',
             '-DVALMAX_CORE_LIBRARY='+str(CORE/'valmax_core.dll.lib'),'-DVALMAX_PRIVACY_LIBRARY='+str(HOOKS/'valmax_privacy.lib'),'-DVALMAX_GHOST_LIBRARY='+str(HOOKS/'valmax_ghost.lib'),'-DVALMAX_NOTIFICATION_LIBRARY='+str(HOOKS/'valmax_notifications.lib')]
    compile_command=['cmake','--build',str(BUILD),'--target',*TARGETS,'-j','4']
    for name,args in (('configure',command),('build',compile_command)):
        result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=1200,creationflags=subprocess.CREATE_NO_WINDOW)
        (BUILD/(name+'.log')).write_text(result.stdout,encoding='utf8')
        if result.returncode:print(result.stdout[-10000:]);raise RuntimeError(name+' failed')
    require(before=={str(p.relative_to(ROOT)).replace('\\','/'):sha(p) for p in sorted(files)},'source changed during build')
    for path,digest in inputs.items():require(sha(path)==digest,'frozen input changed during build')
    artifacts=dict(inputs)
    for target in TARGETS:
        path=BUILD/'qml/ValMAX/Native/valmaxnativeplugin.dll' if target=='valmaxnativeplugin' else BUILD/(target+'.exe')
        artifacts[str(path)]=sha(path)
    for name in ('qmldir','SettingsTransport.qml','ChatLockTransport.qml','SupportTransport.qml'):
        path=BUILD/'qml/ValMAX/Native'/name;artifacts[str(path)]=sha(path)
    glue={str(BUILD/name):sha(BUILD/name) for name in ('CMakeCache.txt','build.ninja','configure.log','build.log')}
    proof={'schema':1,'compiled':True,'allFeatures':['core','ghost','notifications','privacy','media'],'mediaEngineContextLinked':True,'mediaServiceLinked':True,'runtimeRoot':previous['runtimeRoot'],'hookBuild':str(HOOKS),'qtBuild':str(BUILD),'artifacts':artifacts,'sourceHashes':before,'buildGlue':glue,'compiler':{'path':str(compiler),'sha256':sha(compiler)},'configureCommand':command,'buildCommand':compile_command,'scope':'Combined native media service/adapter runtime with frozen Rust core and existing pinned hooks; no installed runtime launch, early gate or retention.'}
    REPORT.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'report':str(REPORT),'pluginSha256':artifacts[str(BUILD/'qml/ValMAX/Native/valmaxnativeplugin.dll')]}))
if __name__=='__main__':main()
