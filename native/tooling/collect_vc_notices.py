"""Collect Microsoft runtime license materials for the frozen transfer bundle.

Development-time only. Does not install software or accept a license on behalf
of a user. Keeps the original license document and source/archive identities.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from package_transfer_helper import BUILD_ROOT, REPORT_ROOT, below, ordinary, sha, require

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = BUILD_ROOT / "downloads/Microsoft.VC.14.44.17.14.CRT.Redist.X64.base.vsix"
ARCHIVE_SHA = "4aaf54db0bfc9435f7c3660e1a00237a4b556042bfeea64bde44c2e0194e6ee5"
CRT_PREFIX = "Contents/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT/"
LICENSE_URL = "https://visualstudio.microsoft.com/wp-content/uploads/2021/09/Visual-C-Runtime-2015-2022-License-1.docx"
REDIST_URL = "https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution"
HOSTS = {"visualstudio.microsoft.com", "learn.microsoft.com"}
MAX_DOCUMENT = 4 << 20


def official_url(url):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname in HOSTS
            and parsed.port in (None, 443) and not parsed.username and not parsed.password,
            "non-official license URL")


class OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, target):
    official_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "ValMAX-License-Collector/1.0",
                                                 "Accept-Encoding": "identity"})
    with urllib.request.build_opener(OfficialRedirect()).open(request, timeout=45) as response:
        official_url(response.url)
        data = response.read(MAX_DOCUMENT + 1)
        require(len(data) <= MAX_DOCUMENT, "license document exceeds bound")
        with target.open("xb") as stream:
            stream.write(data)
        return {"url": url, "resolvedUrl": response.url, "path": target.name,
                "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def license_text(path):
    with zipfile.ZipFile(path) as archive:
        require(len(archive.infolist()) <= 256, "DOCX entry bound")
        info = archive.getinfo("word/document.xml")
        require(info.file_size <= MAX_DOCUMENT, "DOCX XML bound")
        data = archive.read(info)
    require(b"<!DOCTYPE" not in data.upper() and b"<!ENTITY" not in data.upper(), "unexpected XML declaration")
    root = ET.fromstring(data)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines = ["".join(p.itertext()) for p in root.findall(".//w:p", ns)]
    text = "\n".join(lines).strip() + "\n"
    require("MICROSOFT" in text and "LICENSE" in text and "2022" in text, "unexpected license document")
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-proof", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    proof_path = below(args.package_proof, REPORT_ROOT, "package proof")
    output = below(args.output, BUILD_ROOT / "package-notices", "materials output")
    report = below(args.report, REPORT_ROOT, "report")
    require(not output.exists() and not report.exists(), "output and report must be new")
    proof_sha = sha(proof_path)
    proof = json.loads(proof_path.read_text("utf-8"))
    package = below(Path(proof["package"]), BUILD_ROOT / "packages", "package")
    manifest_path = package / "package-manifest.json"
    require(sha(manifest_path) == proof["manifestSha256"], "package manifest changed")
    manifest = json.loads(manifest_path.read_text("utf-8"))
    selected = [entry for entry in manifest["files"] if entry["origin"]["component"] == "vc-redist"]
    require(0 < len(selected) <= 16, "unexpected VC runtime inventory")
    ordinary(ARCHIVE)
    require(sha(ARCHIVE) == ARCHIVE_SHA, "official pinned VC redist archive changed")
    receipts = json.loads((REPORT_ROOT / "msvc-downloads.json").read_text("utf-8"))
    records = [entry for entry in receipts["records"] if entry["sha256"] == ARCHIVE_SHA]
    require(len(records) == 1 and records[0]["verified"] is True, "missing pinned archive receipt")
    runtime = []
    with zipfile.ZipFile(ARCHIVE) as archive:
        for entry in selected:
            name = entry["path"]
            require(Path(name).name == name and name.lower().endswith(".dll"), "unexpected VC path")
            source_name = CRT_PREFIX + name
            info = archive.getinfo(source_name)
            require(info.file_size <= 16 << 20, "VC member bound")
            source_sha = hashlib.sha256(archive.read(info)).hexdigest()
            ordinary(package / name)
            require(source_sha == entry["sha256"] == sha(package / name), "VC runtime differs from original archive")
            runtime.append({"path": name, "bytes": info.file_size, "sha256": source_sha, "archiveMember": source_name})
    output.mkdir(parents=True)
    downloads = [fetch(LICENSE_URL, output / "Visual-C-Runtime-2015-2022-License.docx"),
                 fetch(REDIST_URL, output / "Visual-Studio-2022-redistribution.html")]
    text = license_text(output / downloads[0]["path"])
    (output / "Visual-C-Runtime-2015-2022-License.txt").write_text(text, encoding="utf-8", newline="\n")
    html = (output / downloads[1]["path"]).read_text("utf-8")
    require("Visual C++ Runtime Files" in html and "VC143" in html, "unexpected redistribution list")
    provenance = {"schema": 1, "component": "Microsoft VC143 release runtime",
                  "archive": {"url": records[0]["url"], "sha256": ARCHIVE_SHA},
                  "runtimeFiles": runtime, "documents": downloads,
                  "notes": ["DLLs are unmodified files from the official VC/Redist release directory.",
                            "The DOCX is the original Microsoft license. The TXT is a convenience extraction of its paragraphs.",
                            "Windows system DLLs and development/debug runtime files are not supplied.",
                            "Materials collection records evidence; it does not certify a distributor's license eligibility."]}
    (output / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    inventory = [{"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path)} for path in sorted(output.iterdir())]
    require(sha(proof_path) == proof_sha and sha(manifest_path) == proof["manifestSha256"], "input changed")
    result = {"schema": 1, "kind": "valmax-vc-materials", "verified": True,
              "collectedAtUtc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "sourcePackageManifestSha256": proof["manifestSha256"],
              "packageProof": {"path": str(proof_path), "sha256": proof_sha},
              "collector": {"path": str(Path(__file__).resolve()), "sha256": sha(__file__)},
              "materials": str(output), "files": inventory, "runtimeFiles": runtime,
              "gaps": [], "distributionReady": False,
              "scope": "VC notice/source-origin materials only; not a full helper distribution approval."}
    with report.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"report": str(report), "materials": str(output), "runtimeFiles": len(runtime), "materialFiles": len(inventory)}))


if __name__ == "__main__":
    main()
