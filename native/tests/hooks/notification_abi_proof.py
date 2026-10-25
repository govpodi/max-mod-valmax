"""Synthetic notification C ABI, canonical Python hash and dependency proof."""
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

class Scope(c.Structure):
    _fields_=[('owner',c.c_int64),('chat',c.c_int64)]
class Policy(c.Structure):
    _fields_=[('size',c.c_uint32),('version',c.c_uint32),('generation',c.c_uint64),('revision',c.c_char*64),
              ('ready',c.c_uint32),('lease_ms',c.c_uint32),('count',c.c_uint32),('reserved',c.c_uint32)]
class Status(c.Structure):
    _fields_=[(field,c.c_uint32) for field in ('size','version','error','ready','applied','replacements','listeners','in_flight')]+[
        ('generation',c.c_uint64),('revision',c.c_char*64)]+[(field,c.c_uint64) for field in ('masked','scope_unknown','suppressed','faults')]+[('reserved',c.c_uint32*2)]

def main():
    c.windll.kernel32.SetErrorMode(0x8003)
    dll_path=BUILD/'valmax_notifications.dll'
    dll=c.CDLL(str(dll_path))
    dll.valmax_notifications_install.argtypes=[c.c_wchar_p,c.c_wchar_p,c.c_wchar_p]
    dll.valmax_notifications_set_policy.argtypes=[c.POINTER(Policy),c.POINTER(Scope)]
    dll.valmax_notifications_status.argtypes=[c.POINTER(Status)]
    dll.valmax_notifications_shutdown.argtypes=[]
    checks=[]
    def check(name,ok):
        if not ok:raise AssertionError(name)
        checks.append(name)
    check('ABI96/16/144',c.sizeof(Policy)==96 and c.sizeof(Scope)==16 and c.sizeof(Status)==144 and Status.revision.offset==40)
    check('null status rejected',dll.valmax_notifications_status(None)==1)
    check('null policy rejected',dll.valmax_notifications_set_policy(None,None)==1)
    check('null install rejected',dll.valmax_notifications_install(None,None,None)==1)
    state=Status(c.sizeof(Status),1)
    check('cold unavailable',dll.valmax_notifications_status(c.byref(state))==0 and not state.ready and not state.applied)
    counter=0
    def policy(rows):
        nonlocal counter
        counter+=1
        ordered=sorted(rows,key=lambda x:(str(x[0]),str(x[1])))
        wire={'schema':1,'ready':True,'protected':[{'ownerId':str(o),'chatId':str(ch)} for o,ch in ordered]}
        digest=hashlib.sha256(json.dumps(wire,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest().encode()
        scopes=(Scope*len(ordered))(*(Scope(o,ch) for o,ch in ordered))
        return Policy(96,1,counter,digest,1,5000,len(ordered),0),scopes
    for name,rows in [('empty',[]),('zero_chat',[(42,0)]),('large_ids',[(9007199254740993,-9223372036854775808),(9223372036854775807,9223372036854775807)]),
                      ('lexicographic_not_numeric',[(2,10),(10,2),(10,-1)]),('full256',[(42,i) for i in range(256)])]:
        value,scopes=policy(rows)
        check('canonical Python SHA '+name,dll.valmax_notifications_set_policy(c.byref(value),scopes)==0)
        dll.valmax_notifications_status(c.byref(state))
        check('no fabricated ready '+name,not state.ready and not state.applied and state.generation==counter and state.revision==value.revision)
    value,scopes=policy([(42,5)])
    value.revision=b'0'*64
    check('wrong digest rejected',dll.valmax_notifications_set_policy(c.byref(value),scopes)==6)
    value,scopes=policy([(42,5)]);value.ready=2
    check('strict boolean',dll.valmax_notifications_set_policy(c.byref(value),scopes)==6)
    value,scopes=policy([(42,5)]);value.reserved=1
    check('reserved rejected',dll.valmax_notifications_set_policy(c.byref(value),scopes)==6)
    value,scopes=policy([(42,5)]);value.lease_ms=5001
    check('bounded lease',dll.valmax_notifications_set_policy(c.byref(value),scopes)==6)
    value,scopes=policy([(42,5)]);value.generation=1
    check('stale generation',dll.valmax_notifications_set_policy(c.byref(value),scopes)==6)
    value,scopes=policy([(42,5)]);value.ready=0
    check('unavailable cannot clear last good catalogue',dll.valmax_notifications_set_policy(c.byref(value),scopes)==13)
    dll.valmax_notifications_status(c.byref(state));check('last good generation kept',state.generation==5)
    check('cold shutdown safe',dll.valmax_notifications_shutdown()==0)
    check('module loading transient',dll.valmax_notifications_install('C:/ValMAXBuild/absent/max.exe','C:/ValMAXBuild/absent/notifications.dll','C:/ValMAXBuild/absent/valmax-private-notification-f2bb5bbaca678eca.png')==3)
    check('mismatched parents rejected',dll.valmax_notifications_install('C:/ValMAXBuild/a/max.exe','C:/ValMAXBuild/b/notifications.dll','C:/ValMAXBuild/a/asset.png')==1)
    native=json.loads(subprocess.run([str(BUILD/'valmax_notification_test.exe')],text=True,capture_output=True,check=True,timeout=20).stdout)
    pe=pefile.PE(str(dll_path))
    exports=sorted(s.name.decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols if s.name)
    imports=sorted(i.dll.decode() for i in pe.DIRECTORY_ENTRY_IMPORT)
    check('only four production exports',exports==sorted('valmax_notifications_'+s for s in ('install','set_policy','status','shutdown')))
    check('no Python JS dynamic CRT imports',not any(any(s in name.lower() for s in ('python','quickjs','v8','msvcp','vcruntime')) for name in imports))
    bad=Path('C:/ValMAXBuild/fixtures')/('notification-invalid-pins-'+uuid.uuid4().hex);bad.mkdir()
    (bad/'max.exe').write_bytes(b'MZ');(bad/'notifications.dll').write_bytes(b'MZ')
    generated=bad/'must-not-exist.h'
    result=subprocess.run([sys.executable,'-O',str(ROOT/'hooks/notification_generate_pins.py'),'--runtime-root',str(bad),'--output',str(generated),'--proof',str(bad/'proof.json')],text=True,capture_output=True,timeout=10)
    check('optimized Python cannot bypass guards',result.returncode!=0 and not generated.exists())
    sources=[*ROOT.glob('hooks/notification*'),ROOT/'hooks/hook_set.h',ROOT/'hooks/hook_set.cpp',ROOT/'hooks/ghost_module.cpp',ROOT/'hooks/CMakeLists.txt',Path(__file__),Path(__file__).with_name('notification_test.cpp')]
    report={'schema':1,'ok':True,'scope':'synthetic buffers/native C ABI; no OS toast or server delivery claim','dllSha256':hashlib.sha256(dll_path.read_bytes()).hexdigest(),
        'dllSize':dll_path.stat().st_size,'nativeChecks':native,'abiChecks':checks,'exports':exports,'imports':imports,
        'pins':json.loads((BUILD/'notification-pins.json').read_text()),'sourceSha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        'policyChangeFromJS':'Expired or invalid lease preserves exact known PIN scopes; verified ordinary notifications pass; unknown owner with nonempty scopes is suppressed. Never global generic mask.',
        'ackMeaning':'Fresh confidentiality enforcement and callbacks drained, not proof of complete delivery for unknown identities.',
        'bounds':{'scopes':256,'owners':64,'lateIdentityContexts':64,'activeCallbacks':64,'counterSaturation':1000000},
        'realProfilesAccessed':False,'installedClientModified':False,'networkUsed':False}
    target=ROOT.parent/'analysis/native-migration-v1/notification-native-proof.json';target.write_text(json.dumps(report,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'ok':True,'nativeChecks':native['checks'],'abiChecks':len(checks),'dllSha256':report['dllSha256'],'proof':str(target)}))

if __name__=='__main__':main()
