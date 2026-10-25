"""Production static-privacy verifier proof; no client launch or network access."""
from __future__ import annotations
import ctypes as c
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
import pefile
ROOT=Path(__file__).resolve().parents[2]
BUILD=Path('C:/ValMAXBuild/build/hooks')
class Status(c.Structure):
    _fields_=[(name,c.c_uint32) for name in ('size','version','error','ready','role','disk_rules','loaded_rules','files_verified','modules_verified','patches_verified','required_loaded_rules','reserved')]+[('manifest_sha256',c.c_char*64)]
def main():
    c.windll.kernel32.SetErrorMode(0x8003)
    path=BUILD/'valmax_privacy.dll';dll=c.CDLL(str(path))
    dll.valmax_privacy_verify.argtypes=[c.c_wchar_p,c.c_uint32]
    dll.valmax_privacy_status.argtypes=[c.POINTER(Status)]
    dll.valmax_privacy_shutdown.argtypes=[]
    checks=[]
    def check(name,value):
        if not value:raise AssertionError(name)
        checks.append(name)
    check('C ABI112/manifest offset48',c.sizeof(Status)==112 and Status.manifest_sha256.offset==48)
    check('null status refused',dll.valmax_privacy_status(None)==1)
    state=Status(c.sizeof(Status),1)
    check('cold state not verified',dll.valmax_privacy_status(c.byref(state))==0 and not state.ready and state.error==6)
    check('null root refused',dll.valmax_privacy_verify(None,1)==1)
    check('relative root refused',dll.valmax_privacy_verify('relative',1)==1)
    check('unknown role refused',dll.valmax_privacy_verify('C:/ValMAXBuild',3)==1)
    check('UNC path refused',dll.valmax_privacy_verify('\\\\server\\share',1)==1)
    pins=json.loads((BUILD/'privacy-pins.json').read_text());runtime=pins['runtimeRoot']
    # This reads only the pinned program files. The Python process has no MAX
    # modules, so disk checks must never fabricate loaded protection readiness.
    check('disk files alone cannot fabricate readiness',dll.valmax_privacy_verify(runtime,1)==3)
    dll.valmax_privacy_status(c.byref(state));check('missing loaded module explicit',not state.ready and state.error==3 and state.manifest_sha256.decode()==pins['manifestSha256'])
    check('shutdown clears verification only',dll.valmax_privacy_shutdown()==0)
    dll.valmax_privacy_status(c.byref(state));check('shutdown no stale status',not state.ready and not state.disk_rules and not state.loaded_rules)
    native=json.loads(subprocess.run([str(BUILD/'valmax_privacy_test.exe')],text=True,capture_output=True,check=True,timeout=20).stdout)
    pe=pefile.PE(str(path));exports=sorted(s.name.decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols if s.name);imports=sorted(i.dll.decode() for i in pe.DIRECTORY_ENTRY_IMPORT)
    check('only three production exports',exports==sorted('valmax_privacy_'+s for s in ('verify','status','shutdown')))
    check('no observer/Gum/Python/JS/network imports',set(s.lower() for s in imports)<={'kernel32.dll','bcrypt.dll'})
    bad=Path('C:/ValMAXBuild/fixtures')/('privacy-invalid-pins-'+uuid.uuid4().hex);(bad/'max-service').mkdir(parents=True)
    (bad/'max-service/max-service.exe').write_bytes(b'MZ')
    header=bad/'must-not-exist.h'
    rejected=subprocess.run([sys.executable,'-O',str(ROOT/'hooks/privacy_generate_pins.py'),'--runtime-root',str(bad),'--output',str(header),'--proof',str(bad/'proof.json')],text=True,capture_output=True,timeout=20)
    check('optimized Python cannot bypass reverse pins',rejected.returncode!=0 and not header.exists())
    sources=[*ROOT.glob('hooks/privacy*'),ROOT/'hooks/CMakeLists.txt',Path(__file__),Path(__file__).with_name('privacy_test.cpp')]
    report={'schema':1,'ok':True,'scope':'static binary privacy verification; no network absence guarantee','dllSha256':hashlib.sha256(path.read_bytes()).hexdigest(),'dllSize':path.stat().st_size,
        'nativeChecks':native,'abiChecks':checks,'exports':exports,'imports':imports,'pins':pins,
        'sourceSha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        'observerCallbacks':0,'runtimeCodePatches':0,'networkUsed':False,'realProfilesAccessed':False,'installedClientModified':False,
        'limitations':['Tracer imported entrypoints are disabled; no claim that every possible telemetry subsystem is removed','MyTracker pinned initializer is disabled; prior active traffic from that path was not established','No connection/packet/fatal-stack instrumentation','Actual loaded MAX verification is a separate guarded offline integration check']}
    target=ROOT.parent/'analysis/native-migration-v1/privacy-native-proof.json';target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'nativeChecks':native['checks'],'abiChecks':len(checks),'dllSha256':report['dllSha256'],'dllSize':report['dllSize'],'imports':imports,'proof':str(target)}))
if __name__=='__main__':main()
