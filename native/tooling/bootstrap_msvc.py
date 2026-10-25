"""Extract a pinned Microsoft x64 compiler/SDK without running any installer.

Only development tools are installed here. Runtime deployment is a separate step.
MSIs are opened READONLY and their File/Directory/Media tables are interpreted;
Windows expand.exe unpacks verified CABs. No MSI actions or registry writes run.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile

CATALOG_URL = "https://download.visualstudio.microsoft.com/download/pr/c95450be-0a45-4779-b142-33f567ccfd6e/a871a5ff41acebbe5037455fd6f51fcd4907b47d8f3c9860871ea0b222a2fd45/VisualStudio.vsman"
# Exact bytes returned by the official HTTPS endpoint, checked at bootstrap.
# The mutable channel reports a different catalog hash/size; evidence preserves
# that discrepancy. Payload hashes are independently verified against this pin.
CATALOG_SHA256 = "99dda859c4ae3c446eb25620d1a6237bb135d95d14d55cd1d30a15340bb39d80"
VC_PREFIX = "Microsoft.VC.14.44.17.14."
VC_IDS = (
    "Tools.HostX64.TargetX64.base", "Tools.HostX64.TargetX64.Res.base",
    "CRT.Headers.base", "CRT.x64.Desktop.base", "CRT.x64.Store.base", "CRT.Redist.X64.base",
)
SDK_ID = "Win11SDK_10.0.26100"
SDK_MSIS = (
    "Universal CRT Headers Libraries and Sources-x86_en-us.msi",
    "Windows SDK Desktop Headers x86-x86_en-us.msi",
    "Windows SDK Desktop Headers x64-x86_en-us.msi",
    "Windows SDK Desktop Libs x64-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Headers-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Headers OnecoreUap-x86_en-us.msi",
    "Windows SDK OnecoreUap Headers x86-x86_en-us.msi",
    "Windows SDK OnecoreUap Headers x64-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Libs-x86_en-us.msi",
    "Windows SDK Desktop Tools x64-x86_en-us.msi",
    "Windows SDK for Windows Store Apps Tools-x86_en-us.msi",
    "Windows SDK EULA-x86_en-us.msi",
)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def safe_path(base: Path, relative: str) -> Path:
    posix = PurePosixPath(relative.replace("\\", "/"))
    if posix.is_absolute() or any(p in ("..", "") or ":" in p for p in posix.parts):
        raise ValueError("unsafe package path")
    result = base.joinpath(*posix.parts)
    if not result.resolve().is_relative_to(base.resolve()):
        raise ValueError("package path escapes destination")
    return result


def download(url: str, destination: Path, expected: str) -> dict:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in (
        "download.visualstudio.microsoft.com", "download.microsoft.com",
    ):
        raise ValueError("not an official Microsoft payload URL")
    if not destination.exists() or digest(destination) != expected.lower():
        destination.parent.mkdir(parents=True, exist_ok=True)
        part = destination.with_suffix(destination.suffix + ".partial")
        request = urllib.request.Request(url, headers={"User-Agent": "ValMAX-Toolchain/1.0", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=120) as response, part.open("wb") as target:
            if urllib.parse.urlsplit(response.url).hostname not in (
                "download.visualstudio.microsoft.com", "download.microsoft.com",
            ):
                raise ValueError("unexpected payload redirect")
            shutil.copyfileobj(response, target, 1024 * 1024)
        if digest(part) != expected.lower():
            raise ValueError("SHA256 mismatch: " + destination.name)
        part.replace(destination)
    return {"url": url, "file": str(destination), "sha256": expected.lower(), "bytes": destination.stat().st_size, "verified": True}


class MsiDatabase:
    """Minimal read-only MSI SQL reader. Never calls MsiInstallProduct."""
    def __init__(self, path: Path):
        self.api = ctypes.WinDLL("msi.dll")
        self.api.MsiOpenDatabaseW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
        self.api.MsiDatabaseOpenViewW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
        self.api.MsiViewExecute.argtypes = [ctypes.c_uint, ctypes.c_uint]
        self.api.MsiViewFetch.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)]
        self.api.MsiRecordGetStringW.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
        self.api.MsiRecordGetFieldCount.argtypes = [ctypes.c_uint]
        self.api.MsiCloseHandle.argtypes = [ctypes.c_uint]
        self.handle = ctypes.c_uint()
        self.check(self.api.MsiOpenDatabaseW(str(path), None, ctypes.byref(self.handle)))

    @staticmethod
    def check(code: int):
        if code != 0:
            raise OSError(code, "MSI read-only query failed")

    def query(self, sql: str) -> list[list[str]]:
        view = ctypes.c_uint()
        self.check(self.api.MsiDatabaseOpenViewW(self.handle, sql, ctypes.byref(view)))
        rows = []
        try:
            self.check(self.api.MsiViewExecute(view, 0))
            while True:
                record = ctypes.c_uint()
                code = self.api.MsiViewFetch(view, ctypes.byref(record))
                if code == 259:
                    break
                self.check(code)
                try:
                    row = []
                    for index in range(1, self.api.MsiRecordGetFieldCount(record) + 1):
                        size = ctypes.c_uint(32767)
                        value = ctypes.create_unicode_buffer(size.value)
                        self.check(self.api.MsiRecordGetStringW(record, index, value, ctypes.byref(size)))
                        row.append(value.value)
                    rows.append(row)
                finally:
                    self.api.MsiCloseHandle(record)
        finally:
            self.api.MsiCloseHandle(view)
        return rows

    def close(self):
        self.api.MsiCloseHandle(self.handle)


def msi_tables(path: Path) -> dict:
    db = MsiDatabase(path)
    try:
        return {
            "media": db.query("SELECT `Cabinet` FROM `Media`"),
            "directories": db.query("SELECT `Directory`, `Directory_Parent`, `DefaultDir` FROM `Directory`"),
            "components": db.query("SELECT `Component`, `Directory_` FROM `Component`"),
            "files": db.query("SELECT `File`, `Component_`, `FileName`, `FileSize` FROM `File`"),
        }
    finally:
        db.close()


def extract_vsix(path: Path, root: Path):
    count = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            name = urllib.parse.unquote(info.filename)
            if not name.startswith("Contents/") or info.is_dir():
                continue
            target = safe_path(root, name[len("Contents/"):])
            target.parent.mkdir(parents=True, exist_ok=True)
            content = archive.read(info)
            # Re-running while the compiler's own mspdbsrv holds runtime DLLs
            # must not rewrite identical files.
            if not target.exists() or target.read_bytes() != content:
                target.write_bytes(content)
            count += 1
    return count


def extract_msi(path: Path, tables: dict, root: Path, work: Path) -> int:
    work.mkdir(parents=True, exist_ok=True)
    for row in tables["media"]:
        cab = row[0]
        if not cab:
            continue
        if cab.startswith("#"):
            raise ValueError("embedded CAB not supported")
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/expand.exe"), "-R", "-F:*", str(safe_path(path.parent, cab)), str(work)], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    directories = {row[0]: (row[1], row[2]) for row in tables["directories"]}
    components = dict(tables["components"])

    def relative_dir(key: str, visited: tuple[str, ...] = ()) -> str:
        if not key or key == "TARGETDIR":
            return ""
        if key in visited:
            raise ValueError("cyclic MSI directory")
        parent, name = directories[key]
        # DefaultDir is target:source; either can be short|long.
        name = name.split(":")[0].split("|")[-1]
        base = relative_dir(parent, visited + (key,))
        if name == ".":
            return base
        return (base + "/" + name).lstrip("/")

    count = 0
    for file_id, component, filename, size in tables["files"]:
        source = safe_path(work, file_id)
        if not source.is_file() or source.stat().st_size != int(size):
            raise ValueError("missing/incorrect MSI member " + file_id)
        relative = relative_dir(components[component]) + "/" + filename.split("|")[-1]
        # SDK's ProgramFilesFolder is just an MSI root property. Extract under
        # this private toolchain, never resolve any real machine directory.
        parts = relative.lstrip("/").split("/")
        for prefix in ("PFiles", "Program Files", "Program Files (x86)"):
            if parts and parts[0] == prefix:
                parts.pop(0)
        target = safe_path(root, "/".join(parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or digest(source) != digest(target):
            shutil.copy2(source, target)
        count += 1
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("C:/ValMAXBuild/msvc"))
    parser.add_argument("--downloads", type=Path, default=Path("C:/ValMAXBuild/downloads"))
    parser.add_argument("--proof", type=Path, default=Path("D:/ValMAX/analysis/native-migration-v1/msvc-downloads.json"))
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    args.downloads.mkdir(parents=True, exist_ok=True)
    catalog = args.downloads / "vs17-catalog.json"
    records = [download(CATALOG_URL, catalog, CATALOG_SHA256)]
    data = json.loads(catalog.read_bytes())
    selected = [package for package in data["packages"] if package["id"] in {VC_PREFIX + suffix for suffix in VC_IDS} and package.get("language", "en-US") == "en-US"]
    if len(selected) != len(VC_IDS):
        raise ValueError("pinned compiler package selection incomplete")
    sdk = next(package for package in data["packages"] if package["id"] == SDK_ID)
    payloads = [payload for package in selected for payload in package["payloads"]]
    sdk_payloads = {Path(payload["fileName"].replace("\\", "/")).name: payload for payload in sdk["payloads"]}
    payloads += [sdk_payloads[name] for name in SDK_MSIS]

    def fetch(payload):
        local = safe_path(args.downloads, payload["fileName"])
        result = download(payload["url"], local, payload["sha256"])
        result["advertisedBytes"] = payload.get("size")
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for record in pool.map(fetch, payloads):
            records.append(record)
            print("verified", Path(record["file"]).name, record["bytes"], flush=True)
    msi_specs = {}
    cab_names = set()
    for name in SDK_MSIS:
        msi = safe_path(args.downloads, sdk_payloads[name]["fileName"])
        msi_specs[name] = msi_tables(msi)
        cab_names.update(row[0] for row in msi_specs[name]["media"] if row[0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for record in pool.map(fetch, [sdk_payloads[name] for name in sorted(cab_names)]):
            records.append(record)
            print("verified", Path(record["file"]).name, record["bytes"], flush=True)
    args.proof.parent.mkdir(parents=True, exist_ok=True)
    proof = {"schema": 1, "catalogVersion": data["info"]["productDisplayVersion"], "catalogSha256": CATALOG_SHA256, "catalogChannelHashMismatch": True, "catalogTrust": "Pinned exact HTTPS response; channel metadata hash mismatch recorded, not claimed verified", "records": records, "installerExecuted": False, "registryModified": False, "extracted": False, "root": str(args.root)}
    args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    counts = {}
    for package in selected:
        for payload in package["payloads"]:
            counts[payload["fileName"]] = extract_vsix(safe_path(args.downloads, payload["fileName"]), args.root)
    for name, tables in msi_specs.items():
        msi = safe_path(args.downloads, sdk_payloads[name]["fileName"])
        counts[name] = extract_msi(msi, tables, args.root, args.root / ".extract" / msi.stem)
        print("extracted", name, counts[name], flush=True)
    proof.update({"extracted": True, "memberCounts": counts})
    args.proof.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"extracted": True, "files": sum(counts.values()), "proof": str(args.proof)}))


if __name__ == "__main__":
    main()
