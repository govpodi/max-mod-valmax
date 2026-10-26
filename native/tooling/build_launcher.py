"""Compile the ordinary C++ launcher for one immutable native runtime inventory.

No payload executable, installed client, user profile or credentials are opened
for execution. The receipt confirms compilation and bytes, not feature readiness.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OWNED = (Path("C:/ValMAXBuild/build"), Path("D:/ValMAXBuild/build"), Path("E:/ValMAXBuild/build"))
REPORT_ROOT = ROOT / "analysis/native-migration-v1"

def require(value, reason):
    if not value:
        raise ValueError(reason)

def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def no_reparse(path):
    for parent in (path, *path.parents):
        if parent.exists():
            require(not parent.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse path")

def owned_build(path):
    require(path.is_absolute() and path == path.absolute(), "explicit absolute build path required")
    require(all(part not in (".", "..") and not part.endswith((".", " ")) for part in path.parts[1:]), "ambiguous build path")
    require(any(path != parent and path.is_relative_to(parent) for parent in OWNED), "outside owned build root")
    no_reparse(path)
    return path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--webview2-mode", choices=("fixed", "evergreen"), default="fixed")
    parser.add_argument("--qt-source-mode", choices=("inline", "separate-asset"), default="inline")
    args = parser.parse_args()
    build = owned_build(args.build)
    runtime = owned_build(args.runtime_root)
    require(runtime.name == "runtime" and runtime.is_dir(), "final runtime directory required")
    require(not build.exists(), "new build directory required")
    require(not runtime.is_relative_to(build) and not build.is_relative_to(runtime), "build and payload may not nest")
    report = args.report
    require(report.is_absolute() and report != REPORT_ROOT and report.is_relative_to(REPORT_ROOT), "outside report root")
    no_reparse(report)
    require(not report.exists(), "new report required")
    compiler = Path(shutil.which("cl") or "")
    require(compiler.is_file(), "invoke inside msvc_env.ps1")
    spec = importlib.util.spec_from_file_location("valmax_launcher_generator", ROOT / "native/launcher/launcher_generate_pins.py")
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    inventory = generator.inventory(runtime, args.webview2_mode, args.qt_source_mode)
    sources = {path for path in (ROOT / "native/launcher").glob("*") if path.is_file()
        and (path.suffix in (".cpp", ".h", ".cmake", ".py") or path.name == "CMakeLists.txt")}
    sources.update(ROOT / name for name in ("native/core/valmax_core.h", "native/tooling/msvc_env.ps1",
        "native/transfer/closed_clients.cpp", "native/transfer/closed_clients.h",
        "native/transfer/closed_clients.cmake", "native/common/startup_context.cpp",
        "native/common/startup_context.h", "native/common/startup_context.cmake",
        "native/common/window_activation.h"))
    sources.add(Path(__file__).resolve())
    hashes = {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted(sources)}
    commands = {
        "configure": ["cmake", "-S", str(ROOT / "native/launcher"), "-B", str(build), "-G", "Ninja",
            "-DCMAKE_BUILD_TYPE=Release", f"-DVALMAX_LAUNCHER_RUNTIME_ROOT={runtime}",
            f"-DVALMAX_BUILD_PYTHON={sys.executable}",
            f"-DVALMAX_WEBVIEW2_MODE={args.webview2_mode}",
            f"-DVALMAX_QT_SOURCE_MODE={args.qt_source_mode}"],
        "build": ["cmake", "--build", str(build), "--target", "ValMAX", "--parallel", "1"],
    }
    build.mkdir(parents=True)
    for label, command in commands.items():
        result = subprocess.run(command, capture_output=True, encoding="utf8", errors="strict", timeout=900,
            creationflags=subprocess.CREATE_NO_WINDOW)
        (build / (label + ".log")).write_text(result.stdout + result.stderr, encoding="utf8")
        require(result.returncode == 0, label + " failed; inspect owned build log")
    require(inventory == generator.inventory(runtime, args.webview2_mode, args.qt_source_mode), "payload changed during compilation")
    require(hashes == {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted(sources)}, "source changed during compilation")
    pins = build / "launcher-pins.json"
    pinned = json.loads(pins.read_text(encoding="utf8"))
    require(pinned["files"] == inventory and pinned["webview2Mode"] == args.webview2_mode
            and pinned["qtSourceMode"] == args.qt_source_mode,
            "compiled inventory, WebView2 or Qt source mode differs")
    executable = build / "ValMAX.exe"
    require(executable.is_file(), "launcher artifact missing")
    icon_spec = importlib.util.spec_from_file_location("valmax_launcher_icon", ROOT / "native/launcher/launcher_icon.py")
    icon_generator = importlib.util.module_from_spec(icon_spec)
    icon_spec.loader.exec_module(icon_generator)
    icon_receipt = build / "stock-icon/launcher-icon.json"
    icon_verification = icon_generator.verify(runtime / "mod/max.exe", executable, icon_receipt)
    receipt = {"schema": 1, "compiled": True, "build": str(build), "runtimeRoot": str(runtime),
        "webview2Mode": args.webview2_mode,
        "qtSourceMode": args.qt_source_mode,
        "artifacts": {str(executable): sha(executable)}, "sourceHashes": hashes,
        "pinsReceipt": {"path": str(pins), "sha256": sha(pins)},
        "iconReceipt": {"path": str(icon_receipt), "sha256": sha(icon_receipt)},
        "iconVerification": icon_verification,
        "compiler": {"path": str(compiler), "sha256": sha(compiler)}, "commands": commands,
        "buildGlue": {str(build / name): sha(build / name) for name in
            ("CMakeCache.txt", "build.ninja", "launcher_pins.generated.h", "configure.log", "build.log")},
        "distributionReady": False, "runtimeExecuted": False, "protectionReadinessVerified": False,
        "installedClientChanged": False,
        "scope": "C++ compilation and complete runtime byte inventory; integration and release checks are separate"}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x", encoding="utf8") as stream:
        json.dump(receipt, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"compiled": True, "report": str(report), "executable": str(executable), "distributionReady": False}))

if __name__ == "__main__":
    main()
