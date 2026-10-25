"""Fetch the pinned C-only Gum devkit, never a server or JavaScript runtime."""
from pathlib import Path
import hashlib
import json
import tarfile
import urllib.request

VERSION = "17.23.3"
NAME = f"frida-gum-devkit-{VERSION}-windows-x86_64.tar.xz"
URL = f"https://github.com/frida/frida/releases/download/{VERSION}/{NAME}"
# GitHub release asset digest, checked from the official release API.
SHA256 = "6e5a5f5bf5d40f5c8277cfbe4ca7e8876f9de4f7ccee1937ee63d6a67bfcfd5a"
SIZE = 8443400

def main():
    downloads = Path("C:/ValMAXBuild/downloads")
    target = Path("C:/ValMAXBuild/gum") / VERSION
    downloads.mkdir(parents=True, exist_ok=True)
    archive = downloads / NAME
    if not archive.exists():
        request = urllib.request.Request(URL, headers={"User-Agent": "ValMAX-native-build"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("xb") as out:
            while part := response.read(1024 * 1024):
                out.write(part)
    if archive.stat().st_size != SIZE or hashlib.sha256(archive.read_bytes()).hexdigest() != SHA256:
        raise RuntimeError("Gum devkit checksum mismatch")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as source:
        source.extractall(target, filter="data")
    files = [{"path": p.relative_to(target).as_posix(), "size": p.stat().st_size,
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(target.rglob("*")) if p.is_file()]
    proof = {"schema": 1, "version": VERSION, "source": URL,
             "archiveSha256": SHA256, "directory": str(target), "files": files,
             "scope": "C-only build dependency; runtime integration and license packaging separate"}
    path = Path(__file__).resolve().parents[2] / "analysis/native-migration-v1/gum-sdk.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf8")
    print(json.dumps({"directory": str(target), "files": len(files), "verified": True}))

if __name__ == "__main__":
    main()
