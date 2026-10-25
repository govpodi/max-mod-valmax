"""Bind five bundled Qt DLLs to official 6.11.2 Windows archives.

Downloads only the named archives selected from Qt's online installer index,
verifies their Metalink SHA-256 and size, and extracts the one expected DLL per
archive with 7-Zip into task-owned E: storage. No client or installer is run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET


BASE = "https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_6112/qt6_6112_msvc2022_64/"
MODULES = ("qtlottie", "qtmultimedia", "qtpositioning", "qtsvg", "qtwebchannel")
MODULE_DLLS = {
    "qtlottie": ("Qt6Lottie.dll", "Qt6LottieVectorImageGenerator.dll"),
    "qtmultimedia": ("Qt6Multimedia.dll", "Qt6MultimediaQuick.dll"),
    "qtpositioning": ("Qt6Positioning.dll",),
    "qtsvg": ("Qt6Svg.dll",),
    "qtwebchannel": ("Qt6WebChannel.dll", "Qt6WebChannelQuick.dll"),
}
SERVICE_DLLS = {
    "Qt6Lottie.dll", "Qt6LottieVectorImageGenerator.dll",
    "Qt6Multimedia.dll", "Qt6Svg.dll",
}
NAMESPACE = {"m": "urn:ietf:params:xml:ns:metalink"}
OUTPUT_PARENT = Path(r"E:\ValMAXBuild\materials")
REPORT_PARENT = Path(__file__).resolve().parents[2] / "analysis" / "native-migration-v1"
STAGED_RUNTIME = Path(r"E:\ValMAXBuild\build\staging-integrated-api2-p12-20261025-1300\runtime\mod")
SEVEN_ZIP = Path(r"C:\Program Files\7-Zip\7z.exe")
MAX_ARCHIVE = 500 << 20


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(url: str, limit: int) -> bytes:
    require(url.startswith(BASE), "official Qt online repository required")
    with urllib.request.urlopen(url, timeout=60) as response:
        body = response.read(limit + 1)
    require(len(body) <= limit, "official metadata exceeds bound")
    return body


def archive_ref(module: str, root: ET.Element) -> tuple[str, str]:
    package_name = ("qt.qt6.6112.win64_msvc2022_64" if module == "qtsvg"
                    else f"qt.qt6.6112.addons.{module}.win64_msvc2022_64")
    matches = [p for p in root.findall("PackageUpdate") if p.findtext("Name") == package_name]
    require(len(matches) == 1, "exact Qt binary package missing")
    package = matches[0]
    version = package.findtext("Version")
    require(isinstance(version, str) and version.startswith("6.11.2-"),
            "Qt binary package version differs")
    archives = [value.strip() for value in package.findtext("DownloadableArchives", "").split(",")]
    selected = [value for value in archives if value.startswith(module + "-")
                and value.endswith("-X86_64.7z")]
    require(len(selected) == 1 and re.fullmatch(r"[A-Za-z0-9_.+-]+", selected[0]) is not None,
            "exact Qt binary archive missing")
    return package_name, BASE + package_name + "/" + version + selected[0]


def acquire(module: str, index: ET.Element, downloads: Path) -> dict:
    package_name, url = archive_ref(module, index)
    name = url.rsplit("/", 1)[1]
    metadata = read(url + ".meta4", 4 << 20)
    entries = ET.fromstring(metadata).findall("m:file", NAMESPACE)
    require(len(entries) == 1 and entries[0].get("name") == name,
            "Qt binary Metalink identity differs")
    size = int(entries[0].findtext("m:size", namespaces=NAMESPACE))
    digest = entries[0].findtext("m:hash[@type='sha-256']", namespaces=NAMESPACE)
    require(0 < size <= MAX_ARCHIVE and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "Qt binary Metalink size/hash invalid")
    meta_path = downloads / (name + ".meta4")
    if not meta_path.exists():
        with meta_path.open("xb") as stream:
            stream.write(metadata)
    archive = downloads / name
    if not archive.exists():
        partial = downloads / (name + ".part")
        require(not partial.exists(), "partial Qt binary download exists")
        total = 0
        hasher = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=90) as source, partial.open("xb") as target:
            while chunk := source.read(1 << 20):
                total += len(chunk)
                require(total <= size, "Qt binary archive exceeds Metalink size")
                hasher.update(chunk)
                target.write(chunk)
        require(total == size and hasher.hexdigest() == digest,
                "downloaded Qt binary differs from Metalink")
        os.replace(partial, archive)
    require(archive.is_file() and not archive.is_symlink()
            and archive.stat().st_size == size and sha256(archive) == digest,
            "cached Qt binary differs from Metalink")
    return {"module": module, "package": package_name, "url": url,
            "archive": str(archive), "archiveBytes": size, "archiveSha256": digest,
            "metalink": str(meta_path), "metalinkSha256": sha256(meta_path)}


def archive_member(archive: Path, dll: str) -> tuple[str, int]:
    command = [str(SEVEN_ZIP), "l", "-slt", "-sccUTF-8", str(archive)]
    proc = subprocess.run(command, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    require(proc.returncode == 0, "7-Zip could not list official archive")
    blocks = re.split(r"\r?\n\s*\r?\n", proc.stdout)
    matches = []
    for block in blocks:
        values = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        name = values.get("Path", "")
        if name.replace("\\", "/").split("/")[-1].casefold() == dll.casefold():
            require(values.get("Folder") != "+" and values.get("Size", "").isdigit(),
                    "Qt DLL member is not a regular file")
            matches.append((name, int(values["Size"])))
    require(len(matches) == 1, "official Qt archive DLL member is not unique")
    return matches[0]


def compare(record: dict, extracted: Path) -> dict:
    module = record["module"]
    target_dir = extracted / module
    target_dir.mkdir(parents=True, exist_ok=True)
    compared = []
    for dll in MODULE_DLLS[module]:
        member, expected_size = archive_member(Path(record["archive"]), dll)
        target = target_dir / dll
        if not target.exists():
            proc = subprocess.run([str(SEVEN_ZIP), "e", "-bb0", "-sccUTF-8",
                                   "-o" + str(target_dir), record["archive"], member],
                                  capture_output=True, timeout=180)
            require(proc.returncode == 0, "7-Zip could not extract selected Qt DLL")
        require(target.is_file() and not target.is_symlink() and target.stat().st_size == expected_size,
                "extracted Qt DLL size differs")
        official_sha = sha256(target)
        runtime_images = []
        for relative in ([Path(dll), Path("max-service") / dll]
                         if dll in SERVICE_DLLS else [Path(dll)]):
            runtime = STAGED_RUNTIME / relative
            require(runtime.is_file() and not runtime.is_symlink(), "staged runtime Qt DLL missing")
            runtime_sha = sha256(runtime)
            runtime_images.append({"path": str(runtime),
                                   "relativePath": "mod/" + relative.as_posix(),
                                   "sha256": runtime_sha,
                                   "exactBinaryMatch": official_sha == runtime_sha})
        compared.append({"name": dll, "archiveMember": member,
                         "officialDll": str(target), "officialDllBytes": expected_size,
                         "officialDllSha256": official_sha,
                         "runtimeImages": runtime_images,
                         "exactBinaryMatch": all(row["exactBinaryMatch"] for row in runtime_images)})
    return {"dlls": compared,
            "exactBinaryMatch": all(row["exactBinaryMatch"] for row in compared)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    report = args.report.resolve()
    require(output.is_relative_to(OUTPUT_PARENT.resolve()) and output != OUTPUT_PARENT.resolve(),
            "output must be inside E:/ValMAXBuild/materials")
    require(report.parent == REPORT_PARENT.resolve() and not report.exists(),
            "new analysis report required")
    require(SEVEN_ZIP.is_file(), "7-Zip executable missing")
    output.mkdir(parents=True, exist_ok=True)
    downloads = output / "downloads"
    downloads.mkdir(exist_ok=True)
    extracted = output / "selected-dlls"
    extracted.mkdir(exist_ok=True)
    index_body = read(BASE + "Updates.xml", 4 << 20)
    index_path = output / "Updates.xml"
    if not index_path.exists():
        with index_path.open("xb") as stream:
            stream.write(index_body)
    index = ET.fromstring(index_body)
    records = []
    for module in MODULES:
        record = acquire(module, index, downloads)
        record.update(compare(record, extracted))
        records.append(record)
        print(json.dumps({"module": module, "exactBinaryMatch": record["exactBinaryMatch"],
                          "archiveSha256": record["archiveSha256"]}), flush=True)
    result = {"schema": 2, "checkedAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "officialVersion": "6.11.2", "officialIndex": str(index_path),
              "officialIndexSha256": sha256(index_path), "records": records,
              "allExactBinaryMatches": all(item["exactBinaryMatch"] for item in records),
              "clientExecuted": False, "runtimeChanged": False,
              "scope": "Exact official binary archive and staged root/service Qt DLL comparison for five modules. Other runtime components and redistribution obligations are separate."}
    with report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"allExactBinaryMatches": result["allExactBinaryMatches"],
                      "modules": len(records), "report": str(report)}), flush=True)
    require(result["allExactBinaryMatches"], "staged Qt DLL differs from official package")


if __name__ == "__main__":
    main()
