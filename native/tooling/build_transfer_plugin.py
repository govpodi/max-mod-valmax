"""Freeze the newly tested Rust pair and build a separate combined Qt plugin."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path("C:/ValMAXBuild/build/qt-runtime-transfer")
CORE = Path("C:/ValMAXBuild/target-runtime-transfer/release/deps")
HOOKS = Path("C:/ValMAXBuild/build/hooks-all-features")
REPORT = ROOT / "analysis/native-migration-v1/qt-runtime-transfer-build.json"
BUILD_ROOT = Path("C:/ValMAXBuild")
REPORT_ROOT = Path("D:/ValMAX/analysis/native-migration-v1")
TARGETS = ("valmaxnativeplugin", "valmax_all_features_test", "valmax_qml_bridge_test", "valmax_privacy_host_test",
           "valmax_privacy_delivery_test", "valmax_protection_delivery_test", "valmax_ghost_host_test", "valmax_notification_host_test")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(ok, why):
    if not ok:
        raise RuntimeError(why)


def checked_path(value: Path, root: Path, label: str) -> Path:
    require(value.is_absolute(), label + " must be absolute")
    # Resolve existing ancestors as well as lexical '..' before allowing build
    # tools to write. A junction cannot redirect a custom target outside its
    # explicitly permitted root. The root itself is never a target.
    resolved, allowed = value.resolve(), root.resolve()
    require(resolved != allowed and resolved.is_relative_to(allowed),
            label + " must be below " + str(root))
    require(all(":" not in part and not part.endswith((".", " "))
                for part in resolved.parts[1:]), label + " has an ambiguous component")
    return resolved


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, default=BUILD)
    parser.add_argument("--core-dir", type=Path, default=CORE)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    require(not args.report.exists() and not args.report.is_symlink(), "proof must be new")
    args.build = checked_path(args.build, BUILD_ROOT, "build directory")
    args.core_dir = checked_path(args.core_dir, BUILD_ROOT, "core directory")
    args.report = checked_path(args.report, REPORT_ROOT, "report")
    require(not args.report.exists(), "proof must be new")
    require(args.core_dir.is_dir(), "core directory must exist")
    require(args.report.parent.is_dir(), "report parent must exist")
    return args


def main():
    args = arguments()
    build_directory, source_core, report = args.build, args.core_dir, args.report
    compiler = Path(shutil.which("cl") or "")
    require(compiler.is_file(), "invoke inside native_env.ps1")
    pair = Path("C:/ValMAXBuild/build") / ("transfer-inputs-" + sha(source_core / "valmax_core.dll"))
    pair.mkdir(exist_ok=True)
    for name in ("valmax_core.dll", "valmax_core.dll.lib"):
        target = pair / name
        if target.exists():
            require(sha(target) == sha(source_core / name), "frozen pair collision")
        else:
            shutil.copy2(source_core / name, target)
    core = pair / "valmax_core.dll"
    pin = {"sha256": sha(core), "bytes": core.stat().st_size}
    prior = json.loads((ROOT / "analysis/native-migration-v1/qt-all-features-native-media-build.json").read_text("utf8"))
    inputs = {path: digest for path, digest in prior["artifacts"].items() if Path(path).parent.resolve() == HOOKS.resolve()}
    inputs.update({str(pair / name): sha(pair / name) for name in ("valmax_core.dll", "valmax_core.dll.lib")})
    for path, digest in inputs.items():
        require(sha(path) == digest, "input changed: " + Path(path).name)
    files = set()
    for folder in ("native/qt", "native/media", "native/core"):
        files.update(p for p in (ROOT / folder).rglob("*") if p.is_file() and (p.suffix in (".cpp", ".h", ".qml", ".cmake", ".inc", ".rs", ".toml") or p.name in ("CMakeLists.txt", "qmldir")))
    files.update(p for p in (ROOT / "native/tests/qt").glob("*") if p.suffix in (".cpp", ".qml", ".py"))
    files.update(ROOT / name for name in ("native/Cargo.toml", "native/Cargo.lock", "native/tooling/build_native_qml.py",
                 "native/tooling/native_env.ps1", "native/tooling/msvc_env.ps1", "native/hooks/privacy_api.h", "native/hooks/ghost.h", "native/hooks/notification_api.h"))
    files.add(Path(__file__).resolve())
    sources = {str(p.relative_to(ROOT)).replace("\\", "/"): sha(p) for p in sorted(files)}
    build_directory.mkdir(parents=True, exist_ok=True)
    commands = {
        "configure": ["cmake", "-S", str(ROOT / "native/qt"), "-B", str(build_directory), "-G", "Ninja", "-DCMAKE_BUILD_TYPE=Release",
                      "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreadedDLL", "-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt", "-DVALMAX_REQUIRE_ALL_FEATURES=ON",
                      "-DVALMAX_CORE_LIBRARY=" + str(pair / "valmax_core.dll.lib"), "-DVALMAX_CORE_PIN_SHA256=" + pin["sha256"],
                      "-DVALMAX_CORE_PIN_BYTES=" + str(pin["bytes"]), "-DVALMAX_PRIVACY_LIBRARY=" + str(HOOKS / "valmax_privacy.lib"),
                      "-DVALMAX_GHOST_LIBRARY=" + str(HOOKS / "valmax_ghost.lib"), "-DVALMAX_NOTIFICATION_LIBRARY=" + str(HOOKS / "valmax_notifications.lib")],
        "build": ["cmake", "--build", str(build_directory), "--target", *TARGETS, "-j", "4"],
    }
    for name, command in commands.items():
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=1200,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        (build_directory / (name + ".log")).write_text(result.stdout, encoding="utf8")
        if result.returncode:
            print(result.stdout[-12000:])
            raise RuntimeError(name + " failed")
    for path, digest in sources.items():
        require(sha(ROOT / path) == digest, "source changed during build: " + path)
    artifacts = dict(inputs)
    for target in TARGETS:
        path = build_directory / "qml/ValMAX/Native/valmaxnativeplugin.dll" if target == "valmaxnativeplugin" else build_directory / (target + ".exe")
        artifacts[str(path)] = sha(path)
    for name in ("qmldir", "SettingsTransport.qml", "ChatLockTransport.qml", "SupportTransport.qml", "MDataTransport.qml"):
        path = build_directory / "qml/ValMAX/Native" / name
        artifacts[str(path)] = sha(path)
    for path, digest in inputs.items():
        require(sha(path) == digest, "input changed during build: " + Path(path).name)
    proof = {"schema": 1, "compiled": True, "qtBuild": str(build_directory), "hookBuild": str(HOOKS), "coreDll": str(core), "corePin": pin,
             "sourceHashes": sources, "artifacts": artifacts, "commands": commands,
             "buildGlue": {str(build_directory / name): sha(build_directory / name) for name in ("CMakeCache.txt", "build.ninja", "configure.log", "build.log")},
             "compiler": {"path": str(compiler), "sha256": sha(compiler)}, "installedClientChanged": False,
             "scope": "Combined plugin with runtime transfer routes; integration execution is a separate proof"}
    # Refuse a report created by another process while the build was running.
    with report.open("x", encoding="utf8") as output:
        output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"compiled": True, "report": str(report), "corePin": pin}))


if __name__ == "__main__":
    main()
