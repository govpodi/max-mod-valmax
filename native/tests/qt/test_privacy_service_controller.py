"""Exercise the privacy page's service proof truth table with synthetic QML data.

This starts only Qt's local runner, not MAX or a user profile.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[3]


def controller_source() -> str:
    panel = (ROOT / "native/qt/PrivacyPanel.qml").read_text(encoding="utf8")
    body = panel.split("    SettingsBaseItem {\n", 1)[1].split("        component DashboardText", 1)[0]
    lines = []
    for line in body.splitlines():
        if "property var selfComponent:" in line or line.strip().startswith(("title:", "onBackClicked:")):
            continue
        if "readonly property color " in line and "verificationColor" not in line:
            color = "#00aa00" if "successColor" in line else "#ffaa00" if "warningColor" in line else "#aaaaaa"
            line = line.split(":", 1)[0] + f': "{color}"'
        if "readonly property real " in line:
            line = line.split(":", 1)[0] + ": 8"
        lines.append(line)
    return "import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\nItem {\n" + "\n".join(lines) + "\n}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qml", type=Path,
                        default=Path(shutil.which("qml") or "C:/ValMAXBuild/Qt/bin/qml.exe"))
    parser.add_argument("--scratch", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args()
    if not args.qml.is_file():
        raise SystemExit("Qt QML runner is missing")
    args.scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="privacy-service-controller-", dir=args.scratch) as directory:
        fixture = Path(directory)
        module = fixture / "ValMAX/Native"
        module.mkdir(parents=True)
        (module / "qmldir").write_text("module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\n", encoding="utf8")
        (module / "Bridge.qml").write_text('''pragma Singleton
import QtQuick
Item {
    property string runtimeState: "unconfigured"
    property var privacyReport: ({schema: 1, state: "unavailable", buildVerified: false,
        currentProcessVerified: false, serviceProcessVerified: false,
        serviceError: "not_verified", error: "starting",
        groups: []})
    Timer { interval: 20; running: true; repeat: false; onTriggered: parent.privacyReport =
        {schema: 1, state: "unavailable", buildVerified: false,
         currentProcessVerified: false, serviceProcessVerified: false,
         serviceError: "not_verified", error: "io_error",
         groups: ["tracer_main", "tracer_service", "ui_statistics", "mytracker", "log_masking", "error_logs"].map(
             function(id) { return {id: id, state: "unavailable"}; })} }
}
''', encoding="utf8")
        (fixture / "PrivacyController.qml").write_text(controller_source(), encoding="utf8")
        shutil.copy2(ROOT / "native/tests/qt/privacy_controller.qml", fixture / "test.qml")
        env = {name: value for name, value in os.environ.items()
               if not name.startswith(("QT_", "QML", "PYTHON", "MAX_MOD", "MXMOD", "VALMAX_NATIVE"))}
        env["PATH"] = str(args.qml.parent) + os.pathsep + os.path.join(env.get("SystemRoot", "C:/Windows"), "System32")
        env.update(QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
                   QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(fixture),
                   TEMP=str(fixture), TMP=str(fixture))
        result = subprocess.run([str(args.qml), "-I", str(fixture), str(fixture / "test.qml")],
                                cwd=fixture, env=env, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise RuntimeError(f"synthetic privacy QML failed ({result.returncode}):\n"
                               f"stdout: {result.stdout[-3000:]}\nstderr: {result.stderr[-3000:]}")
    print('{"schema":1,"syntheticPrivacyControllerPassed":true,"installedClientStarted":false}')


if __name__ == "__main__":
    main()
