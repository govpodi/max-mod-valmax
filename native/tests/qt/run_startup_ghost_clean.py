"""Exercise protection hosts with packaged DLLs and clean Windows user dirs.

Only synthetic in-EXE host ABIs and temporary Rust profiles are used. This
does not start MAX or establish protection before native background activity.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[3]
QT = Path("C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb")
BUILD_ROOT = Path("C:/ValMAXBuild/build")
QT_CORE_SHA256 = "22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if os.name != "nt":
        raise SystemExit("This packaged native startup fixture requires Windows")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qt-build", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--include-notification", action="store_true")
    args = parser.parse_args()
    executables = [args.qt_build / "valmax_ghost_host_test.exe"]
    if args.include_notification:
        executables.append(args.qt_build / "valmax_notification_host_test.exe")
    if any(not item.is_file() for item in executables) or not args.core.is_file() \
            or sha(QT / "Qt6Core.dll") != QT_CORE_SHA256:
        raise RuntimeError("Pinned test executables, Rust core, or QtCore are unavailable")
    if args.report.parent.resolve() != (ROOT / "analysis/native-migration-v1").resolve() or args.report.exists():
        raise RuntimeError("A new proof path under analysis/native-migration-v1 is required")

    fixture = Path(tempfile.mkdtemp(prefix="startup-hosts-clean windows-", dir=BUILD_ROOT))
    for source in QT.glob("*.dll"):
        if source.name == "Qt6Core.dll" or source.name.lower().startswith(
            ("msvcp", "vcruntime", "concrt", "api-ms-", "ucrt")
        ):
            shutil.copy2(source, fixture / source.name)
    for executable in executables:
        shutil.copy2(executable, fixture / executable.name)
    shutil.copy2(args.core, fixture / "valmax_core.dll")
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir()

    system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT"}}
    env.update(PATH=str(fixture) + os.pathsep + str(system_root / "System32"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"),
               APPDATA=str(fixture / "roaming"), TEMP=str(fixture / "temp"),
               TMP=str(fixture / "temp"))
    previous_error_mode = ctypes.windll.kernel32.SetErrorMode(0x8003)
    try:
        results = {}
        for executable in executables:
            synthetic = fixture / (executable.stem + "-synthetic")
            synthetic.mkdir()
            run = subprocess.run([str(fixture / executable.name), str(synthetic)],
                                 cwd=fixture, env=env, capture_output=True, text=True,
                                 timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            if run.returncode:
                raise RuntimeError(f"{executable.name} failed (exit {run.returncode}): {run.stderr[-1500:]}")
            result = json.loads(run.stdout)
            if result.get("ok") is not True or result.get("syntheticHookABI") is not True \
                    or result.get("realRustRuntime") is not True:
                raise RuntimeError(f"{executable.name} success receipt is incomplete")
            results[executable.name] = result["checks"]
    finally:
        ctypes.windll.kernel32.SetErrorMode(previous_error_mode)
    sources = [ROOT / "native/qt/ghost_host.cpp", ROOT / "native/qt/ghost_host.h",
               ROOT / "native/tests/qt/ghost_host_test.cpp", Path(__file__)]
    if args.include_notification:
        sources.extend((ROOT / "native/qt/notification_host.cpp",
                        ROOT / "native/qt/notification_host.h",
                        ROOT / "native/tests/qt/notification_host_test.cpp"))
    proof = {
        "schema": 1, "verified": True, "checks": sum(results.values()),
        "hostChecks": results,
        "fixture": str(fixture), "cleanUserDirectories": True,
        "inheritedQtQmlPythonValmaxMxmodVariables": False,
        "hostExecutableSha256": {item.name: sha(fixture / item.name) for item in executables},
        "qtCoreSha256": sha(fixture / "Qt6Core.dll"),
        "rustCoreSha256": sha(fixture / "valmax_core.dll"),
        "sourceHashes": {str(path.relative_to(ROOT)).replace("\\", "/"): sha(path)
                         for path in sources},
        "actualMaxStarted": False, "realProfileAccessed": False,
        "installedClientChanged": False, "nativeBackgroundStartupProven": False,
        "scope": "Packaged QtCore/Rust protection host fixtures in isolated Windows user directories; synthetic in-EXE host ABIs only",
    }
    args.report.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", "utf8")
    print(json.dumps({"verified": True, "checks": proof["checks"], "report": str(args.report)}))


if __name__ == "__main__":
    main()
