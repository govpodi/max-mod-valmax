"""Isolated host-ABI release proof; no user profiles or installed DLL changes."""
from __future__ import annotations
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import media_staging_proof as common

ROOT=common.ROOT
TARGET=common.TARGET

def snapshot():
    result=common.snapshot()
    for path in [Path(__file__),ROOT/"native/core/valmax_core.h"]:
        result[path.relative_to(ROOT).as_posix()]=common.sha(path)
    return result

def main():
    sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    parser=argparse.ArgumentParser();parser.add_argument("--full",action="store_true");args=parser.parse_args()
    sources=snapshot()
    frozen={str(p):common.sha(p) for p in common.FROZEN if p.is_file()}
    assert frozen
    if args.full:
        targeted=json.loads((ROOT/"analysis/native-migration-v1/media-staging-ffi-targeted-proof.json").read_text("utf-8"))
        assert targeted["passed"] and targeted["sources"]==sources, "targeted ABI proof must match these bytes"
    commands=["cargo +1.99.1 test --manifest-path D:/ValMAX/native/Cargo.toml --release --offline "+tail+" -- --test-threads=1" for tail in ([""] if args.full else ["--lib staging_ffi","--test media_staging_ffi"])]
    command="\n".join(["$ErrorActionPreference = 'Stop'", ". 'D:/ValMAX/native/tooling/native_env.ps1'", "$env:CARGO_TARGET_DIR='C:/ValMAXBuild/target-media-staging'",*sum(([cmd,"if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"] for cmd in commands),[]),"exit 0"])
    log=common.PROOFS/("media-staging-ffi-"+uuid.uuid4().hex+".log")
    common.PROOFS.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env.pop("VALMAX_SYNTHETIC_STAGING_LOCK_TEST",None)
    shell=shutil.which("pwsh");assert shell
    with log.open("wb") as out:
        run=subprocess.run([shell,"-NoProfile","-NonInteractive","-Command",command],cwd=ROOT/"native",env=env,stdout=out,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    assert snapshot()==sources,"source changed during run"
    assert {str(p):common.sha(p) for p in common.FROZEN if p.is_file()}==frozen,"frozen DLL changed"
    text=log.read_text("utf-8",errors="replace")
    results=re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored",text)
    focus=re.findall(r"^test (?:media_staging_ffi::[^ ]*|staging_ffi_[^ ]*) \.\.\. ok$",text,re.M)
    total=sum(int(p) for p,_,_ in results)
    passed=run.returncode==0 and len(focus)==10 and total==(178 if args.full else 10) and all(f=="0" and i=="0" for _,f,i in results)
    pair={name:{"path":str(TARGET/"release/deps"/name),"sha256":common.sha(TARGET/"release/deps"/name)} for name in ["valmax_core.dll","valmax_core.dll.lib"]}
    if args.full: assert pair==targeted["corePair"],"targeted DLL pair changed during full regression"
    report=ROOT/"analysis/native-migration-v1"/("media-staging-ffi-proof.json" if args.full else "media-staging-ffi-targeted-proof.json")
    value={"schema":1,"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"passed":passed,"scope":"host-only public media staging C ABI; no QML/draft/installed app integration",
        "fullRegression":args.full,"passedTests":total,"targetedAbiTests":len(focus),"ignored":sum(int(i) for _,_,i in results),"returncode":run.returncode,
        "sources":sources,"corePair":pair,"frozenDllsUnchanged":frozen,"log":{"path":str(log),"sha256":common.sha(log)},
        "meaningfulChecks":["real filesystem stage/cancel/retain via extern C functions", "ABI sizes/offsets/null/range validation and owned URL free",
            "deterministic active stage -> signal/cancel/release before publication -> no stale URL",
            "revoked-running job retains bounded slot until its Arc drops", "begin reservations and failed roots return capacity",
            "Rust panic becomes finite terminal releasable job", "held reader cleanup failure stays visible", "retained handoff survives later cancellation/release"],
        "limitations":["caller must keep borrowed input and DLL alive through stage return, even after release",
            "ordinary filesystem calls may block on I/O worker; only signal_cancel is guaranteed not to perform filesystem work",
            "release/publish are linearized: host generation check remains mandatory before native handoff",
            "Rust C ABI direct calls tested here; separately linked C++ DLL interoperability is a separate proof",
            "no real MAX draft ACK exists in observed void presenter API; retain means terminal TTL handoff, not confirmed attachment"]}
    report.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":passed,"tests":total,"report":str(report),"dllSha256":pair["valmax_core.dll"]["sha256"]}))
    if not passed: print(text[-14000:]);raise SystemExit(1)

if __name__=="__main__":main()
