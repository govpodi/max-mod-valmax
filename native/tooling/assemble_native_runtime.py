"""Assemble an independent developer runtime from frozen, previously tested inputs.

Build tooling only: no MAX, helper, browser, installer, server or target DLL is
executed. This is deliberately NOT a user-release packager. Unresolved QML is
preserved and reported; component receipts cannot clear product release gates.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tarfile
import tomllib
import zipfile

import pefile
from package_transfer_helper import system_dependency

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "analysis/native-migration-v1"
WORK = Path("D:/ValMAXBuild")
BUILD_ROOTS = (Path("C:/ValMAXBuild/build"), WORK / "build", Path("E:/ValMAXBuild/build"))
BASELINE = Path("D:/CodexLabs/max-mod-26.35.1/runtime/baseline")
LAYOUT = Path("D:/CodexLabs/max-mod-26.35.1/analysis/restore_layout_report.json")
FORBIDDEN_SUFFIXES = {".py", ".pyc", ".pyd", ".mdata", ".dmp", ".pdb", ".lib"}
FORBIDDEN_NAMES = {"privacy_monitor.js", "ghost_monitor.js", "notification_monitor.js",
                   "notification_updater.js", "message_retention_monitor.js", "chat_export_end_monitor.js",
                   "privacy_server.py", "optimization-active.json"}
LOCAL_ENDPOINT = re.compile(rb"127\.0\.0\.1:18973|localhost:18973|privacy_server|privacy_monitor\.js")
QT_CONF = b"[Paths]\nPrefix=.\nLibraries=.\nPlugins=plugins\nQmlImports=qml\nTranslations=translations\n"
QT_WEBENGINE_CONF = QT_CONF + b"LibraryExecutables=.\nData=.\n"
LIMITATIONS = [
    {"id": "message_retention", "reason": "Passive native capture and the account-scoped local archive are integrated as BETA. Mixed-account installed lifecycle, failure recovery and restart evidence remain insufficient for a full release."},
    {"id": "early_protection", "reason": "Dynamic ghost/notification readiness before first use remains unverified. Ordinary DLL presence is not a lifecycle barrier."},
    {"id": "installed_full_client", "reason": "This staging is not an installed full-client feature/lifecycle test. No real MAX was executed."},
]
QT_REMAINING_LAYOUT = {
    "qtlottie": {"Qt6Lottie.dll": True, "Qt6LottieVectorImageGenerator.dll": True},
    "qtmultimedia": {"Qt6Multimedia.dll": True, "Qt6MultimediaQuick.dll": False},
    "qtpositioning": {"Qt6Positioning.dll": False},
    "qtsvg": {"Qt6Svg.dll": True},
    "qtwebchannel": {"Qt6WebChannel.dll": False, "Qt6WebChannelQuick.dll": False},
}
QT_IMAGEFORMATS_DLLS = frozenset({"qicns.dll", "qtga.dll", "qtiff.dll", "qwbmp.dll", "qwebp.dll"})
QT_IMAGEFORMATS_LICENSES = frozenset({
    "LICENSES/BSD-3-Clause.txt", "LICENSES/GFDL-1.3-no-invariants-only.txt",
    "LICENSES/GPL-2.0-only.txt", "LICENSES/GPL-3.0-only.txt",
    "LICENSES/LGPL-3.0-only.txt", "LICENSES/LicenseRef-Qt-Commercial.txt",
    "LICENSES/libtiff.txt", "licenseRule.json",
})
QT_IMAGEFORMATS_THIRDPARTY = frozenset({
    "src/3rdparty/libtiff/COPYRIGHT", "src/3rdparty/libtiff/qt_attribution.json",
    "src/3rdparty/libwebp/COPYING", "src/3rdparty/libwebp/qt_attribution.json",
})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")


def relative(value):
    require(isinstance(value, str) and value and "\\" not in value, "invalid relative path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value and all(
        p not in (".", "..") and not p.endswith((".", " "))
        and not any(c in p for c in '<>:"|?*\\\0')
        and not re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p)
        and p.casefold() not in {"mxmod", "oneme", ".mxmod-helper-temp"}
        for p in path.parts), "unsafe relative path")
    return Path(*path.parts)


def ordinary(path, directory=False):
    info = Path(path).lstat()
    require(not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT, "reparse entry")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "nonordinary entry")
    if not directory:
        require(info.st_nlink == 1, "hardlinked input")
    return info


def ancestors(path):
    require(Path(path).is_absolute(), "absolute path required")
    for candidate in (Path(path), *Path(path).parents):
        if candidate.exists():
            ordinary(candidate, candidate.is_dir())


def inventory(root):
    ancestors(root)
    ordinary(root, True)
    result, seen, total = [], set(), 0
    for path in sorted(Path(root).rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        name = path.relative_to(root).as_posix()
        relative(name)
        require(name.casefold() not in seen, "case-alias entry")
        seen.add(name.casefold())
        if path.is_dir():
            ordinary(path, True)
            continue
        size = ordinary(path).st_size
        total += size
        require(size <= 1 << 31 and total <= 6 << 30 and len(result) < 16384, "inventory bounds")
        result.append({"path": name, "bytes": size, "sha256": sha(path)})
    return result


def read_json(path):
    ancestors(path)
    require(ordinary(path).st_size <= 16 << 20, "receipt size bound")
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, "duplicate receipt key")
            value[key] = item
        return value
    return json.loads(Path(path).read_text("utf8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite receipt")))


def verify_inventory(root, records):
    expected = [{"path": row["path"], "bytes": row.get("bytes", row.get("size")), "sha256": row["sha256"]}
                for row in records]
    require(len({row["path"].casefold() for row in expected}) == len(expected), "duplicate manifest entry")
    for row in expected:
        relative(row["path"])
        require(type(row["bytes"]) is int and row["bytes"] >= 0
                and isinstance(row["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid file record")
    actual = inventory(root)
    require(actual == sorted(expected, key=lambda row: row["path"]), "input tree differs from frozen inventory")
    return actual


def literal_specs(path):
    tree = ast.parse(Path(path).read_text("utf8"))
    assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                   and any(isinstance(v, ast.Name) and v.id == "PATCH_FILES" for v in node.targets)]
    require(len(assignments) == 1, "one literal PATCH_FILES required")
    return ast.literal_eval(assignments[0].value)


def existing_module(name):
    # Three reviewed, side-effect-free existing patch modules only. The old
    # launcher/package/server modules must never be imported by this tool.
    require(name in {"accounts", "privacy", "isolation", "build_mod"}, "unsupported patch module")
    path = ROOT / "modkit" / (name + ".py")
    spec = importlib.util.spec_from_file_location("staging_existing_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Plan:
    def __init__(self):
        self.entries = {}

    def add(self, destination, source, digest, component, replace=False):
        name = relative(destination).as_posix()
        require(Path(name).suffix.lower() not in FORBIDDEN_SUFFIXES and Path(name).name.lower() not in FORBIDDEN_NAMES
                and not Path(name).name.lower().startswith(("python", "frida")), "legacy/private/development runtime input")
        ancestors(source)
        ordinary(source)
        require(sha(source) == digest, "frozen input changed: " + name)
        key = name.casefold()
        require(key not in self.entries or replace, "destination collision: " + name)
        self.entries[key] = {"path": name, "source": str(source), "sha256": digest, "component": component}

    def tree(self, branch, directory, records, component):
        for row in verify_inventory(directory, records):
            self.add(branch + "/" + row["path"], directory / relative(row["path"]), row["sha256"], component)

    def copy(self, runtime):
        require(not runtime.exists(), "new runtime required")
        runtime.mkdir(parents=True)
        for row in sorted(self.entries.values(), key=lambda row: row["path"]):
            source, target = Path(row["source"]), runtime / relative(row["path"])
            require(sha(source) == row["sha256"], "source changed before copy")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            require(ordinary(target).st_nlink == 1 and sha(target) == row["sha256"], "copy not independent or changed")


def generated_sources(directory):
    proof_path = directory / "native-qml-proof.json"
    proof = read_json(proof_path)
    require(proof.get("nativeRuntime") is True, "native resource proof required")
    require(set(proof.get("generatedArtifacts", {})) == {"ui_native.py", "icons/icon-pack-plus-valmax.rcc"}, "unbound generated artifacts")
    checked = {}
    for name, digest in proof["generatedArtifacts"].items():
        path = directory / relative(name)
        ordinary(path)
        require(sha(path) == digest, "generated artifact changed")
    for name, digest in proof["sourceHashes"].items():
        require(name.startswith(":/qt/qml/") and "/" in name, "invalid source resource")
        checked[name] = directory / relative(name.rsplit("/", 1)[1])
        require(sha(checked[name]) == digest, "generated QML changed")
    for name, digest in proof["extraResourceHashes"].items():
        require(name.startswith(":/valmax/"), "invalid extra resource")
        path = directory / "icons/inputs" / relative(name[2:])
        ordinary(path)
        require(sha(path) == digest, "extra resource changed")
        checked[name] = path
    unresolved = [{"resource": name, "sha256": sha(path)} for name, path in sorted(checked.items())
                  if path.suffix == ".qml" and LOCAL_ENDPOINT.search(path.read_bytes())]
    return proof, unresolved


def add_qt_completion(plan, binary_path, materials_path):
    """Admit exact official release runtime files, not SDK tools/debug builds."""
    binary, materials = read_json(binary_path), read_json(materials_path)
    require(binary.get("verified") is True and binary.get("downloadOnly") is False
            and binary.get("qtVersion") == "6.11.2" and binary.get("architecture") == "msvc2022_64", "exact Qt completion required")
    require(materials.get("verified") is True and materials.get("binaryProofSha256") == sha(binary_path), "matching completion materials required")
    admitted, overlaps, release_images, versions = [], [], [], []
    modules = {row["module"]: row for row in materials["modules"]}
    require(set(modules) == {"qtserialport", "qtwebengine"}, "both corresponding-source modules required")
    for package in binary["archives"]:
        module = package["module"]
        require(module in modules and package.get("publishedSha256Verified") is True
                and package["url"].startswith("https://download.qt.io/"), "official Qt completion provenance required")
        require(sha(Path(package["path"])) == package["sha256"], "Qt binary archive changed")
        root = Path(package["extracted"])
        verify_inventory(root, package["files"])
        source_record = modules[module]
        require(source_record["binaryArchiveSha256"] == package["sha256"], "source/binary module mismatch")
        source_archive = source_record["sourceArchive"]
        require(source_archive.get("publishedSha256Verified") is True
                and source_archive["url"].startswith("https://download.qt.io/archive/qt/6.11/6.11.2/submodules/"), "official corresponding source required")
        plan.add("mod/licenses/QtRuntimeCompletion/sources/" + Path(source_archive["path"]).name,
                 Path(source_archive["path"]), source_archive["sha256"], "qt-completion-corresponding-source")
        for license_file in source_record["licenseFiles"]:
            plan.add("mod/licenses/QtRuntimeCompletion/" + license_file["path"],
                     Path(materials["materialsRoot"]) / relative(license_file["path"]), license_file["sha256"], "qt-completion-original-license-text")
        release_bins = {"Qt6SerialPort.dll"} if module == "qtserialport" else {
            "Qt6WebEngineCore.dll", "Qt6WebEngineQuick.dll", "Qt6WebEngineQuickDelegatesQml.dll",
            "Qt6WebEngineWidgets.dll", "QtWebEngineProcess.exe"}
        for row in package["files"]:
            name = row["path"]
            p = PurePosixPath(name)
            destination = None
            if len(p.parts) == 2 and p.parts[0] == "bin" and p.name in release_bins:
                destination = "mod/" + p.name
                release_images.append(p.name)
                image = pefile.PE(str(root / relative(name)), fast_load=True)
                try:
                    image.parse_data_directories(directories=[2])
                    version = image.VS_FIXEDFILEINFO[0]
                    numbers = [version.FileVersionMS >> 16, version.FileVersionMS & 65535,
                               version.FileVersionLS >> 16, version.FileVersionLS & 65535]
                    require(image.FILE_HEADER.Machine == 0x8664 and numbers == [6, 11, 2, 0]
                            and not version.FileFlags & 1, "wrong Qt completion binary version/architecture/debug flag")
                    versions.append({"path": destination, "sha256": row["sha256"], "version": numbers, "machine": "x64", "debug": False})
                finally:
                    image.close()
            elif module == "qtwebengine" and name.startswith(("qml/QtWebEngine/", "resources/", "translations/qtwebengine_locales/")):
                if p.name.endswith(("d.dll", ".debug.pak", ".debug.bin")):
                    continue
                destination = "mod/" + name
            elif name.startswith("sbom/"):
                destination = "mod/licenses/QtRuntimeCompletion/" + module + "/" + name
            if destination:
                existing = plan.entries.get(destination.casefold())
                if existing:
                    require(existing["sha256"] == row["sha256"], "official Qt archive does not match baseline plugin: " + name)
                    overlaps.append(destination)
                else:
                    plan.add(destination, root / relative(name), row["sha256"], "official-qt-runtime-completion")
                    admitted.append(destination)
        require(release_bins <= set(release_images), "required release Qt DLL/process missing")
    require({"mod/qml/QtWebEngine/qtwebenginequickplugin.dll", "mod/qml/QtWebEngine/ControlsDelegates/qtwebenginequickdelegatesplugin.dll"} <= set(overlaps),
            "baseline WebEngine plugins must be byte-identical to the official completion package")
    return {"modules": sorted(modules), "newRuntimeFiles": admitted, "byteIdenticalBaselineOverlaps": overlaps,
            "releaseImages": release_images, "resourcesAndAllLocalesIncluded": True,
            "binaryVersions": versions,
            "deploymentReference": "https://doc.qt.io/qt-6.11/qtwebengine-deploying.html", "targetsExecuted": False}


def add_qt_remaining_materials(plan, source_path, binary_path):
    """Bind additional Qt legal materials to identical staged release DLLs."""
    sources, binaries = read_json(source_path), read_json(binary_path)
    required = set(QT_REMAINING_LAYOUT)
    require(sources.get("schema") == 1 and sources.get("verified") is True
            and sources.get("officialSourceVersion") == "6.11.2"
            and binaries.get("schema") == 2 and binaries.get("officialVersion") == "6.11.2"
            and binaries.get("allExactBinaryMatches") is True,
            "official remaining Qt source/binary receipts required")
    source_rows = {row["module"]: row for row in sources["modules"]}
    binary_rows = {row["module"]: row for row in binaries["records"]}
    require(set(source_rows) == required and set(binary_rows) == required
            and len(sources["modules"]) == len(binaries["records"]) == len(required),
            "complete exact remaining Qt module set required")
    copied = []
    matched_images = 0
    for module in sorted(required):
        source, binary = source_rows[module], binary_rows[module]
        require(source["url"] == "https://download.qt.io/archive/qt/6.11/6.11.2/submodules/"
                + module + "-everywhere-src-6.11.2.tar.xz"
                and binary["url"].startswith("https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_6112/")
                and binary["exactBinaryMatch"] is True,
                "remaining Qt official source/binary identity differs")
        binary_archive = Path(binary["archive"])
        ancestors(binary_archive)
        ordinary(binary_archive)
        require(binary_archive.name == binary["url"].rsplit("/", 1)[1]
                and sha(binary_archive) == binary["archiveSha256"],
                "remaining Qt official binary archive changed")
        dll_rows = {row["name"]: row for row in binary["dlls"]}
        require(len(binary["dlls"]) == len(QT_REMAINING_LAYOUT[module])
                and set(dll_rows) == set(QT_REMAINING_LAYOUT[module]),
                "remaining Qt DLL set differs")
        for dll, service_copy in QT_REMAINING_LAYOUT[module].items():
            row = dll_rows[dll]
            expected_paths = {"mod/" + dll}
            if service_copy:
                expected_paths.add("mod/max-service/" + dll)
            images = {item["relativePath"]: item for item in row["runtimeImages"]}
            official_dll = Path(row["officialDll"])
            ancestors(official_dll)
            ordinary(official_dll)
            require(row["exactBinaryMatch"] is True
                    and sha(official_dll) == row["officialDllSha256"]
                    and len(images) == len(expected_paths) and set(images) == expected_paths,
                    "remaining Qt official DLL or runtime image set differs")
            for path in expected_paths:
                image = images[path]
                runtime_image = Path(image["path"])
                ancestors(runtime_image)
                ordinary(runtime_image)
                planned = plan.entries.get(path.casefold())
                require(image["exactBinaryMatch"] is True
                        and image["sha256"] == row["officialDllSha256"]
                        and sha(runtime_image) == image["sha256"]
                        and planned is not None and planned["sha256"] == image["sha256"],
                        "remaining Qt source is not bound to this runtime image: " + path)
                matched_images += 1
        archive = Path(source["archive"])
        require(archive.name == module + "-everywhere-src-6.11.2.tar.xz",
                "remaining Qt source archive name differs")
        plan.add("mod/licenses/QtRuntimeRemaining/sources/" + archive.name,
                 archive, source["sha256"], "qt-remaining-corresponding-source")
        require(isinstance(source["licenseTexts"], list) and source["licenseTexts"],
                "remaining Qt license texts missing")
        for item in source["licenseTexts"]:
            path = Path(item["path"])
            member = relative(item["archiveMember"]).parts
            require(len(member) >= 2 and member[0] == module + "-everywhere-src-6.11.2"
                    and tuple(path.parts[-len(member) + 1:]) == tuple(member[1:]),
                    "remaining Qt license member identity differs")
            destination = "mod/licenses/QtRuntimeRemaining/" + module + "/" + "/".join(member[1:])
            plan.add(destination, path, item["sha256"], "qt-remaining-original-license-text")
            copied.append(destination)
    return {"modules": sorted(required), "sourceProofSha256": sha(source_path),
            "binaryProofSha256": sha(binary_path), "licenseTexts": len(copied),
            "runtimeImagesMatched": matched_images,
            "runtimeDllsMatched": True, "targetsExecuted": False}


def add_qt_imageformats_materials(plan, audit_path, notices_path):
    """Bind QtImageFormats sources and notices to five exact planned DLLs."""
    audit, notices = read_json(audit_path), read_json(notices_path)
    required_modules = {"qtlottie", "qtmultimedia", "qtpositioning", "qtsvg", "qtwebchannel", "qtimageformats"}
    modules = audit.get("modules")
    require(audit.get("schema") == 1 and audit.get("verified") is True
            and audit.get("qtVersion") == "6.11.2" and audit.get("architecture") == "msvc2022_64"
            and isinstance(modules, list) and len(modules) == len(required_modules)
            and {row.get("module") for row in modules} == required_modules,
            "verified six-module Qt imageformats audit required")
    module = next(row for row in modules if row["module"] == "qtimageformats")
    source, binary = module["sourceArchive"], module["binaryArchive"]
    source_name = "qtimageformats-everywhere-src-6.11.2.tar.xz"
    source_archive = Path(source["archive"])
    metalink = Path(source["metalink"])
    binary_archive = Path(binary["archive"])
    official_index = Path(audit["officialIndex"])
    for path in (source_archive, metalink, binary_archive, official_index):
        ancestors(path)
        ordinary(path)
    require(source.get("module") == "qtimageformats"
            and source.get("url") == "https://download.qt.io/archive/qt/6.11/6.11.2/submodules/" + source_name
            and source_archive.name == source_name and sha(source_archive) == source["sha256"]
            and metalink.name == source_name + ".meta4"
            and sha(metalink) == source["metalinkSha256"],
            "official QtImageFormats source archive or metalink changed")
    require(binary.get("package") == "qt.qt6.6112.addons.qtimageformats.win64_msvc2022_64"
            and binary["url"].startswith("https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_6112/")
            and binary_archive.name == binary["url"].rsplit("/", 1)[1]
            and ordinary(binary_archive).st_size == binary["archiveBytes"]
            and sha(binary_archive) == binary["archiveSha256"],
            "official QtImageFormats binary archive changed")
    require(sha(official_index) == audit["officialIndexSha256"],
            "official Qt imageformats index changed")

    images = module["runtimeImages"]
    expected_images = {"mod/plugins/imageformats/" + name for name in QT_IMAGEFORMATS_DLLS}
    require(len(images) == len(expected_images)
            and {row["runtimePath"] for row in images} == expected_images
            and module.get("exactBinaryMatches") == len(expected_images),
            "exact QtImageFormats runtime image set required")
    for image in images:
        path = image["runtimePath"]
        official = image["officialDll"]
        selected = Path(official["path"])
        ancestors(selected)
        planned = plan.entries.get(path.casefold())
        require(official["member"] == "plugins\\imageformats\\" + PurePosixPath(path).name
                and ordinary(selected).st_size == official["bytes"]
                and sha(selected) == official["sha256"] == image["runtimeSha256"]
                and planned is not None and planned["sha256"] == image["runtimeSha256"]
                and sha(Path(planned["source"])) == image["runtimeSha256"],
                "QtImageFormats source does not match planned runtime image: " + path)

    require(notices.get("schema") == 1 and notices.get("verified") is True
            and notices.get("qtVersion") == "6.11.2"
            and notices.get("sourceAuditSha256") == sha(audit_path)
            and notices.get("sourceSha256") == source["sha256"]
            and Path(notices["sourceArchive"]) == source_archive
            and notices.get("completeForWholeClient") is False,
            "matching original QtImageFormats third-party notices required")
    license_rows = source["licenseTexts"]
    prefix = "qtimageformats-everywhere-src-6.11.2/"
    require(len(license_rows) == len(QT_IMAGEFORMATS_LICENSES)
            and {row["archiveMember"].removeprefix(prefix) for row in license_rows} == QT_IMAGEFORMATS_LICENSES
            and all(row["archiveMember"].startswith(prefix) for row in license_rows),
            "exact QtImageFormats original license text set required")
    original_rows = notices["originalTexts"]
    require(len(original_rows) == len(QT_IMAGEFORMATS_THIRDPARTY)
            and {row["archiveMember"].removeprefix(prefix) for row in original_rows} == QT_IMAGEFORMATS_THIRDPARTY
            and all(row["archiveMember"].startswith(prefix) for row in original_rows)
            and {(row["component"], PurePosixPath(row["archiveMember"]).name) for row in original_rows}
            == {("libtiff", "COPYRIGHT"), ("libtiff", "qt_attribution.json"),
                ("libwebp", "COPYING"), ("libwebp", "qt_attribution.json")},
            "exact original QtImageFormats codec notice set required")
    require({(row["component"], row["licenseFile"]) for row in notices["upstreamAttribution"]}
            == {("libtiff", "COPYRIGHT"), ("libwebp", "COPYING")}
            and len(notices["upstreamAttribution"]) == 2,
            "codec attribution does not match original notice files")

    # Read exact members from the official archive; no extraction to disk or
    # assertion that the optional codecs are linked into the binary is made.
    with tarfile.open(source_archive, "r:xz") as archive:
        for row in [*license_rows, *original_rows]:
            member = archive.getmember(row["archiveMember"])
            require(member.isfile() and member.size == row["bytes"],
                    "QtImageFormats source member is missing or not a plain file")
            stream = archive.extractfile(member)
            require(stream is not None and hashlib.sha256(stream.read()).hexdigest() == row["sha256"],
                    "QtImageFormats original source text differs from official archive")

    copied = []
    plan.add("mod/licenses/QtImageFormats/sources/" + source_name, source_archive,
             source["sha256"], "qt-imageformats-corresponding-source")
    copied.append("mod/licenses/QtImageFormats/sources/" + source_name)
    for row in license_rows:
        relative_member = row["archiveMember"][len(prefix):]
        path = Path(row["path"])
        require(tuple(path.parts[-len(PurePosixPath(relative_member).parts):])
                == PurePosixPath(relative_member).parts and ordinary(path).st_size == row["bytes"],
                "QtImageFormats license path or size differs")
        destination = "mod/licenses/QtImageFormats/" + relative_member
        plan.add(destination, path, row["sha256"], "qt-imageformats-original-license-text")
        copied.append(destination)
    for row in original_rows:
        relative_member = row["archiveMember"][len(prefix):]
        path = Path(row["path"])
        require(tuple(path.parts[-2:]) == (row["component"], PurePosixPath(relative_member).name)
                and ordinary(path).st_size == row["bytes"],
                "QtImageFormats codec notice path or size differs")
        destination = "mod/licenses/QtImageFormats/" + relative_member
        plan.add(destination, path, row["sha256"], "qt-imageformats-original-codec-notice")
        copied.append(destination)
    return {"module": "qtimageformats", "runtimeImagesMatched": len(images),
            "sourceArchiveSha256": source["sha256"], "auditProofSha256": sha(audit_path),
            "noticeProofSha256": sha(notices_path), "copiedMaterialFiles": len(copied),
            "codecBundlingProven": False, "wholeClientMaterialsComplete": False,
            "targetsExecuted": False}


def patched_files(baseline, native_specs):
    merged = {}
    for spec in [*(entry for name in ("accounts", "privacy", "isolation")
                   for entry in existing_module(name).PATCH_FILES), *native_specs]:
        name = relative(spec["relative_path"]).as_posix()
        old = merged.setdefault(name, {"relative_path": name, "sha256": spec["sha256"], "patches": []})
        require(old["sha256"] == spec["sha256"], "conflicting baseline pin")
        old["patches"].extend(spec["patches"])
    patch_bytes = existing_module("build_mod").patch_bytes
    prepared = []
    for name, spec in sorted(merged.items()):
        data = (baseline / relative(name)).read_bytes()
        require(hashlib.sha256(data).hexdigest() == spec["sha256"], "patch baseline mismatch")
        output = patch_bytes(data, spec["patches"])
        require(patch_bytes(output, spec["patches"], reverse=True) == data, "patch reversibility failed")
        prepared.append((name, output, {"path": "mod/" + name, "baselineSha256": spec["sha256"],
                         "sha256": hashlib.sha256(output).hexdigest(), "bytes": len(output),
                         "patchIds": [entry["id"] for entry in spec["patches"]], "reverseVerified": True}))
    return prepared


def dependency_closure(runtime, rows, source_paths=None, patched_bytes=None):
    """Bounded static PE direct/delay graph, preserving service/browser contexts.

    Does not load any target, consult PATH/SDK, infer LoadLibrary coverage, or
    claim imported-symbol/OS-version compatibility. The browser can contain
    x86 support binaries; OS lookup uses matching Windows system directory.
    """
    paths = {row["path"].casefold(): row["path"] for row in rows}
    edges, missing, machines = [], [], {}
    for row in rows:
        if Path(row["path"]).suffix.lower() not in {".dll", ".exe"}:
            continue
        name = row["path"]
        binary = source_paths[name] if source_paths else runtime / relative(name)
        pe = pefile.PE(data=patched_bytes[name], fast_load=True) if patched_bytes and name in patched_bytes else pefile.PE(str(binary), fast_load=True)
        try:
            require(pe.FILE_HEADER.Machine in (0x8664, 0x14c), "unsupported PE architecture")
            machine = pe.FILE_HEADER.Machine
            machines[name] = machine
            pe.parse_data_directories(directories=[1, 13])
            for kind, attribute, index in (("direct", "DIRECTORY_ENTRY_IMPORT", 1), ("delay", "DIRECTORY_ENTRY_DELAY_IMPORT", 13)):
                imports = getattr(pe, attribute, [])
                directory = pe.OPTIONAL_HEADER.DATA_DIRECTORY[index]
                require(not (directory.VirtualAddress and directory.Size and not imports), "unparsed import directory")
                require(len(imports) <= 4096, "PE import bounds")
                for entry in imports:
                    imported = entry.dll.decode("ascii").lower()
                    require(re.fullmatch(r"[a-z0-9_.-]+\.(dll|drv|cpl)", imported), "invalid PE import")
                    require(not imported.startswith(("python", "frida")), "legacy import")
                    branch = name.split("/", 1)[0]
                    app = "mod/max-service" if name.startswith("mod/max-service/") else branch
                    candidates = [(PurePosixPath(name).parent / imported).as_posix(), app + "/" + imported]
                    target = next((paths[v.casefold()] for v in candidates if v.casefold() in paths), None)
                    resolution = "bundled" if target else system_dependency(imported)
                    if not resolution:
                        # Browser/Qt may use additional ordinary Windows DLLs
                        # such as BTHPROPS.cpl. Match an exact system basename,
                        # never a PATH/SDK fallback or a package in another branch.
                        system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
                        system_path = system_root / ("System32" if machine == 0x8664 else "SysWOW64") / imported
                        if system_path.is_file():
                            # Windows component-store files normally have
                            # several NTFS links. They are observed in place,
                            # never copied into or accepted as package input.
                            system_info = system_path.lstat()
                            require(stat.S_ISREG(system_info.st_mode)
                                    and not getattr(system_info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                                    "nonordinary Windows system dependency")
                            resolution = "windows-system-file-present-on-build-host"
                    if machine == 0x14c and resolution and resolution.startswith("windows-system"):
                        resolution = "windows-system-x86-present" if (Path(os.environ.get("SystemRoot", "C:/Windows")) / "SysWOW64" / imported).is_file() else None
                    edge = {"image": name, "kind": kind, "import": imported, "resolution": resolution or "unresolved", "target": target}
                    edges.append(edge)
                    if not resolution:
                        missing.append(edge)
        finally:
            pe.close()
    # All bundled targets are checked after parsing: directory resolution alone
    # must not admit a wrong-architecture DLL with the same basename.
    for edge in edges:
        if edge["target"] and machines[edge["image"]] != machines.get(edge["target"]):
            missing.append({**edge, "resolution": "architecture-mismatch"})
    return {"images": len(machines), "edges": edges, "missing": missing,
            "verified": not missing, "actualLoaderTested": False, "symbolResolutionVerified": False,
            "scope": "Direct/delay module availability under explicit package branches plus API schema/system files on this build host. No dynamic-load or clean-machine proof."}


def write_new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(data)


def write_exact_or_new(path, data):
    if path.exists():
        ordinary(path)
        require(path.read_bytes() == data, "existing staging metadata differs")
    else:
        write_new(path, data)


def materials_status(helper, baseline_rows, browser, native_core, remaining_qt=None, imageformats=None):
    rust = read_json(helper / "licenses/Rust/rust-crates.json")
    packages = {(p["name"], p["version"]) for p in rust["packages"]}
    lock = tomllib.loads((ROOT / "native/Cargo.lock").read_text("utf8"))
    missing_crates = sorted([p["name"] + "-" + p["version"] for p in lock["package"]
                             if p.get("source", "").startswith("registry+") and (p["name"], p["version"]) not in packages])
    qt = read_json(helper / "licenses/Qt/source-materials/materials-manifest.json")
    limitations = [
        "The baseline MAX runtime contains no license/notice files; applicable MAX and bundled third-party redistribution materials have not been assembled.",
        "Modified Qt WebView source and original SDK notices are included; applicable Microsoft Fixed Version Runtime redistribution terms have not been independently collected here.",
        "Rust crate license files are reused by exact name/version and retain origin; no claim that a helper receipt is a receipt for the new common core.",
    ]
    if remaining_qt is None:
        limitations.insert(1, "Copied Qt materials are complete for the standalone helper, not every Qt module bundled by MAX (including multimedia/lottie/positioning/svg/webchannel). Whole-runtime applicability remains unverified.")
    else:
        limitations.insert(1, "Eight additional root Qt DLLs and four service copies now match official 6.11.2 binary archives and carry corresponding source/license texts; other bundled Qt plugins and third-party material scope still require whole-client review.")
    if imageformats is None:
        limitations.insert(1, "Five QtImageFormats plugins lack staged corresponding sources and original codec notices.")
    else:
        limitations.insert(1, "Five QtImageFormats plugins now match official 6.11.2 binaries and carry corresponding sources and original libtiff/libwebp notices; actual codec linkage and whole-client license scope remain unverified.")
    return {"completeForWholeClient": False, "commonCoreSha256": native_core,
            "helperMaterialsKeptIndependent": True,
            "copiedQtCorrespondingSourceModules": qt["modules"],
            "additionalQtMaterialProof": remaining_qt,
            "additionalQtImageFormatsMaterialProof": imageformats,
            "commonRustCargoLockSha256": sha(ROOT / "native/Cargo.lock"),
            "commonRustCargoLockMatchesWindowsNotices": sha(ROOT / "native/Cargo.lock") == rust["cargoLockSha256"],
            "crossPlatformLockEntriesAbsentFromWindowsTargetMaterials": missing_crates,
            "baselineNoticeFiles": [r["path"] for r in baseline_rows if re.search(r"license|notice|copying", r["path"], re.I)],
            "fixedBrowserFilesUnmodified": True,
            "limitations": limitations}


def assemble(args):
    output, report = args.output, args.report
    ancestors(output)
    ancestors(report)
    require(output.is_absolute()
            and any(output.resolve() != root.resolve() and output.resolve().is_relative_to(root.resolve())
                    for root in BUILD_ROOTS)
            and (not output.exists() or args.resume_output), "new owned build directory required")
    require(report.parent.resolve() == REPORTS.resolve() and not report.exists(), "new analysis receipt required")
    input_refs = {}
    patch_module_hashes = {name: sha(ROOT / "modkit" / (name + ".py"))
                           for name in ("accounts", "privacy", "isolation", "build_mod")}
    def receipt(label, path):
        value = read_json(path)
        input_refs[label] = {"path": str(path), "sha256": sha(path)}
        return value
    layout = receipt("baselineLayout", args.layout_proof)
    require(layout["output"] and Path(layout["output"]).resolve() == args.baseline.resolve()
            and layout.get("applications_executed") is False and layout["file_count"] == len(layout["layout"]), "frozen baseline proof required")
    baseline_rows = verify_inventory(args.baseline, layout["layout"])
    qml, unresolved = generated_sources(args.qml)
    input_refs["nativeQml"] = {"path": str(args.qml / "native-qml-proof.json"), "sha256": sha(args.qml / "native-qml-proof.json")}
    prepared = patched_files(args.baseline, literal_specs(args.qml / "ui_native.py"))
    build = receipt("ordinaryBuild", args.plugin_proof)
    runtime_proof = receipt("ordinaryRuntime", args.plugin_runtime_proof)
    require(build.get("compiled") is True and build.get("exportHistoryHookBuilt") is False
            and runtime_proof.get("verified") is True and runtime_proof.get("exportHistoryHookBuilt") is False
            and build.get("exportSelectionHostBuilt") is True
            and runtime_proof.get("exportSelectionHostBuilt") is True
            and runtime_proof.get("exportSelectionHostTested") is True
            and build.get("retentionBuilt") is True and runtime_proof.get("retentionBuilt") is True
            and runtime_proof.get("retentionHostBridgeTested") is True
            and runtime_proof.get("buildProofSha256") == sha(args.plugin_proof),
            "verified ordinary selected-view and passive-retention plugin required")
    require("-DVALMAX_EXPORT_HISTORY_RUNTIME_ROOT=" in build["commands"]["configure"], "EOF option must be empty")
    pair_path = Path(build["corePairProof"])
    require(sha(pair_path) == build["corePairProofSha256"], "core pair receipt changed")
    pair = receipt("ordinaryCorePair", pair_path)
    require(pair.get("verified") is True and pair.get("sourceSnapshotMatches") is True
            and pair.get("importLibrarySymbolsMatch") is True
            and pair["hashes"]["dll"] == build["coreSha256"], "verified matching core pair required")
    for name, digest in build["sourceHashes"].items():
        require(sha(ROOT / relative(name)) == digest, "ordinary source snapshot changed")
    for name, digest in build["artifacts"].items():
        require(sha(name) == digest, "ordinary binary changed")
    plan = Plan()
    plan.tree("mod", args.baseline, baseline_rows, "immutable-baseline")
    build_root, hooks = Path(build["qtBuild"]), Path(build["hookBuild"])
    from hook_target_identity import verify_hook_targets
    for name in ("privacy-pins.json", "privacy_pins.generated.h", "ghost-pins.json",
                 "ghost_pins.generated.h", "notification-pins.json", "notification_pins.generated.h"):
        path = hooks / name
        require(build.get("inputHashes", {}).get(str(path)) == sha(path), "guard metadata was not pinned by the plugin build")
    prepared_targets = {name: data for name, data, _ in prepared}
    hook_identity = verify_hook_targets(hooks, lambda name:
        prepared_targets[name] if name in prepared_targets else (args.baseline / relative(name)).read_bytes())
    module = build_root / "qml/ValMAX/Native"
    module_rows = inventory(module)
    for row in module_rows:
        if row["path"] != "valmaxnativeplugin.dll":
            require("/" not in row["path"] and sha(ROOT / "native/qt" / row["path"]) == row["sha256"], "built QML differs from verified source")
    plan.tree("mod/qml/ValMAX/Native", module, module_rows, "ordinary-native-qml")
    for filename in ("valmax_core.dll", "valmax_privacy.dll", "valmax_ghost.dll", "valmax_notifications.dll"):
        source = Path(build["coreInputs"]) / filename if filename == "valmax_core.dll" else hooks / filename
        require(str(source) in build["artifacts"], "ordinary artifact not in receipt")
        plan.add("mod/" + filename, source, build["artifacts"][str(source)], "ordinary-native")
    notification = receipt("notificationAsset", args.notification_proof)
    asset = notification["pins"]["asset"]
    plan.add("mod/" + asset["name"], hooks / asset["name"], asset["sha256"], "notification-neutral-asset")
    helper = receipt("transferPackage", args.helper_proof)
    require(helper.get("verifiedBundle") is True and helper.get("distributionReady") is True
            and helper.get("distributionBlockers") == [], "independent material-complete helper required")
    helper_root = Path(helper["package"])
    require(sha(helper_root / "package-manifest.json") == helper["manifestSha256"], "helper manifest changed")
    manifest = read_json(helper_root / "package-manifest.json")
    helper_rows = [*manifest["files"], {"path": "package-manifest.json", "bytes": (helper_root / "package-manifest.json").stat().st_size, "sha256": helper["manifestSha256"]}]
    plan.tree("transfer", helper_root, helper_rows, "standalone-transfer-with-independent-core-and-materials")
    browser = receipt("fixedWebView2", args.browser_proof)
    require(browser.get("verified") is True and browser["architecture"] == "x64", "verified fixed browser required")
    plan.tree("webview2", Path(browser["runtime"]), browser["files"], "fixed-webview2-unmodified")
    webview = receipt("guardedWebView", args.webview_proof)
    require(webview.get("verified") is True and webview.get("guardedBackend") is True
            and webview.get("fakeLoaderCalls") == 0, "production guarded WebView proof required")
    guarded = Path("C:/ValMAXBuild/build/qt-webview-guard/qtwebview_webview2.dll")
    plan.add("mod/plugins/webview/qtwebview_webview2.dll", guarded,
             webview["qtHashes"]["plugins/webview/qtwebview_webview2.dll"], "existing-guarded-qt-webview", replace=True)
    sources = receipt("webViewSources", args.webview_source_proof)
    require(sources.get("verified") is True and len(sources["archives"]) == 2, "WebView source/notice archives required")
    for row in sources["archives"]:
        source = Path(row["path"])
        require(source.stat().st_size == row["bytes"] and sha(source) == row["sha256"], "source archive changed")
        with zipfile.ZipFile(source) as archive:
            require(archive.testzip() is None, "corrupt source archive")
        plan.add("mod/licenses/WebView/" + source.name, source, row["sha256"], "existing-webview-source-materials")
    require(bool(args.qt_completion_proof) == bool(args.qt_completion_materials), "both Qt completion receipts required")
    completion = None
    qt_conf = QT_CONF
    if args.qt_completion_proof:
        receipt("qtRuntimeCompletion", args.qt_completion_proof)
        receipt("qtRuntimeCompletionMaterials", args.qt_completion_materials)
        completion = add_qt_completion(plan, args.qt_completion_proof, args.qt_completion_materials)
        qt_conf = QT_WEBENGINE_CONF
    require(bool(args.qt_remaining_source_proof) == bool(args.qt_remaining_binary_proof),
            "both remaining Qt source/binary receipts required")
    qt_remaining = None
    if args.qt_remaining_source_proof:
        receipt("qtRemainingSources", args.qt_remaining_source_proof)
        receipt("qtRemainingBinaries", args.qt_remaining_binary_proof)
        qt_remaining = add_qt_remaining_materials(plan, args.qt_remaining_source_proof,
                                                  args.qt_remaining_binary_proof)
    require(bool(args.qt_imageformats_audit_proof) == bool(args.qt_imageformats_thirdparty_proof),
            "both QtImageFormats audit and original notice receipts required")
    qt_imageformats = None
    if args.qt_imageformats_audit_proof:
        receipt("qtImageFormatsAudit", args.qt_imageformats_audit_proof)
        receipt("qtImageFormatsNotices", args.qt_imageformats_thirdparty_proof)
        qt_imageformats = add_qt_imageformats_materials(plan, args.qt_imageformats_audit_proof,
                                                       args.qt_imageformats_thirdparty_proof)
    if args.qt_sdk_supplement_proof:
        require(completion is not None, "SDK supplements require matching Qt completion")
        supplement = receipt("qtSdkSupplement", args.qt_sdk_supplement_proof)
        require(supplement.get("verified") is True and supplement.get("qtVersion") == "6.11.2"
                and supplement.get("architecture") == "msvc2022_64", "verified Qt SDK supplement required")
        require({row["destination"] for row in supplement["files"]} == {"Qt6QuickWidgets.dll", "Qt6PrintSupport.dll"}, "exact release SDK supplement set required")
        for row in supplement["files"]:
            require(row.get("sdkByteIdentityVerified") is True and row["version"] == [6, 11, 2, 0]
                    and row["debug"] is False and row["archiveUrl"].startswith("https://download.qt.io/"), "incorrect SDK supplement")
            require(sha(Path(row["archive"])) == row["archiveSha256"], "SDK source archive changed")
            destination = "mod/" + row["destination"]
            plan.add(destination, Path(row["path"]), row["sha256"], "verified-existing-sdk-runtime-dependency")
            completion["releaseImages"].append(row["destination"])
            completion["binaryVersions"].append({"path": destination, "sha256": row["sha256"], "version": row["version"], "machine": "x64", "debug": False})
    # Explicitly retain origins and applicability limits, never relabel old
    # helper proofs as proof that the whole MAX runtime is distributable.
    for row in inventory(helper_root / "licenses"):
        plan.add("mod/licenses/shared-component-materials/" + row["path"], helper_root / "licenses" / relative(row["path"]), row["sha256"], "helper-materials-reused-with-limits")
    material_status = materials_status(helper_root, baseline_rows, browser, build["coreSha256"],
                                       qt_remaining, qt_imageformats)
    materials_bytes = json.dumps(material_status, indent=2).encode("utf8") + b"\n"
    warning = ("DEVELOPER STAGING ONLY - NOT A USER RELEASE\n"
               "No installed MAX or real profile was launched. No Python, external JS observer or local server is bundled.\n"
               "Passive message retention and selected-chat export are BETA; coverage and complete history are unverified.\n"
               "Early protection, whole-client licenses and installed lifecycle remain unverified.\n")
    # Resolve the complete future PE graph from frozen source paths and the
    # actual prepared patched bytes BEFORE another bulky runtime is copied.
    planned_rows = [{"path": row["path"], "bytes": Path(row["source"]).stat().st_size, "sha256": row["sha256"]}
                    for row in plan.entries.values()]
    planned_paths = {row["path"]: Path(row["source"]) for row in plan.entries.values()}
    planned_patches = {"mod/" + name: data for name, data, _ in prepared}
    planned_closure = dependency_closure(output / "runtime", planned_rows, planned_paths, planned_patches)
    print(json.dumps({"plannedPeImages": planned_closure["images"], "plannedMissingDependencies": planned_closure["missing"],
                      "plannedDependencyClosureVerified": planned_closure["verified"], "runtimeCopyStarted": False}), flush=True)
    if completion:
        require(planned_closure["verified"], "completed Qt runtime plan still has missing dependencies; no bulky copy was started")
    needed = sum(Path(row["source"]).stat().st_size for row in plan.entries.values()) + sum(len(p[1]) for p in prepared)
    runtime = output / "runtime"
    patches = [row for _, _, row in prepared]
    if output.exists():
        # Explicit recovery of our complete independent copied tree. No partial
        # or arbitrary preexisting file is reused, rewritten, or removed.
        require(args.resume_output and {p.name for p in output.iterdir()} <= {"runtime", "STAGING-NOT-RELEASE.txt"}
                and runtime.is_dir(), "resume requires only a complete unreceipted runtime")
        expected = {row["path"]: {"path": row["path"], "bytes": Path(row["source"]).stat().st_size, "sha256": row["sha256"]}
                    for row in plan.entries.values()}
        for row in patches:
            expected[row["path"]] = {key: row[key] for key in ("path", "bytes", "sha256")}
        expected["mod/qt.conf"] = {"path": "mod/qt.conf", "bytes": len(qt_conf), "sha256": hashlib.sha256(qt_conf).hexdigest()}
        if (runtime / "mod/licenses/STAGING-MATERIALS.json").exists():
            expected["mod/licenses/STAGING-MATERIALS.json"] = {"path": "mod/licenses/STAGING-MATERIALS.json", "bytes": len(materials_bytes), "sha256": hashlib.sha256(materials_bytes).hexdigest()}
        verify_inventory(runtime, list(expected.values()))
    else:
        space_root = next(parent for parent in (output, *output.parents) if parent.exists())
        require(shutil.disk_usage(space_root).free > needed + (1 << 30), "insufficient output-volume space for independent staging")
        output.mkdir(parents=True)
        plan.copy(runtime)
        for name, data, row in prepared:
            # Replacing only newly copied independent files; baseline untouched.
            (runtime / "mod" / relative(name)).write_bytes(data)
            require(sha(runtime / relative(row["path"])) == row["sha256"], "patched output differs")
        write_new(runtime / "mod/qt.conf", qt_conf)
    write_exact_or_new(runtime / "mod/licenses/STAGING-MATERIALS.json", materials_bytes)
    write_exact_or_new(output / "STAGING-NOT-RELEASE.txt", warning.encode("utf8"))
    rows = inventory(runtime)
    retention_pins = read_json(Path(build["retentionPinProof"]))
    require(build["artifacts"].get(build["retentionPinProof"]) == sha(build["retentionPinProof"])
            and retention_pins.get("historySyncVerified") is False
            and retention_pins.get("realProfilesAccessed") is False
            and all(sha(runtime / "mod" / name) == retention_pins["images"][name]["sha256"]
                    for name in ("core.dll", "storage.dll")),
            "staged passive retention image pair differs from compiled pins")
    closure = dependency_closure(runtime, rows)
    required = ["mod/" + name for name in qml["requiredNativeFiles"]]
    paths = {row["path"] for row in rows}
    require(set(required) <= paths, "generated native dependency missing")
    tls = "mod/plugins/tls/qschannelbackend.dll"
    require(tls in paths and sha(runtime / relative(tls)) == runtime_proof["bundledSchannelSha256"], "tested bundled TLS backend missing/changed")
    require(verify_inventory(args.baseline, baseline_rows) == baseline_rows, "baseline changed during assembly")
    for ref in input_refs.values():
        require(sha(ref["path"]) == ref["sha256"], "receipt changed during assembly")
    require(generated_sources(args.qml) == (qml, unresolved), "generated inputs changed during assembly")
    require(all(sha(ROOT / "modkit" / (name + ".py")) == digest for name, digest in patch_module_hashes.items()),
            "patch sources changed during assembly")
    require(all(sha(ROOT / relative(name)) == digest for name, digest in build["sourceHashes"].items()),
            "ordinary sources changed during assembly")
    require(verify_hook_targets(hooks, lambda name: (runtime / "mod" / relative(name)).read_bytes()) == hook_identity,
            "guard target identity changed during assembly")
    for item in hook_identity["hooks"].values():
        require(sha(runtime / "mod" / item["dll"]) == item["dllSha256"], "staged guard DLL differs from verified metadata")
    result = {"schema": 1, "assembled": True, "distributionMode": "developer-staging", "releaseEligible": False,
              "runtime": str(runtime), "files": len(rows), "bytes": sum(row["bytes"] for row in rows),
              "runtimeInventorySha256": hashlib.sha256(canonical(rows)).hexdigest(), "inventory": rows,
              "inputReceipts": input_refs, "copyOrigins": sorted(plan.entries.values(), key=lambda r:r["path"]),
              "patches": patches, "allPatchesReverseVerified": True,
              "nativeQmlUnresolvedResources": unresolved, "requiredNativeFiles": required,
              "ordinaryPluginSha256": runtime_proof["pluginSha256"], "commonCoreSha256": build["coreSha256"],
              "hookInputsRoot": str(hooks), "hookTargetIdentity": hook_identity,
              "transferCoreSha256": manifest["coreSha256"], "transferPackageManifestSha256": helper["manifestSha256"],
              "fixedWebView2Version": browser["version"], "bundledSchannelSha256": sha(runtime / relative(tls)),
              "dependencyClosure": closure, "materials": material_status,
              "plannedDependencyClosureVerifiedBeforeCopy": planned_closure["verified"],
              "qtRuntimeCompletion": completion,
               "qtRemainingMaterials": qt_remaining,
               "qtImageFormatsMaterials": qt_imageformats,
              "releaseBlockers": LIMITATIONS + [{"id": "whole_client_materials", "reason": text} for text in material_status["limitations"]],
              "baselineUnchanged": True, "independentCopies": True,
              "resumedExactUnreceiptedRuntime": args.resume_output,
              "pythonBundled": False, "observersBundled": False, "localServerBundled": False,
              "exportHistoryHookEnabled": False, "exportSelectionHostEnabled": True,
              "retentionListenerBuilt": True, "retentionRuntimeReady": False,
              "newHooksAdded": True, "targetsExecuted": False,
              "installedClientChanged": False, "privateProfilesRead": False,
              "toolSha256": sha(Path(__file__)),
              "patchModuleHashes": patch_module_hashes}
    write_new(output / "assembly-manifest.json", json.dumps(result, indent=2).encode("utf8") + b"\n")
    write_new(report, json.dumps(result, indent=2).encode("utf8") + b"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--layout-proof", type=Path, default=LAYOUT)
    parser.add_argument("--qml", type=Path, default=WORK / "build/qt-generator-wijw74bp/native")
    for option, name in {
        "plugin-proof": "qt-ordinary-export-mdata-build-20261021.json",
        "plugin-runtime-proof": "qt-ordinary-export-mdata-runtime-20261021.json",
        "helper-proof": "native-transfer-recovery-materials-package-20261021.json",
        "browser-proof": "qt-webview2-bundle-proof.json", "webview-proof": "qt-webview2-guard-good-proof.json",
        "webview-source-proof": "qt-webview2-source-distribution.json", "notification-proof": "notification-native-proof.json",
    }.items():
        parser.add_argument("--" + option, type=Path, default=REPORTS / name)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--qt-completion-proof", type=Path, help="Exact official QtSerialPort/QtWebEngine binary acquisition receipt.")
    parser.add_argument("--qt-completion-materials", type=Path, help="Matching full corresponding-source archives and original license texts.")
    parser.add_argument("--qt-remaining-source-proof", type=Path, help="Official corresponding sources and original license texts for five additional Qt modules.")
    parser.add_argument("--qt-remaining-binary-proof", type=Path, help="Exact official binary identity for the five additional Qt DLLs.")
    parser.add_argument("--qt-imageformats-audit-proof", type=Path, help="Official QtImageFormats source/binary identity receipt for five exact runtime plugins.")
    parser.add_argument("--qt-imageformats-thirdparty-proof", type=Path, help="Original libtiff/libwebp notice receipt tied to the QtImageFormats audit.")
    parser.add_argument("--qt-sdk-supplement-proof", type=Path, help="Offline revalidated exact QtQuickWidgets/PrintSupport release dependencies.")
    parser.add_argument("--resume-output", action="store_true", help="Complete only an unreceipted runtime whose entire inventory already matches the exact planned copied/patched bytes.")
    result = assemble(parser.parse_args())
    print(json.dumps({key: result[key] for key in ("assembled", "runtime", "files", "bytes", "runtimeInventorySha256", "releaseEligible")} | {
        "dependencyClosureVerified": result["dependencyClosure"]["verified"], "missingDependencies": len(result["dependencyClosure"]["missing"]),
        "unresolvedQml": len(result["nativeQmlUnresolvedResources"])}))


if __name__ == "__main__":
    main()
