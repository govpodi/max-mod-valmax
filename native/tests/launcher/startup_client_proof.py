"""Build-only C++/Rust startup receipts and legacy Python compatibility proof.

No installed program or profile is opened. One synthetic fixture is retained
for audit; only small, fixed startup classifications are written there.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import pefile

ROOT = Path(__file__).resolve().parents[3]
NATIVE = ROOT / "native"
BUILD = Path("C:/ValMAXBuild/build/launcher-startup-interop")
INPUTS = BUILD / "inputs"
SOURCES = (
    "native/core/src/startup_ffi.rs", "native/core/src/startup_status.rs",
    "native/core/src/support_diagnostics.rs", "native/core/src/settings_io.rs",
    "native/core/src/lib.rs", "native/core/valmax_core.h", "native/Cargo.lock",
    "native/launcher/startup_client.h", "native/launcher/startup_client.cpp",
    "native/launcher/startup_client.cmake", "native/launcher/CMakeLists.txt",
    "native/tests/launcher/startup_client_test.cpp",
    "native/tests/launcher/startup_integration_test.cpp",
    "native/tests/launcher/startup_client_proof.py", "modkit/startup_status.py",
)
def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def run(args, *, timeout=60):
    r = subprocess.run([str(v) for v in args], cwd=NATIVE, capture_output=True,
        encoding="utf-8", errors="strict", timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode:
        BUILD.mkdir(parents=True, exist_ok=True)
        (BUILD / "failure.log").write_text(r.stdout + r.stderr, encoding="utf-8")
        raise RuntimeError("Owned startup fixture failed; inspect build/failure.log")
    return r

def main():
    before = {p: sha(ROOT / p) for p in SOURCES}
    INPUTS.mkdir(parents=True, exist_ok=True)
    original = Path("C:/ValMAXBuild/target/release/deps")
    pair = {name: sha(original / name) for name in ("valmax_core.dll", "valmax_core.dll.lib")}
    for name, expected in pair.items():
        shutil.copyfile(original / name, INPUTS / name)
        if sha(original / name) != expected or sha(INPUTS / name) != expected:
            raise RuntimeError("Core changed during snapshot")
    pe = pefile.PE(str(INPUTS / "valmax_core.dll"))
    assert pe.FILE_HEADER.Machine == 0x8664
    exports = sorted(s.name.decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols if s.name)
    assert "valmax_startup_status" in exports
    run(["cmake", "-S", NATIVE / "launcher", "-B", BUILD, "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release", f"-DVALMAX_STARTUP_TEST_CORE_LIBRARY={INPUTS / 'valmax_core.dll.lib'}"])
    run(["cmake", "--build", BUILD, "--target", "valmax_startup_integration_test", "valmax_startup_client_test"])
    # The fixture uses app-local runtime bits, not a Visual Studio dependency.
    runtime = ROOT / "release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod"
    shutil.copyfile(INPUTS / "valmax_core.dll", BUILD / "valmax_core.dll")
    shutil.copyfile(runtime / "vcruntime140.dll", BUILD / "vcruntime140.dll")
    synthetic = json.loads(run([BUILD / "valmax_startup_client_test.exe"]).stdout)
    assert synthetic["ok"] and not synthetic["filesystemTouched"] and not synthetic["realCoreCalled"]
    fixture = Path("C:/ValMAXBuild/fixtures/startup-client-interop") / uuid.uuid4().hex
    fixture.mkdir(parents=True)
    helper = fixture / ".mxmod-helper-temp"
    first = json.loads(run([BUILD / "valmax_startup_integration_test.exe", fixture, "native-first"]).stdout)
    assert first["ok"] and first["realCoreCalled"] and not first["realProfilesTouched"]
    sys.path.insert(0, str(ROOT / "modkit"))
    from startup_status import StartupStatus
    legacy = StartupStatus(helper, "launcher", "a" * 32)
    result = legacy.read()
    assert result == {"schema":1, "operationId":"a"*32, "role":"observe",
                      "stage":"protection", "state":"error", "code":"notification_ack_failed"}
    previous = legacy.path.read_bytes()
    assert not legacy.fail("startup_failed") and legacy.path.read_bytes() == previous
    newer = StartupStatus(helper, "launcher", "b" * 32)
    assert newer.begin("profile", claim=True)
    second = json.loads(run([BUILD / "valmax_startup_integration_test.exe", fixture, "python-first"]).stdout)
    assert second["ok"] and second["realCoreCalled"] and not second["realProfilesTouched"]
    result = newer.read()
    assert result == {"schema":1,"operationId":"b"*32,"role":"launcher",
                      "stage":"complete","state":"ready","code":"none"}
    assert legacy.read() is None
    after = {p: sha(ROOT / p) for p in SOURCES}
    if before != after:
        raise RuntimeError("Source changed during proof")
    binary = BUILD / "valmax_startup_integration_test.exe"
    image = pefile.PE(str(binary))
    imports = {entry.dll.decode(): [sym.name.decode() for sym in entry.imports if sym.name]
               for entry in image.DIRECTORY_ENTRY_IMPORT}
    assert set(name.lower() for name in imports) == {"kernel32.dll", "valmax_core.dll"}
    assert imports["valmax_core.dll"] == ["valmax_startup_status"]
    proof = {
        "schema":1,"ok":True,"scope":"direct C++ host-only receipt adapter to actual Rust DLL; legacy storage compatibility",
        "sourceSha256":before,"corePair":pair,"coreExports":exports,
        "binary":{"path":str(binary),"sha256":sha(binary),"imports":imports},
        "appLocalVC":{"path":str(BUILD / "vcruntime140.dll"),"sha256":sha(BUILD / "vcruntime140.dll")},
        "syntheticAdapter":synthetic,"nativeFirst":first,"pythonFirst":second,
        "legacyCompatibility":True,"fixture":str(fixture),"userInstallationChanged":False,
        "startupProtectionReadinessProven":False,
        "limitations":["Synthetic receipts only; no profile, credentials, launcher spawn or UI",
                       "No cross-process simultaneous Python/Rust writer-lock contention test",
                       "Receipts are optional diagnostics and never authorize startup",
                       "No installed runtime bootstrap or installer integration proven"],
    }
    path = ROOT / "analysis/native-migration-v1/startup-client-proof.json"
    path.write_text(json.dumps(proof,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"ok":True,"proof":str(path),"actualCppRustInterop":True,"legacyCompatibility":True}))

if __name__ == "__main__":
    main()
