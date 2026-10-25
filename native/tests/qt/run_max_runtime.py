"""Build-time synthetic probe using copied MAX Qt binaries, never PySide Qt.

Python is test tooling only. The tested runtime is a C++ EXE, a C++ QML plugin,
MAX's exact Qt DLLs and QML. This does not launch MAX or open a real profile.
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


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    # Never leave Windows loader error dialogs on the user's desktop.
    ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-runtime", required=True, type=Path)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--proof", required=True, type=Path)
    parser.add_argument("--rust-dll", type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[3]
    args.scratch.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="qt-max-runtime-", dir=args.scratch))
    app = fixture / "valmax_qml_bridge_test.exe"
    candidates = [args.build / "valmax_qml_bridge_test.exe", args.build / "Release/valmax_qml_bridge_test.exe"]
    built = next(path for path in candidates if path.is_file())
    shutil.copy2(built, app)
    copied = {}
    for path in args.max_runtime.glob("*.dll"):
        # Qt DLLs plus the matching MSVC/UCRT dependencies. No MAX components,
        # database, profile, credentials, session files or executable copied.
        if path.name.lower().startswith(("qt6", "msvcp", "vcruntime", "concrt", "api-ms-", "ucrt")):
            destination = fixture / path.name
            shutil.copy2(path, destination)
            assert digest(destination) == digest(path)
            copied[path.name] = digest(path)
    shutil.copytree(args.max_runtime / "plugins", fixture / "plugins")
    shutil.copytree(args.max_runtime / "qml", fixture / "qml")
    native_dest = fixture / "qml/ValMAX/Native"
    native_dest.mkdir(parents=True, exist_ok=True)
    for name in ("valmaxnativeplugin.dll", "qmldir", "SettingsTransport.qml", "ChatLockTransport.qml"):
        shutil.copy2(args.build / "qml/ValMAX/Native" / name, native_dest / name)
    if args.rust_dll:
        shutil.copy2(args.rust_dll, fixture / "valmax_core.dll")
    (fixture / "qt.conf").write_text("[Paths]\nPrefix=.\nPlugins=plugins\nQmlImports=qml\n", encoding="utf8")
    rows_dir = fixture / "RuntimeRows"
    rows_dir.mkdir()
    shutil.copy2(source / "modkit/ValMAXAsyncListRow.qml", rows_dir / "ValMAXAsyncListRow.qml")
    shutil.copy2(source / "native/qt/ValMAXListWorkQueue.qml", rows_dir / "ValMAXListWorkQueue.qml")
    appearance = fixture / "qml/ClientAppearanceModule"
    appearance.mkdir(exist_ok=True)
    (appearance / "qmldir").write_text("module ClientAppearanceModule\nsingleton AppearanceTokens 1.0 AppearanceTokens.qml\n", encoding="utf8")
    (appearance / "AppearanceTokens.qml").write_text(
        "pragma Singleton\nimport QtQuick\nQtObject { property color colorStatesBackgroundCardSelectedPressed: '#333333'; "
        "property color colorBackgroundPrimary: '#111111'; property color colorIconSecondary: '#888888' }\n", encoding="utf8")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MAX_MOD", "MXMOD"))}
    env.update({"PATH": str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
                "QT_QPA_PLATFORM": "windows", "QT_QUICK_BACKEND": "software", "QT_DEBUG_PLUGINS": "1",
                "QML_DISABLE_DISK_CACHE": "1", "QML2_IMPORT_PATH": str(fixture / "qml"),
                "QML_IMPORT_PATH": str(fixture / "qml"), "TEMP": str(fixture), "TMP": str(fixture)})
    results = []
    cases = ["bridge.qml", "loader_admission.qml"]
    if args.rust_dll:
        cases.append("rust_services.qml")
    for name in cases:
        test = fixture / name
        shutil.copy2(Path(__file__).with_name(name), test)
        run = subprocess.run([str(app), str(fixture / "qml"), str(test)], cwd=fixture,
                             env=env, timeout=20, capture_output=True, text=True)
        print(run.stdout, end="")
        (fixture / (name + ".stderr.log")).write_text(run.stderr, encoding="utf8")
        if run.returncode:
            print(run.stderr[-6000:])
        assert run.returncode == 0, (name, run.returncode)
        result = json.loads(run.stdout.strip())
        assert result["ok"] and result["qtVersion"] == "6.11.2" and result["checks"] >= 20
        for key, dll in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
            if key == "loadedQtQuick" and name != "loader_admission.qml":
                continue
            assert Path(result[key]).resolve() == (fixture / dll).resolve()
            assert digest(Path(result[key])) == digest(args.max_runtime / dll)
        assert Path(result["loadedNativePlugin"]).resolve() == (native_dest / "valmaxnativeplugin.dll").resolve()
        result["test"] = name
        results.append(result)
    # Negative controls prove both that the plugin is used and that Loader
    # creation depends on the real native admission queue. Mutate this owned
    # fixture only and restore it before returning its final evidence.
    negative = []
    plugin = native_dest / "valmaxnativeplugin.dll"
    disabled = native_dest / "valmaxnativeplugin.disabled"
    plugin.rename(disabled)
    try:
        run = subprocess.run([str(app), str(fixture / "qml"), str(fixture / "bridge.qml")],
                             cwd=fixture, env=env, timeout=15, capture_output=True, text=True)
        assert run.returncode == 3 and "plugin" in run.stderr.lower()
        negative.append({"case": "missing_native_plugin", "exit": run.returncode, "rejected": True})
    finally:
        disabled.rename(plugin)
    adapter = rows_dir / "ValMAXListWorkQueue.qml"
    original_adapter = adapter.read_bytes()
    try:
        adapter.write_bytes(original_adapter.replace(b"viewActive: root.completed && !root.closing && root.viewActive", b"viewActive: false"))
        run = subprocess.run([str(app), str(fixture / "qml"), str(fixture / "loader_admission.qml")],
                             cwd=fixture, env=env, timeout=15, capture_output=True, text=True)
        assert run.returncode == 1 and json.loads(run.stdout)["error"] == "actual visible Loader ready"
        negative.append({"case": "disabled_native_admission", "exit": run.returncode, "rejected": True})
    finally:
        adapter.write_bytes(original_adapter)
    run = subprocess.run([str(app), str(fixture / "qml"), str(fixture / "bridge.qml")],
                         cwd=fixture, env={**env, "QT_QPA_PLATFORM": "valmax-missing-test-platform"},
                         timeout=15, capture_output=True, text=True)
    assert run.returncode == 6 and "platform plugin" in run.stderr.lower()
    negative.append({"case": "missing_platform_no_dialog", "exit": run.returncode, "rejected": True})
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "runtimeSource": str(args.max_runtime),
             "maxStarted": False, "profileAccessed": False, "pythonRuntimeRequired": False,
             "qtDllHashes": copied, "pluginSha256": digest(native_dest / "valmaxnativeplugin.dll"),
             "originalAsyncListRowSha256": digest(rows_dir / "ValMAXAsyncListRow.qml"), "tests": results,
             "negativeControls": negative}
    if args.rust_dll:
        proof["rustCoreSha256"] = digest(fixture / "valmax_core.dll")
    args.proof.parent.mkdir(parents=True, exist_ok=True)
    args.proof.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
