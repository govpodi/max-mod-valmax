"""Actual native BusinessPanel JS in MAX Qt, with a no-network fake WebView."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[3]
fixture = Path("C:/ValMAXBuild/build/qt-webview-blank-eifyoipa")
if not (fixture / "Qt6Core.dll").is_file(): raise RuntimeError("owned_qt_fixture_missing")
work = fixture / ("business-controller-" + uuid.uuid4().hex)
work.mkdir()
stub = work / "QtWebView"; stub.mkdir()
(stub / "qmldir").write_text("module QtWebView\nWebView 1.0 WebView.qml\n")
(stub / "WebView.qml").write_text('''import QtQuick
Item {
    readonly property bool isSynthetic: true
    enum LoadStatus { LoadStartedStatus, LoadSucceededStatus, LoadFailedStatus }
    property url url
    property int reloads: 0
    property int stops: 0
    property int scripts: 0
    signal loadingChanged(var request)
    function stop() { ++stops; }
    function reload() { ++reloads; }
    function runJavaScript(script, callback) { ++scripts; callback(); }
}
''')
panel = (ROOT / "native/qt/BusinessPanel.qml").read_text(encoding="utf8")
body = panel.split("    Control {\n", 1)[1].split("        Component.onCompleted:", 1)[0]
lines = [line for line in body.splitlines() if not any(fragment in line for fragment in
    ("property var selfComponent:", "property var maxwellColorTokens:", "readonly property real gap:"))]
(work / "BusinessController.qml").write_text("import QtQuick\nItem {\n" + "\n".join(lines)
    + "\nItem { id: businessWebHost }\n}\n", encoding="utf8")
qml = work / "test.qml"
shutil.copy2(Path(__file__).with_name("business_controller.qml"), qml)
exe = fixture / "valmax_business_controller_test.exe"
shutil.copy2("C:/ValMAXBuild/build/qt-support-bridge/valmax_qml_bridge_test.exe", exe)
env = {key: value for key, value in os.environ.items() if not key.upper().startswith(
    ("QT_", "QML", "WEBVIEW2_", "PYTHON", "MAX_MOD", "MXMOD", "VALMAX_NATIVE"))}
env.update(PATH=str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
    QT_QPA_PLATFORM="windows", QT_DEBUG_PLUGINS="1", QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
    TEMP=str(work), TMP=str(work))
run = subprocess.run([str(exe), str(work), str(qml)], cwd=fixture, env=env,
    capture_output=True, text=True, timeout=15)
(work / "stderr.log").write_text(run.stderr, encoding="utf8")
if run.returncode:
    print(run.stdout); print(run.stderr[-6000:]); raise RuntimeError("business_controller_failed")
proof = json.loads(run.stdout)
if not proof["ok"] or proof["checks"] != 17: raise RuntimeError("business_checks_missing")
proof.update(verified=True, actualNativeController=True, browserBackend="synthetic_no_network",
    businessUrlLoaded=False, userInstallationChanged=False, fullVisualSdkTest=False,
    productionPanelSha256=hashlib.sha256(panel.encode()).hexdigest(), fixture=str(work),
    qtHashes={name: hashlib.sha256((fixture/name).read_bytes()).hexdigest() for name in ("Qt6Core.dll", "Qt6Qml.dll")})
(ROOT / "analysis/native-migration-v1/qt-business-controller-proof.json").write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
