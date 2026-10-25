"""Isolated QML import check for a staged runtime without QtWebEngine.

The tested bridge executable and Qt files are copied from pinned build/staging
inputs into a new E: fixture. MAX, the installed client and profiles are not run.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
from assemble_native_runtime import read_json, relative, require, sha  # noqa: E402
import run_native_media_plugin as job  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging-proof", required=True, type=Path)
    parser.add_argument("--build-proof", required=True, type=Path)
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--reuse-fixture", action="store_true",
                        help="Resume only an owned fixture that has not executed the test yet.")
    args = parser.parse_args()
    owned_build = Path("E:/ValMAXBuild/build").resolve(strict=True)
    require(args.fixture.is_absolute() and args.fixture.resolve() != owned_build
            and args.fixture.resolve().is_relative_to(owned_build)
            and (not args.fixture.exists() or args.reuse_fixture), "fresh E: fixture required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve()
            and not args.report.exists(), "fresh local analysis receipt required")

    staging, build = read_json(args.staging_proof), read_json(args.build_proof)
    require(staging["assembled"] is True and staging["qtWebEngineOmission"]["enabled"] is True,
            "pruned assembly receipt required")
    runtime = Path(staging["runtime"])
    mod = runtime / "mod"
    rows = {row["path"]: row for row in staging["inventory"]}
    executable = Path(build["qtBuild"]) / "valmax_qml_bridge_test.exe"
    require(build["artifacts"].get(str(executable)) == sha(executable),
            "bridge executable differs from verified p16 build")

    if args.fixture.exists():
        allowed = {source.name for source in mod.iterdir()
                   if source.is_file() and source.suffix.lower() == ".dll"}
        allowed |= {"qml", "plugins", "qt.conf", executable.name}
        require(args.fixture.is_dir() and {item.name for item in args.fixture.iterdir()} <= allowed,
                "existing fixture has unexpected files or has already run")
        for directory in ("qml", "plugins"):
            for existing in (args.fixture / directory).rglob("*"):
                if existing.is_file():
                    require((mod / existing.relative_to(args.fixture)).is_file(),
                            "existing fixture has unexpected Qt file")
    args.fixture.mkdir(exist_ok=args.reuse_fixture)
    copied = []
    for source in sorted(mod.iterdir()):
        if source.is_file() and source.suffix.lower() == ".dll":
            destination = args.fixture / source.name
            shutil.copy2(source, destination)
            name = "mod/" + source.name
            require(sha(destination) == rows[name]["sha256"], "copied root Qt image changed: " + name)
            copied.append(name)
    for directory in ("qml", "plugins"):
        shutil.copytree(mod / directory, args.fixture / directory,
                        dirs_exist_ok=args.reuse_fixture)
        for destination in (args.fixture / directory).rglob("*"):
            if destination.is_file():
                name = "mod/" + destination.relative_to(args.fixture).as_posix()
                require(sha(destination) == rows[name]["sha256"], "copied Qt module changed: " + name)
                copied.append(name)
    shutil.copy2(mod / "qt.conf", args.fixture / "qt.conf")
    shutil.copy2(executable, args.fixture / executable.name)
    require(not (args.fixture / "qml/QtWebEngine").exists(), "WebEngine QML unexpectedly present")
    original = (args.fixture / "qml/QtWebView/qmldir").read_bytes()
    webengine_dependency = b"depends QtWebEngine auto\r\n"
    require(original.count(webengine_dependency) == 1,
            "pinned QtWebView qmldir dependency changed")

    qml = args.fixture / "import-only.qml"
    qml.write_text("import QtQuick\nimport QtWebView\n"
                   "Item { property int checks: 1; property string failure: \"\"; "
                   "Timer { interval: 1; running: true; onTriggered: Qt.exit(0) } }\n", "utf8")
    for name in ("home", "local", "roaming", "temp"):
        (args.fixture / name).mkdir()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT")
           if key in os.environ}
    env.update(PATH=str(args.fixture) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QT_QPA_PLATFORM="windows", QT_QUICK_BACKEND="software", QT_DEBUG_PLUGINS="1",
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(args.fixture / "qml"),
               QML2_IMPORT_PATH=str(args.fixture / "qml"), QT_PLUGIN_PATH=str(args.fixture / "plugins"),
               QT_WEBVIEW_PLUGIN="webview2", USERPROFILE=str(args.fixture / "home"),
               LOCALAPPDATA=str(args.fixture / "local"), APPDATA=str(args.fixture / "roaming"),
               TEMP=str(args.fixture / "temp"), TMP=str(args.fixture / "temp"))

    def run(label):
        try:
            success = job.run(args.fixture, args.fixture / executable.name,
                              [args.fixture / "qml", qml], env, label)
            return {"loaded": True, "result": success["result"],
                    "process": success["process"], "stderrSha256": success["stderrSha256"]}
        except RuntimeError as error:
            stderr = (args.fixture / (label + ".stderr.log")).read_text("utf8", errors="replace")
            process = read_json(args.fixture / (label + ".process.json"))
            return {"loaded": False, "error": str(error), "stderrTail": stderr[-3000:],
                    "process": process, "stderrSha256": sha(args.fixture / (label + ".stderr.log"))}

    stock = run("stock-qmldir")
    (args.fixture / "qml/QtWebView/qmldir").write_bytes(
        original.replace(webengine_dependency, b""))
    adjusted = run("without-dependency")
    report = {"schema": 1, "stagingProof": str(args.staging_proof),
              "stagingProofSha256": sha(args.staging_proof), "buildProof": str(args.build_proof),
              "buildProofSha256": sha(args.build_proof), "fixture": str(args.fixture),
              "sourceFilesCopiedAndHashed": len(copied), "originalQmldirSha256": rows["mod/qml/QtWebView/qmldir"]["sha256"],
              "originalQmldirDependsQtWebEngine": True, "stock": stock, "adjusted": adjusted,
              "actualMaxStarted": False, "browserStarted": False, "privateProfilesRead": False,
              "scope": "Real Qt QML import in a single-process synthetic fixture; no WebView object or navigation."}
    with args.report.open("x", encoding="utf8") as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"stockImportLoaded": stock["loaded"], "adjustedImportLoaded": adjusted["loaded"],
                      "report": str(args.report)}))
    require(stock["loaded"], "stock QtWebView QML import failed in the slim runtime")


if __name__ == "__main__":
    main()
