"""Acquire official Qt 6.11.2 MSVC2022 x64 additions for the frozen baseline.

Development tool only. Official Metalink SHA-256 and archive inventory are
checked before extraction; all new data stays on D. No target is executed.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import urllib.request
import xml.etree.ElementTree as ET

import py7zr

ROOT = Path(__file__).resolve().parents[2]
DEST = Path("D:/ValMAXBuild/deps/qt-runtime-completion-6.11.2-20261021")
REPOSITORIES = {
    "qtserialport": ("https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_6112/qt6_6112_msvc2022_64/", "qt.qt6.6112.addons.qtserialport.win64_msvc2022_64"),
    "qtwebengine": ("https://download.qt.io/online/qtsdkrepository/windows_x86/extensions/qtwebengine/6112/msvc2022_64/", "extensions.qtwebengine.6112.win64_msvc2022_64"),
}


def require(value, message):
    if not value: raise ValueError(message)


def sha(path):
    with path.open("rb") as source: return hashlib.file_digest(source, "sha256").hexdigest()


def read(url, limit):
    require(url.startswith("https://download.qt.io/"), "official Qt URL required")
    with urllib.request.urlopen(url, timeout=60) as response:
        body = response.read(limit + 1)
    require(len(body) <= limit, "download bound")
    return body


def retain(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == body, "existing cached metadata differs")
    else:
        with path.open("xb") as output: output.write(body)


def acquire(url, cache):
    metadata = read(url + ".meta4", 4 << 20)
    tree = ET.fromstring(metadata)
    ns = {"m": "urn:ietf:params:xml:ns:metalink"}
    entries = tree.findall("m:file", ns)
    require(len(entries) == 1, "one Metalink file required")
    entry = entries[0]
    name = entry.get("name")
    require(name == url.rsplit("/", 1)[1] and re.fullmatch(r"[A-Za-z0-9_.+-]+", name), "archive name changed")
    digest = entry.findtext("m:hash[@type='sha-256']", namespaces=ns)
    size = int(entry.findtext("m:size", namespaces=ns))
    require(digest and re.fullmatch(r"[0-9a-f]{64}", digest) and 0 < size <= 2 << 30, "invalid archive digest/size")
    path = cache / name
    # Metalink embeds a dynamic publication timestamp; preserve the first
    # exact received copy instead of rewriting cached evidence on retries.
    if not (cache / (name + ".meta4")).exists(): retain(cache / (name + ".meta4"), metadata)
    if not path.exists():
        require(shutil.disk_usage(cache).free > size + (1 << 30), "insufficient D download space")
        with urllib.request.urlopen(url, timeout=90) as response, path.open("xb") as output:
            total = 0
            while body := response.read(1 << 20):
                total += len(body)
                require(total <= size, "archive larger than official metadata")
                output.write(body)
        require(total == size, "truncated archive")
    require(path.stat().st_size == size and sha(path) == digest, "official archive SHA-256 mismatch")
    print(json.dumps({"acquired": name, "bytes": size, "sha256": digest}), flush=True)
    return {"path": str(path), "url": url, "bytes": size, "sha256": digest,
            "publishedSha256Verified": True, "metadataPath": str(cache / (name + ".meta4")),
            "metadataSha256": sha(cache / (name + ".meta4"))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve() and not args.report.exists(), "new report required")
    cache = DEST / "downloads"
    cache.mkdir(parents=True, exist_ok=True)
    results = []
    for module, (base, package_name) in REPOSITORIES.items():
        index_path = cache / (module + "-Updates.xml")
        if not index_path.exists(): retain(index_path, read(base + "Updates.xml", 4 << 20))
        packages = [p for p in ET.fromstring(index_path.read_bytes()).findall("PackageUpdate") if p.findtext("Name") == package_name]
        require(len(packages) == 1, "exact official package missing")
        package = packages[0]
        version, archive = package.findtext("Version"), package.findtext("DownloadableArchives")
        require(version.startswith("6.11.2-") and re.fullmatch(module + r"-Windows-.*MSVC2022.*X86_64\.7z", archive), "wrong Qt binary version/architecture")
        record = acquire(base + package_name + "/" + version + archive, cache)
        record.update(module=module, package=package_name, packageVersion=version,
                      repositoryIndexPath=str(index_path), repositoryIndexSha256=sha(index_path))
        with py7zr.SevenZipFile(record["path"]) as archive_file:
            members = archive_file.list()
            names = set()
            total = 0
            for item in members:
                name = item.filename
                p = PurePosixPath(name)
                require(name and "\\" not in name and ":" not in name and not p.is_absolute()
                        and p.as_posix() == name and ".." not in p.parts and not item.is_symlink, "unsafe Qt archive entry")
                require(name.casefold() not in names, "case alias in Qt archive")
                names.add(name.casefold())
                total += item.uncompressed or 0
            require(len(names) <= 100000 and total <= 6 << 30, "Qt SDK expansion bound")
            record.update(archiveEntries=len(members), uncompressedBytes=total)
            if not args.download_only:
                target = DEST / "extracted" / module
                require(not target.exists(), "new extraction required")
                require(shutil.disk_usage(DEST).free > total + (2 << 30), "insufficient D extraction space")
                target.mkdir(parents=True)
                archive_file.extractall(target)
                files = [{"path": p.relative_to(target).as_posix(), "bytes": p.stat().st_size, "sha256": sha(p)}
                         for p in sorted(target.rglob("*")) if p.is_file()]
                record.update(extracted=str(target), files=files)
        results.append(record)
    result = {"schema": 1, "verified": True, "qtVersion": "6.11.2", "architecture": "msvc2022_64",
              "downloadOnly": args.download_only, "archives": results, "targetsExecuted": False,
              "toolSha256": sha(Path(__file__))}
    with args.report.open("x", encoding="utf8") as output: output.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"verified": True, "archives": len(results), "report": str(args.report)}))


if __name__ == "__main__": main()
