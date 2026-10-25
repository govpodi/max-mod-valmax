"""Own ordinary Qt model cursor fixture. No MAX, profiles, network or private hooks."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.build.is_absolute() or not args.build.resolve().is_relative_to(Path("D:/ValMAXBuild/build")):
        raise ValueError("explicit owned D build required")
    if args.report.exists() or args.report.parent.resolve() != (ROOT / "analysis/native-migration-v1").resolve():
        raise ValueError("new local proof required")
    sources = ["native/qt/history_model_cursor.h", "native/qt/history_model_cursor.cpp",
        "native/tests/qt/history_model_cursor_test.cpp", "native/tests/qt/history_model_cursor_fixture/CMakeLists.txt",
        "native/tests/qt/run_history_model_cursor.py"]
    hashes = {name: sha(ROOT / name) for name in sources}
    args.build.mkdir(parents=True, exist_ok=True)
    for label, command in (
        ("configure", ["cmake", "-S", str(ROOT / "native/tests/qt/history_model_cursor_fixture"), "-B", str(args.build),
            "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt"]),
        ("build", ["cmake", "--build", str(args.build), "--parallel", "1"]),
    ):
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180,
            creationflags=subprocess.CREATE_NO_WINDOW)
        (args.build / (label + ".log")).write_text(result.stdout + result.stderr, "utf8")
        if result.returncode:
            raise RuntimeError(f"{label} failed; see owned build log")
    env = dict(os.environ)
    env.update(PATH="C:/ValMAXBuild/Qt/bin" + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
        QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH="C:/ValMAXBuild/Qt/qml", QML2_IMPORT_PATH="C:/ValMAXBuild/Qt/qml")
    for name in ("home", "local", "roaming", "temp"):
        (args.build / name).mkdir(exist_ok=True)
    env.update(USERPROFILE=str(args.build / "home"), LOCALAPPDATA=str(args.build / "local"),
        APPDATA=str(args.build / "roaming"), TEMP=str(args.build / "temp"), TMP=str(args.build / "temp"))
    exe = args.build / "valmax_history_model_cursor_test.exe"
    result = subprocess.run([str(exe)], cwd=args.build, env=env, capture_output=True, text=True,
        timeout=40, creationflags=subprocess.CREATE_NO_WINDOW)
    (args.build / "run.log").write_text(result.stdout + result.stderr, "utf8")
    if result.returncode:
        raise RuntimeError(f"ordinary model fixture failed ({result.returncode}); see owned build run.log")
    value = json.loads(result.stdout)
    assert value["ok"] and value["qmlConversionChecks"] == 10
    assert value["benchmark"]["cursorRoleReads"] == 10000 and value["benchmark"]["prefixRescanRoleReads"] == 255000
    assert hashes == {name: sha(ROOT / name) for name in sources}, "source drift"
    proof = {"schema": 1, "verified": True, "result": value, "sourceHashes": hashes,
        "executable": {"path": str(exe), "sha256": sha(exe)}, "build": str(args.build),
        "scope": "Ordinary QAbstractItemModel root rows, interval work queue and <=200 persistent lease indices; actual Qt/QML QObject and Q_GADGET conversion. Synthetic values only, no EOF/private hook/production MAX or history completeness proof."}
    with args.report.open("x", encoding="utf8") as stream:
        json.dump(proof, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"verified": True, "checks": value["checks"], "qmlConversionChecks": 10,
        "cursorRoleReads": 10000, "prefixRescanRoleReads": 255000, "report": str(args.report)}))


if __name__ == "__main__":
    main()
