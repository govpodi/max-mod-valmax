"""Build a self-contained local MAX Mod installer from the verified runtime.

Only program files are packaged. No MAX profile, MData archive or Windows
credential is read by this builder. Outputs must be in a new build directory.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys

import launch_mod

LAYOUT_SHA256 = "1ad798bcd35446fae42e73ce26c95129e9e18a56d5197f58acee3e91e15bbd2d"
DEFAULT_LAYOUT = Path(r"D:\CodexLabs\max-mod-26.35.1\analysis\restore_layout_report.json")
TRANSFER_REPO = Path(__file__).resolve().parent.parent
TRANSFER_REPORTS = TRANSFER_REPO / "analysis/native-migration-v1"
TRANSFER_BUILD = Path("C:/ValMAXBuild")
TRANSFER_PACKAGES = TRANSFER_BUILD / "packages"
TRANSFER_MAX_FILES = 8192
TRANSFER_MAX_BYTES = 512 << 20
TRANSFER_JSON_BYTES = 8 << 20
TRANSFER_QT_CONF = b"[Paths]\nPrefix=.\nLibraries=.\nPlugins=plugins\nQmlImports=qml\nTranslations=translations\n"


def _transfer_require(condition, message):
    if not condition:
        raise ValueError("Native transfer: " + message)


def _transfer_hash(value):
    _transfer_require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "invalid SHA-256")
    return value


def _transfer_relative(value):
    _transfer_require(type(value) is str and 0 < len(value) <= 240 and "\\" not in value, "invalid relative path")
    for part in value.split("/"):
        _transfer_require(part not in ("", ".", "..") and not part.endswith((".", " "))
                          and not any(ord(c) < 32 or c in ':<>"|?*' for c in part)
                          and not re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part),
                          "ambiguous relative path")
    return value


def _transfer_path(value, roots):
    # Validate lexical components BEFORE resolve; never discover inputs outside
    # the explicitly permitted build/report trees or follow reparse ancestors.
    _transfer_require(isinstance(value, (str, Path)), "invalid evidence path")
    raw = str(value).replace("\\", "/")
    _transfer_require(re.match(r"^[A-Za-z]:/", raw) and len(raw) <= 1024, "absolute local path required")
    _transfer_require(all(part not in ("", ".", "..") and not part.endswith((".", " "))
                          and not any(ord(c) < 32 or c in ':<>"|?*' for c in part)
                          for part in raw[3:].split("/")), "ambiguous evidence path")
    path = Path(raw)
    _transfer_require(any(path == root or path.is_relative_to(root) for root in roots), "evidence escapes allowed roots")
    for parent in (path, *path.parents):
        info = parent.lstat()
        _transfer_require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                          "reparse evidence path")
    return path


def _transfer_read(path, limit=TRANSFER_JSON_BYTES):
    info = path.lstat()
    _transfer_require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
                      "invalid or oversized evidence file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    _transfer_require(len(data) <= limit and len(data) == info.st_size, "evidence changed while reading")
    return data


def _transfer_json(path, expected=None):
    data = _transfer_read(path)
    if expected is not None:
        _transfer_require(hashlib.sha256(data).hexdigest() == _transfer_hash(expected), "evidence hash mismatch")
    def pairs(items):
        result = {}
        for key, value in items:
            _transfer_require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    def bad_constant(_):
        raise ValueError("Native transfer: nonfinite JSON")
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError) as exc:
        raise ValueError("Native transfer: malformed evidence JSON") from exc
    _transfer_require(type(result) is dict and type(result.get("schema")) is int and result["schema"] == 1,
                      "unsupported evidence schema")
    return result


def _transfer_records(items):
    _transfer_require(type(items) is list and 0 < len(items) <= TRANSFER_MAX_FILES, "invalid file inventory")
    records, aliases, total = {}, {}, 0
    for item in items:
        _transfer_require(type(item) is dict, "invalid file record")
        name = _transfer_relative(item.get("path"))
        size = item.get("bytes")
        _transfer_require(type(size) is int and 0 <= size <= TRANSFER_MAX_BYTES, "invalid file size")
        _transfer_require(name not in records, "duplicate file record")
        # A file cannot alias either a file or a parent directory on Windows.
        parts = name.split("/")
        for end in range(1, len(parts) + 1):
            spelling = "/".join(parts[:end])
            identity = (spelling, end == len(parts))
            key = spelling.casefold()
            _transfer_require(key not in aliases or aliases[key] == identity, "case or file/directory collision")
            aliases[key] = identity
        records[name] = {"path": name, "bytes": size, "sha256": _transfer_hash(item.get("sha256"))}
        total += size
    _transfer_require(total <= TRANSFER_MAX_BYTES, "package exceeds byte limit")
    return records


def _transfer_tree(root, records):
    expected_dirs = {""}
    for relative in records:
        expected_dirs.update(parent.as_posix() for parent in Path(relative).parents if parent != Path("."))
    seen = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in ["", *dirs, *names]:
            path = Path(directory) / name if name else Path(directory)
            info = path.lstat()
            relative = path.relative_to(root).as_posix() if path != root else ""
            _transfer_require(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                              "linked helper payload")
            if name in names:
                _transfer_require(relative in records and stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                                  "unexpected helper file")
                record = records[relative]
                _transfer_require(info.st_size == record["bytes"] and digest(path) == record["sha256"], "helper file mismatch")
                seen.add(relative)
            else:
                _transfer_require(stat.S_ISDIR(info.st_mode) and relative in expected_dirs, "unexpected helper directory")
    _transfer_require(seen == set(records), "incomplete helper tree")


def verify_native_transfer(proof_path):
    """Validate local build evidence, not a signature or legal attestation.

    The distribution receipt must bind a separately hashed materials receipt
    (schema 1, kind valmax-transfer-materials, components qt/msvc, gaps [],
    sourcePackageManifestSha256 and packagedMaterials) to the FINAL manifest.
    Changing only distributionReady never enables a not-yet-cleared package.
    """
    proof_path = _transfer_path(proof_path, [TRANSFER_REPORTS])
    summary = _transfer_json(proof_path)
    for field in ("verified", "distributionReady", "productionIdleStartupVerified", "nativeCompactImportUiImplemented", "noPythonRuntimeInHelper"):
        _transfer_require(summary.get(field) is True, "helper evidence is not distribution-ready")
    _transfer_require(summary.get("persistentHttpServerInHelper") is False, "unexpected helper runtime")
    refs = summary.get("proofs")
    _transfer_require(type(refs) is dict, "missing proof chain")
    proofs, paths = {}, {}
    for name in ("build", "package", "runtime", "startup", "distribution"):
        ref = refs.get(name)
        _transfer_require(type(ref) is dict and set(ref) == {"path", "sha256"}, "missing hashed proof")
        paths[name] = _transfer_path(ref["path"], [TRANSFER_REPORTS])
        proofs[name] = _transfer_json(paths[name], ref["sha256"])
    build, package, runtime, startup, distribution = (proofs[key] for key in ("build", "package", "runtime", "startup", "distribution"))
    for data, fields in ((build, ("compiled",)), (package, ("packaged", "verifiedBundle", "distributionReady", "distributable")),
                         (runtime, ("verified", "syntheticOnly")), (startup, ("verified", "noSdkOrPythonInPath", "unrelatedWorkingDirectory"))):
        _transfer_require(all(data.get(key) is True for key in fields), "incomplete helper proof")
    _transfer_require(package.get("distributionBlockers") == [] and package.get("missingNonSystemDependencies") == [],
                      "unresolved package dependencies or notices")
    for data in (runtime, startup):
        _transfer_require(_transfer_path(data.get("packageProof"), [TRANSFER_REPORTS]) == paths["package"]
                          and data.get("packageProofSha256") == refs["package"]["sha256"], "unrelated package test proof")
    _transfer_require(_transfer_path(runtime.get("buildProof"), [TRANSFER_REPORTS]) == paths["build"]
                      and runtime.get("buildProofSha256") == refs["build"]["sha256"], "unrelated build test proof")
    for name in ("preview", "controller"):
        case = runtime.get("results", {}).get(name, {})
        process = case.get("process", {})
        _transfer_require(case.get("result", {}).get("ok") is True and process.get("exitCode") == 0
                          and process.get("failure") is None and process.get("remainingProcesses") == 0
                          and process.get("timedOut") is False, "helper test failed")
    process = startup.get("process", {})
    _transfer_require(process.get("exitCode") == 0 and process.get("failure") is None and process.get("remainingProcesses") == 0
                      and process.get("windowReady") is True and process.get("closeOwnerVerified") is True
                      and startup.get("fileSelected") is False and startup.get("importRequested") is False, "idle startup failed")
    manifest_hash = _transfer_hash(summary.get("packageManifestSha256"))
    root = _transfer_path(summary.get("package"), [TRANSFER_PACKAGES])
    _transfer_require(root.parent == TRANSFER_PACKAGES and root.name == "valmax-transfer-" + manifest_hash,
                      "unexpected helper package location")
    _transfer_require(_transfer_path(package.get("package"), [TRANSFER_PACKAGES]) == root
                      and package.get("manifestSha256") == manifest_hash, "package receipt mismatch")
    manifest_path = _transfer_path(root / "package-manifest.json", [root])
    manifest = _transfer_json(manifest_path, manifest_hash)
    _transfer_require(manifest.get("product") == "ValMAX native transfer helper" and manifest.get("distributionBlockers") == [],
                      "unexpected or uncleared package manifest")
    records = _transfer_records(manifest.get("files"))
    _transfer_require("package-manifest.json" not in records, "self-referential manifest")
    for name, expected_hash in (("valmax-transfer.exe", summary.get("helperSha256")), ("valmax_core.dll", summary.get("coreSha256"))):
        _transfer_require(name in records and records[name]["sha256"] == _transfer_hash(expected_hash), "helper/core mismatch")
    _transfer_require(manifest.get("coreSha256") == summary["coreSha256"] == build.get("coreSha256") == runtime.get("coreSha256"), "core proof mismatch")
    _transfer_require(summary.get("packagedFilesExcludingManifest") == len(records)
                      and package.get("files") == len(records)
                      and summary.get("packageBytesExcludingManifest") == sum(row["bytes"] for row in records.values())
                      and package.get("bytes") == summary["packageBytesExcludingManifest"], "package inventory totals mismatch")
    records["package-manifest.json"] = {"path": "package-manifest.json", "bytes": manifest_path.stat().st_size, "sha256": manifest_hash}
    _transfer_require(package.get("fileHashes") == {key: row["sha256"] for key, row in records.items()}, "package hash inventory mismatch")
    _transfer_require(distribution.get("kind") == "valmax-transfer-distribution" and distribution.get("distributionReady") is True
                      and distribution.get("packageManifestSha256") == manifest_hash and distribution.get("gaps") == [], "missing distribution clearance")
    material_ref = distribution.get("materialsProof")
    _transfer_require(type(material_ref) is dict and set(material_ref) == {"path", "sha256"}, "missing materials receipt")
    materials = _transfer_json(_transfer_path(material_ref["path"], [TRANSFER_REPORTS]), material_ref["sha256"])
    _transfer_require(materials.get("kind") == "valmax-transfer-materials" and materials.get("gaps") == []
                      and materials.get("components") == ["qt", "msvc"], "incomplete Qt/VC materials")
    _transfer_require(distribution.get("sourcePackageManifestSha256") == _transfer_hash(materials.get("sourcePackageManifestSha256")),
                      "materials source package mismatch")
    cleared = _transfer_records(distribution.get("packagedMaterials"))
    _transfer_require(cleared == _transfer_records(materials.get("packagedMaterials"))
                      and all(name.startswith("licenses/") and records.get(name) == row for name, row in cleared.items()),
                      "packaged materials mismatch")
    # References never grant arbitrary filesystem access. Recheck all source and
    # build artifact hashes used by the tested helper, with explicit roots.
    for data in (build, runtime, startup):
        hashes = data.get("sourceHashes")
        _transfer_require(type(hashes) is dict and 0 < len(hashes) <= 256, "missing source snapshot")
        for relative, wanted in hashes.items():
            relative = _transfer_relative(relative.replace("\\", "/"))
            _transfer_require(relative.startswith("native/"), "unexpected source evidence")
            path = _transfer_path(TRANSFER_REPO / relative, [TRANSFER_REPO / "native"])
            _transfer_require(digest(path) == _transfer_hash(wanted), "source snapshot changed")
    artifacts = build.get("artifacts")
    _transfer_require(type(artifacts) is dict and 2 <= len(artifacts) <= 16, "missing build artifacts")
    artifact_hashes = {}
    for value, wanted in artifacts.items():
        path = _transfer_path(value, [TRANSFER_BUILD / "build"])
        _transfer_require(path.is_file() and digest(path) == _transfer_hash(wanted), "build artifact changed")
        artifact_hashes.setdefault(path.name.lower(), set()).add(wanted)
    _transfer_require(summary["helperSha256"] in artifact_hashes.get("valmax-transfer.exe", set())
                      and summary["coreSha256"] in artifact_hashes.get("valmax_core.dll", set()), "unrelated helper artifacts")
    _transfer_tree(root, records)
    _transfer_require(_transfer_read(root / "qt.conf", 1024) == TRANSFER_QT_CONF, "non-isolated Qt configuration")
    return {"package": root, "records": records, "manifestSha256": manifest_hash, "proofSha256": digest(proof_path)}


def copy_native_transfer(verified, payload):
    """Exclusive copy; recheck the source and copied bytes before inventory."""
    root, records = verified["package"], verified["records"]
    _transfer_path(root, [TRANSFER_PACKAGES])
    _transfer_tree(root, records)
    target = payload / "runtime" / "transfer"
    target.mkdir()  # No overwrite/merge, even for an existing empty directory.
    for relative, row in sorted(records.items()):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (root / relative).open("rb") as source, destination.open("xb") as output:
            remaining = row["bytes"]
            while remaining:
                chunk = source.read(min(remaining, 1 << 20))
                _transfer_require(bool(chunk), "helper source truncated")
                output.write(chunk)
                remaining -= len(chunk)
            _transfer_require(not source.read(1), "helper source grew")
    _transfer_tree(target, records)
    _transfer_tree(root, records)
    return target


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def notices(destination):
    packages = ("frida", "cryptography", "cffi", "capstone", "pefile", "pyinstaller", "pyinstaller-hooks-contrib")
    sections = ["MAX Mod: локальная модификация MAX 26.35.1.80662.\n"
                "Исходные компоненты MAX принадлежат их правообладателям.\n"
                "Инструменты и библиотеки помощника:\n"]
    for package in packages:
        distribution = importlib.metadata.distribution(package)
        sections.append(f"\n{distribution.metadata['Name']} {distribution.version}\n")
        for item in distribution.files or ():
            if (".dist-info/" in str(item).replace("\\", "/")
                    and (item.name.lower().startswith(("license", "notice", "copying"))
                         or "sbom" in item.name.lower())):
                sections.append(distribution.locate_file(item).read_text(encoding="utf-8", errors="replace"))
                sections.append("\n")
    base = Path(sys.base_prefix)
    for label, path in (("CPython", base / "LICENSE.txt"),
                        ("Tk", base / "tcl" / "tk8.6" / "license.terms")):
        if not path.is_file():
            raise ValueError("Bundled Python/Tk license is missing")
        sections.extend(("\n" + label + "\n", path.read_text(encoding="utf-8"), "\n"))
    (destination / "THIRD-PARTY-NOTICES.txt").write_text("\n".join(sections), encoding="utf-8")


def validate_output(output, runtime, source):
    for protected in (runtime.resolve(), source.resolve()):
        if output.is_relative_to(protected) or protected.is_relative_to(output):
            raise ValueError("Build output overlaps program or source files")
    if output.exists():
        raise ValueError("Build output must be a new directory")


def payload_inventory(payload):
    records = []
    for path in sorted(payload.rglob("*")):
        if path.is_file():
            records.append({"path": path.relative_to(payload).as_posix(), "bytes": path.stat().st_size,
                            "sha256": digest(path)})
    (payload / "package-files.json").write_text(json.dumps({"schema": 1, "files": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def program_hashes(layout_path, manifest):
    data = layout_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != LAYOUT_SHA256:
        raise ValueError("Original MSI program inventory is missing or changed")
    layout = json.loads(data)
    if layout.get("file_count") != 1696 or len(layout["layout"]) != 1696:
        raise ValueError("Unexpected program inventory")
    records = {item["path"]: item["sha256"] for item in layout["layout"]}
    if len(records) != 1696:
        raise ValueError("Duplicate inventory files")
    for item in manifest["files"]:
        if item["relative_path"] not in records:
            raise ValueError("Patched file is absent from the original inventory")
        records[item["relative_path"]] = item["patched_sha256"]
    records["mod-manifest.json"] = None
    return records


def verify_program_tree(root, expected, *, allow_shared_dlls=False, allow_optimization_state=False):
    """Copied payloads are independent; verified source DLLs may share two links."""
    if type(allow_shared_dlls) is not bool or type(allow_optimization_state) is not bool:
        raise ValueError("Invalid program source sharing policy")
    seen = set()
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in ["", *dirs, *names]:
            path = Path(directory) / name if name else Path(directory)
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("Linked program payload is not allowed")
            if name in names:
                relative = path.relative_to(root).as_posix()
                # Public bool-only launch preferences are generated locally.
                # Validate them in the source tree, but never package them.
                if allow_optimization_state and relative == "optimization-active.json":
                    from optimization_store import MAX_STORE_BYTES, parse
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_STORE_BYTES:
                        raise ValueError("Invalid optimization launch snapshot")
                    parse(path.read_bytes())
                    continue
                shared_source_dll = allow_shared_dlls and path.suffix.lower() == ".dll" and info.st_nlink == 2
                if relative not in expected or not stat.S_ISREG(info.st_mode) or (info.st_nlink != 1 and not shared_source_dll):
                    raise ValueError("Unexpected program payload file")
                wanted = expected[relative]
                if wanted is not None and digest(path) != wanted:
                    raise ValueError("Program payload hash mismatch")
                seen.add(relative)
    if seen != set(expected):
        raise ValueError("Program payload inventory is incomplete")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=launch_mod.DEFAULT_MANIFEST)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--layout", type=Path, default=DEFAULT_LAYOUT)
    parser.add_argument("--native-transfer-proof", type=Path,
                        help="Opt in to the separately verified, distribution-ready native MData helper")
    args = parser.parse_args(argv)
    source = Path(__file__).resolve().parent
    executable = launch_mod.verified_executable(args.manifest)
    original_manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected = program_hashes(args.layout, original_manifest)
    verify_program_tree(executable.parent, expected, allow_shared_dlls=True, allow_optimization_state=True)
    output = args.output.resolve()
    validate_output(output, executable.parent, source)
    if not args.iscc.resolve(strict=True).is_file():
        raise ValueError("Inno Setup compiler is missing")
    native_transfer = verify_native_transfer(args.native_transfer_proof) if args.native_transfer_proof is not None else None
    if native_transfer is not None:
        validate_output(output, native_transfer["package"], source)
    output.mkdir(parents=True)
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
               "--name", "ValMAX", "--noupx", "--icon", str(executable), "--paths", str(source),
               "--distpath", str(output / "dist"), "--workpath", str(output / "work"),
               "--specpath", str(output)]
    for name in ("launch_mod", "privacy_server", "accounts", "privacy", "isolation", "ui_privacy",
                 "mdata_recovery", "mdata_import", "desktop_process", "tkinter"):
        command.extend(("--hidden-import", name))
    command.extend(("--collect-binaries", "capstone"))
    for name in ("config.defaults.json", "privacy_callsites.json", "privacy_monitor.js", "ghost_monitor.js", "chat_export_end_monitor.js", "message_retention_monitor.js", "notification_monitor.js", "notification_updater.js"):
        command.extend(("--add-data", str(source / name) + ";."))
    command.append(str(source / "bundle_entry.py"))
    with (output / "pyinstaller.log").open("wb") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    payload = output / "dist" / "ValMAX"
    # Existing MAX Mod shortcuts use the same AppId and relative runtime.
    # Refresh their helper too, so they cannot launch old code against new UI.
    shutil.copyfile(payload / "ValMAX.exe", payload / "MAX Mod.exe")
    runtime = payload / "runtime" / "mod"
    runtime.mkdir(parents=True)
    for relative in sorted(expected):
        target = runtime / launch_mod.build_mod.safe_relative(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(executable.parent / relative, target)
    verify_program_tree(runtime, expected)
    manifest = json.loads((runtime / "mod-manifest.json").read_text(encoding="utf-8"))
    manifest["destination"] = "."
    manifest["baseline"] = "MAX 26.35.1.80662 (program files only)"
    (runtime / "mod-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    launch_mod.verified_executable(runtime / "mod-manifest.json")
    shutil.copyfile(source.parent / "ValMAX-инструкция.txt", payload / "ValMAX-инструкция.txt")
    notices(payload)
    if native_transfer is not None:
        copy_native_transfer(native_transfer, payload)
    payload_inventory(payload)
    setup_command = [str(args.iscc.resolve()), "/Qp", "/DPayload=" + str(payload),
                     "/DOutput=" + str(output / "release")]
    if native_transfer is not None:
        setup_command.append("/DNativeTransfer=1")
    setup_command.append(str(source / "max-mod-setup.iss"))
    with (output / "inno.log").open("wb") as log:
        subprocess.run(setup_command, stdout=log, stderr=subprocess.STDOUT, check=True)
    setup = output / "release" / "ValMAX-26.35.1-Setup.exe"
    report = {"schema": 1, "installer": str(setup), "sha256": digest(setup), "bytes": setup.stat().st_size,
              "profileIncluded": False, "credentialsIncluded": False, "portableManifestVerified": True,
              "pyinstaller": importlib.metadata.version("pyinstaller"), "innoSetup": "6.7.3"}
    if native_transfer is not None:
        report["nativeTransfer"] = {"manifestSha256": native_transfer["manifestSha256"],
                                    "proofSha256": native_transfer["proofSha256"], "freshHelperOnly": True}
    (output / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
