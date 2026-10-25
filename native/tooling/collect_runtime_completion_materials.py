"""Fetch corresponding official Qt sources/notices for runtime completion.

Full source archives remain intact. Only their top-level license texts are
extracted; no source or browser executable is run, and SDK/old packages are not
modified. All acquisition/extraction data is in a new owned D directory.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile

from fetch_qt_runtime_completion import acquire, require, retain, sha, DEST

ROOT = Path(__file__).resolve().parents[2]
SOURCE_BASE = "https://download.qt.io/archive/qt/6.11/6.11.2/submodules/"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary-proof", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve() and not args.report.exists(), "new materials report required")
    binary = json.loads(args.binary_proof.read_text("utf8"))
    require(binary.get("verified") is True and binary.get("downloadOnly") is False, "extracted exact Qt binary proof required")
    output = DEST / "materials"
    output.mkdir(exist_ok=True)
    records = []
    for item in binary["archives"]:
        module = item["module"]
        require(module in {"qtserialport", "qtwebengine"}, "unsupported module")
        archive = acquire(SOURCE_BASE + module + "-everywhere-src-6.11.2.tar.xz", DEST / "downloads")
        top = module + "-everywhere-src-6.11.2"
        licenses = []
        with tarfile.open(archive["path"], "r:xz") as source:
            for member in source:
                p = PurePosixPath(member.name)
                if len(p.parts) < 2 or p.parts[0] != top:
                    continue
                selected = p.parts[1] == "LICENSES" or (len(p.parts) == 2 and p.name.lower().startswith(("license", "copying", "notice")))
                if not selected or member.isdir():
                    continue
                require(member.isfile() and not member.issym() and member.size <= 2 << 20
                        and ".." not in p.parts and ":" not in member.name and "\\" not in member.name,
                        "unsafe source license member")
                data = source.extractfile(member).read()
                data.decode("utf8")
                relative = PurePosixPath(module, *p.parts[1:]).as_posix()
                retain(output / relative, data)
                licenses.append({"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "archiveMember": member.name})
        require(licenses, "source archive has no top-level license texts")
        records.append({"module": module, "sourceArchive": archive, "licenseFiles": licenses,
                        "binaryArchiveSha256": item["sha256"], "licenseOrigin": "Exact original top-level texts from the complete official corresponding-source archive."})
    result = {"schema": 1, "verified": True, "qtVersion": "6.11.2", "binaryProof": str(args.binary_proof),
              "binaryProofSha256": sha(args.binary_proof), "materialsRoot": str(output), "modules": records,
              "targetsExecuted": False, "toolSha256": sha(Path(__file__)),
              "limitations": ["This proof covers only the added QtSerialPort/QtWebEngine binary modules and their exact source archives, not all MAX third-party distribution obligations."]}
    with args.report.open("x", encoding="utf8") as target: target.write(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"verified": True, "modules": len(records), "report": str(args.report)}))


if __name__ == "__main__": main()
