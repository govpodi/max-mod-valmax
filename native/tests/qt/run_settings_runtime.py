"""Actual settings controller -> QML adapter -> C++ -> Rust -> synthetic disk.

Uses the unchanged page controller statements from production templates;
substitutes only visual SDK dependencies and synthetic current account ID.
No MAX process, profile, credentials, network or Python runtime is involved.
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
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT / "native/tooling"))
import build_native_qml

PAGES = (("optimization_panel.qml", "OptimizationPage.qml", "optimization", "optimization-settings"),
         ("ghost_panel.qml", "GhostPage.qml", "ghost", "ghost-settings"),
         ("account_preferences_panel.qml", "AccountsPage.qml", "accounts", "account-settings"))


def controller(source: str) -> str:
    body = source.split("    SettingsBaseItem {\n", 1)[1].split("        component ", 1)[0]
    lines = []
    for line in body.splitlines():
        if "property var selfComponent:" in line or line.strip().startswith(("title:", "onBackClicked:")):
            continue
        if "readonly property real gap:" in line or "readonly property real sectionGap:" in line or "readonly property real radius:" in line:
            line = line.split(":", 1)[0] + ": 8"
        if "property string selectedAccountId:" in line:
            line = '        property string selectedAccountId: "101"'
        if "property string selectedNativeName:" in line:
            line = '        property string selectedNativeName: "Synthetic account"'
        lines.append(line)
    return ('import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\nimport "./OptimizationConfig" as ValMAXOptimization\nItem {\n'
            + "\n".join(lines) + "\n}\n")


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True, help="Previously verified copied MAX Qt fixture")
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--rust-dll", type=Path, required=True)
    parser.add_argument("--ghost-dll", type=Path, help="Optional production DLL; this non-MAX host must remain unavailable")
    args = parser.parse_args()
    fixture = Path(tempfile.mkdtemp(prefix="qt-settings-", dir="C:/ValMAXBuild/build"))
    shutil.copytree(args.fixture, fixture, dirs_exist_ok=True)
    module = fixture / "qml/ValMAX/Native"
    for name in ("valmaxnativeplugin.dll", "qmldir", "SettingsTransport.qml", "ChatLockTransport.qml", "SupportTransport.qml"):
        shutil.copy2(args.build / "qml/ValMAX/Native" / name, module / name)
    shutil.copy2(args.rust_dll, fixture / "valmax_core.dll")
    if args.ghost_dll:
        shutil.copy2(args.ghost_dll, fixture / "valmax_ghost.dll")
    hashes = {}
    for template, target, family, endpoint in PAGES:
        original = (PROJECT / "modkit" / template).read_text(encoding="utf8").replace("\r\n", "\n")
        native = build_native_qml.native_settings_panel(original, family, endpoint)
        (fixture / target).write_text(controller(native), encoding="utf8")
        hashes[template] = hashlib.sha256(original.encode()).hexdigest()
    settings = fixture / "synthetic-profile"
    runtime = fixture / "synthetic-runtime"
    settings.mkdir(); runtime.mkdir()
    config_dir = fixture / "OptimizationConfig"
    config_dir.mkdir()
    (config_dir / "qmldir").write_text("singleton OptimizationConfig 1.0 OptimizationConfig.qml\n")
    flags = ["enabled", "smoothWheel", "listScheduling", "listPrefetch", "readyCache", "historyCache", "historyBookkeeping", "photoDecodeResize", "windowPosition", "hiddenPageWork", "profileRows"]
    env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MAX_MOD", "MXMOD", "VALMAX_NATIVE"))}
    env.update({"PATH": str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
                "QT_QPA_PLATFORM": "windows", "QT_QUICK_BACKEND": "software", "QT_DEBUG_PLUGINS": "1",
                "QML_DISABLE_DISK_CACHE": "1", "QML_IMPORT_PATH": str(fixture / "qml"),
                "QML2_IMPORT_PATH": str(fixture / "qml"), "TEMP": str(fixture), "TMP": str(fixture)})
    cases = []
    for phase in ("write", "existing", "restart"):
        values = {flag: phase != "restart" or flag not in ("enabled", "smoothWheel") for flag in flags}
        (config_dir / "OptimizationConfig.qml").write_text('pragma Singleton\nimport QtQml\nQtObject { property bool loaded: true; property string failureCode: ""; function snapshot() { return '
            + json.dumps(values) + '; } }\n', encoding="utf8")
        qml = fixture / "settings_pages.qml"
        qml.write_text(Path(__file__).with_name("settings_pages.qml").read_text(encoding="utf8").replace("@PHASE@", phase), encoding="utf8")
        config = {"profileDirectory": str(settings), "runtimeDirectory": str(runtime), "existingClient": phase == "existing"}
        run = subprocess.run([str(fixture / "valmax_qml_bridge_test.exe"), str(fixture / "qml"), str(qml)], cwd=fixture,
            env={**env, "VALMAX_NATIVE_RUNTIME_CONFIG": json.dumps(config)}, capture_output=True, text=True, timeout=15)
        (fixture / (phase + ".stderr.log")).write_text(run.stderr, encoding="utf8")
        if run.returncode:
            print(run.stdout); print(run.stderr[-12000:])
        assert run.returncode == 0, (phase, run.returncode)
        result = json.loads(run.stdout); result["phase"] = phase
        assert result["ok"] and result["checks"] >= 9
        cases.append(result)
        print(json.dumps({"phase": phase, "ok": True, "checks": result["checks"]}))
    for name in ("optimization-settings.json", "ghost-settings.json", "account-settings.json"):
        assert (settings / name).is_file()
    negative = []
    for label, configured, state in (("missing_host_configuration", None, "unconfigured"),
            ("malformed_host_configuration", "{", "error"),
            ("relative_host_paths", json.dumps({"profileDirectory": "relative-profile", "runtimeDirectory": "relative-runtime", "existingClient": False}), "error")):
        qml = fixture / "settings_unavailable.qml"
        qml.write_text(Path(__file__).with_name("settings_unavailable.qml").read_text(encoding="utf8").replace("@STATE@", state), encoding="utf8")
        case_env = dict(env)
        if configured is not None:
            case_env["VALMAX_NATIVE_RUNTIME_CONFIG"] = configured
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in settings.iterdir() if path.is_file()}
        run = subprocess.run([str(fixture / "valmax_qml_bridge_test.exe"), str(fixture / "qml"), str(qml)],
            cwd=fixture, env=case_env, capture_output=True, text=True, timeout=15)
        (fixture / (label + ".stderr.log")).write_text(run.stderr, encoding="utf8")
        if run.returncode:
            print(run.stdout); print(run.stderr[-5000:])
        assert run.returncode == 0, label
        result = json.loads(run.stdout)
        assert result["ok"] and result["checks"] == 6
        assert before == {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in settings.iterdir() if path.is_file()}
        assert not (fixture / "relative-profile").exists() and not (fixture / "relative-runtime").exists()
        negative.append({"case": label, "rejected": True, "checks": result["checks"], "storesUnchanged": True})
        print(json.dumps(negative[-1]))
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "tests": cases,
             "actualPageControllerTemplateHashes": hashes, "syntheticAccountOnly": True,
             "negativeControls": negative,
             "legacyTemplatesModified": False, "noHttpTransport": True, "fullVisualSdkTest": False,
             "pluginSha256": hashlib.sha256((module / "valmaxnativeplugin.dll").read_bytes()).hexdigest(),
             "rustSha256": hashlib.sha256((fixture / "valmax_core.dll").read_bytes()).hexdigest()}
    proof_name = "qt-settings-runtime-proof.json"
    if args.ghost_dll:
        proof["ghostSha256"] = hashlib.sha256((fixture / "valmax_ghost.dll").read_bytes()).hexdigest()
        proof["unsupportedHookHostRemainsUnavailable"] = True
        proof_name = "qt-ghost-host-negative-proof.json"
    (PROJECT / "analysis/native-migration-v1" / proof_name).write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
