"""Build the actual redistributed corresponding-source archive, tests disabled."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[3]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--archive", type=Path,
                    default=Path("C:/ValMAXBuild/deps/webview2-build/redistribution/qtwebview-6.11.2-valmax-source.zip"))
parser.add_argument("--fixture", type=Path, help="New isolated extraction/build directory")
parser.add_argument("--proof", type=Path,
                    default=ROOT / "analysis/native-migration-v1/qt-webview2-source-build-proof.json")
args = parser.parse_args()
archive = args.archive
if args.fixture:
    fixture = args.fixture
    fixture.mkdir(parents=True, exist_ok=False)
else:
    fixture = Path(tempfile.mkdtemp(prefix="qt-webview-source-", dir="E:/ValMAXBuild/build"))
with zipfile.ZipFile(archive) as source:
    for member in source.namelist():
        if member.startswith(("/", "\\")) or ".." in Path(member).parts or ":" in member:
            raise RuntimeError("invalid_source_archive_member")
    source.extractall(fixture)
cmake = "D:/ValMAX/tools/native/python/Scripts/cmake.exe"
commands = [
    [cmake, "-S", str(fixture/"build-config"), "-B", str(fixture/"build"), "-G", "Ninja",
     "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_PREFIX_PATH=C:/ValMAXBuild/Qt;C:/ValMAXBuild/deps/webview2-build/qt-sdk",
     "-DQt6WebView_DIR=C:/ValMAXBuild/deps/webview2-build/qt-sdk/lib/cmake/Qt6WebView",
     "-DQt6WebViewPrivate_DIR=C:/ValMAXBuild/deps/webview2-build/qt-sdk/lib/cmake/Qt6WebViewPrivate",
     "-DCMAKE_MAKE_PROGRAM=D:/ValMAX/tools/native/python/Scripts/ninja.exe",
     "-DVALMAX_QT_WEBVIEW_SOURCE=" + str(fixture/"qtwebview-6.11.2/src/plugins/windows"),
     "-DWEBVIEW2_SDK_ROOT=C:/ValMAXBuild/deps/webview2-build/microsoft-sdk",
     "-DVALMAX_WEBVIEW_BUILD_TESTS=OFF"],
    [cmake, "--build", str(fixture/"build"), "--target", "qtwebview_webview2"],
]
for i, command in enumerate(commands):
    run = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (fixture/f"build-{i}.log").write_text(run.stdout + run.stderr, encoding="utf8")
    if run.returncode:
        print(run.stdout + run.stderr); raise RuntimeError("source_archive_build_failed")
dll = fixture/"build/qtwebview_webview2.dll"
if not dll.is_file(): raise RuntimeError("source_archive_output_missing")
proof = {"schema": 1, "verified": True, "sourceArchive": str(archive),
    "sourceArchiveSha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "fixture": str(fixture),
    "builtDllSha256": hashlib.sha256(dll.read_bytes()).hexdigest(), "testTargetsEnabled": False,
    "browserLaunched": False, "abiClaim": "Compilation from packaged corresponding source only; runtime ABI proof uses the primary build."}
args.proof.parent.mkdir(parents=True, exist_ok=True)
args.proof.write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
