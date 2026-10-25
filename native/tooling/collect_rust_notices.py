"""Build-time crate license inventory; never part of the installed runtime.

Collects only files from resolved Windows Cargo packages, with SHA-256 receipts.
Does not decide legal compliance or replace the separate MAX/Qt/Gum inventory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PREFIXES = ("license", "licence", "copying", "notice", "copyright")
LIMIT = 1024 * 1024


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect_files(package: dict) -> list[tuple[str, bytes]]:
    root = Path(package["manifest_path"]).resolve(strict=True).parent
    candidates = set()
    explicit = package.get("license_file")
    if explicit:
        path = (root / explicit).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("License file escapes package")
        candidates.add(path)
    for entry in root.iterdir():
        if entry.name.lower().startswith(PREFIXES) and entry.is_file():
            candidates.add(entry)
        if entry.is_dir() and entry.name.lower() in ("license", "licenses", "licence", "licences"):
            candidates.update(child for child in entry.iterdir() if child.is_file())
    result = []
    for path in sorted(candidates):
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved.is_symlink():
            raise ValueError("Unsafe license file")
        data = resolved.read_bytes()
        if len(data) > LIMIT:
            raise ValueError("Unbounded license file")
        data.decode("utf-8")  # The notice bundle is readable text, not a binary dump.
        result.append((path.relative_to(root).as_posix(), data))
    return result


def write_owned(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A rerun is idempotent. Never silently overwrite another build's evidence.
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError("Existing notice differs; choose a new output directory")
        return
    with path.open("xb") as stream:
        stream.write(data)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    output = arguments.output.resolve(strict=False)
    if not output.is_relative_to(Path("C:/ValMAXBuild").resolve(strict=True)):
        raise ValueError("New build notices belong under C:/ValMAXBuild")
    for parent in [output, *output.parents]:
        if parent.exists() and (parent.is_symlink() or parent.is_junction()):
            raise ValueError("Notice output cannot use reparse paths")
    completed = subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--offline",
         "--filter-platform", "x86_64-pc-windows-msvc"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    metadata = json.loads(completed.stdout)
    resolved = {node["id"] for node in metadata["resolve"]["nodes"]}
    rows = []
    for package in sorted(metadata["packages"], key=lambda value: (value["name"], value["version"])):
        if package["id"] not in resolved or package["source"] is None:
            continue
        name, version = package["name"], package["version"]
        # Cargo registry names/versions still cannot create arbitrary paths.
        if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-" for c in name + version):
            raise ValueError("Unexpected Cargo package identity")
        files = []
        for relative, data in collect_files(package):
            destination = Path("crates") / f"{name}-{version}" / relative
            write_owned(output / destination, data)
            files.append({"path": destination.as_posix(), "bytes": len(data), "sha256": digest(data)})
        rows.append({"name": name, "version": version, "licenseExpression": package.get("license"),
                     "repository": package.get("repository"), "source": package["source"],
                     "licenseFiles": files,
                     "hasBuildScript": any("custom-build" in target["kind"] for target in package["targets"]),
                     "hasProcMacro": any("proc-macro" in target["kind"] for target in package["targets"])})
    missing = [f"{row['name']} {row['version']}" for row in rows if not row["licenseFiles"]]
    manifest = {"schema": 1, "target": "x86_64-pc-windows-msvc",
                "cargoLockSha256": digest((ROOT / "Cargo.lock").read_bytes()),
                "collectorSha256": digest(Path(__file__).read_bytes()), "packages": rows,
                "missingLicenseFiles": missing,
                "scope": "Resolved Rust crates including build/proc-macro dependencies; not a runtime DLL inventory.",
                "limitations": ["MAX, Qt, Gum and compiler redistributables require their own notices.",
                                "Transitive native sources inside crates need separate review.",
                                "This collection does not itself establish license compliance."]}
    write_owned(output / "rust-crates.json", (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode())
    lines = ["ValMAX — Rust dependency notices", "", manifest["scope"], ""]
    for row in rows:
        lines.extend([f"{row['name']} {row['version']}", f"License: {row['licenseExpression'] or 'see bundled files'}"])
        lines.extend(f"  {item['path']}" for item in row["licenseFiles"])
        lines.append("")
    write_owned(output / "ThirdParty-Rust.txt", ("\n".join(lines) + "\n").encode())
    # Verify every copied artifact before reporting success.
    for row in rows:
        for item in row["licenseFiles"]:
            data = (output / item["path"]).read_bytes()
            if len(data) != item["bytes"] or digest(data) != item["sha256"]:
                raise ValueError("Notice verification failed")
    print(json.dumps({"packages": len(rows), "licenseFiles": sum(len(row["licenseFiles"]) for row in rows),
                      "missingLicenseFiles": missing, "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
