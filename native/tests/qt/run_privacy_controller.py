"""Real Qt/production verifier with the native page controller, synthetic host."""
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
source = Path("C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb")
build = Path("C:/ValMAXBuild/build/qt-privacy-bridge")
fixture = Path(tempfile.mkdtemp(prefix="qt-privacy-controller-", dir="C:/ValMAXBuild/build"))
# Hard-link only immutable Qt files; each mod DLL/test input is copied separately.
for file in source.glob("*.dll"):
    if not file.name.startswith("valmax_"): os.link(file, fixture / file.name)
for name in ("plugins", "qml"):
    shutil.copytree(source / name, fixture / name, copy_function=os.link,
        ignore=lambda path, names: [name for name in names if name == "ValMAX"])
shutil.copy2(source / "qt.conf", fixture / "qt.conf")
module = fixture / "qml/ValMAX/Native"; module.mkdir(parents=True)
for name in ("qmldir", "SettingsTransport.qml", "ChatLockTransport.qml", "valmaxnativeplugin.dll"):
    shutil.copy2(build / "qml/ValMAX/Native" / name, module / name)
shutil.copy2(build / "valmax_qml_bridge_test.exe", fixture / "valmax_qml_bridge_test.exe")
shutil.copy2("C:/ValMAXBuild/build/max-engine-probes/probe-2d9717b3aa5c41b9aa065df3ae2695f2/runtime/valmax_core.dll", fixture / "valmax_core.dll")
shutil.copy2("C:/ValMAXBuild/build/hooks/valmax_privacy.dll", fixture / "valmax_privacy.dll")
panel = (PROJECT / "native/qt/PrivacyPanel.qml").read_text(encoding="utf8")
body = panel.split("    SettingsBaseItem {\n", 1)[1].split("        component DashboardText", 1)[0]
lines = []
for line in body.splitlines():
    if "property var selfComponent:" in line or line.strip().startswith(("title:", "onBackClicked:")): continue
    if "readonly property color " in line: line = line.split(":", 1)[0] + ': "#aaaaaa"'
    if "readonly property real " in line: line = line.split(":", 1)[0] + ': 8'
    lines.append(line)
(fixture / "PrivacyController.qml").write_text("import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\nItem {\n" + "\n".join(lines) + "\n}\n", encoding="utf8")
qml = fixture / "test.qml"; shutil.copy2(Path(__file__).with_name("privacy_controller.qml"), qml)
env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MAX_MOD", "MXMOD", "VALMAX_NATIVE"))}
env.update(PATH=str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
    QT_QPA_PLATFORM="windows", QT_QUICK_BACKEND="software", QT_DEBUG_PLUGINS="1", QML_DISABLE_DISK_CACHE="1",
    QML_IMPORT_PATH=str(fixture / "qml"), QML2_IMPORT_PATH=str(fixture / "qml"), TEMP=str(fixture), TMP=str(fixture))
run = subprocess.run([str(fixture / "valmax_qml_bridge_test.exe"), str(fixture / "qml"), str(qml)], cwd=fixture,
    env=env, capture_output=True, text=True, timeout=15)
(fixture / "stderr.log").write_text(run.stderr, encoding="utf8")
if run.returncode:
    print(run.stdout); print(run.stderr[-6000:]); raise RuntimeError("privacy_controller_failed")
proof = json.loads(run.stdout)
assert proof["ok"] and proof["checks"] == 23
proof.update(fixture=str(fixture), productionVerifier=True, realPageController=True, fullVisualSdkTest=False,
    actualMaxStarted=False, legacyTemplateModified=False, noHttpTransport=True,
    hashes={name: hashlib.sha256((fixture / name).read_bytes()).hexdigest() for name in ("Qt6Core.dll", "Qt6Qml.dll", "valmax_core.dll", "valmax_privacy.dll")},
    pluginSha256=hashlib.sha256((module / "valmaxnativeplugin.dll").read_bytes()).hexdigest(),
    panelSha256=hashlib.sha256(panel.encode()).hexdigest())
(PROJECT / "analysis/native-migration-v1/qt-privacy-controller-proof.json").write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
