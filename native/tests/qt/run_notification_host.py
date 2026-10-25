"""Real Rust + Qt C++ NotificationHost with synthetic hooks in a test EXE."""
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
fixture = Path(tempfile.mkdtemp(prefix="qt-notification-host-", dir="C:/ValMAXBuild/build"))
source = Path("D:/ValMAX/release/valmax-v8.8.4-20261016/build/dist/ValMAX/runtime/mod")
for dll in source.glob("*.dll"):
    if dll.name == "Qt6Core.dll" or dll.name.lower().startswith(("msvcp", "vcruntime", "concrt", "api-ms-", "ucrt")):
        shutil.copy2(dll, fixture / dll.name)
shutil.copy2("C:/ValMAXBuild/build/qt-bridge/valmax_notification_host_test.exe", fixture / "valmax_notification_host_test.exe")
shutil.copy2("C:/ValMAXBuild/target/release/valmax_core.dll", fixture / "valmax_core.dll")
env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MXMOD", "MAX_MOD", "VALMAX_NATIVE"))}
env["PATH"] = str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32")
run = subprocess.run([str(fixture / "valmax_notification_host_test.exe"), str(fixture / "synthetic")],
    cwd=fixture, env=env, timeout=25, capture_output=True, text=True)
if run.returncode:
    print(run.stderr)
if run.returncode != 0:
    raise RuntimeError("notification_host_test_failed")
proof = json.loads(run.stdout)
proof.update(fixture=str(fixture), hashes={name: hashlib.sha256((fixture / name).read_bytes()).hexdigest()
    for name in ("valmax_notification_host_test.exe", "Qt6Core.dll", "valmax_core.dll")},
    actualMaxStarted=False, realProfileAccessed=False, productionHooksMocked=True)
(PROJECT / "analysis/native-migration-v1/qt-notification-host-lifecycle-proof.json").write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
