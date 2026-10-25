"""Synthetic verifier ABI with the actual MAX QtCore runtime, no MAX launch."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

PROJECT = Path(__file__).resolve().parents[3]
ctypes.windll.kernel32.SetErrorMode(0x8003)
fixture = Path(tempfile.mkdtemp(prefix="qt-privacy-host-", dir="C:/ValMAXBuild/build"))
source = Path("D:/ValMAX/release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod")
for dll in source.glob("*.dll"):
    if dll.name == "Qt6Core.dll" or dll.name.lower().startswith(("msvcp", "vcruntime", "concrt", "api-ms-", "ucrt")):
        shutil.copy2(dll, fixture / dll.name)
shutil.copy2("C:/ValMAXBuild/build/qt-privacy-bridge/valmax_privacy_host_test.exe", fixture / "valmax_privacy_host_test.exe")
env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MXMOD", "MAX_MOD", "VALMAX_NATIVE"))}
env["PATH"] = str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32")
run = subprocess.run([str(fixture / "valmax_privacy_host_test.exe")], cwd=fixture, env=env, timeout=25, capture_output=True, text=True)
if run.returncode:
    print(run.stderr)
    raise RuntimeError("privacy_host_test_failed")
proof = json.loads(run.stdout)
shutil.copy2("C:/ValMAXBuild/build/qt-privacy-bridge/valmax_privacy_delivery_test.exe", fixture / "valmax_privacy_delivery_test.exe")
delivery = subprocess.run([str(fixture / "valmax_privacy_delivery_test.exe")], cwd=fixture, env=env, timeout=15, capture_output=True, text=True)
if delivery.returncode:
    print(delivery.stderr)
    raise RuntimeError("privacy_delivery_test_failed")
proof["delivery"] = json.loads(delivery.stdout)
proof.update(fixture=str(fixture), hashes={name: hashlib.sha256((fixture / name).read_bytes()).hexdigest()
    for name in ("valmax_privacy_host_test.exe", "valmax_privacy_delivery_test.exe", "Qt6Core.dll")},
    actualMaxStarted=False, realProfileAccessed=False, productionVerifierMocked=True)
(PROJECT / "analysis/native-migration-v1/qt-privacy-host-proof.json").write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
