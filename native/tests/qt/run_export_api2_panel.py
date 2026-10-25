"""Run generated API2 panel functions against a fake selected view and Bridge."""
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
from build_native_qml import native_export_panel
from chat_lock_ui import transform_export_panel
from optimization_ui import transform_export_panel as beta_panel


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("C:/ValMAXBuild/build/qt-support-0gb8v6n6"))
    parser.add_argument("--build-root", type=Path, default=Path("D:/ValMAXBuild/build"))
    parser.add_argument("--report", type=Path,
                        default=ROOT / "analysis/native-migration-v1/export-api2-panel-proof.json")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    exe = runtime / "valmax_qml_bridge_test.exe"
    for name in (exe.name, "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll", "plugins/platforms/qwindows.dll"):
        if not (runtime / name).is_file():
            raise RuntimeError("missing existing Qt test runtime")

    base = (ROOT / "modkit/chat_export_panel.qml").read_text(encoding="utf8")
    source = native_export_panel(beta_panel(transform_export_panel(base)))
    assert 'property string exportMode: "client"' in base
    assert 'property string exportMode: "api2-text"' in source
    assert 'onAccepted: exportDialog.startExport(selectedFolder.toString())' in source
    assert 'if (nativeApi2ExportAvailable && exportMode === "api2-text") { startApi2Export(directory); return; }' in source
    assert 'function previewHistory()' in source and 'api2.history.preview' in source
    assert 'route === "api2-next" ? 14700000 : 15000' in source
    assert 'когда доступны (до 4 ГиБ на файл)' in source
    assert 'if (!validSelectedScope()) { invalidateSelectedScope(); return; }\n            if (!getContext(api2HistoryContext)) {' in source
    assert 'request("api2-next", jobBody()' in source
    assert 'История читается через штатный API. Доступные фото, видео и голосовые скачиваются по одному' in source

    counts_begin = source.index("        function validApi2Counts(value) {")
    counts_end = source.index("        function fail(reason, text) {")
    start_begin = source.index("        function startApi2Export(directory) {")
    start_end = source.index("        function startExport(directory) {")
    functions = source[counts_begin:counts_end] + source[start_begin:start_end]
    fixture = args.build_root.resolve() / ("qt-export-api2-panel-" + uuid.uuid4().hex)
    fixture.mkdir(parents=True)
    qml = fixture / "panel.qml"
    template = Path(__file__).with_name("export_api2_panel.qml").read_text(encoding="utf8")
    assert template.count("// GENERATED_API2_FUNCTIONS") == 1
    qml.write_text(template.replace("// GENERATED_API2_FUNCTIONS", functions), encoding="utf8")
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir()
    module = fixture / "qml/ValMAX/Native"
    module.mkdir(parents=True)
    (module / "qmldir").write_text(
        "module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\n"
        "singleton ExportTransport 1.0 ExportTransport.qml\n", encoding="utf8")
    shutil.copy2(ROOT / "native/qt/ExportTransport.qml", module / "ExportTransport.qml")
    shutil.copy2(Path(__file__).with_name("export_api2_fake_bridge.qml"), module / "Bridge.qml")
    qml_utils = fixture / "qml/QmlSingletonesModule"
    qml_utils.mkdir(parents=True)
    (qml_utils / "qmldir").write_text(
        "module QmlSingletonesModule\nsingleton QmlUtils 1.0 QmlUtils.qml\n", encoding="utf8")
    (qml_utils / "QmlUtils.qml").write_text(
        'pragma Singleton\nimport QtQml\nQtObject {\n'
        '    function chatIdString(value) { return value && value.synthetic === "selected-chat" ? "42" : "99"; }\n'
        '}\n', encoding="utf8")

    spec = importlib.util.spec_from_file_location(
        "export_panel_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    job.c.windll.kernel32.SetErrorMode(0x8003)
    env = {key: os.environ[key] for key in
           ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"),
               QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
               QML_IMPORT_PATH=str(fixture / "qml"), QML2_IMPORT_PATH=str(fixture / "qml"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"),
               APPDATA=str(fixture / "roaming"), TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    paths = ["modkit/chat_export_panel.qml", "native/qt/ExportTransport.qml",
             "native/tooling/build_native_qml.py", "native/tests/qt/export_api2_panel.qml",
             "native/tests/qt/export_api2_fake_bridge.qml", "native/tests/qt/run_export_api2_panel.py"]
    hashes = {name: sha(ROOT / name) for name in paths}
    result = job.run(fixture, exe, [fixture / "qml", qml], env, "panel")
    job.require(result["result"]["loadedNativePlugin"] == "", "test must use fake Bridge only")
    job.require(hashes == {name: sha(ROOT / name) for name in paths}, "sources changed during run")
    proof = {
        "schema": 1, "verified": True, "fixture": str(fixture), "result": result,
        "sourceHashes": hashes, "generatedFunctionSha256": hashlib.sha256(functions.encode()).hexdigest(),
        "syntheticOnly": True, "nativeSdkObjectsStubbed": True,
        "realMessagesRead": False, "networkUsed": False, "nativeHostBound": False,
        "fullChatExportVerified": False,
        "scope": "Generated native API2 panel functions: explicit local folder, selected chat seed context distinct from typed View, chat scope check, begin before View creation, bind before next, progress receipts, terminal Partial URL only, detached cancellation, failed bind/missing view/scope change fail closed. Host functions and selected view are synthetic."
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"verified": True, "checks": result["result"]["checks"], "proof": str(args.report)}))


if __name__ == "__main__":
    main()
