"""Run the complete Rust regression in a separate target; synthetic fixtures only."""
from __future__ import annotations
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from generate_media_staging_fixtures import generate, OUTPUT

ROOT = Path(__file__).resolve().parents[3]
TARGET = Path("C:/ValMAXBuild/target-media-staging")
PROOFS = Path("C:/ValMAXBuild/proofs")
REPORT = ROOT / "analysis/native-migration-v1/media-staging-proof.json"
FROZEN = [Path(p) for p in (
    "C:/ValMAXBuild/target/release/valmax_core.dll",
    "C:/ValMAXBuild/target/release/deps/valmax_core.dll",
    "C:/ValMAXBuild/build/all-feature-inputs-core-startup/valmax_core.dll",
    "C:/ValMAXBuild/build/all-feature-inputs/valmax_core.dll",
)]

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def snapshot():
    paths = list((ROOT/"native/core/src").rglob("*.rs")) + list((ROOT/"native/core/tests").rglob("*.rs"))
    paths += list((ROOT/"native/core/tests").rglob("*.json"))
    paths += [ROOT/"native/Cargo.toml", ROOT/"native/Cargo.lock", ROOT/"native/core/Cargo.toml", ROOT/"native/rust-toolchain.toml"]
    paths += [Path(__file__), OUTPUT.with_name("generate_media_staging_fixtures.py")]
    return {p.relative_to(ROOT).as_posix():sha(p) for p in sorted(paths)}

def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # Existing checked-in synthetic fixtures must still match the exact pure
    # legacy predicate/constant extraction. No service imports or network.
    expected = generate()
    assert json.loads(OUTPUT.read_text("utf-8")) == expected
    sources = snapshot()
    frozen = {str(p):sha(p) for p in FROZEN if p.is_file()}
    assert frozen and all("target-media-staging" not in p for p in frozen)
    free_before = shutil.disk_usage("C:/").free
    assert free_before > 3*1024**3, "insufficient room for isolated release regression"
    PROOFS.mkdir(parents=True,exist_ok=True)
    log = PROOFS / ("media-staging-"+uuid.uuid4().hex+".log")
    command = (
        "$ErrorActionPreference = 'Stop'\n"
        ". 'D:/ValMAX/native/tooling/native_env.ps1'\n"
        "$env:CARGO_TARGET_DIR = 'C:/ValMAXBuild/target-media-staging'\n"
        "cargo +1.99.1 test --manifest-path D:/ValMAX/native/Cargo.toml --release --offline -- --test-threads=1\n"
        "exit $LASTEXITCODE\n"
    )
    env = os.environ.copy()
    env.pop("VALMAX_SYNTHETIC_STAGING_LOCK_TEST",None)
    with log.open("wb") as out:
        shell = shutil.which("pwsh")
        assert shell, "use the configured PowerShell 7 host; no execution-policy changes"
        completed = subprocess.run([shell,"-NoProfile","-NonInteractive","-Command",command],cwd=ROOT/"native",env=env,stdout=out,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
    assert snapshot() == sources, "source changed during regression"
    assert {str(p):sha(p) for p in FROZEN if p.is_file()} == frozen, "frozen DLL changed"
    text = log.read_text("utf-8",errors="replace")
    totals = re.findall(r"test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored",text)
    focus = re.findall(r"^test (?:media_staging::[^ ]*|media_staging_[^ ]*) \.\.\. ok$",text,re.M)
    passed = completed.returncode == 0 and bool(totals) and len(focus)==26 and all(f=="0" and i=="0" for _,f,i in totals)
    proof = {
        "schema":1,"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"passed":passed,
        "scope":"native selected public-media staging; no QML/FFI/draft integration",
        "target":str(TARGET),"returncode":completed.returncode,"focusedTests":len(focus),
        "fullRustPassed":sum(int(p) for p,_,_ in totals),"ignored":sum(int(i) for _,_,i in totals),
        "pythonMagicDifferentialCases":len(expected["cases"]),"legacySourceSha256":expected["sourceSha256"],
        "sources":sources,"frozenDllsUnchanged":frozen,"log":{"path":str(log),"sha256":sha(log)},
        "disk":{"freeBefore":free_before,"freeAfter":shutil.disk_usage("C:/").free,
                "targetBytes":sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file())},
        "testsInclude":["six MIME signatures and Python parity", "25MiB maximum and 128MiB quota incl orphan/pending lease",
            "24h exact boundary/future timestamp/held reader", "exclusive no-overwrite and read-only handoff",
            "same-file same-size mutation in close/reopen gap", "cancel/error checkpoints through write/flush/readback",
            "owned-identity cleanup refuses replacement/hardlink/junction", "4096 enumeration bound and new entry reservation",
            "actual child-process lock contention; CREATE_NO_WINDOW; own fixture", "Unicode file URL; root/drop lifetime"],
        "limitations":["No selected draft attachment or real MAX reader exercised: synthetic File reader only",
            "No physical disk-full, fsync failure or power loss injection: explicit test checkpoints return NoSpace",
            "Windows filesystem/flush syscalls are synchronous; cancellation is observed between bounded chunks, not a hard I/O deadline",
            "Locks serialize cooperating native writers; arbitrary same-user mutation and hostile writable mappings are not an isolation boundary",
            "Retain requires a later host generation recheck and native draft ACK; no sending or network is implemented",
            "No installer/production DLL replacement; release artifacts are confined to the isolated target"]
    }
    REPORT.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"passed":passed,"focused":len(focus),"fullRustPassed":proof["fullRustPassed"],"report":str(REPORT),"log":str(log)}))
    if not passed:
        print(text[-12000:])
        raise SystemExit(1)

if __name__ == "__main__": main()
