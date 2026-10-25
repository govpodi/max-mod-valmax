"""Package small corresponding sources/notices, without duplicating WebView2."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
deps = Path("C:/ValMAXBuild/deps/webview2-build")
upstream = deps / "qt-source/qtwebview-6.11.2"
generated = Path("C:/ValMAXBuild/build/qt-webview-guard-source")
output = deps / "redistribution"
output.mkdir(exist_ok=True)
source_zip = output / "qtwebview-6.11.2-valmax-source.zip"
notices_zip = output / "webview-third-party-notices.zip"
readme = """ValMAX Qt WebView backend source

The qtwebview-6.11.2 directory is the original Qt v6.11.2 source module with
three backend files replaced by the corresponding ValMAX build sources.
Their changes are also available as qt-6.11.2-init.patch. Public Qt ABI is
unchanged. The plugin uses the private Qt ABI, so use the exact Qt 6.11.2 x64
MSVC 2022 SDK and compatible matching Qt runtime DLLs.

Prerequisites: Windows x64, MSVC 2022, CMake >= 3.28, Ninja, Qt 6.11.2 Core,
Gui, Widgets and WebView SDK including private development files, and the
Microsoft.Web.WebView2 NuGet package 1.0.4258.31 (unzip it).

In a native MSVC x64 shell, from this archive's root:
cmake -S build-config -B build -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=<Qt-6.11.2-SDK> \
  -DVALMAX_QT_WEBVIEW_SOURCE=<absolute-root>/qtwebview-6.11.2/src/plugins/windows \
  -DWEBVIEW2_SDK_ROOT=<absolute-unpacked-Microsoft.Web.WebView2-package> \
  -DVALMAX_WEBVIEW_BUILD_TESTS=OFF
cmake --build build --target qtwebview_webview2

The result qtwebview_webview2.dll is a dynamically loaded plugin for the
matching Qt6WebView.dll. Source inputs and license texts are included; Qt SDK,
compiler and Microsoft's SDK are separate dependencies. The upstream module
also retains its regular Qt build files. The optional fault tests from the
ValMAX repository are deliberately excluded from this release source archive.

The host must provide validated WEBVIEW2_BROWSER_EXECUTABLE_FOLDER and
WEBVIEW2_USER_DATA_FOLDER paths before process creation; no user-data fallback
is allowed. The source does not contain user accounts or credentials.
"""
with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(upstream.rglob("*")):
        if file.is_file():
            relative = file.relative_to(upstream)
            selected = generated/file.name if relative.parent.as_posix() == "src/plugins/windows" and file.name in ("qwebview2webview.cpp", "qwebview2webview_p.h") else file
            archive.write(selected, "qtwebview-6.11.2/" + relative.as_posix())
    archive.write(generated/"user_data_preflight.h", "qtwebview-6.11.2/src/plugins/windows/user_data_preflight.h")
    archive.write(ROOT/"native/qt/webview/CMakeLists.txt", "build-config/CMakeLists.txt")
    archive.write(ROOT/"native/qt/webview/qt-6.11.2-init.patch", "qt-6.11.2-init.patch")
    archive.writestr("BUILD.txt", readme)
with zipfile.ZipFile(notices_zip, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.write(ROOT/"native/qt/webview/THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.txt")
    for file in sorted((upstream/"LICENSES").glob("*.txt")):
        archive.write(file, "Qt-WebView/" + file.name)
    for name in ("LICENSE.txt", "NOTICE.txt"):
        archive.write(deps/"microsoft-sdk"/name, "Microsoft-WebView2-SDK/"+name)
for path in (source_zip, notices_zip):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None: raise RuntimeError("archive_invalid")
proof = {"schema": 1, "verified": True, "fixedRuntimeDuplicated": False,
    "archives": [{"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (source_zip,notices_zip)],
    "releaseDistributionComplete": False,
    "remaining": "Installer must include these sources/notices and applicable Microsoft runtime redistribution terms; test DLLs are excluded."}
(ROOT/"analysis/native-migration-v1/qt-webview2-source-distribution.json").write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
