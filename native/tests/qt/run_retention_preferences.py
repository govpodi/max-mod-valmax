"""Owned Qt tests of config-only retention transport and generated controllers.

No retention archive/capture backend, native client, profile or network is used.
Python is build/test orchestration only and is never part of the target runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "native/tooling"), str(ROOT / "modkit")]
import build_native_qml

FAKE_BRIDGE = '''pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property bool accept: true
    property bool throwOnInvoke: false
    property int sequence: 0
    property string forcedId: ""
    property var requests: []
    property var duringInvoke: null
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const id = forcedId || "preference-operation-" + (++sequence);
        requests.push({id: id, value: JSON.parse(raw)});
        if (duringInvoke) { const action = duringInvoke; duringInvoke = null; action(); }
        if (throwOnInvoke) throw new Error("synthetic invoke failure");
        return {accepted: accept, requestId: id, error: accept ? "" : "queue_full"};
    }
}
'''

REAL_RUNTIME_QML = '''import QtQuick
import ValMAX.Native 1.0 as Native
Item {
    id: test
    property int checks: 0
    property string failure: ""
    property string testPhase: "@PHASE@"
    property int step: 0
    property int ticks: 0
    property var request: null
    property string revision: ""
    function check(value, text) { ++checks; if (!value) throw new Error(text); }
    function start(method, enabled, expectedRevision) {
        request = Native.RetentionTransport.createRequest("settings"); request.open(method);
        request.send(method === "GET" ? "" : JSON.stringify({schema: 1, revision: expectedRevision, enabled: enabled}));
    }
    function validate(value, enabled) {
        check(value.schema === 2 && value.enabled === enabled && /^[0-9a-f]{64}$/.test(value.revision), "real Rust preference status identity");
        check(JSON.stringify(value.capabilities) !== "" && value.capabilities.capture === false && value.capabilities.lookup === false
              && value.capabilities.enable === false && value.capabilities.disable === true, "real Rust fixed unavailable capabilities");
        const r = value.runtime;
        check(r.ready === false && r.applied === false && r.enabled === false && r.historySyncVerified === false
              && r.error === "retention_not_integrated" && r.retainedEvents === null && r.retainedMessages === null,
              "real runtime never fabricates capture or archive counters");
    }
    function done() { run.stop(); Qt.exit(0); }
    Timer {
        id: run; interval: 5; repeat: true; running: true
        onTriggered: {
            try {
                if (++test.ticks > 1000) throw new Error("real preference fixture deadline");
                if (test.step === 0) {
                    if (Native.Bridge.runtimeState === "opening") return;
                    test.check(Native.Bridge.runtimeAvailable, "actual native Bridge opens only the explicit owned profile");
                    test.check(Native.RetentionTransport.lookupAvailable === false, "real transport has no archive lookup backend");
                    test.start("GET", false, ""); test.step = 1; return;
                }
                if (!test.request || test.request.readyState !== 4) return;
                if (test.step === 1) {
                    test.check(test.request.status === 200, "actual C++ to Rust preference GET succeeds");
                    const value = JSON.parse(test.request.responseText);
                    test.validate(value, test.testPhase !== "restart"); test.revision = value.revision;
                    if (test.testPhase === "restart") { test.done(); return; }
                    test.start("POST", true, test.revision); test.step = 2; return;
                }
                if (test.step === 2) {
                    test.check(test.request.status === 503 && JSON.parse(test.request.responseText).error === "retention_unavailable",
                               "actual Rust refuses enable intent without a backend");
                    if (test.testPhase === "denied") test.start("POST", false, "0".repeat(64));
                    else test.start("POST", false, test.revision);
                    test.step = 3; return;
                }
                if (test.step === 3) {
                    if (test.testPhase === "denied") {
                        test.check(test.request.status === 409 && JSON.parse(test.request.responseText).error === "revision_conflict",
                                   "stale actual Rust CAS cannot modify the legacy preference");
                        test.start("GET", false, ""); test.step = 5; return;
                    }
                    test.check(test.request.status === 200, "actual C++ to Rust disables the old preference");
                    test.validate(JSON.parse(test.request.responseText), false);
                    test.start("POST", false, test.revision); test.step = 4; return;
                }
                if (test.step === 4) {
                    test.check(test.request.status === 409, "pre-disable revision is stale after native preference update");
                    test.start("GET", false, ""); test.step = 5; return;
                }
                if (test.step === 5) {
                    test.check(test.request.status === 200, "real preference state remains readable after rejection/CAS");
                    const value = JSON.parse(test.request.responseText);
                    test.validate(value, test.testPhase === "denied");
                    test.check(test.testPhase === "denied" ? value.revision === test.revision : value.revision !== test.revision,
                               "revision identifies preserved or changed preference bytes");
                    test.done();
                }
            } catch (error) {
                test.failure = String(error); console.error("real-retention-preferences:", test.failure);
                stop(); Qt.exit(1);
            }
        }
    }
}
'''


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def page_controller(source):
    """Keep real controller statements; replace only visual SDK dimensions."""
    begin = "    SettingsBaseItem {\n"
    require(source.count(begin) == 1 and "        component " in source, "retention page boundaries changed")
    body = source.split(begin, 1)[1].split("        component ", 1)[0]
    lines = []
    for line in body.splitlines():
        if "property var selfComponent:" in line or line.strip().startswith(("title:", "onBackClicked:")):
            continue
        if any(token in line for token in ("readonly property real gap:", "readonly property real sectionGap:", "readonly property real radius:")):
            line = line.split(":", 1)[0] + ": 8"
        lines.append(line)
    def enabled(name):
        matches = re.findall(r'objectName:\s*"' + name + r'";[^}]*?\benabled:\s*([^;]+);', source)
        require(len(matches) == 1, "actual visual capability binding changed: " + name)
        return matches[0]
    return ('import QtQuick\nimport ValMAX.Native 1.0 as ValMAXNative\nItem {\n' + "\n".join(lines)
            + "\n    readonly property bool testToggleEnabled: " + enabled("retentionEnabled")
            + "\n    readonly property bool testSaveEnabled: " + enabled("saveRetentionSettings") + "\n}\n")


def run_real(args, runtime, build_root, sources, runtime_hashes):
    """Actual plugin/core, explicit temporary profile; legacy bool bytes only."""
    module = runtime / "qml/ValMAX/Native"
    plugin = module / "valmaxnativeplugin.dll"
    core = runtime / "valmax_core.dll"
    for path in (plugin, core, module / "RetentionTransport.qml"):
        require(path.is_file(), "new common native runtime with retention preferences required")
    require(sha(module / "RetentionTransport.qml") == sha(ROOT / "native/qt/RetentionTransport.qml"),
            "real runtime transport differs from frozen source")
    compiled_inputs = {"qml/ValMAX/Native/valmaxnativeplugin.dll": sha(plugin), "valmax_core.dll": sha(core),
                       "qml/ValMAX/Native/RetentionTransport.qml": sha(module / "RetentionTransport.qml")}
    fixture = build_root / ("qt-real-retention-preferences-" + uuid.uuid4().hex); fixture.mkdir()
    for name in ("home", "local", "roaming", "temp", "synthetic-profile", "synthetic-runtime"):
        (fixture / name).mkdir()
    store = fixture / "synthetic-profile/message-retention-settings.json"
    old_bytes = b'{ "schema" : 1, "enabled" : true }\r\n'
    store.write_bytes(old_bytes)
    spec = importlib.util.spec_from_file_location("real_retention_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
    host.c.windll.kernel32.SetErrorMode(0x8003)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QT_QUICK_BACKEND="software",
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(runtime / "qml"), QML2_IMPORT_PATH=str(runtime / "qml"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    runs = []
    for phase in ("denied", "disable", "restart"):
        qml = fixture / (phase + ".qml"); qml.write_text(REAL_RUNTIME_QML.replace("@PHASE@", phase), "utf8")
        config = {"profileDirectory": str(fixture / "synthetic-profile"), "runtimeDirectory": str(fixture / "synthetic-runtime"),
                  "existingClient": phase == "restart"}
        result = host.run(fixture, runtime / "valmax_qml_bridge_test.exe", [runtime / "qml", qml],
                          {**env, "VALMAX_NATIVE_RUNTIME_CONFIG": json.dumps(config)}, phase)
        require(Path(result["result"]["loadedNativePlugin"]).resolve() == plugin.resolve(), "unexpected real native plugin")
        require(Path(result["result"].get("loadedRustCore", "")).resolve() == core.resolve(),
                "real Rust core was not loaded from the supplied native runtime")
        for key, name in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
            require(Path(result["result"][key]).resolve() == (runtime / name).resolve(), "Qt loaded outside owned runtime")
        expected = old_bytes if phase == "denied" else b'{"enabled":false,"schema":1}\n'
        require(store.read_bytes() == expected, "actual preference bytes were changed unexpectedly: " + phase)
        runs.append({"phase": phase, "result": result, "storeSha256": sha(store), "expectedBytesVerified": True})
    require(sources == {name: sha(ROOT / name) for name in sources}, "source changed during real runtime preference proof")
    require(runtime_hashes == {name: sha(runtime / name) for name in runtime_hashes}, "Qt changed during real runtime proof")
    require(compiled_inputs == {name: sha(runtime / name) for name in compiled_inputs}, "compiled native inputs changed")
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "runtime": str(runtime), "checks": sum(r["result"]["result"]["checks"] for r in runs),
             "runs": runs, "sourceHashes": sources, "runtimeInputs": {**runtime_hashes, **compiled_inputs},
             "syntheticProfileOnly": True, "actualNativePluginAndCore": True, "rustPersistenceVerified": True,
             "legacyReadNoRewrite": True, "enableAndStaleCasNoWrite": True, "restartVerified": True,
             "captureBackendImplemented": False, "archiveBackendImplemented": False,
             "actualMaxStarted": False, "realCredentialsUsed": False, "networkRequestsSent": False,
             "scope": "Actual QML RetentionTransport to C++ Bridge to Rust config-only store on explicit fresh synthetic profile. Legacy bool bytes preserved by read/denied enable/stale CAS, native disable writes exact legacy canonical format, read after process restart. No capture, archive lookup, MAX/profile discovery or backend implementation."}
    with args.report.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "checks": proof["checks"], "report": str(args.report), "mode": "real-runtime"}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--real-runtime", action="store_true", help="Actual plugin/Rust store on explicit fresh own profile; no fake Bridge")
    args = parser.parse_args()
    runtime = args.runtime.resolve(strict=True)
    build_root = Path("D:/ValMAXBuild/build").resolve(strict=True)
    require(runtime.is_relative_to(build_root), "owned D Qt runtime required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve()
            and not args.report.exists(), "fresh migration report required")
    exe = runtime / "valmax_qml_bridge_test.exe"
    runtime_inputs = [exe, runtime / "Qt6Core.dll", runtime / "Qt6Qml.dll", runtime / "Qt6Quick.dll",
                      runtime / "plugins/platforms/qwindows.dll"]
    require(all(path.is_file() for path in runtime_inputs), "complete copied Qt harness required")
    paths = ["native/qt/RetentionTransport.qml", "native/qt/SettingsTransport.qml", "native/tooling/build_native_qml.py",
             "modkit/message_retention_settings_panel.qml", "modkit/message_retention_controller.qml",
             "native/tests/qt/retention_preferences.qml", "native/tests/qt/run_retention_preferences.py",
             "native/tests/qt/run_native_media_plugin.py", "native/tests/qt/run_webview_blank_native.py"]
    sources = {name: sha(ROOT / name) for name in paths}
    runtime_hashes = {path.relative_to(runtime).as_posix(): sha(path) for path in runtime_inputs}
    if args.real_runtime:
        for name in ("native/core/src/retention_preferences.rs", "native/core/src/runtime.rs", "native/qt/native_bridge.cpp"):
            sources[name] = sha(ROOT / name)
        run_real(args, runtime, build_root, sources, runtime_hashes)
        return
    panel = build_native_qml.native_retention_settings_panel((ROOT / "modkit/message_retention_settings_panel.qml").read_text("utf8"))
    controller = build_native_qml.native_retention_controller((ROOT / "modkit/message_retention_controller.qml").read_text("utf8"))
    require("127.0.0.1" not in panel + controller and "XMLHttpRequest" not in panel + controller,
            "actual transformed sources must not retain local HTTP")
    fixture = build_root / ("qt-retention-preferences-" + uuid.uuid4().hex)
    fixture.mkdir()
    qml = fixture / "retention_preferences.qml"
    shutil.copy2(Path(__file__).with_name("retention_preferences.qml"), qml)
    (fixture / "RetentionPage.qml").write_text(page_controller(panel), "utf8")
    (fixture / "RetentionController.qml").write_text(controller, "utf8")
    module = fixture / "qml/ValMAX/Native"
    module.mkdir(parents=True)
    (module / "qmldir").write_text("module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\nsingleton RetentionTransport 1.0 RetentionTransport.qml\nsingleton SettingsTransport 1.0 SettingsTransport.qml\n", "utf8")
    (module / "Bridge.qml").write_text(FAKE_BRIDGE, "utf8")
    shutil.copy2(ROOT / "native/qt/RetentionTransport.qml", module / "RetentionTransport.qml")
    shutil.copy2(ROOT / "native/qt/SettingsTransport.qml", module / "SettingsTransport.qml")
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir()
    spec = importlib.util.spec_from_file_location("retention_preferences_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    host.c.windll.kernel32.SetErrorMode(0x8003)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    imports = str(fixture / "qml") + os.pathsep + str(runtime / "qml")
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QT_QUICK_BACKEND="software",
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=imports, QML2_IMPORT_PATH=imports,
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    result = host.run(fixture, exe, [fixture / "qml", qml], env, "preferences")
    require(result["result"]["loadedNativePlugin"] == "", "transport fixture must not load the real native plugin or hooks")
    require(result["result"]["checks"] >= 45, "required controller/transport cases did not finish")
    for key, name in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
        require(Path(result["result"][key]).resolve() == (runtime / name).resolve(), "Qt loaded outside the supplied runtime")
    require(sources == {name: sha(ROOT / name) for name in paths}, "source changed during preference test")
    require(runtime_hashes == {path.relative_to(runtime).as_posix(): sha(path) for path in runtime_inputs}, "Qt fixture changed during preference test")
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "runtime": str(runtime), "result": result,
             "sourceHashes": sources, "runtimeInputs": runtime_hashes,
             "generatedPanelSha256": hashlib.sha256(panel.encode()).hexdigest(),
             "generatedControllerSha256": hashlib.sha256(controller.encode()).hexdigest(),
             "pageControllerSha256": sha(fixture / "RetentionPage.qml"), "fakeBridgeSha256": sha(module / "Bridge.qml"),
             "syntheticOnly": True, "realClientStarted": False, "realProfilesRead": False,
             "archiveBackendImplemented": False, "captureBackendImplemented": False,
             "retentionStorageVerified": False, "rustPersistenceVerified": False, "networkUsed": False,
             "scope": "Actual native RetentionTransport and transformed settings/controller code on real Qt. Config-only status, capabilities, legacy disable path, denied enable/lookup, opening/abort/stale/max8, widget destruction and unavailable lookup polling. Fake Bridge replies; visual SDK dimensions/widgets stubbed. No archive, message capture, real Rust preference persistence, native hooks or MAX."}
    with args.report.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "checks": result["result"]["checks"], "report": str(args.report)}))


if __name__ == "__main__":
    main()
