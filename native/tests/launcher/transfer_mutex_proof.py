"""Synthetic named-mutex proof; never acquire the production transfer mutex."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import pefile

ROOT=Path(__file__).resolve().parents[2]
BUILD=Path('C:/ValMAXBuild/build/launcher')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    legacy=ROOT.parent/'modkit/session_transfer.py'
    tree=ast.parse(legacy.read_text(encoding='utf8'))
    function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='transfer_lock')
    calls=[node for node in ast.walk(function) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)]
    create=next(node for node in calls if node.func.attr=='CreateMutexW')
    wait=next(node for node in calls if node.func.attr=='WaitForSingleObject')
    if not (ast.literal_eval(create.args[1]) is False and ast.literal_eval(create.args[2])=='Local\\MAXModSessionTransferV1' and ast.literal_eval(wait.args[1])==0):
        raise AssertionError('Legacy transfer mutex contract changed')
    test=BUILD/'valmax_transfer_mutex_test.exe'
    completed=subprocess.run([str(test)],capture_output=True,text=True,check=True,timeout=30)
    native=json.loads(completed.stdout)
    if not native['ok'] or native['productionMutexAcquired'] or native['ownedChildProcesses']!=3:raise AssertionError('Unexpected test scope')
    pe=pefile.PE(str(test));imports={item.dll.decode():sorted(symbol.name.decode() for symbol in item.imports if symbol.name) for item in pe.DIRECTORY_ENTRY_IMPORT}
    if set(name.lower() for name in imports)!={'kernel32.dll'}:raise AssertionError('Unexpected dependency')
    library=BUILD/'valmax_transfer_mutex.lib'
    sources=[ROOT/'launcher/transfer_mutex.h',ROOT/'launcher/transfer_mutex.cpp',ROOT/'launcher/CMakeLists.txt',Path(__file__),Path(__file__).with_name('transfer_mutex_test.cpp')]
    report={'schema':1,'ok':True,'scope':'standalone C++ RAII transfer mutex; renamed synthetic names and owned children only',
        'productionName':'Local\\MAXModSessionTransferV1','waitMilliseconds':0,'compatiblePythonSourceSha256':sha(legacy),
        'library':{'path':str(library),'sha256':sha(library),'size':library.stat().st_size},
        'test':{'path':str(test),'sha256':sha(test),'native':native,'imports':imports},
        'sourceSha256':{str(path.relative_to(ROOT)):sha(path) for path in sources},
        'confirmed':['Default production name is compile-time fixed; no caller-provided production name',
            'Test-only build has no default constructor and rejects names outside its synthetic prefix',
            'Different guards recurse on the same thread; each releases exactly one Windows acquisition',
            'Wrong-thread release returns WrongThread and leaves ownership intact',
            'Wrong-thread ownership check rejects by immutable thread ID before reading mutable ownership/handles; stress overlaps64 releases with foreign checks',
            'Held thread handle prevents owner thread-ID reuse',
            'Two-process contention fails immediately Busy; normal child exit permits a fresh acquisition',
            'Abandoned ownership is acquired and explicitly flagged; subsequent release clears abandoned state',
            'Wrong-thread destruction invokes std::terminate in an isolated owned child; test-only terminate handler exits86 without CRT/WER dialog',
            'Event/mutex name collision produces finite IoError; repeated acquisition/release does not leak handles'],
        'productionMutexAcquired':False,'realProfilesAccessed':False,'filesystemAccessByMutex':False,'credentialsAccessed':False,'userAppTouched':False,
        'limitations':['WAIT_ABANDONED never permits skipping the independent recovery-marker check',
            'A successful mutex acquisition does not prove profile validity, runtime readiness or permission to launch',
            'Guard lifetime must remain on the acquiring thread; live wrong-thread destruction is a programming-error fail-fast, distinct from normal finite Busy/IoError',
            'Zero wait is nonblocking acquisition policy, not a hard wall-clock bound for Windows calls/scheduling',
            'Concurrency stress confirms functional denial only; no ThreadSanitizer proof is claimed',
            'Same-user named-object squatting can deny acquisition; a finite error must never be treated as an unlocked profile']}
    target=ROOT.parent/'analysis/native-migration-v1/launcher-transfer-mutex-proof.json';target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'nativeChecks':native['checks'],'librarySha256':sha(library),'productionMutexAcquired':False,'proof':str(target)}))
if __name__=='__main__':main()
