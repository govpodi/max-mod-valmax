"""Fetch the small pinned-version Qt WebView / Microsoft SDK build inputs."""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile
import py7zr

ROOT = Path("C:/ValMAXBuild/deps/webview2-build")
ROOT.mkdir(parents=True, exist_ok=True)
QT_NAME = "6.11.2-0-202608131017qtwebview-Windows-Windows_11_24H2-MSVC2022-Windows-Windows_11_24H2-X86_64.7z"
QT_URL = "https://download.qt.io/online/qtsdkrepository/windows_x86/desktop/qt6_6112/qt6_6112_msvc2022_64/qt.qt6.6112.addons.qtwebview.win64_msvc2022_64/" + QT_NAME
INPUTS = [("qtwebview-sdk.7z", QT_URL, 2 * 1024 * 1024),
    ("qtwebview-v6.11.2.zip", "https://codeload.github.com/qt/qtwebview/zip/refs/tags/v6.11.2", 8 * 1024 * 1024),
    ("microsoft.web.webview2.1.0.4258.31.nupkg", "https://api.nuget.org/v3-flatcontainer/microsoft.web.webview2/1.0.4258.31/microsoft.web.webview2.1.0.4258.31.nupkg", 16 * 1024 * 1024)]
result = []
for name, url, limit in INPUTS:
    path = ROOT / name
    if not path.exists():
        with urllib.request.urlopen(url, timeout=30) as response: data = response.read(limit + 1)
        if len(data) > limit: raise RuntimeError("dependency_size_limit")
        path.write_bytes(data)
    data = path.read_bytes()
    entry = {"name": name, "url": url, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if name.endswith(".7z"):
        sha1 = urllib.request.urlopen(url + ".sha1", timeout=20).read(200).decode().split()[0]
        if hashlib.sha1(data).hexdigest() != sha1: raise RuntimeError("qt_sdk_sha1_mismatch")
        entry["publishedSha1"] = sha1
        target = ROOT / "qt-sdk"
        if not target.exists():
            target.mkdir()
            with py7zr.SevenZipFile(path) as archive: archive.extractall(target)
    else:
        target = ROOT / ("qt-source" if name.endswith(".zip") else "microsoft-sdk")
        if not target.exists():
            target.mkdir()
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if not (target / member.filename).resolve().is_relative_to(target.resolve()): raise RuntimeError("zip_path_escape")
                archive.extractall(target)
    result.append(entry)
(ROOT / "sources.json").write_text(json.dumps({"schema": 1, "qtVersion": "6.11.2", "microsoftSdkVersion": "1.0.4258.31", "inputs": result}, indent=2))
print(json.dumps(result))
