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
import shutil
import stat
import subprocess
import sys

import launch_mod

LAYOUT_SHA256 = "1ad798bcd35446fae42e73ce26c95129e9e18a56d5197f58acee3e91e15bbd2d"
DEFAULT_LAYOUT = Path(r"D:\CodexLabs\max-mod-26.35.1\analysis\restore_layout_report.json")


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
    payload_inventory(payload)
    setup_command = [str(args.iscc.resolve()), "/Qp", "/DPayload=" + str(payload),
                     "/DOutput=" + str(output / "release"), str(source / "max-mod-setup.iss")]
    with (output / "inno.log").open("wb") as log:
        subprocess.run(setup_command, stdout=log, stderr=subprocess.STDOUT, check=True)
    setup = output / "release" / "ValMAX-26.35.1-Setup.exe"
    report = {"schema": 1, "installer": str(setup), "sha256": digest(setup), "bytes": setup.stat().st_size,
              "profileIncluded": False, "credentialsIncluded": False, "portableManifestVerified": True,
              "pyinstaller": importlib.metadata.version("pyinstaller"), "innoSetup": "6.7.3"}
    (output / "build-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
