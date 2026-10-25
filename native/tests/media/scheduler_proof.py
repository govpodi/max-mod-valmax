"""Build/run only the synthetic QtCore scheduler executable in an owned build."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import pefile

ROOT = Path(__file__).resolve().parents[3]
BUILD = Path("C:/ValMAXBuild/build/media-scheduler")
OUT = Path("C:/ValMAXBuild/fixtures/media-scheduler")
RUNTIME = ROOT / "release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod"
FILES = ["native/media/scheduler.h", "native/media/scheduler.cpp", "native/media/scheduler.cmake",
         "native/tests/media/scheduler_test.cpp", "native/tests/media/scheduler_proof.py",
         "native/media/protocol.h", "native/media/protocol.cpp", "native/media/html_entities.inc",
         "native/media/CMakeLists.txt"]
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
before = {name: digest(ROOT / name) for name in FILES}
OUT.mkdir(parents=True, exist_ok=True)
command = ". D:/ValMAX/native/tooling/native_env.ps1\ncmake --build C:/ValMAXBuild/build/media-scheduler --target valmax_media_scheduler_test\nexit $LASTEXITCODE"
build = subprocess.run(["powershell.exe", "-NoProfile", "-Command", command], capture_output=True, text=True,
                       timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
(OUT / "build.log").write_text(build.stdout + build.stderr, encoding="utf-8")
if build.returncode:
    raise SystemExit("synthetic scheduler build failed")
runtime_hashes = {}
for name in ("Qt6Core.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
             "msvcp140_atomic_wait.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
    source, destination = RUNTIME / name, BUILD / name
    destination.write_bytes(source.read_bytes())
    runtime_hashes[name] = digest(destination)
    assert runtime_hashes[name] == digest(source)
binary = BUILD / "valmax_media_scheduler_test.exe"
pe = pefile.PE(str(binary), fast_load=True)
pe.parse_data_directories(directories=[1, 13])
imports = {kind: sorted(e.dll.decode("ascii") for e in getattr(pe, attr, []))
           for kind, attr in (("normal", "DIRECTORY_ENTRY_IMPORT"), ("delay", "DIRECTORY_ENTRY_DELAY_IMPORT"))}
pe.close()
assert all("network" not in name.lower() and "winhttp" not in name.lower() and "wininet" not in name.lower()
           for names in imports.values() for name in names)
env = dict(os.environ)
for key in list(env):
    if key.upper().startswith(("QT_", "QML_", "WEBVIEW2_", "VALMAX_")):
        env.pop(key)
run = subprocess.run([str(binary)], cwd=BUILD, env=env, capture_output=True, text=True,
                     timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
(OUT / "run.log").write_text(run.stdout + run.stderr, encoding="utf-8")
if run.returncode:
    raise SystemExit("synthetic scheduler regression failed")
result = json.loads(run.stdout)
assert result["ok"] and result["checks"] == 400
assert before == {name: digest(ROOT / name) for name in FILES}, "sources changed during proof"
proof = {"schema": 1, "verified": True, "scope": "QtCore scheduler only; synthetic jobs, fake clocks and one real 5s queue timer",
         "sourceSha256": before, "binary": {"path": str(binary), "sha256": digest(binary), "imports": imports},
         "runtimeSha256": runtime_hashes, "result": result,
         "buildLog": {"path": str(OUT / "build.log"), "sha256": digest(OUT / "build.log")},
         "runLog": {"path": str(OUT / "run.log"), "sha256": digest(OUT / "run.log")},
         "limits": {"active": 2, "waiting": 64, "outstandingIncludingPendingCallbacks": 66, "lanes": 32, "queueSeconds": 5},
         "covered": ["foreground FIFO before previews", "bounded admission and 5s expiry", "full provider cooldown, >day retry advertisement omitted",
                     "active cancellation retains slot until transport acknowledgement", "stale generation and close/shutdown after complete before publication",
                     "complete/cancel/shutdown reentrancy", "delete-this from all callback types", "wrong-thread rejection",
                     "drain waits for all active acknowledgements", "callback exception does not release a live slot", "long cooldown does not overflow QTimer"],
         "networkTransportImplemented": False, "realHttpTested": False, "qmlIntegrated": False, "userProfilesTouched": False,
         "limitations": ["No QNetworkReply, actual provider response, HTTP, disk staging or UI in this step",
                         "Destructor silently detaches callbacks; external transport must own reply cancellation and use QPointer",
                         "Shutdown drain needs complete from every stopped reply; no premature drain is reported if a transport fails to acknowledge",
                         "Original-file serialization/download_busy belongs to the higher service and is not provided by max2 provider scheduling",
                         "Clock callbacks are trusted pure non-reentrant injection points; foreign-thread destruction and invalid pointers are outside QObject ownership contract",
                         "Synthetic filesUsed=false means no application file operations by the tests, not absence of operating-system DLL loads"]}
path = ROOT / "analysis/native-migration-v1/media-scheduler-proof.json"
path.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"verified": True, "checks": result["checks"], "proof": str(path), "sha256": digest(path)}))
