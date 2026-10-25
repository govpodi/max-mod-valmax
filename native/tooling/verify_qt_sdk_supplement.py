"""Offline re-extract two release dependencies from the verified official SDK.

No SDK/old proof is changed; targeted extraction goes to a new D directory.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import hashlib
import py7zr
import pefile

from fetch_qt_runtime_completion import require, sha

ROOT = Path(__file__).resolve().parents[2]
DEST = Path("D:/ValMAXBuild/deps/qt-runtime-completion-6.11.2-20261021/sdk-supplement")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk-proof", type=Path, default=ROOT / "analysis/native-migration-v1/qt-sdk.json")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    require(args.report.parent.resolve() == (ROOT / "analysis/native-migration-v1").resolve() and not args.report.exists(), "new report required")
    require(not DEST.exists(), "new SDK supplement directory required")
    sdk = json.loads(args.sdk_proof.read_text("utf8"))
    require(sdk["version"] == "6.11.2" and sdk["architecture"] == "msvc2022_64", "exact SDK required")
    DEST.mkdir(parents=True)
    rows = []
    for module, name in (("qtbase", "Qt6PrintSupport.dll"), ("qtdeclarative", "Qt6QuickWidgets.dll")):
        archives = [r for r in sdk["archives"] if (module + "-Windows-") in r["url"]]
        require(len(archives) == 1, "one exact SDK archive required")
        record = archives[0]
        require(record["url"].startswith("https://download.qt.io/"), "official SDK provenance required")
        archive_path = Path("C:/ValMAXBuild/downloads/qt") / record["url"].rsplit("/", 1)[1]
        require(sha(archive_path) == record["sha256"] and archive_path.stat().st_size == record["bytes"], "frozen SDK archive changed")
        target = DEST / module
        target.mkdir()
        with py7zr.SevenZipFile(archive_path) as archive:
            names = archive.getnames()
            require(names.count("bin/" + name) == 1, "unique release binary missing")
            archive.extract(target, targets=["bin/" + name])
        source = target / "bin" / name
        require(sha(source) == sha(Path(sdk["root"]) / "bin" / name), "SDK extracted byte identity failed")
        image = pefile.PE(str(source), fast_load=True)
        try:
            image.parse_data_directories(directories=[2])
            version = image.VS_FIXEDFILEINFO[0]
            numbers = [version.FileVersionMS >> 16, version.FileVersionMS & 65535, version.FileVersionLS >> 16, version.FileVersionLS & 65535]
            require(numbers == [6, 11, 2, 0] and image.FILE_HEADER.Machine == 0x8664 and not version.FileFlags & 1, "wrong SDK dependency version/architecture/debug flag")
        finally:
            image.close()
        rows.append({"path": str(source), "destination": name, "bytes": source.stat().st_size, "sha256": sha(source),
                     "version": numbers, "machine": "x64", "debug": False, "module": module,
                     "archive": str(archive_path), "archiveSha256": record["sha256"], "archiveUrl": record["url"],
                     "archiveMember": "bin/" + name, "sdkByteIdentityVerified": True})
    proof = {"schema": 1, "verified": True, "qtVersion": "6.11.2", "architecture": "msvc2022_64",
             "sdkProof": str(args.sdk_proof), "sdkProofSha256": sha(args.sdk_proof), "files": rows,
             "materialsModules": ["qtbase", "qtdeclarative"], "targetsExecuted": False, "sourceSha256": sha(Path(__file__))}
    with args.report.open("x", encoding="utf8") as output: output.write(json.dumps(proof, indent=2) + "\n")
    print(json.dumps({"verified": True, "files": len(rows), "report": str(args.report)}))


if __name__ == "__main__": main()
