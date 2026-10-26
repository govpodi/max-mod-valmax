"""Post-install publication gate for the exact public-stable-core Setup.

The packager can only use an installed candidate to build Setup. This second
gate runs after installing that final Setup and binds its SHA-256, installed
bytes, BETA-labelled QML proof and final installed-client smoke. It never
starts or modifies the client.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import package_native_client as package


def verify(build_receipt_path: Path, final_smoke_path: Path, qml_proof_path: Path):
    receipt_path = package.evidence_path(str(build_receipt_path))
    package.require(receipt_path.name == "build-receipt.json", "final package receipt required")
    receipt = package.read_json(receipt_path)
    package.require(receipt.get("distributionMode") == "public-stable-core"
                    and receipt.get("releaseEligible") is True
                    and receipt.get("installerCompiled") is True
                    and receipt.get("featureRuntimeTested") is False,
                    "final stable core Setup was not built by the eligible packager")
    payload = package.owned(Path(receipt["payload"]))
    package.require(payload == receipt_path.parent / "payload",
                    "final package payload is outside its build receipt")
    manifest_path = payload / "native-package.json"
    package.ordinary(manifest_path)
    package.require(package.sha(manifest_path) == receipt["manifestSha256"],
                    "final package manifest changed")
    manifest = package.read_json(manifest_path)
    package.require(manifest.get("distributionMode") == "public-stable-core"
                    and manifest.get("releaseEligible") is True
                    and manifest.get("publicStableCore") is True
                    and manifest.get("stableCoreTested") is True
                    and manifest.get("betaUiChannelVerified") is True
                    and manifest.get("featureRuntimeTested") is False
                    and manifest.get("ffmpegSourceMode") == "separate-asset",
                    "final package is not a slim, BETA-labelled stable core")
    package.require_stable_version(manifest.get("version"))
    subject = {"version": manifest["version"],
               "launcherSha256": manifest["launcherSha256"],
               "runtimeInventorySha256": package.inventory_digest(manifest["runtimeFiles"]),
               "qmlProofSha256": manifest["qml"]["proofSha256"]}
    package.validate_subject(subject)
    setup = package.owned(Path(receipt["installer"]))
    package.require(setup.parent == receipt_path.parent / "release"
                    and setup.name == "ValMAX-" + subject["version"] + "-Setup.exe"
                    and package.sha(setup) == receipt["installerSha256"],
                    "final Setup bytes changed")
    packaged_at = package.evidence_time(receipt.get("packagedAt"), datetime.now(timezone.utc))
    package.require(package.sha(payload / "ValMAX.exe") == subject["launcherSha256"]
                    and package.pins.inventory(payload / "runtime",
                        manifest["webview2Mode"], manifest["qtSourceMode"]) ==
                        manifest["runtimeFiles"],
                    "final package launcher/runtime bytes differ from the installed subject")
    for key, distribution_key in (("ffmpegSourceAsset", "ffmpegSourceDistribution"),
                                  ("qtSourceAsset", "qtSourceDistribution")):
        asset = receipt.get(key)
        if key == "ffmpegSourceAsset":
            package.require(isinstance(asset, dict), "required FFmpeg source asset is missing")
        declared = manifest.get(distribution_key)
        package.require(receipt.get(distribution_key) == declared,
                        "source asset distribution differs from final manifest")
        if asset is not None:
            if key == "qtSourceAsset":
                package.require(manifest.get("qtSourceMode") == "separate-asset",
                                "Qt source asset mode differs from final manifest")
            asset_path = package.owned(Path(asset["path"]))
            package.require(asset_path.parent == receipt_path.parent / "source-assets"
                            and isinstance(declared, dict)
                            and asset_path.name == asset.get("name") == declared.get("assetName")
                            and package.ordinary(asset_path).st_size ==
                                asset.get("size") == declared.get("assetBytes")
                            and isinstance(asset.get("sha256"), str)
                            and re.fullmatch(r"[0-9a-f]{64}", asset["sha256"])
                            and asset["sha256"] == declared.get("assetSha256")
                            and package.sha(asset_path) == asset["sha256"],
                            "required source asset differs from final manifest")
            if key == "ffmpegSourceAsset":
                package.validate_ffmpeg_source_distribution(declared, subject)
                package.require(manifest["betaMaterials"].get("ffmpegSourceDistribution") == declared,
                                "FFmpeg source asset differs from final materials manifest")
                package.verify_ffmpeg_source_asset(asset_path, declared, manifest["betaMaterials"])
        else:
            package.require(declared is None and manifest.get("qtSourceMode") == "inline",
                            "external Qt source asset missing from final release")
    preflight_path = package.evidence_path(receipt["preflightReport"])
    package.require(package.sha(preflight_path) == receipt["preflightReportSha256"],
                    "packager preflight receipt changed")
    preflight = package.read_json(preflight_path)
    package.require(preflight.get("distributionMode") == "public-stable-core"
                    and preflight.get("releaseEligible") is True
                    and preflight.get("subject") == subject
                    and preflight.get("artifactValidation", {}).get("verified") is True
                    and preflight["artifactValidation"].get("identity") ==
                        {key: subject[key] for key in package.ARTIFACT_IDENTITY_KEYS}
                    and preflight.get("nativeQmlCompatibility", {}).get("verifiedNoLegacyReferences") is True
                    and preflight.get("betaUiChannelVerified") is True,
                    "final Setup does not match an eligible byte-bound preflight")
    smoke_path = package.evidence_path(str(final_smoke_path))
    smoke = package.read_json(smoke_path)
    package.require(smoke.get("installedPackage", {}).get("distributionMode") == "public-stable-core"
                    and smoke["installedPackage"].get("version") == subject["version"]
                    and smoke["installedPackage"].get("sha256") == receipt["manifestSha256"]
                    and smoke.get("candidatePackage") ==
                        {"path": str(receipt_path), "sha256": package.sha(receipt_path)},
                    "final smoke is not bound to this installed Setup and manifest")
    observed_at = datetime.now(timezone.utc)
    idle = smoke.get("retentionIdleHealth")
    package.require(isinstance(idle, dict), "final installed idle-health window required")
    idle_started = package.evidence_time(idle.get("startedAt"), observed_at)
    idle_checked = package.evidence_time(idle.get("checkedAt"), observed_at)
    smoke_tested = package.evidence_time(smoke.get("testedAt"), observed_at)
    first_snapshot = idle.get("start")
    package.require(isinstance(first_snapshot, dict)
                    and isinstance(first_snapshot.get("process"), dict),
                    "final installed max.exe process observation required")
    process_created = package.evidence_time(first_snapshot["process"].get("createdAt"), observed_at)
    package.require(packaged_at < process_created <= idle_started <= idle_checked <= smoke_tested <= observed_at,
                    "final installed smoke must postdate final Setup packaging")
    material_limitations = package.beta_material_limitations(
        manifest["webview2Mode"], manifest["qtSourceMode"])
    report = package.release_preflight(
        subject, smoke_path, mode="public-stable-core",
        artifact_identity={key: subject[key] for key in package.ARTIFACT_IDENTITY_KEYS},
        materials={"completeForWholeClient": False,
                   "limitations": [item["statusText"] for item in material_limitations],
                   "qtSourceDistribution": manifest.get("qtSourceDistribution")},
        artifact_verified=True, qml_compatibility_verified=True,
        max_rights_attested=manifest.get("maxRightsAttested") is True,
        beta_materials=manifest["betaMaterials"],
        webview2_mode=manifest["webview2Mode"],
        qt_source_mode=manifest["qtSourceMode"],
        ffmpeg_source_mode=manifest["ffmpegSourceMode"],
        qml_proof_path=qml_proof_path)
    report["artifactValidation"] = preflight["artifactValidation"]
    report["nativeQmlCompatibility"] = preflight["nativeQmlCompatibility"]
    package.require_distribution("public-stable-core", report, installer=True)
    package.require(report["knownLimitations"] == manifest["knownLimitations"],
                    "final installed stable core limitations changed")
    return {"schema": 1, "kind": "native-public-stable-core-publication",
            "verifiedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "publicationEligible": True, "version": subject["version"],
            "subject": subject, "installerSha256": receipt["installerSha256"],
            "packageReceiptSha256": package.sha(receipt_path),
            "finalInstalledSmokeSha256": package.sha(smoke_path),
            "preflightSha256": package.sha(preflight_path),
            "sourceAssets": [{"name": Path(receipt[key]["path"]).name,
                              "sha256": receipt[key]["sha256"]}
                             for key in ("qtSourceAsset", "ffmpegSourceAsset")
                             if receipt.get(key) is not None],
            "knownLimitations": report["knownLimitations"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-receipt", type=Path, required=True)
    parser.add_argument("--final-smoke", type=Path, required=True)
    parser.add_argument("--qml-proof", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = package.owned(args.output)
    package.require(not output.exists(), "new publication receipt path required")
    result = verify(args.build_receipt, args.final_smoke, args.qml_proof)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                      encoding="utf8")
    print(json.dumps({"publicationEligible": True, "receipt": str(output),
                      "version": result["version"],
                      "installerSha256": result["installerSha256"]}))


if __name__ == "__main__":
    main()
