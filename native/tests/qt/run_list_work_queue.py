"""A/B public-counter QML invalidations on owned native Qt fixtures; no MAX/profile."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("queue_owned_process", Path(__file__).with_name("run_native_media_plugin.py"))
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
sha, require = host.sha, host.require


def command(arguments, cwd, label):
    result = subprocess.run(list(map(str, arguments)), cwd=cwd, capture_output=True, text=True, timeout=180)
    (cwd / (label + ".stdout.log")).write_text(result.stdout, "utf8")
    (cwd / (label + ".stderr.log")).write_text(result.stderr, "utf8")
    require(result.returncode == 0, label + " failed; see owned build log")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.build.resolve().is_relative_to(Path("D:/ValMAXBuild/build").resolve()), "owned build root required")
    require(args.baseline_source.resolve().is_relative_to(Path("D:/ValMAXBuild/build").resolve()), "owned baseline copy required")
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve() and not args.report.exists(), "new proof required")
    args.build.mkdir(parents=True, exist_ok=False)
    paths = ["native/qt/list_work_queue.h", "native/qt/list_work_queue.cpp",
             "native/tests/qt/list_work_queue_test.cpp", "native/tests/qt/run_list_work_queue.py"]
    sources = {name: sha(ROOT / name) for name in paths}
    baseline_sources = {name: sha(args.baseline_source / ("baseline_" + name)) for name in ("list_work_queue.h", "list_work_queue.cpp")}
    source = args.build / "source"
    source.mkdir()
    for label in ("current", "baseline"):
        folder = source / label
        folder.mkdir()
        for name in ("list_work_queue.h", "list_work_queue.cpp"):
            original = ROOT / "native/qt" / name if label == "current" else args.baseline_source / ("baseline_" + name)
            shutil.copy2(original, folder / name)
        shutil.copy2(ROOT / "native/tests/qt/list_work_queue_test.cpp", folder / "list_work_queue_test.cpp")
    (source / "CMakeLists.txt").write_text('''cmake_minimum_required(VERSION 3.28)
project(ValMAXListQueueProof LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 6.11.2 EXACT REQUIRED COMPONENTS Core Qml)
foreach(name current baseline)
    add_executable(queue_${name} ${name}/list_work_queue_test.cpp ${name}/list_work_queue.cpp ${name}/list_work_queue.h)
    target_include_directories(queue_${name} PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/${name})
    target_link_libraries(queue_${name} PRIVATE Qt6::Core Qt6::Qml)
    target_compile_definitions(queue_${name} PRIVATE QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)
    target_compile_options(queue_${name} PRIVATE /W4 /WX /permissive-)
    set_target_properties(queue_${name} PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
endforeach()
''', "utf8")
    cmake = shutil.which("cmake")
    require(cmake is not None, "cmake unavailable")
    output = args.build / "compiled"
    command([cmake, "-S", source, "-B", output, "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt"], args.build, "configure")
    command([cmake, "--build", output, "--parallel", "1"], args.build, "compile")
    fixture = args.build / "fixture"
    fixture.mkdir()
    names = ("Qt6Core.dll", "Qt6Qml.dll", "Qt6Network.dll", "Qt6QmlModels.dll", "Qt6QmlWorkerScript.dll",
             "Qt6QmlMeta.dll", "msvcp140.dll", "msvcp140_1.dll",
             "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll")
    copied = {}
    for name in names:
        require((args.runtime / name).is_file(), "missing fixture dependency: " + name)
        shutil.copy2(args.runtime / name, fixture / name)
        copied[name] = sha(fixture / name)
    qml = fixture / "qml"
    qml.mkdir()
    for name in ("home", "local", "roaming", "temp"):
        (fixture / name).mkdir()
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "PATHEXT") if key in os.environ}
    env.update(PATH=str(fixture) + os.pathsep + str(Path(os.environ["SystemRoot"]) / "System32"),
               QML_DISABLE_DISK_CACHE="1", QML_IMPORT_PATH=str(qml), QML2_IMPORT_PATH=str(qml),
               USERPROFILE=str(fixture / "home"), LOCALAPPDATA=str(fixture / "local"), APPDATA=str(fixture / "roaming"),
               TEMP=str(fixture / "temp"), TMP=str(fixture / "temp"))
    # Set only this process's child-error behavior; no registry or global changes.
    host.c.windll.kernel32.SetErrorMode(0x8003)
    results = {}
    entries = []
    for label in ("baseline", "current"):
        executable = fixture / ("queue_" + label + ".exe")
        shutil.copy2(output / executable.name, executable)
        entries.append(executable)
        results[label] = host.run(fixture, executable, ["--baseline"] if label == "baseline" else [], env, label)
    closure = host.closure(entries + list(qml.rglob("*.dll")), fixture)
    baseline_ns = statistics.median(item["guiThreadNanoseconds"] for item in results["baseline"]["result"]["benchmarks"])
    current_ns = statistics.median(item["guiThreadNanoseconds"] for item in results["current"]["result"]["benchmarks"])
    require(sources == {name: sha(ROOT / name) for name in paths}, "queue sources changed while testing")
    require(baseline_sources == {name: sha(args.baseline_source / ("baseline_" + name)) for name in baseline_sources}, "baseline snapshot changed")
    for name in ("home", "local", "roaming"):
        require(not any((fixture / name).iterdir()), "unexpected profile fixture write")
    proof = {"schema": 1, "verified": True, "sourceHashes": sources, "baselineSourceHashes": baseline_sources,
             "fixture": str(fixture), "fixtureFiles": copied, "dependencyClosure": closure, "results": results,
             "medianGuiThreadMilliseconds": {"baseline": baseline_ns / 1e6, "current": current_ns / 1e6},
             "syntheticGuiTimeRatio": baseline_ns / current_ns if current_ns else None,
             "actualClientPerformanceMeasured": False, "realClientStarted": False, "realProfileUsed": False,
             "networkUsed": False, "scope": "Actual native queue with real QQmlEngine binding and owned synthetic delegates. Same-count duplicate requests and no-op cancels: 40000 calls per trial, five trials per build; fixed synthetic binding CPU work. No frame-rate or full-client claim."}
    args.report.write_text(json.dumps(proof, indent=2) + "\n", "utf8")
    print(json.dumps({"verified": True, "checks": {key: value["result"]["checks"] for key, value in results.items()},
                      "medianGuiThreadMilliseconds": proof["medianGuiThreadMilliseconds"], "report": str(args.report)}))


if __name__ == "__main__":
    main()
