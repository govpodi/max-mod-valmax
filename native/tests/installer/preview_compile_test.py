"""Compile-check explicit local-preview/public-BETA/release gates; never emit or run Setup."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
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
    beta_warning = payload / "native-beta.txt"
    beta_warning_bytes = "ValMAX public BETA\nCompile-only synthetic notice; Setup is not emitted.\n".encode("utf-8-sig")
    beta_warning.write_bytes(beta_warning_bytes)
    beta_material_info = payload / "native-beta-materials.json"
    beta_material_info.write_bytes(b'{"synthetic":true,"wholeClientMaterialsVerified":false}\n')
    ffmpeg = payload / "materials/FFmpeg"
    ffmpeg.mkdir(parents=True)
    source713 = ffmpeg / "ffmpeg-7.1.3.tar.xz"
    source715 = ffmpeg / "ffmpeg-7.1.5.tar.xz"
    signature713 = ffmpeg / "ffmpeg-7.1.3.tar.xz.asc"
    signature715 = ffmpeg / "ffmpeg-7.1.5.tar.xz.asc"
    configuration = ffmpeg / "build-configurations.json"
    source713.write_bytes(b"owned inert FFmpeg 7.1.3 source compile fixture")
    source715_bytes = b"owned inert FFmpeg 7.1.5 source compile fixture"
    source715.write_bytes(source715_bytes)
    signature713.write_bytes(b"owned inert FFmpeg 7.1.3 signature compile fixture")
    signature715_bytes = b"owned inert FFmpeg 7.1.5 signature compile fixture"
    signature715.write_bytes(signature715_bytes)
    configuration.write_bytes(b'{"synthetic":true,"exactCorrespondingSourceProven":false}\n')
    manifest_sha, warning_sha = sha(payload / "native-package.json"), sha(warning)
    beta_warning_sha = sha(beta_warning)
    beta_material_sha, source713_sha, source715_sha, signature713_sha, signature715_sha, config_sha = (
        sha(beta_material_info), sha(source713), sha(source715),
        sha(signature713), sha(signature715), sha(configuration))
    external_payload = build / "payload-external-ffmpeg"
    (external_payload / "runtime/transfer").mkdir(parents=True)
    for name in ("ValMAX.exe", "native-package.json", "native-beta.txt", "native-beta-materials.json"):
        shutil.copyfile(payload / name, external_payload / name)
    shutil.copyfile(payload / "runtime/transfer/valmax-transfer.exe",
                    external_payload / "runtime/transfer/valmax-transfer.exe")
    external_notice = external_payload / "FFMPEG-SOURCE-AVAILABILITY.txt"
    external_notice_bytes = b"Owned synthetic external FFmpeg source availability fixture\n"
    external_notice.write_bytes(external_notice_bytes)
    external_asset = build / "source-assets" / "synthetic-ffmpeg-sources.zip"
    external_asset.parent.mkdir()
    external_asset.write_bytes(b"owned inert FFmpeg source ZIP compile fixture")
    external_asset_sha, external_notice_sha = sha(external_asset), sha(external_notice)
    source_paths = (TEMPLATE, ROOT / "native/installer/managed-upgrade.iss",
                    ROOT / "native/tooling/package_native_client.py", Path(__file__))
    sources = {p.relative_to(ROOT).as_posix(): sha(p) for p in source_paths}
    # (mode, eligible, blocker count, manifest hash, info hash, marker version, compile version[, BETA core])
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
        ("beta-missing-info-pin", ("public-beta", 1, 0, manifest_sha, None, VERSION, VERSION, 1), False),
        ("beta-missing-version-pin", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, None, VERSION, 1), False),
        ("beta-missing-core-pin", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION), False),
        ("beta-failed-core", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 0), False),
        ("beta-ineligible", ("public-beta", 0, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-unresolved-blocker", ("public-beta", 1, 1, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-wrong-info", ("public-beta", 1, 0, manifest_sha, "0" * 64, VERSION, VERSION, 1), False),
        ("beta-missing-notice", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-missing-materials-pin", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-wrong-materials", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-missing-source715-pin", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-missing-source715", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-wrong-source713", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-missing-signature713-pin", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-missing-signature715", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-wrong-signature713", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-wrong-signature715", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-wrong-config", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-wrong-manifest", ("public-beta", 1, 0, "0" * 64, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-unmarked-version", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, "8.9.0", "8.9.0", 1), False),
        ("beta-mismatched-version", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, "8.9.0-native.preview.2", 1), False),
        ("public-beta-compile-only", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), True),
        ("beta-external-missing-asset-arg", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-external-missing-asset", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-external-wrong-asset", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-external-missing-notice", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-external-reintroduced-materials", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("beta-external-embedded-asset", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), False),
        ("public-beta-external-compile-only", ("public-beta", 1, 0, manifest_sha, beta_warning_sha, VERSION, VERSION, 1), True),
    ]
    beta_errors = {
        "beta-missing-info-pin": "Public BETA information must be pinned",
        "beta-missing-version-pin": "A public BETA version is required",
        "beta-missing-core-pin": "Public BETA installed core smoke must be pinned",
        "beta-failed-core": "A blocked or untested public BETA cannot become a user installer",
        "beta-ineligible": "A blocked or untested public BETA cannot become a user installer",
        "beta-unresolved-blocker": "A blocked or untested public BETA cannot become a user installer",
        "beta-wrong-info": "Public BETA information belongs to another package",
        "beta-missing-notice": "Public BETA information is missing",
        "beta-missing-materials-pin": "Public BETA materials summary must be pinned",
        "beta-wrong-materials": "Public BETA materials summary belongs to another package",
        "beta-missing-source715-pin": "FFmpeg 7.1.5 source must be pinned",
        "beta-missing-source715": "Both versioned FFmpeg source archives are required",
        "beta-wrong-source713": "FFmpeg 7.1.3 source belongs to another package",
        "beta-missing-signature713-pin": "FFmpeg 7.1.3 signature must be pinned",
        "beta-missing-signature715": "Both versioned FFmpeg source signatures are required",
        "beta-wrong-signature713": "FFmpeg 7.1.3 signature belongs to another package",
        "beta-wrong-signature715": "FFmpeg 7.1.5 signature belongs to another package",
        "beta-wrong-config": "FFmpeg build configurations belong to another package",
        "beta-wrong-manifest": "Native eligibility include belongs to another package manifest",
        "beta-unmarked-version": "The public BETA product version must be visibly marked",
        "beta-mismatched-version": "The public BETA product version must be visibly marked",
        "beta-external-missing-asset-arg": "Separate FFmpeg source ZIP path must be supplied",
        "beta-external-missing-asset": "Separate FFmpeg source ZIP is missing",
        "beta-external-wrong-asset": "Separate FFmpeg source ZIP differs from the package receipt",
        "beta-external-missing-notice": "Separate FFmpeg source availability notice is missing",
        "beta-external-reintroduced-materials": "External FFmpeg materials directory must not remain in Setup",
        "beta-external-embedded-asset": "External source assets directory must not be inside Setup",
    }
    results = []
    environment = os.environ.copy()
    environment.update(TEMP="E:/ValMAXBuild/tmp", TMP="E:/ValMAXBuild/tmp")
    for label, values, accepted in cases:
        external = label.startswith("beta-external-") or label == "public-beta-external-compile-only"
        selected_payload = external_payload if external else payload
        stamp = selected_payload / "native-release-state.iss"
        if values is None:
            if stamp.exists():
                stamp.unlink()  # Only this harness's generated file in its fresh build.
            version = VERSION
        else:
            mode, eligible, blockers, digest, info, marked, version = values[:7]
            beta_core = values[7] if len(values) == 8 else None
            text = ('#define NativeDistributionMode "' + mode + '"\n'
                    '#define NativeWebView2Mode "fixed"\n'
                    '#define NativeQtSourceMode "inline"\n'
                    '#define NativeFFmpegSourceMode "' + ("separate-asset" if external else "inline") + '"\n'
                    '#define NativeReleaseEligible ' + str(eligible) + '\n'
                    '#define NativeReleaseBlockerCount ' + str(blockers) + '\n'
                    '#define NativePackageManifestSha256 "' + digest + '"\n')
            if mode == "public-beta":
                if info is not None:
                    text += '#define NativeBetaInfoSha256 "' + info + '"\n'
                if marked is not None:
                    text += '#define NativeBetaVersion "' + marked + '"\n'
                if beta_core is not None:
                    text += '#define NativeBetaCoreTested ' + str(beta_core) + '\n'
                if label != "beta-missing-materials-pin":
                    value = "0" * 64 if label == "beta-wrong-materials" else beta_material_sha
                    text += '#define NativeBetaMaterialsInfoSha256 "' + value + '"\n'
                text += '#define NativeBetaSource713Sha256 "' + (
                    "0" * 64 if label == "beta-wrong-source713" else source713_sha) + '"\n'
                if label != "beta-missing-source715-pin":
                    text += '#define NativeBetaSource715Sha256 "' + source715_sha + '"\n'
                if label != "beta-missing-signature713-pin":
                    text += '#define NativeBetaSource713SignatureSha256 "' + (
                        "0" * 64 if label == "beta-wrong-signature713" else signature713_sha) + '"\n'
                text += '#define NativeBetaSource715SignatureSha256 "' + (
                    "0" * 64 if label == "beta-wrong-signature715" else signature715_sha) + '"\n'
                text += '#define NativeBetaBuildConfigSha256 "' + (
                    "0" * 64 if label == "beta-wrong-config" else config_sha) + '"\n'
                if external:
                    text += '#define NativeFFmpegSourceAssetSha256 "' + (
                        "0" * 64 if label == "beta-external-wrong-asset" else external_asset_sha) + '"\n'
                    text += '#define NativeFFmpegSourceInfoSha256 "' + external_notice_sha + '"\n'
            else:
                if info is not None:
                    text += '#define NativePreviewInfoSha256 "' + info + '"\n'
                if marked is not None:
                    text += '#define NativePreviewVersion "' + marked + '"\n'
            stamp.write_text(text, encoding="utf8")
        command = [str(ISCC), "/Qp", "/O-", "/DPayload=" + str(selected_payload), "/DOutput=" + str(output),
                   "/DProductVersion=" + version, str(TEMPLATE)]
        if external and label != "beta-external-missing-asset-arg":
            asset_path = (build / "source-assets" / "missing-ffmpeg.zip"
                          if label == "beta-external-missing-asset" else external_asset)
            command.insert(-1, "/DFFmpegSourceAsset=" + str(asset_path))
        if label == "beta-missing-notice":
            beta_warning.unlink()
        if label == "beta-missing-source715":
            source715.unlink()
        if label == "beta-missing-signature715":
            signature715.unlink()
        if label == "beta-external-missing-notice":
            external_notice.unlink()
        injected = external_payload / "materials/FFmpeg/stray.asc"
        if label == "beta-external-reintroduced-materials":
            injected.parent.mkdir(parents=True)
            injected.write_bytes(b"owned synthetic late FFmpeg source file")
        injected_asset = external_payload / "source-assets/stray.zip"
        if label == "beta-external-embedded-asset":
            injected_asset.parent.mkdir()
            injected_asset.write_bytes(b"owned synthetic late source asset")
        result = subprocess.run(command, env=environment, capture_output=True, timeout=60,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if label == "beta-missing-notice":
            beta_warning.write_bytes(beta_warning_bytes)
        if label == "beta-missing-source715":
            source715.write_bytes(source715_bytes)
        if label == "beta-missing-signature715":
            signature715.write_bytes(signature715_bytes)
        if label == "beta-external-missing-notice":
            external_notice.write_bytes(external_notice_bytes)
        if label == "beta-external-reintroduced-materials":
            injected.unlink()
            injected.parent.rmdir()
        if label == "beta-external-embedded-asset":
            injected_asset.unlink()
            injected_asset.parent.rmdir()
        log = output / (label + ".log")
        messages = result.stdout + result.stderr
        log.write_bytes(messages)
        if (result.returncode == 0) != accepted:
            raise AssertionError("unexpected compile gate result: " + label + "; inspect " + str(log))
        if label in beta_errors and beta_errors[label] not in messages.decode("utf8", errors="replace"):
            raise AssertionError("wrong BETA compile gate rejection: " + label + "; inspect " + str(log))
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
