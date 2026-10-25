"""Routing-only retention queue proof, synthetic IDs and no native MAX launch."""
from __future__ import annotations
import ctypes as c
import hashlib
import json
import struct
import subprocess
import sys
import uuid
from pathlib import Path
import pefile

ROOT=Path(__file__).resolve().parents[2]
BUILD=Path("C:/ValMAXBuild/build/hooks")
class Policy(c.Structure):
    _fields_=[("size",c.c_uint32),("version",c.c_uint32),("generation",c.c_uint64),
              ("revision",c.c_char*64)]+[(n,c.c_uint32) for n in ("enabled","storage_ready","wait_ms","reserved")]
class Request(c.Structure):
    _fields_=[(n,c.c_uint32) for n in ("size","version","kind","count")]+[(n,c.c_uint64) for n in ("nonce","epoch","generation")]+[(n,c.c_int64) for n in ("owner","chat")]+[("deadline_tick",c.c_uint64),("ids_digest",c.c_char*64)]
class Ack(c.Structure):
    _fields_=[(n,c.c_uint32) for n in ("size","version","result","reserved")]+[("request",Request),("retained",c.c_uint8*512)]
class Status(c.Structure):
    _fields_=[(n,c.c_uint32) for n in ("size","version","error","ready","applied","enabled","listeners","gates")]+[("generation",c.c_uint64),("revision",c.c_char*64),("epoch",c.c_uint64)]+[(n,c.c_uint32) for n in ("queued","in_flight")]+[(n,c.c_uint64) for n in ("received_batches","deletion_requests","acknowledged","missed","rejected_ack","storage_failures")]

def main():
    c.windll.kernel32.SetErrorMode(0x8003)
    path=BUILD/"valmax_retention_queue.dll"
    dll=c.CDLL(str(path))
    dll.valmax_retention_set_policy.argtypes=[c.POINTER(Policy)]
    dll.valmax_retention_take.argtypes=[c.POINTER(Request),c.POINTER(c.c_int64),c.c_uint32,c.c_uint32]
    dll.valmax_retention_ack.argtypes=[c.POINTER(Ack)]
    dll.valmax_retention_status.argtypes=[c.POINTER(Status)]
    dll.valmax_retention_shutdown.argtypes=[]
    checks=[]
    def check(name,value):
        if not value: raise AssertionError(name)
        checks.append(name)
    check("C ABI sizes96/128/656/168",[c.sizeof(t) for t in (Policy,Request,Ack,Status)]==[96,128,656,168])
    check("C ABI field offsets",Policy.enabled.offset==80 and Request.owner.offset==40 and Request.ids_digest.offset==64 and Ack.retained.offset==144 and Status.received_batches.offset==120)
    check("null policy rejected",dll.valmax_retention_set_policy(None)==1)
    check("null status rejected",dll.valmax_retention_status(None)==1)
    check("null ACK rejected",dll.valmax_retention_ack(None)==1)
    state=Status(c.sizeof(Status),1)
    check("initial status unavailable",dll.valmax_retention_status(c.byref(state))==0 and not state.ready and not state.applied and state.error==2)
    revision=hashlib.sha256(b'{"enabled":true,"schema":1}\n').hexdigest().encode()
    p=Policy(c.sizeof(Policy),1,1,revision,1,1,10,0)
    check("strict Python canonical revision accepted",dll.valmax_retention_set_policy(c.byref(p))==0)
    dll.valmax_retention_status(c.byref(state))
    check("policy never fabricates full readiness",state.generation==1 and state.enabled==1 and state.revision==revision and not state.ready and not state.applied and not state.listeners and not state.gates)
    r=Request(c.sizeof(Request),1);ids=(c.c_int64*4096)()
    check("empty queue bounded poll",dll.valmax_retention_take(c.byref(r),ids,4096,0)==2)
    check("bad caller capacity rejected",dll.valmax_retention_take(c.byref(r),ids,4095,0)==1)
    check("oversized wait rejected",dll.valmax_retention_take(c.byref(r),ids,4096,26)==1)
    check("arbitrary ACK cannot produce a decision",dll.valmax_retention_ack(c.byref(Ack(c.sizeof(Ack),1)))==1)
    p.generation=2;p.enabled=2
    check("strict bool rejected",dll.valmax_retention_set_policy(c.byref(p))==7)
    p.enabled=1;p.revision=b"0"*64
    check("noncanonical revision rejected",dll.valmax_retention_set_policy(c.byref(p))==7)
    check("clean empty shutdown",dll.valmax_retention_shutdown()==0)
    p.revision=revision
    check("stopped queue cannot reactivate via policy",dll.valmax_retention_set_policy(c.byref(p))==2)
    native=json.loads(subprocess.run([str(BUILD/"valmax_retention_queue_test.exe")],capture_output=True,text=True,check=True,timeout=20).stdout)
    synthetic_ids=(9007199254740993,9223372036854775807,31)
    expected=hashlib.sha256(b"valmax-retention-ids-v1\0"+struct.pack("<I",3)+struct.pack("<3q",*synthetic_ids)).hexdigest()
    check("C++ ordered ID digest matches Python",native["syntheticIdsDigest"]==expected)
    check("C++ settings hash matches Python",native["enabledRevision"]==revision.decode())
    pins=json.loads((BUILD/"retention-pins.json").read_text())
    bad=Path("C:/ValMAXBuild/fixtures")/("retention-invalid-pins-"+uuid.uuid4().hex)
    bad.mkdir(parents=True);(bad/"core.dll").write_bytes(b"MZ")
    rejected=subprocess.run([sys.executable,"-O",str(ROOT/"hooks/retention_generate_pins.py"),"--runtime-root",str(bad),"--output",str(bad/"pins.h"),"--proof",str(bad/"pins.json")],capture_output=True,text=True,timeout=20)
    check("optimized generator rejects invalid native files",rejected.returncode!=0 and not (bad/"pins.h").exists())
    pe=pefile.PE(str(path));imports=sorted(item.dll.decode() for item in pe.DIRECTORY_ENTRY_IMPORT)
    exports=sorted(item.name.decode() for item in pe.DIRECTORY_ENTRY_EXPORT.symbols if item.name)
    check("only five host queue exports",exports==sorted("valmax_retention_"+s for s in ("set_policy","take","ack","status","shutdown")))
    check("no observer/network/dynamic CRT dependencies",{s.lower() for s in imports}<={"kernel32.dll","bcrypt.dll"})
    sources=[*ROOT.glob("hooks/retention*"),ROOT/"hooks/CMakeLists.txt",Path(__file__),Path(__file__).with_name("retention_queue_test.cpp")]
    report={"schema":1,"ok":True,"stage":"routing-only queue/ACK and generated pins; not a full retention module",
        "dllSha256":hashlib.sha256(path.read_bytes()).hexdigest(),"dllSize":path.stat().st_size,
        "nativeChecks":native,"abiChecks":checks,"imports":imports,"exports":exports,"pins":pins,
        "limits":{"queueSlots":8,"idsPerRequest":4096,"bitmapBytes":512,"maximumAckWaitMs":25,"defaultHostWaitMs":10,"consumers":1},
        "sourceSha256":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "actualHooksInstalled":False,"rustStorageIntegrated":False,"historySyncVerified":False,
        "networkUsed":False,"realProfilesAccessed":False,"installedClientModified":False,
        "limitations":["No MAX callback/gate is installed by this queue-only module; readiness and applied stay false",
            "ACK must follow durable received lookup and marker commit in Rust; that worker is a separate implementation/proof",
            "MAX database and marker SQLite have no common atomic transaction",
            "A durable marker describes a received message's server deletion event, not proof its MAX row was retained",
            "A late marker commit can exist after timeout without native retention; stale ACK cannot authorize a later request",
            "The condition-variable ACK wait budget is25ms; this is not a hard end-to-end callback deadline: acquiring the mutex, sorting4096 IDs, BCrypt hashing, OS scheduling and SQLite can exceed it",
            "Cancellation cannot undo a database commit already running in the consumer",
            "History-wide synchronization remains unverified; only the pinned incoming and sync-row paths are planned"]}
    target=ROOT.parent/"analysis/native-migration-v1/retention-queue-proof.json"
    target.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"ok":True,"nativeChecks":native["checks"],"abiChecks":len(checks),"dllSha256":report["dllSha256"],"imports":imports,"proof":str(target)}))
if __name__=="__main__":main()
