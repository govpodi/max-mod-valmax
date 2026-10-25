"""Read-only Microsoft/Qt metadata; never downloads the complete runtime."""
import datetime
import hashlib
import json
from pathlib import Path
import re
import struct
import urllib.request

PROJECT = Path(__file__).resolve().parents[2]
PAGE = "https://developer.microsoft.com/en-us/microsoft-edge/webview2/"
QT_ROOT = "https://raw.githubusercontent.com/qt/qtwebview/v6.11.2/"


def fetch(url, limit=2 * 1024 * 1024):
    with urllib.request.urlopen(url, timeout=30) as response:
        value = response.read(limit + 1)
        if len(value) > limit: raise RuntimeError("metadata_too_large")
        return value


def main():
    page = fetch(PAGE).decode("utf8").replace(r"\u002F", "/")
    urls = re.findall(r'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/filestreamingservice/files/[a-f0-9-]+/Microsoft\.WebView2\.FixedVersionRuntime\.([0-9.]+)\.x64\.cab', page)
    if not urls: raise RuntimeError("official_download_metadata_missing")
    version = max(set(urls), key=lambda value: tuple(map(int, value.split("."))))
    link = re.search(r'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/filestreamingservice/files/[a-f0-9-]+/Microsoft\.WebView2\.FixedVersionRuntime\.' + re.escape(version) + r'\.x64\.cab', page).group(0)
    with urllib.request.urlopen(urllib.request.Request(link, method="HEAD"), timeout=30) as response:
        headers = {key: response.headers.get(key) for key in ("Content-Length", "Last-Modified", "ETag", "Accept-Ranges")}
    # Only a bounded CAB directory range, for unpacked-size planning. If a CDN
    # ignores Range, close immediately instead of consuming a 300 MB response.
    with urllib.request.urlopen(urllib.request.Request(link, headers={"Range": "bytes=0-65535"}), timeout=30) as response:
        if response.status != 206 or not response.headers.get("Content-Range", "").startswith("bytes 0-65535/"):
            raise RuntimeError("bounded_metadata_range_not_honored")
        data = response.read(65537)
    if len(data) != 65536 or data[:4] != b"MSCF": raise RuntimeError("cab_metadata_invalid")
    cabinet_size, files_at = struct.unpack_from("<I4xI", data, 8)
    expected_size = int(headers["Content-Length"])
    count = struct.unpack_from("<H", data, 28)[0]
    # Signed CAB files may append a certificate after the cabinet payload.
    if not 0 <= expected_size - cabinet_size <= 1024 * 1024 or not 1 <= count <= 8192: raise RuntimeError("cab_header_invalid")
    position, unpacked = files_at, 0
    for _ in range(count):
        if position + 16 >= len(data): raise RuntimeError("cab_directory_exceeds_metadata_range")
        unpacked += struct.unpack_from("<I", data, position)[0]
        position = data.index(0, position + 16) + 1
    sources = {}
    for relative in ("src/plugins/windows/qwebview2webview.cpp", "src/plugins/windows/qwebview2webviewplugin.cpp", "src/webview/qwebviewfactory.cpp"):
        raw = fetch(QT_ROOT + relative)
        lines = raw.decode().splitlines()
        sources[relative] = {"url": QT_ROOT + relative, "sha256": hashlib.sha256(raw).hexdigest(),
            "relevantLines": {name: [i + 1 for i, line in enumerate(lines) if name in line]
                for name in ("CreateCoreWebView2EnvironmentWithOptions", "AppLocalDataLocation", "QT_WEBVIEW_PLUGIN", "CreateCoreWebView2Controller")}}
    result = {"schema": 1, "checkedAtUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "runtimeDownloaded": False, "runtimeStarted": False, "readOnly": True,
        "downloadPage": PAGE, "downloadPageSha256": hashlib.sha256(page.encode()).hexdigest(),
        "runtime": {"version": version, "architecture": "x64", "url": link, "compressedBytes": expected_size,
            "cabFileCount": count, "cabinetPayloadBytes": cabinet_size, "unpackedFileBytes": unpacked, "metadataRangeBytes": len(data),
            "headers": headers, "sha256": None, "sha256Reason": "Microsoft download page and HEAD do not publish a CAB SHA256; calculate after authorized download and verify Microsoft signatures."},
        "qtVersion": "6.11.2", "qtSources": sources,
        "hostEnvironment": {"QT_WEBVIEW_PLUGIN": "webview2", "WEBVIEW2_BROWSER_EXECUTABLE_FOLDER": "<bundle>/webview2/155.0.4283.60", "WEBVIEW2_USER_DATA_FOLDER": "<prepared MXMOD>/business-webview2"},
        "scope": "Child environment before WebView initialization; paths are native launcher owned, never QML supplied; no system registry/browser changes.",
        "sources": ["https://doc.qt.io/qt-6.11/qtwebview-index.html",
            "https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution",
            "https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/user-data-folder",
            "https://learn.microsoft.com/en-us/microsoft-edge/webview2/reference/win32/webview2-idl?view=webview2-1.0.4129.50"],
        "nextSteps": ["Authorize one CAB download; record SHA256 and Microsoft signature verification before use.",
            "Extract once into C:/ValMAXBuild; retain complete distribution and license/notice files; no Evergreen bootstrapper.",
            "Native launcher validates bundle/profile paths, overwrites child-only environment case-insensitively, preflights runtime; use finite unavailable UI on failure.",
            "Review Qt environment/controller callbacks: v6.11.2 source lacks HRESULT/env null guard, so plain QML catch is insufficient for native init failure.",
            "Blank owned fixture only: verify native browser path/version and owned user data directory, bounded child cleanup; no real business URL or login.",
            "Installer ships runtime, keeps user data outside bundle, updates runtime with ValMAX release; Windows 10 AppContainer RX ACL only on bundled runtime per Microsoft guidance."],
        "limitations": ["Not yet an execution/installation proof.", "Packaged runtime is over 250 MB and adds installer/unpacked size.", "Current business tab is one app-wide login, not per-account browser isolation.", "Qt WebView cannot safely overlay normal QML items over native web content."]}
    (PROJECT / "analysis/native-migration-v1/qt-webview2-discovery.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps({"version": version, "url": link, "compressedBytes": expected_size, "unpackedFileBytes": unpacked, "files": count, "runtimeDownloaded": False}))


if __name__ == "__main__":
    main()
