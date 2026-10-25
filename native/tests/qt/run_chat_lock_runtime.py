"""Actual PIN controller -> QML adapter -> real Qt/Rust. No hook => no PIN."""
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

ctypes.windll.kernel32.SetErrorMode(0x8003)
fixture = Path(tempfile.mkdtemp(prefix="qt-pin-controller-", dir="C:/ValMAXBuild/build"))
shutil.copytree("C:/ValMAXBuild/build/qt-probes/qt-max-runtime-2tq1c3sb", fixture, dirs_exist_ok=True)
module = fixture / "qml/ValMAX/Native"
for name in ("valmaxnativeplugin.dll", "qmldir", "SettingsTransport.qml", "ChatLockTransport.qml"):
    shutil.copy2(Path("C:/ValMAXBuild/build/qt-bridge/qml/ValMAX/Native") / name, module / name)
shutil.copy2("C:/ValMAXBuild/target/release/valmax_core.dll", fixture / "valmax_core.dll")
original = (PROJECT / "modkit/chat_lock_controller.qml").read_text(encoding="utf8").replace("\r\n", "\n")
native = build_native_qml.native_chat_lock_controller(original)
body = native.split("Item {\n", 1)[1].split("    readonly property var pinAppearance:", 1)[0]
# Keep all actual request/grant/secret clearing statements. Only the visual SDK
# dialog is replaced by an inert object; no claim about visual layout here.
controller = ('import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\nItem {\n' + body + '''
    QtObject { id: pinDialog; function close() {} function open() {} }
    function testSetSecrets(first, second) { pin.text = first; confirmation.text = second; }
    function testSecretsClear() { return pin.text === "" && confirmation.text === ""; }
}
''')
(fixture / "PinController.qml").write_text(controller, encoding="utf8")
shutil.copy2(Path(__file__).with_name("chat_lock_controller.qml"), fixture / "test.qml")
profile = fixture / "synthetic-profile"; profile.mkdir()
runtime = fixture / "synthetic-runtime"; runtime.mkdir()
env = {key: value for key, value in os.environ.items() if not key.startswith(("QT_", "QML", "PYTHON", "MAX_MOD", "MXMOD", "VALMAX_NATIVE"))}
env.update({"PATH": str(fixture) + os.pathsep + os.path.join(os.environ["SystemRoot"], "System32"),
    "QT_QPA_PLATFORM": "windows", "QT_QUICK_BACKEND": "software", "QT_DEBUG_PLUGINS": "1", "QML_DISABLE_DISK_CACHE": "1",
    "QML_IMPORT_PATH": str(fixture / "qml"), "QML2_IMPORT_PATH": str(fixture / "qml"), "TEMP": str(fixture), "TMP": str(fixture),
    "VALMAX_NATIVE_RUNTIME_CONFIG": json.dumps({"profileDirectory": str(profile), "runtimeDirectory": str(runtime), "existingClient": False})})
run = subprocess.run([str(fixture / "valmax_qml_bridge_test.exe"), str(fixture / "qml"), str(fixture / "test.qml")],
    cwd=fixture, env=env, timeout=15, capture_output=True, text=True)
# Qt diagnostics do not contain request payloads; keep bounded failure tail.
if run.returncode:
    print(run.stdout); print(run.stderr[-10000:]); raise RuntimeError("pin_controller_failed")
proof = json.loads(run.stdout)
store = profile / "chat-locks.json"
if store.exists() and json.loads(store.read_text()).get("locks"):
    raise RuntimeError("unprotected_host_persisted_pin")
proof.update(fixture=str(fixture), productionControllerSha256=hashlib.sha256(original.encode()).hexdigest(),
    nativePluginSha256=hashlib.sha256((module / "valmaxnativeplugin.dll").read_bytes()).hexdigest(),
    rustSha256=hashlib.sha256((fixture / "valmax_core.dll").read_bytes()).hexdigest(),
    noPinPersisted=True, productionHooksMocked=False, actualMaxStarted=False, realProfileAccessed=False,
    visualSdkSubstituted=True)
(PROJECT / "analysis/native-migration-v1/qt-pin-controller-proof.json").write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
