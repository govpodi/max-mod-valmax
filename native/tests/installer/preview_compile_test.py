"""Compile-check explicit local-preview/release gates; never emit or run Setup."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[3]
ISCC = Path("D:/CodexLabs/max-mod-26.35.1/tools/InnoSetup/ISCC.exe")
TEMPLATE = ROOT / "native/installer/valmax-native.iss"
VERSION = "8.9.0-native.preview.1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    if args.report.resolve().parent != (ROOT / "analysis/native-migration-v1").resolve() or args.report.exists():
        raise ValueError("new report required under analysis/native-migration-v1")
    build = Path("E:/ValMAXBuild/build") / ("native-preview-compile-" + uuid.uuid4().hex)
    payload, output = build / "payload", build / "compile-only"
    (payload / "runtime/transfer").mkdir(parents=True)
    output.mkdir()
    (payload / "ValMAX.exe").write_bytes(b"owned compile-only inert launcher")
    (payload / "runtime/transfer/valmax-transfer.exe").write_bytes(b"owned compile-only inert helper")
    (payload / "native-package.json").write_text(json.dumps({"synthetic": True, "version": VERSION}))
    warning = payload / "native-preview.txt"
    warning.write_bytes("ValMAX native preview\nЛокальная проверка компиляции; Setup не создаётся.\n".encode("utf-8-sig"))
    manifest_sha, warning_sha = sha(payload / "native-package.json"), sha(warning)
    source_paths = (TEMPLATE, ROOT / "native/installer/managed-upgrade.iss",
                    ROOT / "native/tooling/package_native_client.py", Path(__file__))
    sources = {p.relative_to(ROOT).as_posix(): sha(p) for p in source_paths}
    # (mode, eligible, blocker count, manifest hash, info hash, marker version, compile version)
    cases = [
        ("missing-state", None, False),
        ("developer-staging", ("developer-staging", 0, 5, manifest_sha, None, None, VERSION), False),
        ("blocked-release", ("user-release", 0, 5, manifest_sha, None, None, VERSION), False),
        ("complete-release-compile-only", ("user-release", 1, 0, manifest_sha, None, None, "0.0.0-compile-only"), True),
        ("release-wrong-manifest", ("user-release", 1, 0, "0" * 64, None, None, VERSION), False),
        ("preview-missing-info-pin", ("local-preview", 0, 7, manifest_sha, None, VERSION, VERSION), False),
        ("preview-missing-version-pin", ("local-preview", 0, 7, manifest_sha, warning_sha, None, VERSION), False),
        ("preview-wrong-info", ("local-preview", 0, 7, manifest_sha, "0" * 64, VERSION, VERSION), False),
        ("preview-wrong-manifest", ("local-preview", 0, 7, "0" * 64, warning_sha, VERSION, VERSION), False),
        ("preview-false-release-claim", ("local-preview", 1, 7, manifest_sha, warning_sha, VERSION, VERSION), False),
        ("preview-cleared-blockers", ("local-preview", 0, 0, manifest_sha, warning_sha, VERSION, VERSION), False),
        ("preview-too-few-blockers", ("local-preview", 0, 5, manifest_sha, warning_sha, VERSION, VERSION), False),
        ("preview-too-many-blockers", ("local-preview", 0, 8, manifest_sha, warning_sha, VERSION, VERSION), False),
        ("preview-unmarked-version", ("local-preview", 0, 7, manifest_sha, warning_sha, "8.9.0", "8.9.0"), False),
        ("preview-mismatched-version", ("local-preview", 0, 7, manifest_sha, warning_sha, VERSION, "8.9.0-native.preview.2"), False),
        ("local-preview-compile-only", ("local-preview", 0, 7, manifest_sha, warning_sha, VERSION, VERSION), True),
        ("local-preview-with-materials-ready", ("local-preview", 0, 6, manifest_sha, warning_sha, VERSION, VERSION), True),
    ]
    results = []
    environment = os.environ.copy()
    environment.update(TEMP="E:/ValMAXBuild/tmp", TMP="E:/ValMAXBuild/tmp")
    stamp = payload / "native-release-state.iss"
    for label, values, accepted in cases:
        if values is None:
            if stamp.exists():
                stamp.unlink()  # Only this harness's generated file in its fresh build.
            version = VERSION
        else:
            mode, eligible, blockers, digest, info, marked, version = values
            text = ('#define NativeDistributionMode "' + mode + '"\n'
                    '#define NativeReleaseEligible ' + str(eligible) + '\n'
                    '#define NativeReleaseBlockerCount ' + str(blockers) + '\n'
                    '#define NativePackageManifestSha256 "' + digest + '"\n')
            if info is not None:
                text += '#define NativePreviewInfoSha256 "' + info + '"\n'
            if marked is not None:
                text += '#define NativePreviewVersion "' + marked + '"\n'
            stamp.write_text(text, encoding="utf8")
        command = [str(ISCC), "/Qp", "/O-", "/DPayload=" + str(payload), "/DOutput=" + str(output),
                   "/DProductVersion=" + version, str(TEMPLATE)]
        result = subprocess.run(command, env=environment, capture_output=True, timeout=60,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        log = output / (label + ".log")
        log.write_bytes(result.stdout + result.stderr)
        if (result.returncode == 0) != accepted:
            raise AssertionError("unexpected compile gate result: " + label + "; inspect " + str(log))
        results.append({"case": label, "accepted": accepted, "exitCode": result.returncode,
                        "log": str(log), "logSha256": sha(log)})
    if list(output.glob("*.exe")):
        raise AssertionError("compile-only probe unexpectedly emitted an executable")
    if sources != {p.relative_to(ROOT).as_posix(): sha(p) for p in source_paths}:
        raise AssertionError("source changed during compile gate checks")
    proof = {"schema": 1, "verified": True, "checkedAt": datetime.now(timezone.utc).isoformat(),
             "checks": len(results), "build": str(build), "sourceHashes": sources,
             "compiler": {"path": str(ISCC), "sha256": sha(ISCC)}, "results": results,
             "setupEmitted": False, "installerExecuted": False, "installedClientChanged": False,
             "profilesOrCredentialsAccessed": False,
             "scope": "Actual Inno /O- compile admission checks on an inert owned fixture. No installed product or functional readiness claim."}
    with args.report.open("x", encoding="utf8") as stream:
        json.dump(proof, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"verified": True, "checks": len(results), "report": str(args.report)}))


if __name__ == "__main__":
    main()
