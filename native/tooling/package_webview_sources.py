"""Package small corresponding sources/notices, without duplicating WebView2."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
deps = Path("C:/ValMAXBuild/deps/webview2-build")
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--upstream", type=Path, default=deps / "qt-source/qtwebview-6.11.2")
parser.add_argument("--generated", type=Path, default=Path("C:/ValMAXBuild/build/qt-webview-guard-source"))
parser.add_argument("--sdk", type=Path, default=deps / "microsoft-sdk")
parser.add_argument("--output", type=Path, default=deps / "redistribution")
parser.add_argument("--proof", type=Path, default=ROOT / "analysis/native-migration-v1/qt-webview2-source-distribution.json")
parser.add_argument("--webview2-mode", choices=("fixed", "evergreen"), default="fixed")
parser.add_argument("--guard-source-proof", type=Path)
parser.add_argument("--guarded-build-proof", type=Path)
parser.add_argument("--webview-proof", type=Path)
parser.add_argument("--guarded-plugin", type=Path)
args = parser.parse_args()
upstream, generated, output = args.upstream, args.generated, args.output


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


source_binding = None
binding_inputs = (args.guard_source_proof, args.webview_proof, args.guarded_plugin)
require(all(binding_inputs) or not any(binding_inputs), "all WebView source binding inputs are required together")
require(args.webview2_mode != "evergreen" or (all(binding_inputs) and args.guarded_build_proof),
        "Evergreen source distribution requires guarded source, build, and DLL proofs")
require(not args.guarded_build_proof or all(binding_inputs),
        "guarded build proof requires all WebView source binding inputs")
if all(binding_inputs):
    guard = json.loads(args.guard_source_proof.read_text("utf8"))
    webview = json.loads(args.webview_proof.read_text("utf8"))
    require(guard.get("schema") == 1 and Path(guard["output"]).resolve(strict=True) == generated.resolve(strict=True),
            "guarded source proof does not identify generated sources")
    require(args.webview2_mode in guard.get("supportedWebView2Modes", ()),
            "guarded source proof does not support selected WebView2 mode")
    original_names = {"qwebview2webview.cpp", "qwebview2webview_p.h",
                      "qwebview2webviewplugin.cpp", "windows.json"}
    expected_names = original_names | {"user_data_preflight.h"}
    require(set(guard.get("outputs", {})) == expected_names, "guarded source output set changed")
    require(set(guard.get("sourcePins", {})) == original_names,
            "upstream Qt source pin set changed")
    for name, expected in guard["sourcePins"].items():
        require(digest(upstream / "src/plugins/windows" / name) == expected,
                "upstream Qt source differs from guarded source proof: " + name)
    for name, expected in guard["outputs"].items():
        require(digest(generated / name) == expected,
                "generated WebView source differs from guard proof: " + name)
    for name in ("qwebview2webviewplugin.cpp", "windows.json"):
        require(guard["outputs"][name] == guard["sourcePins"][name],
                "copied Qt plugin input differs from upstream pin: " + name)
    patch = ROOT / "native/qt/webview/qt-6.11.2-init.patch"
    # Git on Windows may check a text patch out with CRLF. The generated proof
    # and redistributed patch use the canonical LF bytes from the generator.
    patch_bytes = patch.read_bytes().replace(b"\r\n", b"\n")
    patch_sha = hashlib.sha256(patch_bytes).hexdigest()
    require(patch_sha == guard.get("patchSha256"), "checked-in WebView patch differs from guard proof")
    guarded_cpp = (generated / "qwebview2webview.cpp").read_bytes()
    if args.webview2_mode == "evergreen":
        require(all(marker in guarded_cpp for marker in (
            b'VALMAX_WEBVIEW2_MODE', b'QStringLiteral("evergreen")',
            b'(evergreenMode && !fixedRuntime.isEmpty())')),
            "Evergreen guard is absent from generated WebView source")
    require(webview.get("verified") is True and webview.get("guardedBackend") is True
            and webview.get("webview2Mode") == args.webview2_mode
            and webview.get("fakeLoaderCalls") == 0,
            "guarded DLL proof does not match selected WebView2 mode")
    plugin_sha = webview.get("qtHashes", {}).get("plugins/webview/qtwebview_webview2.dll")
    require(isinstance(plugin_sha, str) and len(plugin_sha) == 64
            and Path(webview["fixture"]).resolve(strict=True) / "plugins/webview/qtwebview_webview2.dll"
                == args.guarded_plugin.resolve(strict=True)
            and digest(args.guarded_plugin) == plugin_sha,
            "guarded plugin bytes differ from WebView proof")
    build_binding = {}
    if args.guarded_build_proof:
        build = json.loads(args.guarded_build_proof.read_text("utf8"))
        require(build.get("schema") == 1 and build.get("verified") is True
                and build.get("webview2Mode") == args.webview2_mode
                and build.get("sourceInputsUnchanged") is True
                and build.get("windowsJsonConsumedByMoc") is True,
                "guarded DLL build proof is incomplete")
        require(Path(build["generatedSource"]).resolve(strict=True) == generated.resolve(strict=True)
                and Path(build["guardSourceProof"]).resolve(strict=True)
                    == args.guard_source_proof.resolve(strict=True)
                and build.get("guardSourceProofSha256") == digest(args.guard_source_proof)
                and build.get("sourceHashes") == guard["outputs"],
                "guarded DLL build inputs differ from source generator")
        cmake_source = ROOT / "native/qt/webview/CMakeLists.txt"
        require(build.get("pinnedBuildInputs", {}).get("CMakeLists", {}).get("sha256")
                    == digest(cmake_source)
                and digest(Path(build["pinnedBuildInputs"]["CMakeLists"]["path"]))
                    == digest(cmake_source),
                "guarded DLL CMake source changed")
        require(build.get("dllSha256") == plugin_sha and digest(Path(build["dll"])) == plugin_sha,
                "guarded DLL bytes differ from recorded source build")
        build_binding = {"guardedBuildProof": str(args.guarded_build_proof),
                         "guardedBuildProofSha256": digest(args.guarded_build_proof),
                         "guardedBuildDllSha256": build["dllSha256"]}
    source_binding = {
        "guardSourceProof": str(args.guard_source_proof),
        "guardSourceProofSha256": digest(args.guard_source_proof),
        "guardSourcePatchSha256": patch_sha,
        "generatedSourceSha256": guard["outputs"],
        "guardedWebViewProof": str(args.webview_proof),
        "guardedWebViewProofSha256": digest(args.webview_proof),
        "guardedWebViewPlugin": str(args.guarded_plugin),
        "guardedWebViewDllSha256": plugin_sha,
        **build_binding,
    }

output.mkdir(parents=True, exist_ok=True)
source_zip = output / "qtwebview-6.11.2-valmax-source.zip"
notices_zip = output / "webview-third-party-notices.zip"
readme = """ValMAX Qt WebView backend source

The qtwebview-6.11.2 directory is the original Qt v6.11.2 source module with
the guarded backend source/header and data preflight header. The plugin source
and windows.json are byte-pinned copies of the inputs used by the guarded
build. Changes are also available as qt-6.11.2-init.patch. Public Qt ABI is
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

The host must provide VALMAX_WEBVIEW2_MODE=fixed or evergreen and a validated
WEBVIEW2_USER_DATA_FOLDER path before process creation. Fixed mode additionally
requires a validated WEBVIEW2_BROWSER_EXECUTABLE_FOLDER. Evergreen mode
requires that variable to be absent and uses the system WebView2 Runtime.
There is no user-data fallback. The source does not contain user accounts or
credentials.
"""
with zipfile.ZipFile(source_zip, "w", zipfile.ZIP_DEFLATED) as archive:
    for file in sorted(upstream.rglob("*")):
        if file.is_file():
            relative = file.relative_to(upstream)
            selected = (generated/file.name if relative.parent.as_posix() == "src/plugins/windows"
                        and file.name in ("qwebview2webview.cpp", "qwebview2webview_p.h",
                                          "qwebview2webviewplugin.cpp", "windows.json") else file)
            archive.write(selected, "qtwebview-6.11.2/" + relative.as_posix())
    archive.write(generated/"user_data_preflight.h", "qtwebview-6.11.2/src/plugins/windows/user_data_preflight.h")
    archive.write(ROOT/"native/qt/webview/CMakeLists.txt", "build-config/CMakeLists.txt")
    if source_binding:
        archive.writestr("qt-6.11.2-init.patch", patch_bytes)
    else:
        archive.write(ROOT/"native/qt/webview/qt-6.11.2-init.patch", "qt-6.11.2-init.patch")
    archive.writestr("BUILD.txt", readme)
with zipfile.ZipFile(notices_zip, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.write(ROOT/"native/qt/webview/THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.txt")
    for file in sorted((upstream/"LICENSES").glob("*.txt")):
        archive.write(file, "Qt-WebView/" + file.name)
    for name in ("LICENSE.txt", "NOTICE.txt"):
        archive.write(args.sdk/name, "Microsoft-WebView2-SDK/"+name)
for path in (source_zip, notices_zip):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None: raise RuntimeError("archive_invalid")
if source_binding:
    with zipfile.ZipFile(source_zip) as archive:
        archived = {name: hashlib.sha256(archive.read("qtwebview-6.11.2/src/plugins/windows/" + name)).hexdigest()
                    for name in expected_names}
        require(archived == source_binding["generatedSourceSha256"],
                "redistributed WebView source differs from guarded source proof")
        require(hashlib.sha256(archive.read("qt-6.11.2-init.patch")).hexdigest()
                == source_binding["guardSourcePatchSha256"],
                "redistributed WebView patch differs from guarded source proof")
proof = {"schema": 1, "verified": True, "webview2Mode": args.webview2_mode, "fixedRuntimeDuplicated": False,
    "archives": [{"path": str(path), "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (source_zip,notices_zip)],
    "sourceBinding": source_binding,
    "releaseDistributionComplete": False,
    "remaining": ("Installer must include these sources/notices and applicable Microsoft runtime redistribution terms; test DLLs are excluded."
                  if args.webview2_mode == "fixed" else
                  "Installer must include these sources/notices, declare the system Evergreen prerequisite, and verify clean-PC/offline behavior; test DLLs are excluded.")}
args.proof.parent.mkdir(parents=True, exist_ok=True)
args.proof.write_text(json.dumps(proof, indent=2))
print(json.dumps(proof))
