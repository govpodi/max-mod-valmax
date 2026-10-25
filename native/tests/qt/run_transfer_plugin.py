"""Actual combined Qt/Rust transfer routes in an owned synthetic fixture."""
from __future__ import annotations
import argparse
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("transfer_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)
sha, require = job.sha, job.require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-proof", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    require(not args.report.exists(), "proof must be new")
    # Freeze the runner and QML fixture as well as the build's production input
    # set. A successful run must not be attributed to subsequently edited tests.
    proof_source_names = ("native/tests/qt/mdata_runtime.qml", "native/tests/qt/run_transfer_plugin.py")
    proof_sources = {name: sha(ROOT / name) for name in proof_source_names}
    job.c.windll.kernel32.SetErrorMode(0x8003)
    build = json.loads(args.build_proof.read_text("utf8"))
    directory, core, hooks = map(Path, (build["qtBuild"], build["coreDll"], build["hookBuild"]))
    for path, digest in build["sourceHashes"].items():
        require(sha(ROOT / path) == digest, "source changed: " + path)
    for path, digest in build["artifacts"].items():
        require(sha(path) == digest, "artifact changed: " + Path(path).name)
    require(sha(core) == build["corePin"]["sha256"] and core.stat().st_size == build["corePin"]["bytes"], "core pin mismatch")
    fixture = Path("C:/ValMAXBuild/build") / ("qt-transfer-fixture-" + uuid.uuid4().hex)
    fixture.mkdir()
    for source in job.QT.glob("*.dll"):
        if source.name.lower().startswith(("qt6", "msvcp", "vcruntime", "concrt", "api-ms-", "ucrt")):
            os.link(source, fixture / source.name)
    for name in ("plugins", "qml"):
        shutil.copytree(job.QT / name, fixture / name, copy_function=os.link,
                        ignore=lambda path, names: [n for n in names if n == "ValMAX"])
    shutil.copy2(job.QT / "qt.conf", fixture / "qt.conf")
    module = fixture / "qml/ValMAX/Native"
    module.mkdir(parents=True)
    for name in ("qmldir", "SettingsTransport.qml", "ChatLockTransport.qml", "SupportTransport.qml", "MDataTransport.qml", "valmaxnativeplugin.dll"):
        shutil.copy2(directory / "qml/ValMAX/Native" / name, module / name)
    shutil.copy2(core, fixture / core.name)
    for name in ("valmax_ghost.dll", "valmax_notifications.dll", "valmax_privacy.dll"):
        shutil.copy2(hooks / name, fixture / name)
    for source in hooks.glob("valmax-private-notification-*.png"):
        shutil.copy2(source, fixture / source.name)
    for source in directory.glob("*.exe"):
        shutil.copy2(source, fixture / source.name)
    for name in ("home", "local", "roaming", "temp", "synthetic-profile", "synthetic-runtime", "export-output"):
        (fixture / name).mkdir()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_QUICK_BACKEND="software", QT_DEBUG_PLUGINS="1",
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(fixture / "qml"), QML2_IMPORT_PATH=str(fixture / "qml"),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    config = json.dumps({"profileDirectory": str(fixture / "synthetic-profile"),
                         "runtimeDirectory": str(fixture / "synthetic-runtime"), "existingClient": False})
    # mdata_export::profile_path rejects this explicit test root before capture
    # or any WindowsCredentials method. Do not change it to an MXMOD fixture:
    # positive exports belong to the Rust tests with fake credentials.
    require((fixture / "synthetic-profile").resolve().name == "synthetic-profile",
            "native export negative test must reject the profile before WinCred")
    configured = {**env, "VALMAX_NATIVE_RUNTIME_CONFIG": config}
    dependencies = job.closure([module / "valmaxnativeplugin.dll", fixture / "plugins/platforms/qwindows.dll",
                                fixture / "qml/QtQuick/qtquick2plugin.dll"], fixture)
    imports = job.pefile.PE(str(module / "valmaxnativeplugin.dll")).DIRECTORY_ENTRY_IMPORT
    names = {entry.dll.decode("ascii").lower() for entry in imports}
    require({"valmax_core.dll", "valmax_ghost.dll", "valmax_notifications.dll", "valmax_privacy.dll"} <= names, "partial plugin")
    requested = {item.name.decode("ascii") for entry in imports if entry.dll.lower() == b"valmax_core.dll" for item in entry.imports if item.name}
    exported = {item.name.decode("ascii") for item in job.pefile.PE(str(fixture / core.name)).DIRECTORY_ENTRY_EXPORT.symbols if item.name}
    require(requested <= exported and "valmax_runtime_cancel_pending" in requested, "missing shutdown ABI")
    results = {}
    for name in ("valmax_privacy_host_test", "valmax_privacy_delivery_test", "valmax_protection_delivery_test", "valmax_ghost_host_test", "valmax_notification_host_test"):
        arguments = [fixture / (name + "-synthetic")] if name in ("valmax_ghost_host_test", "valmax_notification_host_test") else []
        results[name] = job.run(fixture, fixture / (name + ".exe"), arguments, env, name)

    # Fixture creation is build/test Python; the child process uses only Qt/C++/Rust.
    # No real profile, token, password, message, credential API or network is used.
    sys.path.insert(0, str(ROOT / "modkit"))
    import mdata_archive
    from session_archive import ArchivePolicy
    password = "synthetic-transfer-plugin-password"
    auth = json.dumps({"first_name": "Alice", "last_name": "Example", "short_name": "AE",
                       "private": "synthetic-private-not-projected"}).encode()
    manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                "credentials": [{"name": "access_token_42@MXMOD", "blob": base64.b64encode(b"synthetic-opaque-only").decode()}],
                "files": [{"path": "auth_42", "size": len(auth)}]}
    policy = mdata_archive.MDataPolicy(ArchivePolicy(), lambda name: name == "auth_42", lambda name: name == "access_token_42@MXMOD")
    archive = fixture / "synthetic-selected.mdata"
    with archive.open("xb") as out:
        mdata_archive.encrypt_stream(manifest, password, lambda name: io.BytesIO(auth), out, policy=policy, archive_kind="login")
    invalid = fixture / "synthetic-invalid.mdata"
    invalid.write_bytes(b"SYNTHETIC-NOT-ARCHIVE")
    export_file = fixture / "export-output/synthetic-export.mdata"
    require(not export_file.exists(), "export negative destination must start absent")
    expected = {"schema": 1, "kind": "login", "profiles": 1,
                "accounts": [{"accountId": "42", "displayName": "Alice Example", "shortName": "AE"}], "fingerprint": sha(archive)}
    qml = (ROOT / "native/tests/qt/mdata_runtime.qml").read_text("utf8")
    for token, value in {"@FILE@": archive.as_uri(), "@MISSING_FILE@": (fixture / "missing.mdata").as_uri(),
                         "@INVALID_FILE@": invalid.as_uri(), "@EXPORT_FILE@": export_file.as_uri(),
                         "@PASSWORD@": password, "@EXPECTED_PREVIEW@": expected}.items():
        require(qml.count(token) == 1, "fixture substitution changed")
        qml = qml.replace(token, json.dumps(value))
    selected = fixture / "mdata_runtime.qml"
    selected.write_text(qml, encoding="utf8")
    results["mdata_runtime"] = job.run(fixture, fixture / "valmax_qml_bridge_test.exe", [fixture / "qml", selected], configured, "mdata_runtime")
    require(Path(results["mdata_runtime"]["result"]["loadedNativePlugin"]).resolve() == (module / "valmaxnativeplugin.dll").resolve(), "actual plugin not loaded")
    require(not any(export_file.parent.iterdir()), "rejected native exports created output or temporary files")

    declaration = (ROOT / "native/qt/InlineMediaComposer.qml").read_text("utf8").replace("\r\n", "\n")
    wrapper = '''import QtQuick
import ValMAX.Native 1.0 as ValMAXNative
Item {
 id: root
 property bool textInputVisible: true
 property var modQuickReplySelected: null
 function setText(value, entities) { chatInput.text = value; }
 Item { id: chatInput; objectName: "synthetic-chat-input"; property string text: "@pic fixture"; function moveCursorToEnd() {} }
''' + declaration + '\n Component.onDestruction: modInlineReset(false)\n}\n'
    (fixture / "inline_native_fragment.qml").write_text(wrapper, encoding="utf8")
    shutil.copy2(ROOT / "native/tests/qt/all_features.qml", fixture / "all_features.qml")
    lifecycle = job.run(fixture, fixture / "valmax_all_features_test.exe", [fixture / "qml", fixture / "all_features.qml"], configured, "all_features")
    require(len(lifecycle["result"]["cycles"]) == 3, "missing lifecycle cycles")
    for name, loaded in lifecycle["result"]["loadedModules"].items():
        expected_module = module / name if name == "valmaxnativeplugin.dll" else fixture / name
        require(Path(loaded).resolve() == expected_module.resolve() and sha(loaded) == sha(expected_module), "loaded module mismatch: " + name)
    for key, name in (("loadedQtCore", "Qt6Core.dll"), ("loadedQtQml", "Qt6Qml.dll"), ("loadedQtQuick", "Qt6Quick.dll")):
        loaded = results["mdata_runtime"]["result"][key]
        require(Path(loaded).resolve() == (fixture / name).resolve() and sha(loaded) == sha(job.QT / name), "MData loaded Qt mismatch")
    for path, digest in build["sourceHashes"].items():
        require(sha(ROOT / path) == digest, "source changed during proof: " + path)
    for path, digest in build["artifacts"].items():
        require(sha(path) == digest, "artifact changed during proof: " + Path(path).name)
    for path, digest in proof_sources.items():
        require(sha(ROOT / path) == digest, "test source changed during proof: " + path)
    # Only owned synthetic data is present, but verify child logs still do not echo it.
    for path in fixture.glob("*.log"):
        text = path.read_text("utf8", errors="replace")
        require(all(secret not in text for secret in (password, "synthetic-private-not-projected", "synthetic-opaque-only")), "synthetic secret echoed")
    proof = {"schema": 1, "verified": True, "buildProof": str(args.build_proof), "buildProofSha256": sha(args.build_proof),
             "fixture": str(fixture), "results": results, "lifecycle": lifecycle, "dependencyClosure": dependencies,
             "coreImportedSymbols": sorted(requested), "corePin": build["corePin"], "syntheticOnly": True,
             "proofSourceHashes": proof_sources,
             "networkUsed": False, "actualMaxStarted": False, "installedClientChanged": False,
             "mdataImportAvailable": False, "mdataExportAvailable": True, "realCredentialReads": False,
             "successfulNativeExportTested": False, "exportNativeHistoryMediaHostConnected": False,
             "credentialBoundary": "Export requests reach native dispatch but the explicit synthetic-profile root is rejected as unsafe_path before WinCred; no real-credential instrumentation or positive export is claimed",
             "scope": "Actual Qt bridge authenticated selected-file preview, export route rejection before credentials, read-only status, abort/close late-completion suppression and combined lifecycle. Successful native exports are tested separately in Rust with fake credentials; import and recovery remain unavailable"}
    args.report.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf8")
    total = sum(item["result"]["checks"] for item in results.values()) + sum(item["checks"] for item in lifecycle["result"]["cycles"])
    total += lifecycle["result"]["mediaEngineChecks"] + lifecycle["result"]["mediaComposerChecks"]
    print(json.dumps({"verified": True, "checks": total, "proof": str(args.report)}))


if __name__ == "__main__":
    main()
