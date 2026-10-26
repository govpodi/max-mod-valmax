"""Focused real-QML transport test; stub Bridge, synthetic secrets, no MData IO."""
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


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, default=Path("C:/ValMAXBuild/build/qt-support-0gb8v6n6"))
    parser.add_argument("--report", type=Path, default=ROOT / "analysis/native-migration-v1/mdata-transport-proof.json")
    parser.add_argument("--work-root", type=Path, choices=(Path("C:/ValMAXBuild"), Path("D:/ValMAXBuild"), Path("E:/ValMAXBuild")), default=Path("C:/ValMAXBuild"))
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    exe = runtime / "valmax_qml_bridge_test.exe"
    for name in (exe.name, "Qt6Core.dll", "Qt6Qml.dll", "Qt6Quick.dll", "plugins/platforms/qwindows.dll"):
        if not (runtime / name).is_file():
            raise RuntimeError("missing owned Qt fixture component")
    # Reuse the reviewed suspended-process/job helper, not a new process harness.
    spec = importlib.util.spec_from_file_location("mdata_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    job.c.windll.kernel32.SetErrorMode(0x8003)
    fixture = args.work_root / "build" / ("qt-mdata-transport-" + uuid.uuid4().hex)
    module = fixture / "qml/ValMAX/Native"
    module.mkdir(parents=True)
    (module / "qmldir").write_text("module ValMAX.Native\nsingleton Bridge 1.0 Bridge.qml\nsingleton MDataTransport 1.0 MDataTransport.qml\n", encoding="utf8")
    source = ROOT / "native/qt/MDataTransport.qml"
    shutil.copy2(source, module / source.name)
    shutil.copy2(Path(__file__).with_name("mdata_fake_bridge.qml"), module / "Bridge.qml")
    qml = fixture / "mdata_transport.qml"
    shutil.copy2(Path(__file__).with_name(qml.name), qml)
    for directory in ("home", "local", "roaming", "temp"):
        (fixture / directory).mkdir()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QT_DEBUG_PLUGINS="1",
               QT_QUICK_BACKEND="software", QML_DISABLE_DISK_CACHE="1",
               QML_IMPORT_PATH=str(fixture / "qml"), QML2_IMPORT_PATH=str(fixture / "qml"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"),
               APPDATA=str(fixture / "roaming"), TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    result = job.run(fixture, exe, [fixture / "qml", qml], env, "transport")
    value = result["result"]
    job.require(value["loadedNativePlugin"] == "", "test must use only the stub Bridge")
    hashes = {}
    expected = {"Qt6Core.dll": "22c113a5644b875b2c85482723cd5d2e6a3a4029169778fcc78adde08ef3b6aa",
                "Qt6Qml.dll": "257670ffa28fc84d4ec9f818de27e2e4611d9a0cffcb657179a210931e36671b",
                "Qt6Quick.dll": "ed2917b9905a5b867ecc192fa4429170494af0dcdaacc68053fe88948319cd08"}
    for key, name in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
        path = Path(value[key])
        job.require(path.resolve() == (runtime / name).resolve() and sha(path) == expected[name], "unexpected Qt runtime")
        hashes[name] = sha(path)
    files = ["native/qt/MDataTransport.qml", "native/tests/qt/mdata_fake_bridge.qml",
             "native/tests/qt/mdata_transport.qml", "native/tests/qt/run_mdata_transport.py"]
    proof = {"schema": 1, "verified": True, "fixture": str(fixture), "result": result,
             "sourceHashes": {name: sha(ROOT / name) for name in files},
             "harnessSha256": sha(exe), "qtHashes": hashes,
             "syntheticOnly": True, "bridgeStubbed": True, "nativePluginLoaded": False,
             "networkUsed": False, "realArchiveRead": False, "realArchiveWritten": False,
             "importAvailable": False, "exportTransportAvailable": True,
             "limits": ["Reference cleanup is checked; JS strings cannot be securely wiped",
                        "abort/close suppress delivery, not already admitted native work",
                        "Actual native inspection/export and UI integration are separate checks"]}
    destination = args.report.resolve()
    destination.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"verified": True, "checks": value["checks"], "proof": str(destination)}))


if __name__ == "__main__":
    main()
