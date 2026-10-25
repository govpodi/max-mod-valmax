"""Actual generated harvestPage with real native cursor and owned QML model."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "native/tooling"), str(ROOT / "modkit")]
from build_native_qml import native_export_panel
from chat_lock_ui import transform_export_panel
from optimization_ui import transform_export_panel as beta_panel


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve(strict=True)
    build_root = Path("D:/ValMAXBuild/build").resolve(strict=True)
    require(runtime.is_relative_to(build_root), "owned D build runtime required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve()
            and not args.report.exists(), "new migration proof required")
    exe = runtime / "valmax_qml_bridge_test.exe"
    plugin = runtime / "qml/ValMAX/Native/valmaxnativeplugin.dll"
    required = [exe, plugin, runtime / "valmax_core.dll", runtime / "Qt6Core.dll", runtime / "Qt6Qml.dll",
                runtime / "Qt6Quick.dll", runtime / "plugins/platforms/qwindows.dll"]
    require(all(path.is_file() for path in required), "complete ordinary native fixture required")
    paths = ["native/qt/export_harvest.js", "native/qt/history_model_cursor.cpp", "native/qt/history_model_cursor.h",
             "native/qt/plugin.cpp", "native/tooling/build_native_qml.py", "modkit/chat_export_panel.qml",
             "modkit/chat_lock_ui.py", "modkit/optimization_ui.py", "native/tests/qt/history_harvest.qml",
             "native/tests/qt/run_history_harvest.py", "native/tests/qt/run_native_media_plugin.py",
             "native/tests/qt/run_webview_blank_native.py"]
    hashes = {name: sha(ROOT / name) for name in paths}
    runtime_hashes = {path.relative_to(runtime).as_posix(): sha(path) for path in required}
    source = (ROOT / "modkit/chat_export_panel.qml").read_text(encoding="utf8")
    generated = native_export_panel(beta_panel(transform_export_panel(source)))
    start = "        function harvestPage() {\n"
    end = "        function nextMedia() {\n"
    require(generated.count(start) == 1 and generated.count(end) == 1, "generated harvest boundaries changed")
    functions = generated[generated.index(start):generated.index(end)]
    step_start = "        function historyStep() {\n"
    step_end = "        function pollHistoryProof() {\n"
    require(generated.count(step_start) == 1 and generated.count(step_end) == 1, "generated caller boundaries changed")
    caller = generated[generated.index(step_start):generated.index(step_end)]
    require("if (historyCursor.pendingCount > 0) return;" in caller,
            "actual caller must distinguish unready local rows from local exhaustion")
    canonical = (ROOT / "native/qt/export_harvest.js").read_text(encoding="utf8").replace("\r\n", "\n").rstrip() + "\n"
    require(functions == canonical, "test must execute actual generated harvest source byte for byte")
    require("historyCursor.reserve" in functions and "historyCursor.commit" in functions
            and "historyModel" not in functions, "native cursor collection required")
    template = Path(__file__).with_name("history_harvest.qml").read_text(encoding="utf8")
    marker = "    // GENERATED_HARVEST_FUNCTION"
    caller_marker = "    // GENERATED_HISTORY_STEP"
    require(template.count(marker) == 1 and template.count(caller_marker) == 1, "owned test source markers changed")
    fixture = build_root / ("qt-history-harvest-" + uuid.uuid4().hex)
    fixture.mkdir()
    qml = fixture / "history_harvest.qml"
    qml.write_text(template.replace(marker, functions).replace(caller_marker, caller), encoding="utf8")
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir()
    spec = importlib.util.spec_from_file_location("history_harvest_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    host.c.windll.kernel32.SetErrorMode(0x8003)
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QT_QUICK_BACKEND="software",
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(runtime / "qml"), QML2_IMPORT_PATH=str(runtime / "qml"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    result = host.run(fixture, exe, [runtime / "qml", qml], env, "harvest")
    require(Path(result["result"]["loadedNativePlugin"]).resolve() == plugin.resolve(), "actual plugin not loaded from supplied fixture")
    for field, filename in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
        require(Path(result["result"][field]).resolve() == (runtime / filename).resolve(), "Qt loaded outside supplied runtime")
    require(result["result"]["checks"] >= 50, "required native harvest scenarios did not finish")
    require(hashes == {name: sha(ROOT / name) for name in paths}, "test sources changed while running")
    require(runtime_hashes == {path.relative_to(runtime).as_posix(): sha(path) for path in required}, "native fixture changed while running")
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "runtime": str(runtime), "result": result,
             "sourceHashes": hashes, "runtimeInputs": runtime_hashes,
             "generatedFunctionSha256": hashlib.sha256(functions.encode("utf8")).hexdigest(),
             "generatedHistoryStepSha256": hashlib.sha256(caller.encode("utf8")).hexdigest(),
             "generatedQmlSha256": sha(qml), "realNativeCursor": True, "actualGeneratedFunction": True,
             "model": "Owned QML ListModel with QObject-valued messageContext and wrapper.get()",
             "syntheticOnly": True, "realMessagesRead": False, "networkUsed": False,
             "privateHistoryHookUsed": False, "serverHistoryEOFVerified": False, "fullChatExportVerified": False,
             "scope": "Real C++ model cursor plus actual generated harvestPage/historyStep: QVariant QObject wrapper identity, byte/time prefix acknowledgement, duplicate draining, pending/delayed dataChanged retry, null DTO, exceptions, model replacement, stale tickets and explicit detach. Actual caller waits on pending rows past 500 ms, retries readiness without dataChanged, permits paging/proof only on empty local queue and times out after 30 seconds without claiming completion. Message DTO, attachments, writer ACK, finish result and proof request are synthetic; deterministic lexical clock."}
    with args.report.open("x", encoding="utf8") as stream:
        stream.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "checks": result["result"]["checks"], "report": str(args.report), "pluginSha256": sha(plugin)}))


if __name__ == "__main__":
    main()
