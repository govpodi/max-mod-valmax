"""Actual SettingsTransport QML regression with synthetic Bridge admissions.

Build/test orchestration only. No native MAX client, real profile, Rust core,
guard DLL, network or source-body copy is used.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[3]
FAKE_BRIDGE = '''pragma Singleton
import QtQml
QtObject {
    property string runtimeState: "ready"
    readonly property bool runtimeAvailable: runtimeState === "ready"
    property bool throwOnInvoke: false
    property int sequence: 0
    property var forcedId: null
    property var requests: []
    property var duringInvoke: null
    signal operationFinished(string id, string response)
    function invoke(raw) {
        const id = forcedId === null ? "settings-operation-" + (++sequence) : forcedId;
        requests.push({id: id, value: JSON.parse(raw)});
        if (duringInvoke) { const action = duringInvoke; duringInvoke = null; action(); }
        if (throwOnInvoke) throw new Error("synthetic invoke exception");
        return {accepted: true, requestId: id};
    }
}
'''


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve(strict=True)
    build_root = Path("D:/ValMAXBuild/build").resolve(strict=True)
    require(runtime.is_relative_to(build_root), "owned D Qt runtime required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve()
            and not args.report.exists(), "fresh migration report required")
    source = ROOT / "native/qt/SettingsTransport.qml"
    require(sha(runtime / "qml/ValMAX/Native/SettingsTransport.qml") == sha(source),
            "runtime SettingsTransport differs from frozen production source")
    sources = [source, Path(__file__), Path(__file__).with_name("settings_transport_reentrant.qml"),
               Path(__file__).with_name("run_native_media_plugin.py"), Path(__file__).with_name("run_webview_blank_native.py")]
    source_hashes = {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}
    runtime_paths = [runtime / name for name in ("valmax_qml_bridge_test.exe", "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll", "plugins/platforms/qwindows.dll")]
    runtime_hashes = {p.relative_to(runtime).as_posix(): sha(p) for p in runtime_paths}
    fixture = build_root / ("qt-settings-transport-reentrant-" + uuid.uuid4().hex); fixture.mkdir()
    qml = fixture / "settings_transport_reentrant.qml"
    shutil.copy2(Path(__file__).with_name(qml.name), qml)
    module = fixture / "qml/ValMAX/Native"; module.mkdir(parents=True)
    (module / "qmldir").write_text("module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\nsingleton SettingsTransport 1.0 SettingsTransport.qml\n", "utf8")
    (module / "Bridge.qml").write_text(FAKE_BRIDGE, "utf8")
    shutil.copy2(source, module / source.name)
    require(sha(module / source.name) == source_hashes[source.relative_to(ROOT).as_posix()], "copied actual transport changed")
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir()
    spec = importlib.util.spec_from_file_location("settings_reentrant_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    host = importlib.util.module_from_spec(spec); spec.loader.exec_module(host)
    host.c.windll.kernel32.SetErrorMode(0x8003)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    imports = str(fixture / "qml") + os.pathsep + str(runtime / "qml")
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QT_QUICK_BACKEND="software",
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=imports, QML2_IMPORT_PATH=imports,
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    result = host.run(fixture, runtime / "valmax_qml_bridge_test.exe", [fixture / "qml", qml], env, "settings")
    require(result["result"]["loadedNativePlugin"] == "" and result["result"].get("loadedRustCore", "") == "",
            "fake Bridge test must not load native plugin, core or hooks")
    require(result["result"]["checks"] >= 48, "required SettingsTransport regression cases did not finish")
    for key, name in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
        require(Path(result["result"][key]).resolve() == (runtime / name).resolve(), "Qt loaded outside supplied runtime")
    require(source_hashes == {p.relative_to(ROOT).as_posix(): sha(p) for p in sources}, "source changed during regression")
    require(runtime_hashes == {p.relative_to(runtime).as_posix(): sha(p) for p in runtime_paths}, "Qt runtime changed during regression")
    require(sha(module / source.name) == sha(source), "actual fixture transport changed during regression")
    proof = {"schema": 1, "verified": True, "checks": result["result"]["checks"], "fixture": str(fixture), "runtime": str(runtime),
             "result": result, "sourceHashes": source_hashes, "runtimeInputs": runtime_hashes,
             "fakeBridgeSha256": sha(module / "Bridge.qml"), "actualSettingsTransport": True,
             "cases": ["reentrant_abort", "invoke_exception", "invalid_ids", "duplicate_pending_id", "ordinary_completion",
                       "stale_completion_after_abort", "queued_opening_cancel", "subsequent_recovery"],
             "syntheticOnly": True, "actualMaxStarted": False, "realProfilesUsed": False, "nativeCoreOrHooksLoaded": False,
             "networkUsed": False, "scope": "Actual frozen SettingsTransport.qml on real Qt with synthetic Bridge admissions. No copied dispatch implementation, native MAX, Rust core, guards, real profile or network."}
    with args.report.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "checks": proof["checks"], "report": str(args.report)}))


if __name__ == "__main__":
    main()
