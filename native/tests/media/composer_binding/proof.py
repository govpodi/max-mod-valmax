"""Exact-MAX QtCore/Qml synthetic host binding; no MAX, profile or network."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[4]
BUILD = Path("C:/ValMAXBuild/build/media-composer-binding")
QT = Path("C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb")
PINS = {"Qt6Core.dll": "22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa",
        "Qt6Qml.dll": "257670ffa28fc84d4ec9f818de27e2e4611d9a0cffcb657179a210931e36671b"}
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    for name, digest in PINS.items():
        if sha(QT/name) != digest: raise RuntimeError("runtime_pin_changed")
    sources = ["native/media/composer_binding.h", "native/media/composer_binding.cpp",
        "native/media/composer_binding.cmake", "native/tests/media/composer_binding/CMakeLists.txt",
        "native/tests/media/composer_binding/binding_test.cpp", "native/tests/media/composer_binding/proof.py"]
    hashes = {name: sha(ROOT/name) for name in sources}
    fixture = Path(tempfile.mkdtemp(prefix="media-composer-binding-", dir="C:/ValMAXBuild/build"))
    for file in QT.glob("*.dll"):
        if file.name.lower().startswith(("qt6", "msvcp", "vcruntime", "concrt", "api-ms-", "ucrt")):
            os.link(file, fixture/file.name)
    executable = fixture/"valmax_composer_binding_test.exe"
    shutil.copy2(BUILD/executable.name, executable)
    env = {key:value for key,value in os.environ.items() if not key.upper().startswith(("QT_", "QML", "WEBVIEW2_", "VALMAX_", "PYTHON"))}
    env.update(PATH=str(fixture)+os.pathsep+str(Path(os.environ["SystemRoot"])/"System32"), QML_DISABLE_DISK_CACHE="1")
    result = subprocess.run([str(executable)], cwd=fixture, env=env, capture_output=True, text=True,
        timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
    (fixture/"stdout.log").write_text(result.stdout)
    (fixture/"stderr.log").write_text(result.stderr)
    if result.returncode:
        print(result.stdout); print(result.stderr); raise RuntimeError("binding_proof_failed:"+str(result.returncode))
    outcome = json.loads(result.stdout)
    if outcome.get("ok") is not True: raise RuntimeError("missing_success")
    for name,digest in hashes.items():
        if sha(ROOT/name) != digest: raise RuntimeError("source_changed_during_test")
    proof = {"schema":1,"verified":True,"checks":outcome["checks"],"fixture":str(fixture),
        "qtRuntimeHashes":PINS,"sourceHashes":hashes,"testSha256":sha(executable),
        "librarySha256":sha(BUILD/"valmax_media_composer_binding.lib"),
        "actualMaxStarted":False,"realProfilesUsed":False,"networkUsed":False,
        "productionPluginLinked":False,"noPrivateOffsets":True,
        "scope":"Synthetic exact-metatype native QObject chains on actual MAX Qt; current-account live model binding not exercised"}
    output = ROOT/"analysis/native-migration-v1/media-composer-binding-proof.json"
    output.write_text(json.dumps(proof,indent=2))
    print(json.dumps({"verified":True,"checks":outcome["checks"],"proof":str(output)}))

if __name__ == "__main__": main()
