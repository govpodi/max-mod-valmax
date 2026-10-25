"""Build the guarded Qt WebView DLL with a receipt for its actual source inputs.

Run from an x64 MSVC environment. The output directory must be new; this script
does not touch installed Qt, MAX, or a previous build. A source archive can be
called corresponding only when its hashes match this build receipt.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[2]
SOURCE_NAMES = (
    "qwebview2webview.cpp",
    "qwebview2webview_p.h",
    "qwebview2webviewplugin.cpp",
    "windows.json",
    "user_data_preflight.h",
)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def cache_entries(path: Path) -> dict[str, str]:
    entries = {}
    for line in path.read_text("utf8").splitlines():
        if line and not line.startswith(("#", "//")) and ":" in line and "=" in line:
            key, value = line.split("=", 1)
            entries[key.split(":", 1)[0]] = value
    return entries


def run(command: list[str], log: Path) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
    log.write_text(completed.stdout + completed.stderr, encoding="utf8")
    require(completed.returncode == 0, f"guarded_webview_build_failed:{log}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    parser.add_argument("--guard-source-proof", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--webview2-mode", choices=("fixed", "evergreen"), required=True)
    parser.add_argument("--cmake", type=Path, default=Path("D:/ValMAX/tools/native/python/Scripts/cmake.exe"))
    parser.add_argument("--ninja", type=Path, default=Path("D:/ValMAX/tools/native/python/Scripts/ninja.exe"))
    parser.add_argument("--qt-root", type=Path, default=Path("C:/ValMAXBuild/Qt"))
    parser.add_argument("--qt-webview-sdk", type=Path,
                        default=Path("C:/ValMAXBuild/deps/webview2-build/qt-sdk"))
    parser.add_argument("--webview2-sdk", type=Path,
                        default=Path("C:/ValMAXBuild/deps/webview2-build/microsoft-sdk"))
    args = parser.parse_args()
    generated = args.generated.resolve(strict=True)
    guard_proof = args.guard_source_proof.resolve(strict=True)
    build_dir = args.build_dir.resolve()
    proof_path = args.proof.resolve()
    cmake = args.cmake.resolve(strict=True)
    ninja = args.ninja.resolve(strict=True)
    qt_root = args.qt_root.resolve(strict=True)
    qt_webview_sdk = args.qt_webview_sdk.resolve(strict=True)
    webview2_sdk = args.webview2_sdk.resolve(strict=True)
    source_dir = (ROOT / "native/qt/webview").resolve(strict=True)
    cmake_lists = source_dir / "CMakeLists.txt"
    require(not build_dir.exists() and not proof_path.exists(), "fresh build directory and proof required")

    guard = json.loads(guard_proof.read_text("utf8"))
    require(guard.get("schema") == 1
            and Path(guard["output"]).resolve(strict=True) == generated
            and args.webview2_mode in guard.get("supportedWebView2Modes", ()),
            "guarded source proof does not match build inputs")
    require(set(guard.get("outputs", {})) == set(SOURCE_NAMES),
            "guarded source proof must pin all five compiled inputs")
    source_hashes = {name: sha(generated / name) for name in SOURCE_NAMES}
    require(source_hashes == guard["outputs"], "guarded source input changed before build")
    for name in ("qwebview2webviewplugin.cpp", "windows.json"):
        require(source_hashes[name] == guard.get("sourcePins", {}).get(name),
                "unmodified Qt plugin input differs from upstream pin: " + name)
    require(b'FILE "windows.json"' in (generated / "qwebview2webviewplugin.cpp").read_bytes(),
            "Qt plugin does not compile pinned windows.json metadata")

    qt_sdk_inputs = {
        "Qt6CoreLibrary": qt_root / "lib/Qt6Core.lib",
        "Qt6GuiLibrary": qt_root / "lib/Qt6Gui.lib",
        "Qt6WidgetsLibrary": qt_root / "lib/Qt6Widgets.lib",
        "Qt6WebViewLibrary": qt_webview_sdk / "lib/Qt6WebView.lib",
        "Qt6CoreVersion": qt_root / "include/QtCore/qtcoreversion.h",
        "Qt6WebViewVersion": qt_webview_sdk / "include/QtWebView/qtwebviewversion.h",
        "WebView2Header": webview2_sdk / "build/native/include/WebView2.h",
        "WebView2EnvironmentHeader": webview2_sdk / "build/native/include/WebView2EnvironmentOptions.h",
        "WebView2LoaderLibrary": webview2_sdk / "build/native/x64/WebView2LoaderStatic.lib",
    }
    compiler = shutil.which("cl.exe")
    linker = shutil.which("link.exe")
    require(compiler is not None and linker is not None, "x64 MSVC environment required")
    tools = {"cmake": cmake, "ninja": ninja,
             "compiler": Path(compiler).resolve(strict=True),
             "linker": Path(linker).resolve(strict=True)}
    pinned = {name: {"path": str(path.resolve(strict=True)), "sha256": sha(path)}
              for name, path in {**qt_sdk_inputs, **tools, "CMakeLists": cmake_lists,
                                 "guardSourceProof": guard_proof}.items()}

    configure = [str(cmake), "-S", str(source_dir), "-B", str(build_dir), "-G", "Ninja",
                 "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_MAKE_PROGRAM=" + str(ninja),
                 "-DCMAKE_PREFIX_PATH=" + str(qt_root) + ";" + str(qt_webview_sdk),
                 "-DQt6WebView_DIR=" + str(qt_webview_sdk / "lib/cmake/Qt6WebView"),
                 "-DQt6WebViewPrivate_DIR=" + str(qt_webview_sdk / "lib/cmake/Qt6WebViewPrivate"),
                 "-DVALMAX_QT_WEBVIEW_SOURCE=" + str(generated),
                 "-DWEBVIEW2_SDK_ROOT=" + str(webview2_sdk),
                 "-DVALMAX_WEBVIEW_BUILD_TESTS=OFF"]
    build_dir.mkdir(parents=True)
    run(configure, build_dir / "configure.log")
    cache_path = build_dir / "CMakeCache.txt"
    cache = cache_entries(cache_path)
    expected_paths = {"CMAKE_HOME_DIRECTORY": source_dir,
                      "CMAKE_MAKE_PROGRAM": ninja,
                      "CMAKE_CXX_COMPILER": tools["compiler"],
                      "CMAKE_LINKER": tools["linker"],
                      "VALMAX_QT_WEBVIEW_SOURCE": generated,
                      "WEBVIEW2_SDK_ROOT": webview2_sdk,
                      "Qt6Core_DIR": qt_root / "lib/cmake/Qt6Core",
                      "Qt6Gui_DIR": qt_root / "lib/cmake/Qt6Gui",
                      "Qt6Widgets_DIR": qt_root / "lib/cmake/Qt6Widgets",
                      "Qt6WebView_DIR": qt_webview_sdk / "lib/cmake/Qt6WebView",
                      "Qt6WebViewPrivate_DIR": qt_webview_sdk / "lib/cmake/Qt6WebViewPrivate"}
    for name, expected in expected_paths.items():
        require(Path(cache[name]).resolve(strict=True) == expected,
                "CMake cache uses an unexpected source or tool: " + name)
    require([Path(path).resolve(strict=True) for path in cache["CMAKE_PREFIX_PATH"].split(";")]
            == [qt_root, qt_webview_sdk], "CMake used an unexpected Qt prefix")
    require(cache.get("CMAKE_BUILD_TYPE") == "Release"
            and cache.get("VALMAX_WEBVIEW_BUILD_TESTS") == "OFF",
            "guarded WebView build configuration changed")
    ninja_file = build_dir / "build.ninja"
    ninja_text = ninja_file.read_text("utf8", errors="replace").replace("\\", "/").lower()
    for name in ("qwebview2webview.cpp", "qwebview2webviewplugin.cpp"):
        require(name.lower() in ninja_text, "CMake did not compile guarded input: " + name)
    target = "build qtwebview_webview2.dll:"
    require(ninja_text.count(target) == 1, "single guarded WebView link target required")
    link_rule = ninja_text.split(target, 1)[1].split("\nbuild ", 1)[0].replace("$:", ":")
    for name in ("Qt6CoreLibrary", "Qt6GuiLibrary", "Qt6WidgetsLibrary",
                 "Qt6WebViewLibrary", "WebView2LoaderLibrary"):
        require(Path(pinned[name]["path"]).as_posix().lower() in link_rule,
                "guarded WebView target did not link pinned input: " + name)

    run([str(cmake), "--build", str(build_dir), "--target", "qtwebview_webview2"],
        build_dir / "build.log")
    dll = build_dir / "qtwebview_webview2.dll"
    require(dll.is_file(), "guarded WebView DLL missing")
    moc_dependency = build_dir / "qtwebview_webview2_autogen/include/qwebview2webviewplugin.moc.d"
    dependency_text = moc_dependency.read_text("utf8", errors="replace").replace("\\", "/").lower()
    require((generated / "windows.json").as_posix().lower() in dependency_text,
            "Qt MOC did not consume pinned windows.json")
    for name in ("qwebview2webview.cpp", "qwebview2webviewplugin.cpp"):
        require(len(list(build_dir.rglob(name + ".obj"))) == 1,
                "guarded WebView object missing: " + name)
    require({name: sha(generated / name) for name in SOURCE_NAMES} == source_hashes,
            "guarded source input changed during build")
    for name, item in pinned.items():
        require(sha(Path(item["path"])) == item["sha256"],
                "Qt SDK, tool, or build input changed during build: " + name)
    receipt = {
        "schema": 1,
        "verified": True,
        "webview2Mode": args.webview2_mode,
        "guardSourceProof": str(guard_proof),
        "guardSourceProofSha256": sha(guard_proof),
        "generatedSource": str(generated),
        "sourceHashes": source_hashes,
        "sourceInputsUnchanged": True,
        "pinnedBuildInputs": pinned,
        "buildDir": str(build_dir),
        "cmakeCacheSha256": sha(cache_path),
        "buildNinjaSha256": sha(ninja_file),
        "mocDependencySha256": sha(moc_dependency),
        "windowsJsonConsumedByMoc": True,
        "dll": str(dll),
        "dllSha256": sha(dll),
        "dllBytes": dll.stat().st_size,
        "testTargetsEnabled": False,
    }
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    with proof_path.open("x", encoding="utf8") as output:
        json.dump(receipt, output, indent=2)
        output.write("\n")
    print(json.dumps({"verified": True, "dll": str(dll), "sha256": receipt["dllSha256"],
                      "proof": str(proof_path)}))


if __name__ == "__main__":
    main()
