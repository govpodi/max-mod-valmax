"""Real C++ -> Rust staging ownership proof, confined to an owned fixture.

Uses the newly built isolated Rust DLL, never an installed/frozen replacement.
No HTTP, Qt, real profiles, native composer invocation or message sending.
"""
from __future__ import annotations
import ctypes
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import pefile

ROOT = Path(__file__).resolve().parents[4]
BUILD = Path("C:/ValMAXBuild/build/media-staging-client")
CORE = Path("C:/ValMAXBuild/target-media-staging/release/deps")
FIXTURES = Path("C:/ValMAXBuild/fixtures/media-staging-client")
BASELINE = ROOT / "release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod"
SOURCES = [
    "native/media/staging_client.h", "native/media/staging_client.cpp", "native/media/staging_client.cmake",
    "native/tests/media/staging/CMakeLists.txt", "native/tests/media/staging/client_test.cpp",
    "native/tests/media/staging/integration_test.cpp", "native/tests/media/staging/proof.py",
    "native/core/src/media_staging.rs", "native/core/src/media_staging/win.rs",
    "native/core/src/media_staging_ffi.rs", "native/core/src/ffi.rs", "native/core/src/lib.rs",
    "native/core/valmax_core.h", "native/core/Cargo.toml", "native/Cargo.lock",
]
FROZEN = [Path(p) for p in (
    "C:/ValMAXBuild/target/release/valmax_core.dll",
    "C:/ValMAXBuild/target/release/deps/valmax_core.dll",
    "C:/ValMAXBuild/build/all-feature-inputs-core-startup/valmax_core.dll",
    "C:/ValMAXBuild/build/all-feature-inputs/valmax_core.dll",
)]

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def run(args, timeout=60):
    result = subprocess.run([str(a) for a in args], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", errors="strict", timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        (BUILD / "failure.log").write_text(result.stdout+result.stderr,encoding="utf-8")
        raise RuntimeError(f"Owned staging proof failed, exit {result.returncode}; see build/failure.log")
    return result

def imports(p):
    image=pefile.PE(str(p))
    assert image.FILE_HEADER.Machine==0x8664
    return {e.dll.decode():[s.name.decode() for s in e.imports if s.name]
            for e in getattr(image,"DIRECTORY_ENTRY_IMPORT",[])}

def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    sources={p:sha(ROOT/p) for p in SOURCES}
    frozen={str(p):sha(p) for p in FROZEN if p.is_file()}
    assert len(frozen)==4
    pair={name:sha(CORE/name) for name in ("valmax_core.dll","valmax_core.dll.lib")}
    # The owner builds in its separate Cargo target after these ABI sources.
    assert (CORE/"valmax_core.dll").stat().st_mtime_ns >= max((ROOT/p).stat().st_mtime_ns for p in SOURCES if p.endswith(".rs"))
    dll_image=pefile.PE(str(CORE/"valmax_core.dll"))
    exported={s.name.decode() for s in dll_image.DIRECTORY_ENTRY_EXPORT.symbols if s.name}
    required={"valmax_media_staging_"+n for n in ("abi_version","begin","stage","signal_cancel","cancel","retain","release")}
    required.add("valmax_core_buffer_free")
    assert required<=exported
    snapshot=BUILD/"inputs"/pair["valmax_core.dll"]
    snapshot.mkdir(parents=True,exist_ok=True)
    for name,expected in pair.items():
        dest=snapshot/name
        if not dest.exists(): shutil.copyfile(CORE/name,dest)
        assert sha(dest)==expected and sha(CORE/name)==expected
    shell=shutil.which("pwsh")
    assert shell,"PowerShell7 required; do not change execution policy"
    command=(
        "$ErrorActionPreference='Stop'\n"
        ". 'D:/ValMAX/native/tooling/native_env.ps1'\n"
        f"cmake -S '{ROOT / 'native/tests/media/staging'}' -B '{BUILD}' -G Ninja -DCMAKE_BUILD_TYPE=Release "
        f"'-DVALMAX_STAGING_CORE_LIBRARY={snapshot / 'valmax_core.dll.lib'}'\n"
        "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
        f"cmake --build '{BUILD}' --target valmax_staging_client_test valmax_staging_integration_test\n"
        "exit $LASTEXITCODE\n")
    built=run([shell,"-NoProfile","-NonInteractive","-Command",command],timeout=120)
    (BUILD/"build.log").write_text(built.stdout+built.stderr,encoding="utf-8")
    shutil.copyfile(snapshot/"valmax_core.dll",BUILD/"valmax_core.dll")
    runtime={}
    for name in ("vcruntime140.dll","vcruntime140_1.dll","msvcp140.dll","msvcp140_atomic_wait.dll","concrt140.dll"):
        p=BASELINE/name
        if p.is_file():
            shutil.copyfile(p,BUILD/name);runtime[name]={"source":str(p),"sha256":sha(p)}
    actual_exe=BUILD/"valmax_staging_integration_test.exe"
    fake_exe=BUILD/"valmax_staging_client_test.exe"
    imported=imports(actual_exe)
    actual_core=next((symbols for name,symbols in imported.items() if name.lower()=="valmax_core.dll"),None)
    assert set(actual_core or [])==required
    assert not any("qt" in name.lower() or "python" in name.lower() or "gum" in name.lower() for name in imported)
    synthetic=json.loads(run([fake_exe],timeout=30).stdout)
    assert synthetic["ok"] and not synthetic["realCoreCalled"] and not synthetic["filesystemTouched"]
    FIXTURES.mkdir(parents=True,exist_ok=True)
    fixture=FIXTURES/uuid.uuid4().hex
    fixture.mkdir()
    (fixture/".owned").write_text("valmax-staging-client-owned-v1\n",encoding="ascii")
    real=json.loads(run([actual_exe,fixture,BUILD/"valmax_core.dll"],timeout=45).stdout)
    assert real["ok"] and real["realCoreCalled"] and real["actualDllPathVerified"]
    assert real["remainingMediaFiles"]==0 and real["fixtureRootRemoved"] and not real["draftAttachmentProven"]
    # Non-recursive cleanup, after the process has exited and the Rust read
    # leases have been dropped. Refuse unexpected leftovers instead of deleting.
    assert fixture.resolve().parent==FIXTURES.resolve() and len(fixture.name)==32
    assert [p.name for p in fixture.iterdir()]==[".owned"]
    (fixture/".owned").unlink();fixture.rmdir()
    assert {p:sha(ROOT/p) for p in SOURCES}==sources,"source changed during proof"
    assert {str(p):sha(p) for p in FROZEN}==frozen,"frozen DLL changed"
    assert {name:sha(CORE/name) for name in pair}==pair,"new Rust pair changed during proof"
    report={
        "schema":1,"ok":True,"utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope":"host-only C++ RAII -> real isolated Rust staging DLL; synthetic public media only",
        "sourceSha256":sources,"core":{"directory":str(snapshot),"sha256":pair},
        "frozenDllsUnchanged":frozen,"binary":{"path":str(actual_exe),"sha256":sha(actual_exe),"imports":imported},
        "syntheticBinary":{"path":str(fake_exe),"sha256":sha(fake_exe)},"appLocalRuntime":runtime,
        "synthetic":synthetic,"realInterop":real,"ownedFixture":str(fixture),"fixtureRemoved":not fixture.exists(),
        "userInstallationChanged":False,"networkRequests":0,"qtObjectsCreated":0,
        "limitations":[
            "No actual MAX draft attachment or per-file ACK; retain_for_handoff only transfers file ownership to the TTL cache",
            "Future host must recheck controller generation before retain and GUI generation before native dispatch",
            "Failed or suppressed dispatch after retain leaves the bounded quota-charged TTL file intentionally",
            "Cancellation stress covers sampled scheduling; no TSAN or deterministic in-syscall interruption claim",
            "Filesystem/flush calls remain synchronous on the caller I/O thread; no hard total latency bound",
            "Destructor cleanup is best-effort; an externally held reader leaves a quota-charged orphan",
            "Function table and DLL lifetime must be supplied by a later trusted host; wrapper does not load modules"
        ]}
    report_path=ROOT/"analysis/native-migration-v1/media-staging-client-proof.json"
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"ok":True,"syntheticChecks":synthetic["checks"],"realChecks":real["checks"],
        "fixtureRemoved":not fixture.exists(),"proof":str(report_path)}))

if __name__=="__main__":main()
