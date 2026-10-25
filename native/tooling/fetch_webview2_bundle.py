"""Download the one reviewed Microsoft CAB, recording exact bytes and SHA256."""
import hashlib
import json
from pathlib import Path
import urllib.request

PROJECT = Path(__file__).resolve().parents[2]
metadata = json.loads((PROJECT / "analysis/native-migration-v1/qt-webview2-discovery.json").read_text())
runtime = metadata["runtime"]
if runtime["version"] != "155.0.4283.60" or runtime["compressedBytes"] != 310497658:
    raise RuntimeError("reviewed_bundle_changed")
root = Path("C:/ValMAXBuild/deps/webview2")
root.mkdir(parents=True, exist_ok=True)
target = root / runtime["url"].rsplit("/", 1)[1]
partial = target.with_suffix(".partial")
if target.exists() or partial.exists(): raise RuntimeError("download_already_present_inspect_before_retry")
digest, count = hashlib.sha256(), 0
with urllib.request.urlopen(runtime["url"], timeout=45) as response, partial.open("xb") as output:
    if response.status != 200 or int(response.headers.get("Content-Length", "0")) != runtime["compressedBytes"]:
        raise RuntimeError("bundle_http_metadata_changed")
    while chunk := response.read(1024 * 1024):
        count += len(chunk)
        if count > runtime["compressedBytes"]: raise RuntimeError("bundle_size_exceeded")
        digest.update(chunk); output.write(chunk)
if count != runtime["compressedBytes"]: raise RuntimeError("bundle_incomplete")
partial.rename(target)
result = {"schema": 1, "version": runtime["version"], "url": runtime["url"],
    "cab": str(target), "size": count, "sha256": digest.hexdigest(),
    "source": metadata["downloadPage"], "signatureVerified": False, "extracted": False}
(root / "download.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
