"""Prepare a public-BETA Qt source asset from the byte-checked local prototype.

This only prepares files for a later release. It does not publish them, prove
whole-client license coverage, or change the installed client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import stat
import zipfile

from qt_source_asset import (ASSET_MANIFEST_FIELDS, PUBLIC_BETA_AVAILABILITY, QT_ARCHIVE, SHA256,
                             archived_digest, regular, require, safe_member, sha)


def checked_output(path: Path, inputs: set[Path]) -> Path:
    require(path.is_absolute() and path not in inputs and not path.exists(),
            "new absolute Qt source output required")
    parent = path.parent
    require(parent.is_dir(), "Qt source output parent does not exist")
    for item in (parent, *parent.parents):
        info = item.lstat()
        require(not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                "Qt source output uses a reparse path")
    return path


def verified_prototype(archive_path: Path, manifest_path: Path) -> dict:
    require(regular(archive_path).st_size <= 2 << 30, "Qt source prototype size bound exceeded")
    require(regular(manifest_path).st_size <= 2 << 20, "Qt source prototype manifest size bound exceeded")
    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest)
    require(isinstance(manifest, dict)
            and set(manifest) <= ASSET_MANIFEST_FIELDS - {"publicationStatus"}
            and manifest.get("kind") == "local-qt-source-bundle-prototype"
            and manifest.get("variant") == "p16-integrated-slim-no-qtwebengine"
            and manifest.get("qtVersion") == "6.11.2"
            and manifest.get("distribution") == "local-prototype-only"
            and manifest.get("legalCompleteness") == "not-assessed",
            "only the reviewed local Qt source prototype can be prepared")
    sources = manifest.get("retainedSourceArchives")
    notices = manifest.get("retainedNoticeFiles")
    require(isinstance(sources, list) and sources
            and isinstance(notices, list) and notices,
            "Qt source prototype inventory is missing")
    expected = {"manifest.json", "SOURCE-AVAILABILITY-DRAFT.txt"}
    source_names, notice_names, source_bytes, notice_bytes = set(), set(), 0, 0
    with zipfile.ZipFile(archive_path) as bundle:
        names = bundle.namelist()
        require(len(names) == len(set(names)) and len(names) <= 4096
                and all(safe_member(name) == name and not bundle.getinfo(name).is_dir()
                        for name in names),
                "unsafe or duplicate Qt source prototype member")
        require(bundle.read("manifest.json") == raw_manifest,
                "Qt source prototype ZIP manifest differs")
        draft = bundle.read("SOURCE-AVAILABILITY-DRAFT.txt")
        require(0 < len(draft) <= 1 << 20, "Qt source prototype draft missing")
        for row in sources:
            require(isinstance(row, dict) and set(row) ==
                    {"name", "bytes", "sha256", "sourcePathsInP16", "archiveTest"}
                    and isinstance(row["name"], str) and QT_ARCHIVE.fullmatch(row["name"])
                    and row["name"] not in source_names
                    and type(row["bytes"]) is int and row["bytes"] > 0
                    and isinstance(row["sha256"], str) and SHA256.fullmatch(row["sha256"])
                    and row["archiveTest"] == "passed"
                    and isinstance(row["sourcePathsInP16"], list)
                    and row["sourcePathsInP16"]
                    and all(isinstance(path, str) and safe_member(path) == path
                            for path in row["sourcePathsInP16"]),
                    "invalid Qt source prototype archive record")
            source_names.add(row["name"])
            member = "sources/" + row["name"]
            expected.add(member)
            require(archived_digest(bundle, member) == (row["bytes"], row["sha256"]),
                    "Qt source prototype archive member changed")
            source_bytes += row["bytes"]
        for row in notices:
            require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
                    and isinstance(row["path"], str) and row["path"].startswith("notices/")
                    and row["path"] not in notice_names
                    and type(row["bytes"]) is int and row["bytes"] >= 0
                    and isinstance(row["sha256"], str) and SHA256.fullmatch(row["sha256"]),
                    "invalid Qt source prototype notice record")
            name = safe_member(row["path"])
            notice_names.add(name)
            expected.add(name)
            require(archived_digest(bundle, name) == (row["bytes"], row["sha256"]),
                    "Qt source prototype notice member changed")
            notice_bytes += row["bytes"]
        require(set(names) == expected
                and manifest.get("retainedSourceArchiveCount") == len(sources)
                and manifest.get("retainedSourceArchiveBytes") == source_bytes
                and manifest.get("retainedNoticeFileCount") == len(notices)
                and manifest.get("retainedNoticeBytes") == notice_bytes,
                "Qt source prototype inventory differs")
    return manifest


def prepare(prototype_archive: Path, prototype_manifest: Path,
            output_archive: Path, output_manifest: Path, apply: bool = False) -> dict:
    prototype_archive, prototype_manifest = Path(prototype_archive), Path(prototype_manifest)
    output_archive, output_manifest = Path(output_archive), Path(output_manifest)
    inputs = {prototype_archive, prototype_manifest}
    require(output_archive != output_manifest, "distinct Qt source outputs required")
    checked_output(output_archive, inputs)
    checked_output(output_manifest, inputs)
    manifest = verified_prototype(prototype_archive, prototype_manifest)
    public_manifest = {**manifest, "kind": "qt-source-bundle-public-beta",
                       "distribution": "public-beta-asset",
                       "publicationStatus": "pending-upload"}
    public_bytes = (json.dumps(public_manifest, indent=2, ensure_ascii=False) + "\n").encode("utf8")
    result = {"ready": True, "applied": False,
              "prototypeSha256": sha(prototype_archive),
              "archiveMembers": 2 + len(manifest["retainedSourceArchives"]) + len(manifest["retainedNoticeFiles"]),
              "sourceArchives": len(manifest["retainedSourceArchives"]),
              "noticeFiles": len(manifest["retainedNoticeFiles"]),
              "outputArchive": str(output_archive), "outputManifest": str(output_manifest),
              "publicationVerified": False}
    if not apply:
        return result
    created_archive = created_manifest = False
    try:
        with output_archive.open("xb") as archive_stream:
            created_archive = True
            with zipfile.ZipFile(prototype_archive) as source, \
                 zipfile.ZipFile(archive_stream, "w", allowZip64=True) as target:
                for info in source.infolist():
                    if info.filename == "manifest.json":
                        target.writestr("manifest.json", public_bytes, compress_type=zipfile.ZIP_DEFLATED)
                    elif info.filename == "SOURCE-AVAILABILITY-DRAFT.txt":
                        target.writestr("SOURCE-AVAILABILITY.txt", PUBLIC_BETA_AVAILABILITY,
                                        compress_type=zipfile.ZIP_DEFLATED)
                    else:
                        new_info = zipfile.ZipInfo(info.filename, info.date_time)
                        new_info.compress_type = info.compress_type
                        new_info.external_attr = info.external_attr
                        with source.open(info) as reader, target.open(new_info, "w", force_zip64=True) as writer:
                            shutil.copyfileobj(reader, writer, 1 << 20)
        with output_manifest.open("xb") as stream:
            created_manifest = True
            stream.write(public_bytes)
        with zipfile.ZipFile(output_archive) as bundle:
            require(bundle.testzip() is None and bundle.read("manifest.json") == public_bytes
                    and bundle.read("SOURCE-AVAILABILITY.txt") == PUBLIC_BETA_AVAILABILITY
                    and "SOURCE-AVAILABILITY-DRAFT.txt" not in bundle.namelist(),
                    "prepared Qt source ZIP failed readback")
    except Exception:
        if created_manifest:
            output_manifest.unlink(missing_ok=True)
        if created_archive:
            output_archive.unlink(missing_ok=True)
        raise
    result.update(applied=True,
                  archiveBytes=output_archive.stat().st_size,
                  archiveSha256=sha(output_archive),
                  manifestSha256=hashlib.sha256(public_bytes).hexdigest())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prototype-archive", type=Path, required=True)
    parser.add_argument("--prototype-manifest", type=Path, required=True)
    parser.add_argument("--output-archive", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write new files; omission is read-only.")
    args = parser.parse_args()
    print(json.dumps(prepare(args.prototype_archive, args.prototype_manifest,
                             args.output_archive, args.output_manifest, args.apply)))


if __name__ == "__main__":
    main()
