"""Build/test the pure C++ child-env builder; synthetic values, no client IO."""
from __future__ import annotations
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
BUILD = Path("C:/ValMAXBuild/build/launcher-environment")
OUTPUT = Path("C:/ValMAXBuild/fixtures/launcher-environment-v1")
SOURCES = [
    ROOT / "native/launcher/environment.h", ROOT / "native/launcher/environment.cpp",
    ROOT / "native/tests/launcher/environment_test.cpp", ROOT / "native/launcher/CMakeLists.txt",
    ROOT / "native/launcher/profile_client.cmake", ROOT / "docs/native-launcher-environment.md",
    Path(__file__).resolve(), ROOT / "modkit/business_environment.py",
    ROOT / "modkit/external_environment.py", ROOT / "modkit/optimization_store.py",
    ROOT / "modkit/launch_mod.py",
]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, name):
    completed = subprocess.run(args, cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    log = OUTPUT / name
    log.write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"{name}: nonzero exit; inspect synthetic build log")
    return completed.stdout


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    before = {str(path): sha(path) for path in SOURCES}
    run(["cmake", "-S", str(ROOT / "native/launcher"), "-B", str(BUILD), "-G", "Ninja",
         "-DCMAKE_BUILD_TYPE=Release"], "configure.txt")
    run(["cmake", "--build", str(BUILD), "--target", "valmax_environment_test"], "build.txt")
    tests = run(["ctest", "--test-dir", str(BUILD), "-R", "^child_environment_synthetic$", "--output-on-failure", "-V"], "tests.txt")
    reports = [json.loads(line[line.index('{'):]) for line in tests.splitlines() if '{"schema":1,"syntheticOnly"' in line]
    assert len(reports) == 1 and reports[0]["checks"] == 761 and reports[0]["syntheticOnly"] is True
    assert reports[0]["processesStarted"] == 0 and reports[0]["filesystemOperations"] == 0
    assert reports[0]["parentEnvironmentAccess"] is False
    obj = BUILD / "CMakeFiles/valmax_environment.dir/environment.cpp.obj"
    symbols = run(["dumpbin", "/symbols", str(obj)], "object-symbols.txt")
    imports = sorted(set(re.findall(r"UNDEF[^\r\n]*\| (__imp_[^\s]+)", symbols)))
    assert imports == ["__imp_CompareStringOrdinal"], imports
    after = {str(path): sha(path) for path in SOURCES}
    assert before == after, "source changed during proof"
    artifacts = [BUILD / "valmax_environment_test.exe", BUILD / "valmax_environment.lib", obj,
                 OUTPUT / "tests.txt", OUTPUT / "object-symbols.txt", OUTPUT / "build.txt", OUTPUT / "configure.txt"]
    proof = {
        "schema": 1, "checkedAtUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "verified": True, "scope": "pure synthetic C++ child environment builder only",
        "sources": after, "artifacts": {str(path): {"sha256": sha(path), "bytes": path.stat().st_size} for path in artifacts},
        "tests": reports[0], "objectWin32Imports": imports,
        "compiler": "MSVC x64 /W4 /WX /permissive- /utf-8; C++20; static CRT",
        "compatibility": {
            "preserved": ["explicit MXMOD/business-webview2", "QML_XHR_ALLOW_FILE_READ=1", "ordinary environment and render values", "child-only copy"],
            "intentionalChanges": ["fixed bundled WebView2 path required", "all inherited WEBVIEW2_* removed", "exact Qt plugin/path override list removed", "inherited VALMAX_NATIVE_RUNTIME_CONFIG/MAX_MOD_STARTUP_OPERATION removed", "retained case-insensitive duplicates rejected", "strict UTF-16 and project capacity bounds"],
            "noPyInstaller": "No frozen-bundle PATH filtering or parent SetDllDirectory; host DLL search contract remains separate"
        },
        "limitations": [
            "No MAX/WebView2 process started; no installed/real profile or credentials inspected",
            "Paths checked lexically only: host must retain verified payload and prepared directory guards",
            "PATH unchanged; not a DLL closure/search proof",
            "WebView2 registry policies are not read/disabled by this pure builder",
            "Allocator failure mapped in code, not fault-injected",
            "Future CreateProcessW integration and CREATE_UNICODE_ENVIRONMENT are not exercised here",
            "Host assembly incomplete: trusted runtime config/current receipt ID must be inserted explicitly by the future verified host",
            "Inherited values may contain secrets; builder does not log them or promise memory erasure"
        ],
    }
    target = ROOT / "analysis/native-migration-v1/launcher-environment-proof.json"
    target.write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified": True, "checks": reports[0]["checks"], "proof": str(target)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
