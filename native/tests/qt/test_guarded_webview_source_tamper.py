"""Reject tampered Qt plugin source and metadata before source packaging.

This uses copies under an explicit scratch root; it never edits generated build
inputs, a verified DLL, or an installed browser profile.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
PACKAGER = ROOT / "native/tooling/package_webview_sources.py"
SOURCE_NAMES = ("qwebview2webview.cpp", "qwebview2webview_p.h",
                "qwebview2webviewplugin.cpp", "windows.json", "user_data_preflight.h")


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("generated", "guard-source-proof", "guarded-build-proof",
                 "webview-proof", "guarded-plugin", "upstream", "sdk", "scratch-root", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    scratch = args.scratch_root.resolve(strict=True)
    generated = args.generated.resolve(strict=True)
    original_guard = json.loads(args.guard_source_proof.read_text("utf8"))
    if set(original_guard.get("outputs", {})) != set(SOURCE_NAMES):
        raise RuntimeError("five guarded source hashes required")
    if {name: sha(generated / name) for name in SOURCE_NAMES} != original_guard["outputs"]:
        raise RuntimeError("original guarded source changed")
    results = []
    with tempfile.TemporaryDirectory(prefix="qt-webview-source-tamper-", dir=scratch) as temporary:
        temporary = Path(temporary)
        for name in ("qwebview2webviewplugin.cpp", "windows.json"):
            case = temporary / name.replace(".", "-")
            copy = case / "generated"
            copy.mkdir(parents=True)
            for filename in SOURCE_NAMES:
                shutil.copy2(generated / filename, copy / filename)
            with (copy / name).open("ab") as stream:
                stream.write(b"\n")
            guard = dict(original_guard)
            guard["output"] = str(copy)
            guard_path = case / "guard-proof.json"
            guard_path.write_text(json.dumps(guard), encoding="utf8")
            output = case / "source-output"
            proof = case / "source-proof.json"
            command = [sys.executable, str(PACKAGER),
                       "--upstream", str(args.upstream), "--generated", str(copy),
                       "--sdk", str(args.sdk), "--output", str(output), "--proof", str(proof),
                       "--webview2-mode", "evergreen", "--guard-source-proof", str(guard_path),
                       "--guarded-build-proof", str(args.guarded_build_proof),
                       "--webview-proof", str(args.webview_proof),
                       "--guarded-plugin", str(args.guarded_plugin)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
            expected = "generated WebView source differs from guard proof: " + name
            rejected = (completed.returncode != 0 and expected in completed.stderr
                        and not output.exists() and not proof.exists())
            results.append({"case": name, "rejectedBeforeOutput": rejected,
                            "exitCode": completed.returncode})
            if not rejected:
                raise RuntimeError("tampered Qt source was not rejected: " + name
                                   + " / " + (completed.stdout + completed.stderr)[-600:])
    receipt = {"schema": 1, "verified": len(results) == 2 and all(
        item["rejectedBeforeOutput"] for item in results),
        "cases": results, "guardSourceProofSha256": sha(args.guard_source_proof),
        "guardedBuildProofSha256": sha(args.guarded_build_proof),
        "webviewProofSha256": sha(args.webview_proof),
        "packagerSha256": sha(PACKAGER), "originalInputsModified": False}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x", encoding="utf8") as output:
        json.dump(receipt, output, indent=2)
        output.write("\n")
    print(json.dumps({"verified": receipt["verified"], "cases": len(results),
                      "report": str(args.report)}))


if __name__ == "__main__":
    main()
