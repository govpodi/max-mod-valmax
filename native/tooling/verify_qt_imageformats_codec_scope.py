"""Verify the bundled TIFF/WebP code path in five installed QtImageFormats DLLs.

This is a bounded, read-only materials audit. It does not execute ValMAX,
inspect user profiles, amend an existing assembly receipt, or claim whole-client
license completeness. The only write is a new, small report under E: analysis.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

from package_transfer_helper import ordinary, pe_imports, sha


MODULE = "qtimageformats"
VERSION = "6.11.2"
PREFIX = f"qtimageformats-everywhere-src-{VERSION}/"
SOURCE_NAME = f"qtimageformats-everywhere-src-{VERSION}.tar.xz"
TIFF_CMAKE_SHA256 = "9bc18612a5dab0bd76192077ca4977a969bbec4475dcf27a7608762904f87f16"
WEBP_CMAKE_SHA256 = "d22c2cd37977176d6c7d021cf20ba47baa069ba049283533b9408dabe609d9a0"
SEVEN_ZIP_EXE_SHA256 = "83967f1b02b43c4efeda302795722c809e0e81b8307de73558d10484d5676a7d"
SEVEN_ZIP_DLL_SHA256 = "69fd4df057985c40e510e2fac182881c7f85e90aa13ec703f763a8fdb2ce61f8"
SEVEN_ZIP_EXE_BYTES = 576000
SEVEN_ZIP_DLL_BYTES = 1906688
DLLS = frozenset({"qicns.dll", "qtga.dll", "qtiff.dll", "qwbmp.dll", "qwebp.dll"})
ORIGINAL_CODEC_TEXTS = frozenset({
    "src/3rdparty/libtiff/COPYRIGHT",
    "src/3rdparty/libtiff/qt_attribution.json",
    "src/3rdparty/libwebp/COPYING",
    "src/3rdparty/libwebp/qt_attribution.json",
})
SUMMARY_VALUES = {
    "JasPer": "no", "MNG": "no", "TIFF": "yes",
    "Using system libtiff": "no", "WEBP": "yes",
    "Using system libwebp": "no",
}
SUMMARY_LINE = re.compile(r"^\s*(JasPer|MNG|TIFF|Using system libtiff|WEBP|Using system libwebp)\s+\.{3,}\s+(yes|no)\s*$")


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def read_json(path: Path) -> dict:
    ordinary(path)
    result = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_pairs,
                        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON number")))
    require(isinstance(result, dict), "JSON object required")
    return result


def checked_file(path: Path, expected_sha: str, expected_size: int | None = None) -> None:
    info = ordinary(path)
    require(re.fullmatch(r"[0-9a-f]{64}", expected_sha) is not None, "invalid SHA-256 pin")
    if expected_size is not None:
        require(info.st_size == expected_size, "file size differs from pin")
    require(sha(path) == expected_sha, "file SHA-256 differs from pin")


def require_seven_zip_exe_hash(actual_sha: str) -> None:
    require(actual_sha == SEVEN_ZIP_EXE_SHA256, "7z.exe SHA-256 differs from pin")


def checked_seven_zip(path: Path) -> None:
    require(path.is_absolute() and path.name.casefold() == "7z.exe",
            "absolute 7z.exe path required")
    require(ordinary(path).st_size == SEVEN_ZIP_EXE_BYTES, "7z.exe size differs from pin")
    require_seven_zip_exe_hash(sha(path))
    checked_file(path.with_name("7z.dll"), SEVEN_ZIP_DLL_SHA256, SEVEN_ZIP_DLL_BYTES)


def parse_summary(raw: bytes) -> dict[str, str]:
    require(len(raw) <= 65536, "Qt build summary too large")
    text = raw.decode("utf-8")
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = SUMMARY_LINE.fullmatch(line)
        if match:
            key, value = match.groups()
            require(key not in values, "duplicate Qt build setting")
            values[key] = value
    require(values == SUMMARY_VALUES, "QtImageFormats build does not prove bundled TIFF/WebP")
    return values


def cmake_branch_items(source: str, target: str, condition: str, section: str) -> set[str]:
    # These pinned Qt files use ordinary line comments and simple calls. Reject
    # bracket comments rather than accidentally treating their contents as code.
    require(re.search(r"#\[=*\[", source) is None, "unsupported CMake bracket comment")
    active = "\n".join(line.partition("#")[0] for line in source.splitlines())
    branch = re.compile(
        rf"(?ms)^[ \t]*qt_internal_extend_target\({re.escape(target)}[ \t]+"
        rf"CONDITION[ \t]+{re.escape(condition)}[ \t]*\n(?P<body>.*?)^[ \t]*\)[ \t]*$"
    )
    bodies = [match.group("body") for match in branch.finditer(active)]
    require(len(bodies) == 1, "exact active CMake target branch required")
    lines = [line.strip() for line in bodies[0].splitlines() if line.strip()]
    require(lines.count(section) == 1, "exact CMake target section required")
    start = lines.index(section) + 1
    end = next((i for i in range(start, len(lines))
                if re.fullmatch(r"[A-Z][A-Z0-9_]*", lines[i])), len(lines))
    return set(lines[start:end])


def verify_cmake(tiff: str, webp: str) -> None:
    require("TIFF::TIFF" in cmake_branch_items(
        tiff, "QTiffPlugin", "QT_FEATURE_system_tiff", "LIBRARIES"),
        "TIFF system branch changed")
    require("../../../3rdparty/libtiff/libtiff/tif_codec.c" in cmake_branch_items(
        tiff, "QTiffPlugin", "NOT QT_FEATURE_system_tiff", "SOURCES"),
        "TIFF bundled source branch changed")
    require("../../../3rdparty/libtiff/libtiff/tif_win32.c" in cmake_branch_items(
        tiff, "QTiffPlugin", "WIN32 AND NOT QT_FEATURE_system_tiff", "SOURCES"),
        "TIFF Windows source branch changed")
    webp_sources = cmake_branch_items(
        webp, "QWebpPlugin", "NOT QT_FEATURE_system_webp", "SOURCES")
    require("WrapWebP::WrapWebP" in cmake_branch_items(
        webp, "QWebpPlugin", "QT_FEATURE_system_webp", "LIBRARIES")
        and {"../../../3rdparty/libwebp/src/dec/webp_dec.c",
             "../../../3rdparty/libwebp/src/enc/webp_enc.c"} <= webp_sources,
        "WebP source branches changed")


def archive_bytes(seven_zip: Path, archive: Path, member: str, limit: int) -> bytes:
    require(not any(part in member for part in ("..", ":", "/")), "unsafe binary archive member")
    checked_seven_zip(seven_zip)
    result = subprocess.run([str(seven_zip), "e", "-so", str(archive), member],
                            capture_output=True, timeout=45, check=False)
    require(result.returncode == 0 and 0 < len(result.stdout) <= limit,
            "could not read pinned Qt binary member")
    return result.stdout


def tar_member(archive: tarfile.TarFile, member: str, limit: int) -> bytes:
    info = archive.getmember(PREFIX + member)
    require(info.isfile() and 0 < info.size <= limit, "invalid Qt source member")
    stream = archive.extractfile(info)
    require(stream is not None, "Qt source member unavailable")
    with stream:
        data = stream.read(limit + 1)
    require(len(data) == info.size, "Qt source member size mismatch")
    return data


def zip_source_digest(path: Path) -> tuple[int, str]:
    with zipfile.ZipFile(path) as archive:
        member = "sources/" + SOURCE_NAME
        rows = [item for item in archive.infolist() if item.filename == member]
        require(len(rows) == 1 and not rows[0].is_dir(), "exact Qt source ZIP member required")
        require(rows[0].file_size <= 8 * 1024 * 1024, "QtImageFormats source member too large")
        digest = hashlib.sha256()
        size = 0
        with archive.open(rows[0]) as stream:
            while chunk := stream.read(1 << 20):
                size += len(chunk)
                digest.update(chunk)
        require(size == rows[0].file_size, "Qt source ZIP member truncated")
        return size, digest.hexdigest()


def verify(args: argparse.Namespace) -> dict:
    install = args.install
    package_path = install / "native-package.json"
    package = read_json(package_path)
    require(package.get("version") == "8.9.0-native.preview.20"
            and package.get("distributionMode") == "public-beta"
            and package.get("qtSourceMode") == "separate-asset",
            "exact installed p20 public Beta required")
    runtime_rows = package.get("runtimeFiles")
    require(isinstance(runtime_rows, list), "installed inventory missing")
    inventory = {row["path"]: row for row in runtime_rows}
    require(len(inventory) == len(runtime_rows), "duplicate installed runtime path")

    materials_path = install / "runtime/mod/licenses/STAGING-MATERIALS.json"
    checked_file(materials_path, package["materialsReceiptSha256"])
    material_row = inventory.get("mod/licenses/STAGING-MATERIALS.json")
    require(material_row is not None and material_row["sha256"] == package["materialsReceiptSha256"],
            "installed materials are not inventoried")
    materials = read_json(materials_path)
    prior = materials.get("additionalQtImageFormatsMaterialProof")
    require(isinstance(prior, dict) and prior.get("module") == MODULE
            and prior.get("runtimeImagesMatched") == 5,
            "installed QtImageFormats material subject missing")

    checked_file(args.audit, prior["auditProofSha256"])
    checked_file(args.notices, prior["noticeProofSha256"])
    audit, notices = read_json(args.audit), read_json(args.notices)
    require(audit.get("verified") is True and audit.get("qtVersion") == VERSION
            and notices.get("verified") is True and notices.get("qtVersion") == VERSION
            and notices.get("sourceAuditSha256") == prior["auditProofSha256"],
            "Qt audit/notice identity differs")
    modules = [row for row in audit.get("modules", []) if row.get("module") == MODULE]
    require(len(modules) == 1, "exact QtImageFormats module audit required")
    module = modules[0]
    source = module["sourceArchive"]
    binary = module["binaryArchive"]
    require(source["url"] == f"https://download.qt.io/archive/qt/6.11/{VERSION}/submodules/{SOURCE_NAME}"
            and binary["package"] == "qt.qt6.6112.addons.qtimageformats.win64_msvc2022_64"
            and binary["url"].startswith("https://download.qt.io/online/qtsdkrepository/"),
            "official Qt archive identity differs")
    source_path, binary_path = Path(source["archive"]), Path(binary["archive"])
    checked_file(source_path, source["sha256"], source["bytes"])
    checked_file(binary_path, binary["archiveSha256"], binary["archiveBytes"])
    require(source["sha256"] == prior["sourceArchiveSha256"], "source archive changed")

    asset = package["qtSourceDistribution"]
    checked_file(args.qt_zip, asset["assetSha256"], asset["assetBytes"])
    checked_file(args.qt_manifest, asset["manifestSha256"])
    qt_manifest = read_json(args.qt_manifest)
    entries = [row for row in qt_manifest.get("retainedSourceArchives", [])
               if row.get("name") == SOURCE_NAME]
    require(len(entries) == 1 and entries[0]["sha256"] == source["sha256"]
            and entries[0]["bytes"] == source["bytes"],
            "public Qt source manifest differs")
    zip_size, zip_sha = zip_source_digest(args.qt_zip)
    require(zip_size == source["bytes"] and zip_sha == source["sha256"],
            "public Qt ZIP contains different source")

    summary = parse_summary(archive_bytes(args.seven_zip, binary_path,
                                          "config_qtimageformats.summary", 65536))
    installed_dlls = []
    images = module.get("runtimeImages")
    require(isinstance(images, list) and {Path(r["runtimePath"]).name for r in images} == DLLS
            and len(images) == 5, "five exact QtImageFormats images required")
    for row in sorted(images, key=lambda item: item["runtimePath"]):
        name = Path(row["runtimePath"]).name
        relative = "mod/plugins/imageformats/" + name
        require(row["runtimePath"] == relative and row["officialDll"]["member"] ==
                "plugins\\imageformats\\" + name, "unexpected QtImageFormats DLL path")
        require(inventory[relative]["sha256"] == row["runtimeSha256"]
                and row["officialDll"]["sha256"] == row["runtimeSha256"],
                "installed/official Qt DLL inventory differs")
        dll = install / "runtime" / relative
        checked_file(dll, row["runtimeSha256"], row["officialDll"]["bytes"])
        official_bytes = archive_bytes(args.seven_zip, binary_path,
                                       row["officialDll"]["member"], 2 * 1024 * 1024)
        require(hashlib.sha256(official_bytes).hexdigest() == row["runtimeSha256"]
                and len(official_bytes) == row["officialDll"]["bytes"],
                "official Qt DLL archive member differs")
        imports = sorted({entry["name"] for entry in pe_imports(dll)})
        require(not any("tiff" in item or "webp" in item for item in imports),
                "external TIFF/WebP PE dependency found")
        installed_dlls.append({"name": name, "bytes": len(official_bytes),
                               "sha256": row["runtimeSha256"],
                               "externalTiffWebpPeImports": []})

    license_rows = source.get("licenseTexts")
    codec_rows = notices.get("originalTexts")
    require(isinstance(license_rows, list) and len(license_rows) == 8
            and isinstance(codec_rows, list) and len(codec_rows) == 4
            and {row["archiveMember"].removeprefix(PREFIX) for row in codec_rows}
                == ORIGINAL_CODEC_TEXTS
            and notices.get("sourceSha256") == source["sha256"],
            "exact original QtImageFormats notices required")
    copied = set()
    with tarfile.open(source_path, "r:xz") as archive:
        tiff = tar_member(archive, "src/plugins/imageformats/tiff/CMakeLists.txt", 16384)
        webp = tar_member(archive, "src/plugins/imageformats/webp/CMakeLists.txt", 16384)
        require(hashlib.sha256(tiff).hexdigest() == TIFF_CMAKE_SHA256
                and hashlib.sha256(webp).hexdigest() == WEBP_CMAKE_SHA256,
                "QtImageFormats CMake content differs from p20 pins")
        verify_cmake(tiff.decode("utf-8"), webp.decode("utf-8"))
        for row in [*license_rows, *codec_rows]:
            member = row["archiveMember"]
            require(member.startswith(PREFIX) and ".." not in member,
                    "unsafe Qt original notice member")
            relative = member[len(PREFIX):]
            require(relative not in copied, "duplicate original notice")
            copied.add(relative)
            data = tar_member(archive, relative, 65536)
            require(len(data) == row["bytes"]
                    and hashlib.sha256(data).hexdigest() == row["sha256"],
                    "original Qt notice differs from source archive")
            installed_path = install / "runtime/mod/licenses/QtImageFormats" / relative
            checked_file(installed_path, row["sha256"], row["bytes"])
            inv_path = "mod/licenses/QtImageFormats/" + relative.replace("\\", "/")
            require(inventory[inv_path]["sha256"] == row["sha256"],
                    "installed Qt notice absent from package inventory")
    actual_notice_paths = {row["path"] for row in runtime_rows
                           if row["path"].startswith("mod/licenses/QtImageFormats/")}
    require(actual_notice_paths == {"mod/licenses/QtImageFormats/" + name for name in copied},
            "installed QtImageFormats notice set differs")

    return {
        "schema": 1, "kind": "qtimageformats-installed-codec-scope", "verified": True,
        "verifierSha256": sha(Path(__file__)),
        "subject": {"version": package["version"],
                    "distributionMode": package["distributionMode"],
                    "installedPackageSha256": sha(package_path),
                    "materialsReceiptSha256": sha(materials_path)},
        "scope": "Five QtImageFormats addon DLLs in the installed p20 public Beta; bundled libtiff and libwebp only.",
        "officialBinaryArchiveSha256": binary["archiveSha256"],
        "officialSourceArchiveSha256": source["sha256"],
        "publishedQtSourceAssetSha256": asset["assetSha256"],
        "archiveReaderSha256": {"exe": SEVEN_ZIP_EXE_SHA256,
                                "dll": SEVEN_ZIP_DLL_SHA256},
        "buildSummary": summary, "sourceCmakeBranchesVerified": True,
        "sourceCmakeSha256": {"tiff": TIFF_CMAKE_SHA256, "webp": WEBP_CMAKE_SHA256},
        "dlls": installed_dlls,
        "notices": {"originalLicenseTexts": len(license_rows),
                    "originalCodecTexts": len(codec_rows),
                    "installedExactMatches": len(copied)},
        "bundledCodecs": {"libtiff": True, "libwebp": True},
        "codecBundlingProven": True,
        "wholeClientMaterialsComplete": False,
        "clientExecuted": False,
        "limitations": [
            "This proves bundled libtiff/libwebp for the five official QtImageFormats addon DLLs, not every Qt or MAX codec.",
            "The TIFF plugin may also use Qt's bundled or system zlib; that separate QtBase dependency is outside this targeted codec proof.",
            "The official Qt package configuration and source branches are linked by version and hashes; a byte-reproducible vendor build was not reconstructed.",
            "Whole-client source, license and notice closure remains unverified.",
        ],
    }


def self_test() -> None:
    require_seven_zip_exe_hash(SEVEN_ZIP_EXE_SHA256)
    try:
        require_seven_zip_exe_hash("0" * 64)
    except ValueError:
        pass
    else:
        raise AssertionError("wrong 7z.exe SHA-256 accepted")
    good = b"  TIFF ................................... yes\n    Using system libtiff ................. no\n  WEBP ................................... yes\n    Using system libwebp ................. no\n  JasPer ................................. no\n  MNG .................................... no\n"
    assert parse_summary(good) == SUMMARY_VALUES
    for bad in (good.replace(b"libtiff ................. no", b"libtiff ................. yes"),
                good.replace(b"  WEBP ................................... yes\n", b""),
                good + b"  TIFF ................................... yes\n"):
        try:
            parse_summary(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe Qt configuration accepted")
    tiff = ("qt_internal_extend_target(QTiffPlugin CONDITION QT_FEATURE_system_tiff\n"
            "    LIBRARIES\n        TIFF::TIFF\n)\n"
            "qt_internal_extend_target(QTiffPlugin CONDITION NOT QT_FEATURE_system_tiff\n"
            "    SOURCES\n        ../../../3rdparty/libtiff/libtiff/tif_codec.c\n)\n"
            "qt_internal_extend_target(QTiffPlugin CONDITION WIN32 AND NOT QT_FEATURE_system_tiff\n"
            "    SOURCES\n        ../../../3rdparty/libtiff/libtiff/tif_win32.c\n)\n")
    webp = ("qt_internal_extend_target(QWebpPlugin CONDITION QT_FEATURE_system_webp\n"
            "    LIBRARIES\n        WrapWebP::WrapWebP\n)\n"
            "qt_internal_extend_target(QWebpPlugin CONDITION NOT QT_FEATURE_system_webp\n"
            "    SOURCES\n        ../../../3rdparty/libwebp/src/dec/webp_dec.c\n"
            "        ../../../3rdparty/libwebp/src/enc/webp_enc.c\n)\n")
    verify_cmake(tiff, webp)
    for bad_tiff, bad_webp in (
        ("\n".join("# " + line for line in tiff.splitlines()),
         "\n".join("# " + line for line in webp.splitlines())),
        (tiff.replace("        ../../../3rdparty/libtiff/libtiff/tif_codec.c",
                      "        # ../../../3rdparty/libtiff/libtiff/tif_codec.c"), webp),
        ("#[[\n" + tiff + "]]\n", webp),
    ):
        try:
            verify_cmake(bad_tiff, bad_webp)
        except ValueError:
            pass
        else:
            raise AssertionError("commented or missing CMake branch accepted")
    print("QtImageFormats codec verifier self-test passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--install", type=Path)
    parser.add_argument("--audit", type=Path)
    parser.add_argument("--notices", type=Path)
    parser.add_argument("--qt-zip", type=Path)
    parser.add_argument("--qt-manifest", type=Path)
    parser.add_argument("--seven-zip", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    require(all(getattr(args, key) is not None for key in
                ("install", "audit", "notices", "qt_zip", "qt_manifest", "seven_zip", "report")),
            "all exact installed p20 input and report paths are required")
    checked_seven_zip(args.seven_zip)
    require(args.report.is_absolute()
            and args.report.resolve().is_relative_to(Path("E:/ValMAXBuild/analysis").resolve())
            and not args.report.exists(), "new report under E: analysis required")
    result = verify(args)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    require(len(payload.encode("utf-8")) <= 16384, "QtImageFormats report too large")
    args.report.write_text(payload, encoding="utf-8", newline="\n")
    print(f"verified QtImageFormats installed codec scope; report SHA-256 {sha(args.report)}")


if __name__ == "__main__":
    main()
