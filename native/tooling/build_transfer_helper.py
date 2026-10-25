"""Build the standalone transfer helper without changing the installed client."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BUILD_ROOT = Path("C:/ValMAXBuild")
WORK_ROOTS = (BUILD_ROOT, Path("D:/ValMAXBuild"))
REPORT_ROOT = ROOT / "analysis/native-migration-v1"
TARGETS = ("valmax-transfer", "valmax_transfer_app_test")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(ok, reason):
    if not ok:
        raise RuntimeError(reason)


def below(path, parent):
    require(path.is_absolute(), "absolute path required")
    value, allowed = path.resolve(), parent.resolve()
    require(value != allowed and value.is_relative_to(allowed), "output outside owned root")
    require(all(":" not in part and not part.endswith((".", " ")) for part in value.parts[1:]),
            "ambiguous output path")
    return value


def owned_build(path):
    for root in WORK_ROOTS:
        if path.is_absolute() and path.resolve().is_relative_to((root / "build").resolve()):
            return below(path, root / "build")
    raise RuntimeError("output outside owned build roots")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--core-directory", type=Path, required=True)
    parser.add_argument("--core-sha256", required=True)
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=1)
    parser.add_argument("--incremental", action="store_true",
                        help="Reuse this helper's owned CMake build; earlier build receipts become historical")
    args = parser.parse_args()
    build, report = owned_build(args.build), below(args.report, REPORT_ROOT)
    require(not report.exists(), "proof must be new")
    require(args.incremental or not build.exists(), "build directory must be new")
    compiler = Path(shutil.which("cl") or "")
    require(compiler.is_file(), "invoke inside native_env.ps1")
    core = owned_build(args.core_directory)
    require(re.fullmatch(r"[0-9a-f]{64}", args.core_sha256), "invalid Rust DLL hash")
    require(sha(core / "valmax_core.dll") == args.core_sha256, "frozen Rust DLL changed")
    require((core / "valmax_core.dll.lib").is_file(), "matching Rust import library missing")
    if args.incremental:
        cache = (build / "CMakeCache.txt").read_text(encoding="utf8")
        entries = dict(line.split("=", 1) for line in cache.splitlines() if "=" in line and not line.startswith(("#", "//")))
        for key, expected in (("CMAKE_HOME_DIRECTORY:INTERNAL", ROOT / "native/transfer/app"),
                              ("CMAKE_CACHEFILE_DIR:INTERNAL", build),
                              ("VALMAX_CORE_LIBRARY:FILEPATH", core / "valmax_core.dll.lib")):
            require(Path(entries.get(key, "")).resolve() == expected.resolve(), "unrelated incremental build: " + key)
    inputs = {str(core / name): sha(core / name) for name in ("valmax_core.dll", "valmax_core.dll.lib")}
    files = {path for path in (ROOT / "native/transfer").rglob("*") if path.is_file()
             and (path.suffix in (".cpp", ".h", ".qml", ".cmake", ".qrc", ".rc", ".manifest")
                  or path.name == "CMakeLists.txt")}
    files.update(ROOT / name for name in ("native/core/valmax_core.h", "native/tooling/native_env.ps1",
                                          "native/tooling/msvc_env.ps1", "native/common/startup_context.cpp",
                                          "native/common/startup_context.h", "native/common/startup_context.cmake"))
    files.add(Path(__file__).resolve())
    sources = {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted(files)}
    commands = {
        "configure": ["cmake", "-S", str(ROOT / "native/transfer/app"), "-B", str(build), "-G", "Ninja",
                      "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL",
                      "-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt", "-DVALMAX_TRANSFER_APP_BUILD_TESTS=ON",
                      "-DVALMAX_CORE_LIBRARY=" + str(core / "valmax_core.dll.lib")],
        "build": ["cmake", "--build", str(build), "--target", *TARGETS, "-j", str(args.jobs)],
    }
    build.mkdir(parents=True, exist_ok=args.incremental)
    for label, command in commands.items():
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, timeout=900, creationflags=subprocess.CREATE_NO_WINDOW)
        (build / (label + ".log")).write_text(result.stdout, encoding="utf8")
        if result.returncode:
            print(result.stdout[-12000:])
            raise RuntimeError(label + " failed")
    for path, digest in sources.items():
        require(sha(ROOT / path) == digest, "source changed during build: " + path)
    for path, digest in inputs.items():
        require(sha(path) == digest, "input changed during build: " + Path(path).name)
    artifacts = {str(build / (name + ".exe")): sha(build / (name + ".exe")) for name in TARGETS}
    artifacts.update(inputs)
    proof = {"schema": 1, "compiled": True, "build": str(build), "coreDll": str(core / "valmax_core.dll"),
             "coreSha256": args.core_sha256, "sourceHashes": sources, "artifacts": artifacts, "commands": commands,
             "compiler": {"path": str(compiler), "sha256": sha(compiler)},
             "buildGlue": {str(build / name): sha(build / name) for name in
                           ("CMakeCache.txt", "build.ninja", "configure.log", "build.log")},
             "installedClientChanged": False, "scope": "Standalone Qt helper build; runtime tests and packaging are separate proofs"}
    with report.open("x", encoding="utf8") as output:
        output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"compiled": True, "report": str(report), "build": str(build)}))


if __name__ == "__main__":
    main()
