"""Production account-reader declarations on real Qt; synthetic bridge and IDs.

Reuses an existing owned Qt harness; no build, MAX launch, profile or credential
access. The imported generator/legacy Python is build-only fixture preparation.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "native/tooling"), str(ROOT / "modkit")]
import account_preferences_ui as legacy
import build_native_qml as native


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("C:/ValMAXBuild/build/qt-support-0gb8v6n6"))
    parser.add_argument("--report", type=Path, default=ROOT / "analysis/native-migration-v1/account-readers-qt-proof.json")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    exe = runtime / "valmax_qml_bridge_test.exe"
    for name in (exe.name, "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll", "plugins/platforms/qwindows.dll"):
        if not (runtime / name).is_file(): raise RuntimeError("missing owned Qt fixture component")
    spec = importlib.util.spec_from_file_location("account_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    job.c.windll.kernel32.SetErrorMode(0x8003)
    fixture = Path("C:/ValMAXBuild/build") / ("qt-account-readers-" + uuid.uuid4().hex)
    module = fixture / "qml/ValMAX/Native"
    module.mkdir(parents=True)
    (module / "qmldir").write_text("module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\nsingleton SettingsTransport 1.0 SettingsTransport.qml\n", encoding="utf8")
    shutil.copy2(ROOT / "native/qt/SettingsTransport.qml", module / "SettingsTransport.qml")
    (module / "Bridge.qml").write_text('''pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "opening"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property var requests: []
    signal operationFinished(string id, string response)
    function invoke(payload) {
        if (payload !== '{"operation":"accounts.get"}') throw new Error("unexpected request");
        requests.push(payload);
        return {accepted: true, requestId: "account-" + requests.length};
    }
}
''', encoding="utf8")
    qml_utils = fixture / "qml/QmlSingletonesModule"
    qml_utils.mkdir()
    (qml_utils / "qmldir").write_text("module QmlSingletonesModule\nsingleton QmlUtils 1.0 QmlUtils.qml\n", encoding="utf8")
    (qml_utils / "QmlUtils.qml").write_text('pragma Singleton\nimport QtQml\nQtObject { function contactIdString(value) { if (typeof value !== "string") throw new Error("synthetic exact ID required"); return value; } }\n', encoding="utf8")
    label = legacy._label_reader('import QtQuick\nItem {\n    required property var commonDependency\n}\n')
    label = native.native_account_reader(label, interval=3000)
    label = label.rsplit("}", 1)[0] + "    function expireRequest() { modAccountPrefsWatchdog.triggered(); }\n}\n"
    (fixture / "AccountReader.qml").write_text(label, encoding="utf8")
    schedule = 'import QtQuick\nimport QmlSingletonesModule\nItem {\n    id: root\n' + legacy.reader(interval=5000) + '''
    property alias work: modAccountView
    property alias notification: notificationState
    QtObject { id: notificationState; property bool notificationMuted: false }
    Item {
        property var dependency: ({commonDependency: {selfInfo: {id: "9223372036854775807"}},
            leadingPanelDependency: {settings: {notificationContext: notificationState}}})
''' + legacy.SCHEDULER + "\n    }\n}\n"
    schedule = native.native_account_reader(schedule, interval=5000)
    (fixture / "ScheduleReader.qml").write_text(schedule, encoding="utf8")
    for source in (label, schedule):
        if "XMLHttpRequest" in source or "127.0.0.1" in source:
            raise RuntimeError("legacy reader transport remained")
    qml = fixture / "account_preferences_readers.qml"
    shutil.copy2(Path(__file__).with_name(qml.name), qml)
    for directory in ("home", "local", "roaming", "temp"): (fixture / directory).mkdir()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QT_DEBUG_PLUGINS="1",
               QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
               QML_IMPORT_PATH=str(fixture / "qml"), QML2_IMPORT_PATH=str(fixture / "qml"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"),
               APPDATA=str(fixture / "roaming"), TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    result = job.run(fixture, exe, [fixture / "qml", qml], env, "readers")
    value = result["result"]
    job.require(value["loadedNativePlugin"] == "", "test must use only the stub Bridge")
    expected = {"Qt6Core.dll": "22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa",
                "Qt6Qml.dll": "257670ffa28fc84d4ec9f818de27e2e4611d9a0cffcb657179a210931e36671b",
                "Qt6Quick.dll": "ed2917b9905a5b867ecc192fa4429170494af0dcdaacc68053fe88948319cd08"}
    for key, name in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
        job.require(Path(value[key]).resolve() == (runtime / name).resolve() and sha(runtime / name) == expected[name], "unexpected Qt runtime")
    files = ["native/tooling/build_native_qml.py", "native/qt/SettingsTransport.qml",
             "native/tests/qt/account_preferences_readers.qml", "native/tests/qt/run_account_preferences_readers.py",
             "modkit/account_preferences_ui.py"]
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "result": result,
             "sourceHashes": {name: sha(ROOT / name) for name in files}, "qtHashes": expected,
             "harnessSha256": sha(exe), "syntheticOnly": True, "bridgeStubbed": True,
             "accountModelStubbed": True, "networkUsed": False, "realProfilesUsed": False,
             "limits": ["Real Qt executes production account reader/validator/scheduler declarations",
                        "Native accounts store was tested separately; this proof stubs its completion",
                        "Actual MAX account widgets and installed client were not launched"]}
    args.report.resolve().write_text(json.dumps(proof, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"verified": True, "checks": value["checks"], "proof": str(args.report.resolve())}))


if __name__ == "__main__":
    main()
