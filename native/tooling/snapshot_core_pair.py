"""Read-only PE/export validation, then stable snapshot of a proven Rust pair."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import pefile

ROOT = Path(__file__).resolve().parents[2]
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def require(value, message):
    if not value: raise RuntimeError(message)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--proof", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    proof = json.loads(args.proof.read_text(encoding="utf8"))
    require(proof.get("ok") is True and proof.get("failed") == 0 and proof.get("passed", 0) > 0, "core_proof_not_passed")
    if args.reuse:
        saved = json.loads((args.output/"core-pair-proof.json").read_text(encoding="utf8"))
        require(saved["verified"] and saved["sourceSnapshotMatches"] and saved["importLibrarySymbolsMatch"], "unverified_snapshot")
        require(saved["sourceProofSha256"] == sha(args.proof), "snapshot_proof_changed")
        require(saved["hashes"]["dll"] == proof["dll"]["sha256"] == sha(args.output/"valmax_core.dll"), "snapshot_dll_changed")
        require(saved["hashes"]["importLibrary"] == sha(args.output/"valmax_core.dll.lib"), "snapshot_library_changed")
        if "importLibrary" in proof:
            require(saved["hashes"]["importLibrary"] == proof["importLibrary"]["sha256"], "snapshot_library_differs_from_proof")
        print(json.dumps(saved)); return
    require(not args.output.exists() or (args.output.is_dir() and not any(args.output.iterdir())), "snapshot_directory_not_empty_use_reuse")
    source = Path(proof["dll"]["path"]).resolve(strict=True)
    require(source.name == "valmax_core.dll", "unexpected_core_name")
    library = source.with_name("valmax_core.dll.lib")
    before = {"dll": sha(source), "importLibrary": sha(library)}
    require(before["dll"] == proof["dll"]["sha256"], "core_dll_differs_from_proof")
    if "importLibrary" in proof:
        require(Path(proof["importLibrary"]["path"]).resolve(strict=True) == library, "core_import_library_path_differs_from_proof")
        require(before["importLibrary"] == proof["importLibrary"]["sha256"], "core_import_library_differs_from_proof")
    for name, expected in proof["sourceSnapshotAtReportTime"].items():
        require(sha(ROOT/name) == expected, "core_source_differs_from_proof:"+name)
    pe = pefile.PE(str(source))
    require(pe.FILE_HEADER.Machine == 0x8664 and pe.OPTIONAL_HEADER.Magic == 0x20b, "core_not_x64")
    exports = sorted(item.name.decode("ascii") for item in pe.DIRECTORY_ENTRY_EXPORT.symbols if item.name)
    require(exports == sorted(proof["dll"]["exports"]), "core_exports_differ_from_proof")
    dumpbin = "C:/ValMAXBuild/msvc/VC/Tools/MSVC/14.44.35207/bin/Hostx64/x64/dumpbin.exe"
    result = subprocess.run([dumpbin, "/nologo", "/linkermember:1", str(library)], capture_output=True, text=True, timeout=15)
    require(result.returncode == 0, "core_import_library_unreadable")
    names = re.findall(r"(?m)^\s+[0-9A-Fa-f]+\s+((?:__imp_)?valmax_[A-Za-z0-9_]+)\s*$", result.stdout)
    direct = sorted(name for name in names if not name.startswith("__imp_"))
    indirect = sorted(name[6:] for name in names if name.startswith("__imp_"))
    require(direct == exports and indirect == exports, "core_import_symbols_do_not_match_exports")
    require(b"valmax_core.dll\0" in library.read_bytes(), "core_import_library_wrong_dll")
    args.output.mkdir(parents=True, exist_ok=True)
    for path in (source, library): shutil.copy2(path, args.output/path.name)
    after = {"dll": sha(source), "importLibrary": sha(library)}
    require(before == after, "core_changed_during_snapshot")
    require(sha(args.output/source.name) == before["dll"] and sha(args.output/library.name) == before["importLibrary"], "core_copy_mismatch")
    result = {"schema":1,"verified":True,"sourceProof":str(args.proof.resolve()),"sourceProofSha256":sha(args.proof),
        "sourceDll":str(source),"sourceImportLibrary":str(library),"snapshot":str(args.output.resolve()),
        "hashes":before,"exports":exports,"sourceSnapshotMatches":True,"importLibrarySymbolsMatch":True}
    (args.output/"core-pair-proof.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps(result))
if __name__ == "__main__": main()
