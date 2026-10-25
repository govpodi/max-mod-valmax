"""Own QtCore cache fixture only; no transport or user files."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT=Path(__file__).resolve().parents[3]
BUILD=Path('C:/ValMAXBuild/build/media-protocol')
spec=importlib.util.spec_from_file_location('pure_media_fixture',Path(__file__).with_name('proof.py'))
fixture_api=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture_api)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    compiled=[ROOT/'native/media'/n for n in ('protocol.cpp','protocol.h','result_cache.cpp','result_cache.h')]+[Path(__file__).with_name('result_cache_test.cpp')]
    if any(p.stat().st_mtime_ns>(BUILD/'valmax_media_result_cache_test.exe').stat().st_mtime_ns for p in compiled):
        raise RuntimeError('stale_test_binary_rebuild_required')
    fixture=Path('C:/ValMAXBuild/build/media-cache-fixtures')/uuid.uuid4().hex;fixture.mkdir(parents=True)
    copied=fixture_api.copy_runtime(fixture)
    exe=fixture/'cache-test.exe';shutil.copy2(BUILD/'valmax_media_result_cache_test.exe',exe)
    env={k:os.environ[k] for k in ('SystemRoot','WINDIR','COMSPEC') if k in os.environ}
    env['PATH']=str(fixture)+os.pathsep+str(Path(os.environ['SystemRoot'])/'System32')
    r=subprocess.run([str(exe)],cwd=fixture,env=env,capture_output=True,timeout=30,creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:raise RuntimeError(r.stderr.decode(errors='replace'))
    result=json.loads(r.stdout)
    sources=[ROOT/'native/media'/name for name in ('result_cache.h','result_cache.cpp','result_cache.cmake','protocol.cpp','protocol.h')]+[Path(__file__),Path(__file__).with_name('result_cache_test.cpp')]
    proof={'schema':1,'passed':True,'checks':result['checks'],'fixture':str(fixture),'runtime':copied,
        'librarySha256':sha(BUILD/'valmax_media_result_cache.lib'),'testExeSha256':sha(exe),
        'sources':{str(p.relative_to(ROOT)):sha(p) for p in sources},
        'scope':'Bounded private memory metadata/URL cache. No networking, media decode, disk staging, UI or live accounts.',
        'limits':{'entries':48,'batch':6,'chargedBytes':2097152,'activeScopes':32,'ttlSeconds':1200,'entropyAttemptsPerId':8},
        'coverage':['atomic batch and no partial publication on invalid row, capacity or collision','all count/byte sums, FIFO eviction','exact TTL boundary and invalid/rewound clocks','account/page collision and scope revocation','late publish after close/reopen paused outside mutex','640 concurrent synthetic scope publish/close cycles','deterministic entropy collision, bad-length, throwing callback and default CSPRNG','deep compact strings avoid retained reserved capacity'],
        'limitations':['Returned Candidate copies cannot be physically recalled; delivery must recheck scope. clear drops cache references, not guaranteed memory wipe.',
            'Scope tokens are instance-local and never recycled in that instance. Destruction requires caller drain.',
            'Result IDs are CSPRNG128, checked against live entries; an expired ID has no eternal tombstone. Image provider must bind its own nonce/generation.',
            'Logical UTF16 budget includes conservative fixed overhead; not an exact RSS/allocator limit. Transaction temporarily holds old and new bounded maps.',
            'Race checks are barrier/stress tests, not TSAN proof. Counter-wrap guards reviewed but not exhaustively exercised to 2^64.',
            'TTL at exactly1200s rejects, stricter than legacy received-result lookup which used <.']}
    out=ROOT/'analysis/native-migration-v1/media-result-cache-proof.json';out.write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'checks':result['checks'],'proof':str(out)}))
if __name__=='__main__':main()
