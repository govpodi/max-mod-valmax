"""Run the actual generated nextMedia/invokeNative functions with synthetic SDK objects."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "native/tooling"), str(ROOT / "modkit")]
from build_native_qml import native_export_panel
from chat_lock_ui import transform_export_panel
from optimization_ui import transform_export_panel as beta_panel


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("C:/ValMAXBuild/build/qt-support-0gb8v6n6"))
    parser.add_argument("--report", type=Path, default=ROOT / "analysis/native-migration-v1/export-native-media-panel-proof.json")
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    exe = runtime / "valmax_qml_bridge_test.exe"
    for name in (exe.name, "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll", "plugins/platforms/qwindows.dll"):
        if not (runtime / name).is_file():
            raise RuntimeError("missing existing Qt test runtime")
    source = (ROOT / "modkit/chat_export_panel.qml").read_text(encoding="utf8")
    source = native_export_panel(beta_panel(transform_export_panel(source)))
    begin = source.index("        function nextMedia() {")
    end = source.index("        function mediaFinished(status, reason) {")
    functions = source[begin:end]
    fixture = Path("D:/ValMAXBuild/build") / ("qt-export-media-panel-" + uuid.uuid4().hex)
    fixture.mkdir(parents=True)
    qml = fixture / "panel.qml"
    qml.write_text((Path(__file__).with_name("export_media_panel.qml").read_text(encoding="utf8")).replace("// GENERATED_EXPORT_FUNCTIONS", functions), encoding="utf8")
    for name in ("home", "local", "roaming", "temp", "qml"):
        (fixture / name).mkdir()
    module=fixture / "qml/ValMAX/Native"; module.mkdir(parents=True)
    (module / "qmldir").write_text("module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\nsingleton ExportTransport 1.0 ExportTransport.qml\nsingleton ExportMediaOwner 1.0 ExportMediaOwner.qml\n",encoding="utf8")
    for name in ("ExportTransport.qml","ExportMediaOwner.qml"):
        shutil.copy2(ROOT / "native/qt" / name,module / name)
    shutil.copy2(Path(__file__).with_name("export_fake_bridge.qml"),module / "Bridge.qml")
    sdk_module=fixture / "qml/QmlSingletonesModule"; sdk_module.mkdir()
    (sdk_module / "qmldir").write_text("module QmlSingletonesModule\nMediaDownloaderPhotoType 1.0 MediaDownloaderPhotoType.qml\n",encoding="utf8")
    (sdk_module / "MediaDownloaderPhotoType.qml").write_text("import QtQml\nQtObject { enum Kind { Photo, Gif } }\n",encoding="utf8")
    spec = importlib.util.spec_from_file_location("export_panel_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    job = importlib.util.module_from_spec(spec); spec.loader.exec_module(job)
    job.c.windll.kernel32.SetErrorMode(0x8003)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
        QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QT_QUICK_BACKEND="software",
        QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(fixture / "qml"), QML2_IMPORT_PATH=str(fixture / "qml"),
        USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
        TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    paths = ["native/qt/ExportTransport.qml", "native/qt/ExportMediaOwner.qml", "native/tooling/build_native_qml.py", "modkit/chat_export_panel.qml",
        "native/tests/qt/export_media_panel.qml", "native/tests/qt/run_export_media_panel.py"]
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}
    result = job.run(fixture, exe, [fixture / "qml", qml], env, "panel")
    job.require(result["result"]["loadedNativePlugin"] == "", "test must use synthetic SDK only")
    job.require(hashes == {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in paths}, "sources changed")
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "result": result, "sourceHashes": hashes,
        "generatedFunctionSha256": hashlib.sha256(functions.encode()).hexdigest(), "syntheticOnly": True,
        "nativeSdkObjectsStubbed": True, "realMessagesRead": False, "networkUsed": False,
        "fullChatExportVerified": False,
        "scope": "Actual generated QML functions and engine-owned task: authorized lease before native network worker, image/video/audio task-ID correlation, dedicated file-context success/failure, page destruction, cancellation and busy ACK retry"}
    args.report.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"verified": True, "checks": result["result"]["checks"], "proof": str(args.report)}))


if __name__ == "__main__": main()
