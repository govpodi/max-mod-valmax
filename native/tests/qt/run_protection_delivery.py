"""Delayed GUI delivery regression using MAX QtCore and synthetic host APIs."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
fixture = Path("C:/ValMAXBuild/build/qt-privacy-host-q0gd5xav")
name = "valmax_protection_delivery_test.exe"
shutil.copy2(Path("C:/ValMAXBuild/build/qt-webview-bridge") / name, fixture / name)
env = {k:v for k,v in os.environ.items() if not k.startswith(("QT_", "QML", "VALMAX_NATIVE", "MAX_MOD", "MXMOD", "PYTHON"))}
env["PATH"] = str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32")
run = subprocess.run([str(fixture / name)], env=env, cwd=fixture, capture_output=True, text=True, timeout=10)
if run.returncode: raise RuntimeError(run.stderr)
proof = json.loads(run.stdout)
proof.update(fixture=str(fixture), actualMaxStarted=False, realProfilesAccessed=False,
    hashes={n:hashlib.sha256((fixture/n).read_bytes()).hexdigest() for n in (name,"Qt6Core.dll")})
(ROOT / "analysis/native-migration-v1/qt-protection-delivery-proof.json").write_text(json.dumps(proof,indent=2))
print(json.dumps(proof))
