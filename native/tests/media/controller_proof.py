"""Owned fake QNetworkReply integration; no live HTTP, profiles or disk staging."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import pefile

ROOT=Path(__file__).resolve().parents[3]
BUILD=Path('C:/ValMAXBuild/build/media-controller')
QT=Path('C:/ValMAXBuild/build/max-engine-probes/probe-2335f7ff38f4437d8a23497fc19f704d/runtime')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    names=('protocol','scheduler','result_cache','transport','controller')
    compiled=[ROOT/'native/media'/(name+suffix) for name in names for suffix in ('.cpp','.h')]+[Path(__file__).with_name('controller_test.cpp')]
    source_exe=BUILD/'valmax_media_controller_test.exe'
    if any(p.stat().st_mtime_ns>source_exe.stat().st_mtime_ns for p in compiled):raise RuntimeError('stale_test_binary_rebuild_required')
    fixture=Path('C:/ValMAXBuild/build/media-controller-fixtures')/uuid.uuid4().hex;fixture.mkdir(parents=True)
    copied={};pending=[QT/'Qt6Core.dll',QT/'Qt6Network.dll']
    assert sha(QT/'Qt6Core.dll')=='22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa'
    while pending:
        src=pending.pop()
        if src.name in copied:continue
        shutil.copy2(src,fixture/src.name);copied[src.name]=sha(src)
        pe=pefile.PE(str(src),fast_load=True);pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        for item in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[]):
            child=QT/item.dll.decode()
            if child.is_file():pending.append(child)
        pe.close()
    assert not any(n.lower() in ('max.exe','core.dll','network.dll','qt6gui.dll') for n in copied)
    exe=fixture/'controller-test.exe';shutil.copy2(source_exe,exe)
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC') if k in os.environ}
    env['PATH']=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32')
    result=subprocess.run([str(exe)],cwd=fixture,env=env,capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:raise RuntimeError('native_test_failed:'+str(result.returncode)+':'+result.stderr.decode(errors='replace'))
    report=json.loads(result.stdout)
    sources=compiled+[ROOT/'native/media/controller.cmake',Path(__file__)]
    proof={'schema':1,'passed':True,'checks':report['checks'],'fixture':str(fixture),'runtimeFiles':copied,
        'librarySha256':sha(BUILD/'valmax_media_controller.lib'),'testExeSha256':sha(exe),
        'sources':{str(p.relative_to(ROOT)):sha(p) for p in sources},
        'scope':'Native owner-thread controller integration with protocol, scheduler, memory cache and fake-reply transport. No live HTTP, user profiles, disk staging, QML or message sending.',
        'coverage':['explicit bounded JSON search and public rows only','shared2s debounce; malformed requests/empty callback do not consume it','private original and preview callbacks; original serial gate until terminal even aftercancel','cross-account cache isolation, TTL and close/reopen invalidation','newest-search-wins with out-of-order replies; newsearch invalidates selected download without early busy release','transport or scheduler destruction yields async unavailable exactlyonce and revokes private state','callbacks delete controller or throw safely; destructor suppresses callbacks','32scope/66operation bounds,66callback shutdown drain and wrong-thread rejection','completed preview stamp revoked by newer search while page remains current; public rows carry generation stamp'],
        'limitations':['Caller-owned scheduler/transport must be the same shared pair; production controller belongs on a dedicated service thread.',
            'Future decode/staging/UI adapters must recheck the completion stamp (scope+generation) after async work; page-current alone is insufficient. Private returned copies cannot be physically recalled.',
            'No live server, actual TLS, native draft/staging or QML publication proof. Tests use only own in-memory QNetworkReply fixtures.',
            'No assertion of end-user feature completion or startup readiness.']}
    out=ROOT/'analysis/native-migration-v1/media-controller-proof.json';out.write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'checks':report['checks'],'proof':str(out)}))
if __name__=='__main__':main()
