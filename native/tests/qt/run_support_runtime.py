"""Actual support controller -> direct bridge -> Rust on synthetic files only.

The SDK visuals and SaveFile dialog are replaced by a test item. Controller
request/completion/watchdog code is extracted unchanged from the native page.
Separate fake-bridge tests exercise queue/abort/malformed-response paths. No
MAX process or user profile is opened; no upload or HTTP request is performed.
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


def controller(source):
    body = source.split("    SettingsBaseItem {\n", 1)[1].split("        component SupportText:", 1)[0]
    body = body[:body.index("        Dialogs.FileDialog {")]
    lines = []
    for line in body.splitlines():
        if "property var selfComponent:" in line or line.strip().startswith(("title:", "onBackClicked:")):
            continue
        if any("readonly property real " + name + ":" in line for name in ("gap", "sectionGap", "groupRadius")):
            line = line.split(":", 1)[0] + ": 8"
        lines.append(line)
    return "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\nItem {\n" + "\n".join(lines) + "\n}\n"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ctypes.windll.kernel32.SetErrorMode(0x8003)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--rust-dll", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=PROJECT / "analysis/native-migration-v1/support-qt-proof.json")
    args = parser.parse_args()
    test_root = Path(os.environ.get("VALMAX_TEST_TEMP_ROOT", tempfile.gettempdir()))
    test_root.mkdir(parents=True, exist_ok=True)
    fixture = Path(tempfile.mkdtemp(prefix="qt-support-", dir=test_root))
    shutil.copytree(args.fixture, fixture, dirs_exist_ok=True)
    module = fixture / "qml/ValMAX/Native"
    shutil.copytree(args.build / "qml/ValMAX/Native", module, dirs_exist_ok=True)
    shutil.copy2(args.build / "valmax_qml_bridge_test.exe", fixture / "valmax_qml_bridge_test.exe")
    shutil.copy2(args.rust_dll, fixture / "valmax_core.dll")
    source = (PROJECT / "modkit/support_panel.qml").read_text(encoding="utf-8").replace("\r\n", "\n")
    native = build_native_qml.native_support_panel(source)
    actual = controller(native)
    assert "XMLHttpRequest" not in actual and "http://" not in actual
    assert 'onAccepted: supportPage.request("POST", selectedFile.toString())' in native
    (fixture / "SupportPage.qml").write_text(actual, encoding="utf-8")
    settings, runtime, reports = [fixture / name for name in ("support-profile", "support-runtime", "support-reports")]
    for directory in (settings, runtime, reports):
        directory.mkdir()
    report = reports / "synthetic-report.json"
    env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MAX_MOD", "MXMOD", "VALMAX_NATIVE"))}
    env.update({"PATH": str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
                "QT_QPA_PLATFORM": "windows", "QT_QUICK_BACKEND": "software", "QML_DISABLE_DISK_CACHE": "1",
                "QML_IMPORT_PATH": str(fixture / "qml"), "QML2_IMPORT_PATH": str(fixture / "qml"), "TEMP": str(fixture), "TMP": str(fixture)})
    config = json.dumps({"profileDirectory": str(settings), "runtimeDirectory": str(runtime), "existingClient": False})
    cases = []

    def run(label, qml, import_root, configured):
        case_env = {**env, "QML_IMPORT_PATH": str(import_root), "QML2_IMPORT_PATH": str(import_root)}
        if configured:
            case_env["VALMAX_NATIVE_RUNTIME_CONFIG"] = config
        result = subprocess.run([str(fixture / "valmax_qml_bridge_test.exe"), str(import_root), str(qml)],
                                cwd=fixture, env=case_env, capture_output=True, text=True, timeout=20)
        (fixture / (label + ".stderr.log")).write_text(result.stderr, encoding="utf-8")
        if result.returncode:
            print(result.stdout); print(result.stderr[-12000:])
        assert result.returncode == 0, (label, result.returncode)
        value = json.loads(result.stdout)
        assert value["ok"] and value["checks"] > 0
        value["phase"] = label
        cases.append(value)
        print(json.dumps({"phase": label, "checks": value["checks"], "ok": True}))

    for phase in ("preview", "export", "unconfigured"):
        qml = fixture / "support_pages.qml"
        qml.write_text(Path(__file__).with_name("support_pages.qml").read_text(encoding="utf-8")
                       .replace("@PHASE@", phase).replace("@OUTPUT_URL@", report.as_uri()), encoding="utf-8")
        before = sha(report) if report.exists() else None
        run(phase, qml, fixture / "qml", phase != "unconfigured")
        if phase == "preview":
            assert not report.exists() and not list(reports.iterdir()), "preview must not publish files"
            assert not any(path.is_file() for path in settings.rglob("*")), "preview must not create settings files"
        elif phase == "export":
            value = json.loads(report.read_text(encoding="utf-8"))
            assert value["schema"] == 2 and value["runtime"]["kind"] == "native"
            assert value["flags"]["optimization"]["smoothWheel"] is False, "fresh export differs from earlier preview"
            settings_value = json.loads((settings / "optimization-settings.json").read_text(encoding="utf-8"))
            assert settings_value["settings"]["smoothWheel"] is True, "rejected overwrite must not change existing report"
            assert len(list(reports.iterdir())) == 1
        else:
            assert sha(report) == before
    fake_root = fixture / "fake-qml"
    fake = fake_root / "ValMAX/Native"
    fake.mkdir(parents=True)
    (fake / "qmldir").write_text("module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\nsingleton SupportTransport 1.0 SupportTransport.qml\n")
    shutil.copy2(PROJECT / "native/qt/SupportTransport.qml", fake / "SupportTransport.qml")
    shutil.copy2(Path(__file__).with_name("support_fake_bridge.qml"), fake / "Bridge.qml")
    fake_qml = fixture / "support_transport.qml"
    shutil.copy2(Path(__file__).with_name("support_transport.qml"), fake_qml)
    run("transport_faults", fake_qml, fake_root, False)
    files = ["native/qt/SupportTransport.qml", "native/qt/qmldir", "native/qt/CMakeLists.txt",
             "native/tooling/build_native_qml.py", "native/tests/qt/run_support_runtime.py",
             "native/tests/qt/support_pages.qml", "native/tests/qt/support_transport.qml", "native/tests/qt/support_fake_bridge.qml",
             "modkit/support_panel.qml"]
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "tests": cases,
             "sourceHashes": {name: sha(PROJECT / name) for name in files},
             "pluginSha256": sha(module / "valmaxnativeplugin.dll"), "rustSha256": sha(fixture / "valmax_core.dll"),
             "syntheticOnly": True, "noHttpTransport": True, "previewWritesReport": False,
             "exportUsesFreshSnapshot": True, "existingReportPreserved": True,
             "rawPrivacySnapshotForwarded": False, "legacyTemplateModified": False,
             "fullVisualSdkTest": False, "actualUserAppTest": False,
             "limits": ["SaveFile dialog/SDK visuals replaced; real controller and transport tested", "abort suppresses delivery; accepted export may still finish", "only known client application version copied; unknown host versions/startup/counters remain null"]}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
