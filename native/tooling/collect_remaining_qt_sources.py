"""Collect pinned Qt 6.11.2 sources for five bundled MAX modules.

This development tool uses official Qt Metalink SHA-256/size metadata, retains
the complete corresponding source archives on E:, and extracts only license
texts for inspection. It does not modify or execute the client.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET


SOURCE_BASE = "https://download.qt.io/archive/qt/6.11/6.11.2/submodules/"
MODULES = ("qtlottie", "qtmultimedia", "qtpositioning", "qtsvg", "qtwebchannel")
NAMESPACE = {"m": "urn:ietf:params:xml:ns:metalink"}
OUTPUT_PARENT = Path(r"E:\ValMAXBuild\materials")
REPORT_PARENT = Path(__file__).resolve().parents[2] / "analysis" / "native-migration-v1"
MAX_ARCHIVE = 500 << 20
MAX_LICENSE = 2 << 20


def require(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def official_metadata(module: str) -> tuple[bytes, str, int, str]:
    name = f"{module}-everywhere-src-6.11.2.tar.xz"
    url = SOURCE_BASE + name
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url + ".meta4", timeout=45) as response:
                body = response.read(4 << 20)
                require(response.read(1) == b"", "Qt Metalink exceeds bound")
            break
        except TimeoutError:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    tree = ET.fromstring(body)
    entries = tree.findall("m:file", NAMESPACE)
    require(len(entries) == 1 and entries[0].get("name") == name,
            "Qt Metalink file identity differs")
    entry = entries[0]
    size = int(entry.findtext("m:size", namespaces=NAMESPACE))
    digest = entry.findtext("m:hash[@type='sha-256']", namespaces=NAMESPACE)
    require(0 < size <= MAX_ARCHIVE and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "invalid Qt Metalink size or SHA-256")
    return body, url, size, digest


def acquire(module: str, downloads: Path) -> dict:
    metadata, url, expected_size, expected_sha = official_metadata(module)
    name = url.rsplit("/", 1)[1]
    meta_path = downloads / (name + ".meta4")
    if not meta_path.exists():
        with meta_path.open("xb") as target:
            target.write(metadata)
    archive = downloads / name
    if not archive.exists():
        partial = downloads / (name + ".part")
        require(not partial.exists(), "partial Qt download exists; inspect before retry")
        total = 0
        hasher = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=90) as source, partial.open("xb") as target:
            while chunk := source.read(1 << 20):
                total += len(chunk)
                require(total <= expected_size, "Qt archive larger than official size")
                hasher.update(chunk)
                target.write(chunk)
        require(total == expected_size and hasher.hexdigest() == expected_sha,
                "Qt archive differs from official Metalink")
        os.replace(partial, archive)
    require(archive.is_file() and not archive.is_symlink()
            and archive.stat().st_size == expected_size and sha256(archive) == expected_sha,
            "cached Qt source differs from official Metalink")
    return {"module": module, "url": url, "archive": str(archive),
            "bytes": expected_size, "sha256": expected_sha,
            "metalink": str(meta_path), "metalinkSha256": sha256(meta_path)}


def extract_license_texts(record: dict, output: Path) -> list[dict]:
    module = record["module"]
    top = f"{module}-everywhere-src-6.11.2"
    selected: list[dict] = []
    names: set[str] = set()
    with tarfile.open(record["archive"], mode="r:xz") as archive:
        for member in archive:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or ".." in path.parts or "\\" in member.name
                    or ":" in member.name):
                raise ValueError("unsafe Qt archive member")
            if len(path.parts) < 2 or path.parts[0] != top:
                continue
            wanted = path.parts[1] == "LICENSES" or (len(path.parts) == 2
                    and path.name.lower().startswith(("license", "copying", "notice")))
            if not wanted or member.isdir():
                continue
            require(member.isfile() and not member.issym() and member.size <= MAX_LICENSE,
                    "unsupported Qt license member")
            relative = PurePosixPath(module, *path.parts[1:])
            key = relative.as_posix().casefold()
            require(key not in names, "Qt license path alias")
            names.add(key)
            source = archive.extractfile(member)
            require(source is not None, "Qt license content unavailable")
            data = source.read(MAX_LICENSE + 1)
            require(len(data) == member.size, "Qt license size differs")
            data.decode("utf-8")
            target = output.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                require(target.is_file() and not target.is_symlink()
                        and target.read_bytes() == data,
                        "existing Qt license differs from archive")
            else:
                with target.open("xb") as stream:
                    stream.write(data)
            selected.append({"path": str(target), "archiveMember": member.name,
                             "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            require(len(selected) <= 1000, "too many Qt license texts")
    require(selected, "no top-level Qt license texts found")
    return selected


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
            "new report in analysis/native-migration-v1 required")
    output.mkdir(parents=True, exist_ok=True)
    downloads = output / "downloads"
    downloads.mkdir(exist_ok=True)
    licenses = output / "licenses"
    licenses.mkdir(exist_ok=True)
    records = []
    for module in MODULES:
        record = acquire(module, downloads)
        record["licenseTexts"] = extract_license_texts(record, licenses)
        records.append(record)
        print(json.dumps({"module": module, "sourceSha256": record["sha256"],
                          "licenseTexts": len(record["licenseTexts"])}, ensure_ascii=False), flush=True)
    result = {"schema": 1, "verified": True,
              "checkedAtUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "officialSourceVersion": "6.11.2", "modules": records,
              "clientExecuted": False, "runtimeChanged": False,
              "scope": "Complete official source archives and extracted top-level license texts for five modules; binary provenance and whole-client redistribution readiness remain separate."}
    with report.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"verified": True, "modules": len(records), "report": str(report)}), flush=True)


if __name__ == "__main__":
    main()
