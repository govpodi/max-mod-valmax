"""Read-only production preflight plus owned synthetic file-lifetime fixtures."""
from __future__ import annotations
import hashlib
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
import pefile

ROOT=Path(__file__).resolve().parents[2]
BUILD=Path('C:/ValMAXBuild/build/launcher')
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    checks=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        checks.append(name)
    pins=json.loads((BUILD/'preflight-pins.json').read_text())
    executable=BUILD/'valmax_preflight.exe'
    def run(args):
        outcome=subprocess.run([str(executable),*args],capture_output=True,text=True,timeout=40)
        return outcome,json.loads(outcome.stdout)
    for arguments in ([],['--launch'],['--check-only','--runtime','C:/ValMAXBuild','--sha256','0'*64]):
        outcome,data=run(arguments)
        check('unsupported command cannot request launch/hash',outcome.returncode==2 and data['code']=='invalid_command' and not data['launchAllowed'])
    outcome,data=run(['--check-only','--runtime','relative'])
    check('relative runtime rejected',outcome.returncode==1 and data['code']=='invalid_root' and not data['verified'])
    missing=Path('C:/ValMAXBuild/fixtures')/('preflight-missing-'+uuid.uuid4().hex)
    outcome,data=run(['--check-only','--runtime',str(missing)])
    check('missing runtime finite failure',outcome.returncode==1 and data['code']=='missing_file' and not data['verified'] and not missing.exists())
    started=time.monotonic()
    outcome,data=run(['--check-only','--runtime',pins['runtimeRoot']])
    elapsed=time.monotonic()-started
    check('fixed owned fixture exact inventory verified',outcome.returncode==0 and data['verified'] and data['files']==len(pins['files']) and data['manifestSha256']==pins['manifestSha256'])
    check('verification never authorizes launch',not data['launchAllowed'])
    native=json.loads(subprocess.run([str(BUILD/'valmax_preflight_test.exe')],capture_output=True,text=True,check=True,timeout=30).stdout)
    pe=pefile.PE(str(executable));imports={item.dll.decode():sorted(symbol.name.decode() for symbol in item.imports if symbol.name) for item in pe.DIRECTORY_ENTRY_IMPORT}
    check('no network/Python/Qt runtime dependencies',set(x.lower() for x in imports)<={'kernel32.dll','bcrypt.dll'})
    forbidden={'CreateProcessW','CreateProcessA','ShellExecuteW','ShellExecuteExW','ReadProcessMemory','WriteProcessMemory','OpenProcess','VirtualAllocEx','CreateRemoteThread','EnumWindows','ShowWindow','ShowWindowAsync','CreateDirectoryW','DeleteFileW','MoveFileExW','WritePrivateProfileStringW'}
    check('no launch/memory/GUI/mutating file operations',not any(forbidden.intersection(names) for names in imports.values()))
    bad=Path('C:/ValMAXBuild/fixtures')/('preflight-build-reject-'+uuid.uuid4().hex)
    rejected=subprocess.run([sys.executable,'-O',str(ROOT/'launcher/preflight_generate_pins.py'),'--runtime-root',str(bad),'--output',str(bad/'pins.h'),'--proof',str(bad/'proof.json')],capture_output=True,text=True,timeout=20)
    check('optimized generator still requires owned provenance',rejected.returncode!=0 and not bad.exists())
    # Build-time inventory resolution is evidence, not a release claim. Dynamic
    # loading and Windows API-set forwarding require their own closure proof.
    unresolved={}
    for path,groups in pins['peImports'].items():
        for kind,names in groups.items():
            for name in names:
                if name in pins['externalImportNames']:
                    unresolved.setdefault(name,[]).append({'importer':path,'kind':kind})
    sources=[*ROOT.glob('launcher/preflight*'),ROOT/'launcher/CMakeLists.txt',Path(__file__),Path(__file__).with_name('preflight_test.cpp')]
    report={'schema':1,'ok':True,'scope':'native read-only fixed offline payload preflight; no client bootstrap/spawn',
        'executable':{'path':str(executable),'sha256':sha(executable),'size':executable.stat().st_size,'imports':imports},
        'nativeTests':native,'productionChecks':checks,'actualReadOnlyCheck':data,'observedVerificationSeconds':elapsed,
        'pinnedInventory':{'path':str(BUILD/'preflight-pins.json'),'sha256':sha(BUILD/'preflight-pins.json'),'manifestSha256':pins['manifestSha256'],'files':len(pins['files']),'directories':len(pins['directories']),'peFiles':len(pins['peImports']),'provenanceSha256':pins['provenanceSha256'],'sourceManifestSha256':pins['sourceManifestSha256']},
        'unresolvedImportNamesAndImporters':unresolved,'sourceSha256':{str(path.relative_to(ROOT)):sha(path) for path in sources},
        'retainedHandles':{'files':'GENERIC_READ; share READ only','directories':'FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES; no share DELETE','lifetime':'VerifiedPayload RAII object remains alive through future caller critical startup interval'},
        'realProfilesAccessed':False,'installedClientModified':False,'clientExecuted':False,'fullReleaseClosureVerified':False,
        'limitations':['Immutable test fixture pins only; final payload and native module closure require a new audited generated manifest',
            'PE imports and delay-imports are inventoried; dynamic Qt/plugin/WebView2 and OS API-set resolution is not complete',
            'Integrity is a snapshot under retained existing-file/directory share locks; a trusted installation directory is still required against newly planted files or previously writable mappings',
            'Static MSVC CRT imports loader/environment/write/fatal-runtime primitives; preflight itself only opens existing read-only files and writes its result to stdout',
            'No MData recovery-marker/config creation/IPC/native protection readiness or process launch is implemented by this check-only executable',
            'All directory junction and hardlink tests affect only generated synthetic fixtures under C:/ValMAXBuild/fixtures']}
    target=ROOT.parent/'analysis/native-migration-v1/launcher-preflight-proof.json';target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'nativeChecks':native['checks'],'productionChecks':len(checks),'files':data['files'],'exeSha256':sha(executable),'verificationSeconds':elapsed,'proof':str(target)}))
if __name__=='__main__':main()
