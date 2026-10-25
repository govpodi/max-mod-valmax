"""Cache-enabled synthetic photo lifecycle comparison; no MAX, HTTP or profiles."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("photo_cache_owned_job", Path(__file__).with_name("run_native_media_plugin.py"))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
sha, require = host.sha, host.require


def invoke(arguments, directory, label):
    result = subprocess.run(list(map(str, arguments)), cwd=directory, capture_output=True, text=True, timeout=180,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    (directory / (label + ".log")).write_text(result.stdout + result.stderr, "utf8")
    require(result.returncode == 0, label + " failed; inspect owned log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--staging-proof", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    require(args.build.is_absolute() and args.build.resolve().is_relative_to(Path("D:/ValMAXBuild/build").resolve())
            and not args.build.exists(), "new owned D build required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve() and not args.report.exists(), "new proof required")
    stage = json.loads(args.staging_proof.read_text("utf8"))
    require(stage.get("assembled") is True and stage["dependencyClosure"]["verified"] is True, "completed runtime required")
    runtime = Path(stage["runtime"]) / "mod"
    pinned = {row["path"][4:].casefold(): row["sha256"] for row in stage["inventory"] if row["path"].startswith("mod/")}
    names = ["native/tests/qt/photo_cache_benchmark.cpp", "native/tests/qt/run_photo_cache_benchmark.py",
             "native/tests/qt/photo_cache/PhotoCell.qml.in", "native/tests/qt/photo_cache/scene.qml",
             "native/tests/qt/photo_cache/mask.frag", "modkit/media_resize_ui.py"]
    sources = {name: sha(ROOT / name) for name in names}
    # Consume the exact existing resize-debounce declaration as a literal only.
    module = ast.parse((ROOT / "modkit/media_resize_ui.py").read_text("utf8"))
    properties = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == "PHOTO_DECODE_PROPERTIES" for target in node.targets))
    require(properties.count("onSourceChanged: if (valmaxDecodeInitialized) valmaxCommitDecodeSize()") == 1, "source callback drift")
    properties = properties.replace("onSourceChanged: if (valmaxDecodeInitialized) valmaxCommitDecodeSize()",
        "onSourceChanged: { if (valmaxDecodeInitialized) valmaxCommitDecodeSize(); audit.sourceChanged(); }")
    require(properties.count("onStatusChanged: if (status === Image.Ready) valmaxDecodedSource = source") == 1, "status callback drift")
    properties = properties.replace("onStatusChanged: if (status === Image.Ready) valmaxDecodedSource = source",
        "onStatusChanged: { if (status === Image.Ready) valmaxDecodedSource = source; audit.fullStatus(root.logicalId, status === Image.Ready); }")
    require(properties.count("valmaxDecodeInitialized = true;") == 1, "completion callback drift")
    properties = properties.replace("valmaxDecodeInitialized = true;",
        "valmaxDecodeInitialized = true; audit.fullCreated(root.logicalId, root.viewportVisible, status === Image.Ready);")
    args.build.mkdir(parents=True)
    source = args.build / "source"
    source.mkdir()
    shutil.copy2(ROOT / names[0], source / "photo_cache_benchmark.cpp")
    cell = (ROOT / names[2]).read_text("utf8")
    require(cell.count("@@PHOTO_DECODE_PROPERTIES@@") == 1, "cell template drift")
    (source / "PhotoCell.qml").write_text(cell.replace("@@PHOTO_DECODE_PROPERTIES@@", properties), "utf8")
    for name in ("scene.qml", "mask.frag"):
        shutil.copy2(ROOT / "native/tests/qt/photo_cache" / name, source / name)
    (source / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.28)
project(ValMAXOwnedPhotoCache LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 6.11.2 EXACT REQUIRED COMPONENTS Core Gui Qml Quick)
add_executable(photo_cache_benchmark photo_cache_benchmark.cpp)
target_link_libraries(photo_cache_benchmark PRIVATE Qt6::Core Qt6::Gui Qt6::Qml Qt6::Quick)
target_compile_definitions(photo_cache_benchmark PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
target_compile_options(photo_cache_benchmark PRIVATE /W4 /WX /permissive-)
set_target_properties(photo_cache_benchmark PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
''', "utf8")
    invoke(["C:/ValMAXBuild/Qt/bin/qsb.exe", "--glsl", "100 es,120,150", "--hlsl", "50", "--msl", "12",
            "-o", source / "mask.frag.qsb", source / "mask.frag"], args.build, "shader")
    compiled = args.build / "compiled"
    invoke(["cmake", "-S", source, "-B", compiled, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt"], args.build, "configure")
    invoke(["cmake", "--build", compiled, "--parallel", "1"], args.build, "compile")
    executable = compiled / "photo_cache_benchmark.exe"
    results = {}
    checked_modules = {}
    for label in ("baseline", "candidate", "baselineRepeat", "candidateRepeat"):
        mode = "candidate" if label.startswith("candidate") else "baseline"
        fixture = args.build / label
        fixture.mkdir()
        for name in ("home", "local", "roaming", "temp"):
            (fixture / name).mkdir()
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
        env.update(PATH=str(runtime) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
                   QT_QPA_PLATFORM="windows", QT_PLUGIN_PATH=str(runtime / "plugins"), QML_IMPORT_PATH=str(runtime / "qml"),
                   QML2_IMPORT_PATH=str(runtime / "qml"), QML_DISABLE_DISK_CACHE="1", QSG_RHI_BACKEND="d3d11",
                   QSG_RHI_DISABLE_DISK_CACHE="1", USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"),
                   APPDATA=str(fixture / "roaming"), TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
        results[label] = host.run(fixture, executable, [source / "scene.qml", runtime, mode], env, label)
        for name, value in results[label]["result"]["loadedModules"].items():
            path = Path(value)
            relative = path.relative_to(runtime).as_posix().casefold()
            require(relative in pinned and sha(path) == pinned[relative], "Qt module outside frozen input: " + name)
            checked_modules[str(path)] = sha(path)
        require(len(results[label]["result"]["phases"]) == 7, "missing lifecycle phase")
    require(sources == {name: sha(ROOT / name) for name in names}, "benchmark source changed")
    proof = {"schema": 1, "verified": True, "sourceHashes": sources,
             "stagingProof": str(args.staging_proof), "stagingProofSha256": sha(args.staging_proof),
             "runtimeInventorySha256": stage["runtimeInventorySha256"], "fixture": str(args.build),
             "executableSha256": sha(executable), "generatedCellSha256": sha(source / "PhotoCell.qml"),
             "shaderSha256": sha(source / "mask.frag.qsb"), "verifiedLoadedModules": checked_modules,
             "results": results, "realClientStarted": False, "networkUsed": False, "profilesUsed": False,
             "productionChanged": False, "scope": "Two independent owned processes per mode. Qt 6.11.2 image provider, explicit cache:true, stable initial 704x180 decode-size role, 500px delegate buffer, native async Loader, exact existing 120ms sourceSize debounce and own rounded-layer ShaderEffect. A/B differs only by full-image Loader viewport activation; preview remains present. Provider calls/object lifetimes observed; no actual MAX FPS, GPU-time, source-server or disk-cache claim."}
    with args.report.open("x", encoding="utf8") as stream:
        json.dump(proof, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"verified": True, "checks": {key: value["result"]["checks"] for key, value in results.items()}, "report": str(args.report)}))


if __name__ == "__main__":
    main()
