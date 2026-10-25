"""Verify an external Qt source asset before omitting its duplicate runtime copies.

This only binds exact source bytes to an assembled runtime. It does not assess
license completeness or whether a distributor has published the asset.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile


SHA256 = re.compile(r"[0-9a-f]{64}\Z")
QT_ARCHIVE = re.compile(r"qt[a-z0-9]+-everywhere-src-6\.11\.2\.tar\.xz\Z")
PUBLIC_BETA_AVAILABILITY = (
    "ValMAX Qt 6.11.2 source asset for public BETA packaging.\n"
    "The source archives and notices in this ZIP are listed in manifest.json.\n"
    "Publish this ZIP alongside the matching ValMAX installer and compare its SHA-256 "
    "with QT-SOURCE-AVAILABILITY.txt in that installer.\n"
    "This file does not confirm publication or whole-client license completeness.\n"
).encode("utf8")
ASSET_MODES = {
    "local-prototype-only": ("local-qt-source-bundle-prototype", "SOURCE-AVAILABILITY-DRAFT.txt"),
    "public-beta-asset": ("qt-source-bundle-public-beta", "SOURCE-AVAILABILITY.txt"),
}
ASSET_MANIFEST_FIELDS = frozenset({
    "basedOnFullBundle", "basedOnFullBundleSha256", "createdUtc", "distribution",
    "duplicateBytesSavedBeforeVariant", "excludedNoticeSubtree", "excludedSourceArchive",
    "kind", "legalCompleteness", "originalPackageManifestSha256",
    "originalSourceArchiveCount", "originalUniqueArchiveCount", "qtVersion",
    "retainedModules", "retainedNoticeBytes", "retainedNoticeFileCount",
    "retainedNoticeFiles", "retainedSourceArchiveBytes", "retainedSourceArchiveCount",
    "retainedSourceArchives", "schema", "variant", "version", "publicationStatus",
})


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular(path):
    path = Path(path)
    require(path.is_absolute(), "absolute Qt source asset path required")
    for item in (path, *path.parents):
        info = item.lstat()
        require(not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT,
                "Qt source asset uses a reparse path")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "ordinary Qt source asset file required")
    return info


def safe_member(name):
    path = PurePosixPath(name)
    require(isinstance(name, str) and name and "\\" not in name
            and not path.is_absolute() and path.as_posix() == name
            and all(part not in ("", ".", "..") and not part.endswith((".", " ")) for part in path.parts),
            "unsafe Qt source bundle member")
    return name


def archived_digest(bundle, name):
    digest, count = hashlib.sha256(), 0
    with bundle.open(name) as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
            count += len(chunk)
    return count, digest.hexdigest()


def verify_external_qt_source_asset(plan_entries, archive_path, manifest_path):
    """Return a byte-bound delivery receipt and exact installed paths to omit."""
    archive_path, manifest_path = Path(archive_path), Path(manifest_path)
    archive_size = regular(archive_path).st_size
    require(archive_size <= 2 << 30, "Qt source asset size bound exceeded")
    require(regular(manifest_path).st_size <= 2 << 20, "Qt source manifest size bound exceeded")
    raw_manifest = manifest_path.read_bytes()
    manifest = json.loads(raw_manifest)
    distribution = manifest.get("distribution") if isinstance(manifest, dict) else None
    require(distribution in ASSET_MODES, "unsupported Qt source bundle distribution")
    expected_kind, availability_name = ASSET_MODES[distribution]
    require(isinstance(manifest, dict)
            and set(manifest) <= ASSET_MANIFEST_FIELDS
            and manifest.get("kind") == expected_kind
            and manifest.get("variant") == "p16-integrated-slim-no-qtwebengine"
            and manifest.get("qtVersion") == "6.11.2"
            and manifest.get("legalCompleteness") == "not-assessed"
            and ((distribution == "local-prototype-only"
                  and manifest.get("publicationStatus") is None)
                 or (distribution == "public-beta-asset"
                     and manifest.get("publicationStatus") == "pending-upload")),
            "unsupported or overstated Qt source bundle manifest")
    source_rows = manifest.get("retainedSourceArchives")
    notice_rows = manifest.get("retainedNoticeFiles")
    require(isinstance(source_rows, list) and source_rows
            and isinstance(notice_rows, list) and notice_rows
            and manifest.get("retainedSourceArchiveCount") == len(source_rows)
            and manifest.get("retainedNoticeFileCount") == len(notice_rows),
            "Qt source bundle inventory is incomplete")
    planned = {row["path"]: row for row in plan_entries.values()}
    qt_paths = {name for name in planned if QT_ARCHIVE.fullmatch(PurePosixPath(name).name)
                and "/sources/" in name}
    require(qt_paths, "no Qt source archives were planned for the runtime")
    declared_paths, unique_names = {}, set()
    expected_zip = {"manifest.json", availability_name}
    notice_differences = []
    with zipfile.ZipFile(archive_path) as bundle:
        names = bundle.namelist()
        require(len(names) == len(set(names)) and len(names) <= 4096,
                "duplicate or excessive Qt source bundle members")
        require(all(safe_member(name) == name and not bundle.getinfo(name).is_dir() for name in names),
                "unsafe Qt source bundle entry")
        require(bundle.read("manifest.json") == raw_manifest, "Qt source ZIP manifest differs from pinned file")
        for row in source_rows:
            require(isinstance(row, dict) and set(row) ==
                    {"name", "bytes", "sha256", "sourcePathsInP16", "archiveTest"}
                    and isinstance(row["name"], str) and QT_ARCHIVE.fullmatch(row["name"])
                    and row["name"] not in unique_names
                    and type(row["bytes"]) is int and row["bytes"] > 0
                    and isinstance(row["sha256"], str) and SHA256.fullmatch(row["sha256"])
                    and row["archiveTest"] == "passed"
                    and isinstance(row["sourcePathsInP16"], list) and row["sourcePathsInP16"],
                    "invalid Qt source archive manifest record")
            unique_names.add(row["name"])
            member = "sources/" + row["name"]
            expected_zip.add(member)
            require(archived_digest(bundle, member) == (row["bytes"], row["sha256"]),
                    "Qt source ZIP member changed: " + member)
            for name in row["sourcePathsInP16"]:
                require(isinstance(name, str) and safe_member(name) == name
                        and PurePosixPath(name).name == row["name"]
                        and name not in declared_paths,
                        "invalid or duplicate Qt runtime source path")
                declared_paths[name] = row
                planned_row = planned.get(name)
                require(planned_row is not None and planned_row["sha256"] == row["sha256"]
                        and regular(Path(planned_row["source"])).st_size == row["bytes"]
                        and sha(planned_row["source"]) == row["sha256"],
                        "Qt source asset differs from planned runtime source: " + name)
        require(set(declared_paths) == qt_paths, "Qt source asset does not cover exact runtime archives")
        seen_notices = set()
        for row in notice_rows:
            require(isinstance(row, dict) and set(row) == {"path", "bytes", "sha256"}
                    and isinstance(row["path"], str) and row["path"].startswith("notices/")
                    and row["path"] not in seen_notices
                    and type(row["bytes"]) is int and row["bytes"] >= 0
                    and isinstance(row["sha256"], str) and SHA256.fullmatch(row["sha256"]),
                    "invalid Qt source asset notice inventory")
            name = safe_member(row["path"])
            seen_notices.add(name)
            expected_zip.add(name)
            require(archived_digest(bundle, name) == (row["bytes"], row["sha256"]),
                    "Qt source asset notice changed: " + name)
            parts = PurePosixPath(name).parts
            require(len(parts) >= 3 and parts[1] in {"mod", "transfer"},
                    "Qt source asset notice is outside runtime material branches")
            runtime_name = parts[1] + "/licenses/" + "/".join(parts[2:])
            runtime_row = planned.get(runtime_name)
            if runtime_name == "mod/licenses/STAGING-MATERIALS.json":
                # The assembly writes this receipt after planning, with its new
                # external-asset binding. The ZIP carries an older p16 snapshot.
                notice_differences.append(runtime_name)
                continue
            require(runtime_row is not None, "Qt source asset notice missing from installed runtime: " + runtime_name)
            if (runtime_row["sha256"], regular(Path(runtime_row["source"])).st_size) != (row["sha256"], row["bytes"]):
                notice_differences.append(runtime_name)
        require(set(names) == expected_zip, "Qt source ZIP has unlisted or missing members")
        require(manifest.get("retainedSourceArchiveBytes") == sum(row["bytes"] for row in source_rows)
                and manifest.get("retainedNoticeBytes") == sum(row["bytes"] for row in notice_rows),
                "Qt source bundle byte totals differ")
        availability = bundle.read(availability_name)
        if distribution == "local-prototype-only":
            require(0 < len(availability) <= 1 << 20,
                    "Qt source availability draft missing")
        else:
            require(availability == PUBLIC_BETA_AVAILABILITY,
                    "final Qt source availability differs")
    require(set(notice_differences) == {
        "mod/licenses/STAGING-MATERIALS.json",
        "mod/licenses/WebView/qtwebview-6.11.2-valmax-source.zip",
        "mod/licenses/WebView/webview-third-party-notices.zip",
    }, "unexpected difference between source asset notices and installed runtime")
    omitted = [{"path": name, "bytes": declared_paths[name]["bytes"],
                "sha256": declared_paths[name]["sha256"]} for name in sorted(declared_paths)]
    receipt = {"mode": "separate-asset", "assetName": archive_path.name,
               "assetBytes": archive_size, "assetSha256": sha(archive_path),
               "manifestSha256": hashlib.sha256(raw_manifest).hexdigest(),
               "manifestDistribution": manifest["distribution"],
               "availabilityDraftIncluded": distribution == "local-prototype-only",
               "qtVersion": "6.11.2", "sourceArchiveCount": len(source_rows),
               "omittedRuntimeCopies": omitted, "omittedRuntimeBytes": sum(row["bytes"] for row in omitted),
               "noticeFilesRetained": len(notice_rows),
               "assetNoticeSnapshotDifferences": sorted(notice_differences),
               "requiredTogetherWithInstaller": True, "legalCompleteness": "not-assessed"}
    return receipt, set(declared_paths)
