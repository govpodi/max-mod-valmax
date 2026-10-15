"""Restore an MSI file-table dump without installing or executing MAX.

The original workspace is read only. MSI tables are read with msi.dll APIs;
optional CAB verification uses Microsoft's expand.exe as a data extractor.
No MSI custom actions, registration, application processes, or network calls.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from collections import deque

import pefile


class MsiDatabase:
    def __init__(self, path: Path):
        self.api = ctypes.WinDLL("msi", use_last_error=True)
        handle = wintypes.DWORD
        signatures = {
            "MsiOpenDatabaseW": ([wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.POINTER(handle)], wintypes.UINT),
            "MsiDatabaseOpenViewW": ([handle, wintypes.LPCWSTR, ctypes.POINTER(handle)], wintypes.UINT),
            "MsiViewExecute": ([handle, handle], wintypes.UINT),
            "MsiViewFetch": ([handle, ctypes.POINTER(handle)], wintypes.UINT),
            "MsiRecordGetStringW": ([handle, wintypes.UINT, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.UINT),
            "MsiRecordReadStream": ([handle, wintypes.UINT, ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)], wintypes.UINT),
            "MsiCloseHandle": ([handle], wintypes.UINT),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = handle()
        self.check(self.api.MsiOpenDatabaseW(str(path), None, ctypes.byref(self.handle)))

    @staticmethod
    def check(status: int):
        if status:
            raise OSError(status, f"MSI API error {status}")

    def string(self, record, field):
        size = wintypes.DWORD()
        status = self.api.MsiRecordGetStringW(record, field, None, ctypes.byref(size))
        if status not in (0, 234):
            self.check(status)
        size.value += 1
        buffer = ctypes.create_unicode_buffer(size.value)
        self.check(self.api.MsiRecordGetStringW(record, field, buffer, ctypes.byref(size)))
        return buffer.value

    def records(self, sql):
        view = wintypes.DWORD()
        self.check(self.api.MsiDatabaseOpenViewW(self.handle, sql, ctypes.byref(view)))
        try:
            self.check(self.api.MsiViewExecute(view, 0))
            while True:
                record = wintypes.DWORD()
                status = self.api.MsiViewFetch(view, ctypes.byref(record))
                if status == 259:
                    break
                self.check(status)
                try:
                    yield record
                finally:
                    self.api.MsiCloseHandle(record)
        finally:
            self.api.MsiCloseHandle(view)

    def query(self, table, fields):
        sql = "SELECT " + ", ".join(f"`{name}`" for name in fields) + f" FROM `{table}`"
        return [dict(zip(fields, [self.string(row, index + 1) for index in range(len(fields))]))
                for row in self.records(sql)]

    def extract_stream(self, name, destination):
        if "'" in name or "/" in name or "\\" in name:
            raise ValueError("Unsafe MSI stream name")
        rows = self.records(f"SELECT `Data` FROM `_Streams` WHERE `Name` = '{name}'")
        count = 0
        for record in rows:
            count += 1
            with destination.open("wb") as output:
                buffer = ctypes.create_string_buffer(1024 * 1024)
                while True:
                    size = wintypes.DWORD(len(buffer))
                    self.check(self.api.MsiRecordReadStream(record, 1, buffer, ctypes.byref(size)))
                    if not size.value:
                        break
                    output.write(buffer.raw[:size.value])
        if count != 1:
            raise ValueError(f"Expected one cabinet stream for {name}, got {count}")

    def close(self):
        self.api.MsiCloseHandle(self.handle)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_relative(value):
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or ":" in value:
        raise ValueError(f"Unsafe payload path: {value!r}")
    return path


def long_name(value):
    # Directory DefaultDir may have target:source; FileName may have short|long.
    return value.split(":", 1)[0].split("|")[-1]


def read_layout(db):
    properties = {row["Property"]: row["Value"] for row in db.query("Property", ["Property", "Value"])}
    directories = {row["Directory"]: row for row in db.query("Directory", ["Directory", "Directory_Parent", "DefaultDir"])}
    components = {row["Component"]: row["Directory_"] for row in db.query("Component", ["Component", "Directory_"])}

    def directory_path(identifier, ancestors=()):
        if identifier == "INSTALL_ROOT":
            return Path()
        if identifier in ancestors or identifier not in directories:
            raise ValueError(f"Directory outside INSTALL_ROOT or cyclic: {identifier}")
        row = directories[identifier]
        parent = directory_path(row["Directory_Parent"], (*ancestors, identifier))
        name = long_name(row["DefaultDir"])
        return parent if name in (".", "") else parent / safe_relative(name)

    layout = []
    destinations = set()
    for row in db.query("File", ["File", "Component_", "FileName", "FileSize", "Version", "Sequence"]):
        relative = directory_path(components[row["Component_"]]) / safe_relative(long_name(row["FileName"]))
        key = str(relative).casefold()
        if key in destinations:
            raise ValueError(f"Duplicate target: {relative}")
        destinations.add(key)
        layout.append({"file_id": row["File"], "path": relative.as_posix(),
                       "size": int(row["FileSize"]), "version": row["Version"], "sequence": int(row["Sequence"])})
    return properties, sorted(layout, key=lambda item: item["path"].casefold())


def identify_sources(layout, source):
    files = [path for path in source.iterdir() if path.is_file()]
    aliases = []
    for item in layout:
        expected = source / safe_relative(item["file_id"])
        if expected.is_file():
            item["source_file"] = expected.name
            continue
        # Some cabinet dumpers truncate the tail of long CM_FH IDs with underscores.
        # The path hash prefix stays intact; accept only one candidate, then compare
        # its bytes with the official cabinet when --verify-cab is supplied.
        match = re.match(r"^(CM_FH_[0-9a-f]+_)", item["file_id"], re.IGNORECASE)
        candidates = [path for path in files if match and path.name.startswith(match[1])
                      and path.stat().st_size == item["size"]]
        if len(candidates) != 1:
            raise ValueError(f"Missing or ambiguous original: {item['file_id']}")
        item["source_file"] = candidates[0].name
        aliases.append({"file_id": item["file_id"], "source_file": candidates[0].name})
    return aliases


def verify_cab(db, scratch, layout, source):
    scratch.mkdir(parents=True, exist_ok=True)
    media = db.query("Media", ["DiskId", "LastSequence", "Cabinet"])
    cabinet_reports = []
    extracted_dirs = []
    for row in media:
        cabinet = row["Cabinet"]
        if not cabinet.startswith("#"):
            raise ValueError("Only embedded cabinets are supported")
        filename = safe_relative(cabinet[1:])
        cab_path = scratch / filename
        db.extract_stream(cabinet[1:], cab_path)
        extracted = scratch / (filename.stem + "-extracted")
        extracted.mkdir(parents=True, exist_ok=True)
        result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "expand.exe"),
                                 str(cab_path), "-F:*", str(extracted)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(f"CAB extraction failed: {result.stderr or result.stdout}")
        extracted_dirs.append(extracted)
        cabinet_reports.append({"name": cabinet[1:], "sha256": digest(cab_path), "bytes": cab_path.stat().st_size})
    matches, mismatches = 0, []
    for item in layout:
        file_id = item["file_id"]
        official = next((directory / file_id for directory in extracted_dirs if (directory / file_id).is_file()), None)
        original = source / item["source_file"]
        if official is None or not original.is_file():
            mismatches.append({"file_id": file_id, "error": "missing original or official CAB member"})
        elif digest(original) != digest(official):
            mismatches.append({"file_id": file_id, "error": "SHA-256 mismatch"})
        else:
            matches += 1
    return {"cabinets": cabinet_reports, "matched_files": matches, "mismatches": mismatches}


def pe_information(path):
    pe = pefile.PE(str(path), fast_load=True, max_symbol_exports=65536)
    pe.parse_data_directories(directories=[0, 1, 2, 13])
    imports = []
    for attribute, delayed in (("DIRECTORY_ENTRY_IMPORT", False), ("DIRECTORY_ENTRY_DELAY_IMPORT", True)):
        for descriptor in getattr(pe, attribute, []):
            imports.append({"dll": descriptor.dll.decode("ascii"), "delayed": delayed,
                            "symbols": [entry.name.decode("ascii") if entry.name else f"#{entry.ordinal}"
                                        for entry in descriptor.imports]})
    exports = set()
    for symbol in getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "symbols", []):
        if symbol.name:
            exports.add(symbol.name.decode("ascii"))
        exports.add(f"#{symbol.ordinal}")
    version = {}
    for groups in getattr(pe, "FileInfo", []):
        for group in groups:
            for table in getattr(group, "StringTable", []):
                version.update({key.decode(errors="replace"): value.decode(errors="replace")
                                for key, value in table.entries.items()})
    information = {"machine": f"0x{pe.FILE_HEADER.Machine:04x}", "imports": imports, "exports": exports,
                   "original_filename": version.get("OriginalFilename", ""),
                   "export_dll_name": getattr(getattr(pe, "DIRECTORY_ENTRY_EXPORT", None), "name", b"").decode(errors="replace")}
    pe.close()
    return information


def dependency_audit(root):
    system = Path(os.environ["SystemRoot"]) / "System32"
    all_local = sorted((path for path in root.rglob("*") if path.suffix.lower() in (".exe", ".dll")), key=str)
    info = {path: pe_information(path) for path in all_local}
    system_index = {path.name.casefold(): path for path in system.iterdir() if path.is_file()}
    local_index = {}
    for path in all_local:
        local_index.setdefault(path.parent, {})[path.name.casefold()] = path
    system_info = {}

    def resolve(name, binary, entry):
        folded = name.casefold()
        # These virtual contracts are resolved by the Windows API-set loader.
        if folded.startswith(("api-ms-", "ext-ms-")):
            return "windows_api_set", None
        for directory in (entry.parent, binary.parent):
            if folded in local_index.get(directory, {}):
                return "payload", local_index[directory][folded]
        if folded in system_index:
            return "system32", system_index[folded]
        return "missing", None

    def audit(entries, all_roots=False):
        queue = deque((entry, entry) for entry in entries)
        visited = set()
        missing = []
        missing_symbols = []
        system_dependencies = set()
        api_sets = set()
        delayed_missing = []
        while queue:
            binary, entry = queue.popleft()
            if (binary, entry) in visited:
                continue
            visited.add((binary, entry))
            binary_info = info[binary]
            for imported in binary_info["imports"]:
                dll = imported["dll"]
                kind, target = resolve(dll, binary, entry)
                if kind == "missing":
                    row = {"binary": binary.relative_to(root).as_posix(), "dll": dll}
                    (delayed_missing if imported["delayed"] else missing).append(row)
                elif kind == "windows_api_set":
                    api_sets.add(dll)
                else:
                    if kind == "payload":
                        target_info = info[target]
                        queue.append((target, entry))
                    else:
                        system_dependencies.add(dll)
                        if target not in system_info:
                            system_info[target] = pe_information(target)
                        target_info = system_info[target]
                    unavailable = sorted(set(imported["symbols"]) - target_info["exports"])
                    if unavailable:
                        missing_symbols.append({"binary": binary.relative_to(root).as_posix(), "dll": dll,
                                                "target": str(target), "symbols": unavailable,
                                                "delayed": imported["delayed"]})
        return {"entries": [path.relative_to(root).as_posix() for path in entries],
                "payload_binaries_checked": len({binary for binary, _ in visited}),
                "missing_import_dlls": missing, "missing_delay_import_dlls": delayed_missing,
                "missing_import_symbols": missing_symbols,
                "system32_dependencies": sorted(system_dependencies), "windows_api_sets": sorted(api_sets),
                "system_note": "System32 export names checked directly; API-set contracts and export forwarders are left to Windows. No application execution."}

    entrypoints = [root / "max.exe", root / "max-service" / "max-service.exe"]
    main = audit(entrypoints)
    plugin_entries = []
    for path in all_local:
        # Inspect every plugin with the corresponding application's DLL search root.
        if path not in entrypoints:
            entry = entrypoints[1] if path.is_relative_to(root / "max-service") else entrypoints[0]
            plugin_entries.append((path, entry))
    # Make a combined scan of all additional PE files without pretending they are startup dependencies.
    optional = {"missing_import_dlls": [], "missing_delay_import_dlls": [], "missing_import_symbols": []}
    for binary, entry in plugin_entries:
        for imported in info[binary]["imports"]:
            kind, target = resolve(imported["dll"], binary, entry)
            if kind == "missing":
                field = "missing_delay_import_dlls" if imported["delayed"] else "missing_import_dlls"
                optional[field].append({"binary": binary.relative_to(root).as_posix(), "dll": imported["dll"]})
    return {"entrypoint_closure": main, "all_payload_pe_files": len(all_local), "additional_payload_scan": optional,
            "pe_metadata": [{"path": path.relative_to(root).as_posix(), "machine": values["machine"],
                             "original_filename": values["original_filename"], "export_dll_name": values["export_dll_name"]}
                            for path, values in info.items()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--msi", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--verify-cab", type=Path, help="D: scratch directory for official cabinet verification")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.out.resolve()
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("Input and output must be separate trees")
    db = MsiDatabase(args.msi.resolve())
    try:
        properties, layout = read_layout(db)
        report = {"source": str(source), "output": str(output), "msi": str(args.msi.resolve()),
                  "msi_sha256": digest(args.msi), "product_version": properties.get("ProductVersion"),
                  "product_code": properties.get("ProductCode"), "file_count": len(layout), "layout": layout,
                  "applications_executed": False, "installer_executed": False}
        report["source_filename_aliases"] = identify_sources(layout, source)
        if args.verify_cab:
            report["official_cab_comparison"] = verify_cab(db, args.verify_cab.resolve(), layout, source)
            if report["official_cab_comparison"]["mismatches"]:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                raise ValueError("Original files differ from the official MSI cabinet")
        for item in layout:
            original = source / safe_relative(item["source_file"])
            destination = output / safe_relative(item["path"])
            if not original.is_file() or original.stat().st_size != item["size"]:
                raise ValueError(f"Input absent or wrong size: {original}")
            item["sha256"] = digest(original)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if digest(destination) != item["sha256"]:
                    raise ValueError(f"Refusing to overwrite changed file: {destination}")
            else:
                shutil.copy2(original, destination)
            if destination.stat().st_size != item["size"] or digest(destination) != item["sha256"]:
                raise ValueError(f"Copy verification failed: {destination}")
        report["dependencies"] = dependency_audit(output)
        closure = report["dependencies"]["entrypoint_closure"]
        report["static_startup_imports_resolved"] = not (closure["missing_import_dlls"] or
                                                          any(not row["delayed"] for row in closure["missing_import_symbols"]))
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output), "restored_files": len(layout),
                          "official_cab_matches": report.get("official_cab_comparison", {}).get("matched_files"),
                          "static_startup_imports_resolved": report["static_startup_imports_resolved"],
                          "missing_startup_dlls": closure["missing_import_dlls"],
                          "report": str(args.report)}, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
