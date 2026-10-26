"""Synthetic gates for the separate Qt source asset and Evergreen notices."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
sys.path.insert(0, str(ROOT / "native/launcher"))
import package_native_client as package
from prepare_public_qt_source_asset import prepare as prepare_public_qt_source_asset
from qt_source_asset import PUBLIC_BETA_AVAILABILITY, verify_external_qt_source_asset
from launcher_generate_pins import REQUIRED, EVERGREEN_REQUIRED, generate, inventory
from webview2_bootstrapper import load_pin as webview2_bootstrapper_pin
from webview2_bootstrapper import verify as verify_webview2_bootstrapper


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class SlimPackaging(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qt-external-gate-", dir="E:/ValMAXBuild/build")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def planned_file(self, plan, destination, content):
        source = self.root / "inputs" / destination
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(content)
        plan[destination.casefold()] = {"path": destination, "source": str(source),
                                        "sha256": digest(content)}
        return source

    def test_pinned_microsoft_bootstrapper_and_changed_bytes(self):
        pin = webview2_bootstrapper_pin()
        asset = Path("E:/ValMAXBuild/deps/webview2-evergreen-bootstrapper") / pin["name"]
        if not asset.is_file():
            self.skipTest("pinned Microsoft bootstrapper is not staged on this build host")
        self.assertEqual(verify_webview2_bootstrapper(asset), pin)
        changed = self.root / pin["name"]
        changed.write_bytes(asset.read_bytes() + b"synthetic drift")
        with mock.patch("webview2_bootstrapper.authenticode") as signature:
            with self.assertRaisesRegex(ValueError, "differs from release pin"):
                verify_webview2_bootstrapper(changed)
            signature.assert_not_called()

    def test_evergreen_packager_requires_and_records_exact_bootstrapper(self):
        pin = webview2_bootstrapper_pin()
        asset = Path("E:/ValMAXBuild/deps/webview2-evergreen-bootstrapper") / pin["name"]
        runtime = self.root / "runtime"
        client = runtime / "mod/max.exe"
        client.parent.mkdir(parents=True)
        client.write_bytes(b"inert native runtime fixture")
        launcher = self.root / "ValMAX.exe"
        launcher.write_bytes(b"inert launcher fixture")
        rows = [{"path": "mod/max.exe", "size": client.stat().st_size,
                 "sha256": digest(client.read_bytes())}]
        inputs = {"runtime": runtime, "qml": self.root / "qml", "rows": rows,
                  "launcher": launcher, "launcherHash": digest(launcher.read_bytes()),
                  "qmlResult": {"proofSha256": "c" * 64}, "closure": [],
                  "hookTargetIdentity": {"assemblyReceiptSha256": "d" * 64},
                  "webview2Mode": "evergreen", "qtSourceMode": "inline"}
        base = ["package_native_client.py", "--version", "8.9.0",
                "--distribution", "developer-staging", "--runtime", str(runtime),
                "--qml-build", str(self.root / "qml"), "--launcher-proof", str(self.root / "proof.json")]
        with mock.patch.object(sys, "argv", base + ["--output", str(self.root / "missing")]), \
             mock.patch.object(package, "packaging_inputs", return_value=inputs), \
             mock.patch.object(package.shutil, "copyfile") as copy:
            with self.assertRaisesRegex(ValueError, "requires --evergreen-bootstrapper"):
                package.main()
            copy.assert_not_called()
        if not asset.is_file():
            self.skipTest("pinned Microsoft bootstrapper is not staged on this build host")
        output = self.root / "packaged"
        with mock.patch.object(sys, "argv", base + ["--output", str(output),
                                                     "--evergreen-bootstrapper", str(asset)]), \
             mock.patch.object(package, "packaging_inputs", return_value=inputs), \
             mock.patch.object(package.pins, "inventory", return_value=rows):
            package.main()
        copied = output / "installer-prerequisites" / pin["name"]
        manifest = json.loads((output / "payload/native-package.json").read_text("utf8"))
        receipt = json.loads((output / "build-receipt.json").read_text("utf8"))
        self.assertEqual(manifest["webview2Bootstrapper"], pin)
        self.assertEqual(receipt["webview2Bootstrapper"]["sha256"], pin["sha256"])
        self.assertEqual(receipt["webview2Bootstrapper"]["size"], pin["size"])
        self.assertEqual(copied.stat().st_size, pin["size"])
        self.assertEqual(digest(copied.read_bytes()), pin["sha256"])
        self.assertIn('#define NativeWebView2BootstrapperSha256 "' + pin["sha256"] + '"',
                      (output / "payload/native-release-state.iss").read_text("utf8"))

    def source_fixture(self, distribution="local-prototype-only", availability=None):
        archive_name = "qtbase-everywhere-src-6.11.2.tar.xz"
        source_paths = [
            "mod/licenses/shared-component-materials/Qt/source-materials/sources/" + archive_name,
            "transfer/licenses/Qt/source-materials/sources/" + archive_name,
        ]
        source_bytes = b"synthetic Qt source archive bytes"
        plan = {}
        for path in source_paths:
            self.planned_file(plan, path, source_bytes)
        notice_data = {
            "notices/mod/STAGING-MATERIALS.json": b"old materials snapshot",
            "notices/mod/WebView/qtwebview-6.11.2-valmax-source.zip": b"old WebView source",
            "notices/mod/WebView/webview-third-party-notices.zip": b"old WebView notices",
            "notices/transfer/Qt/source-materials/QT-NOTICES.txt": b"same notice",
        }
        self.planned_file(plan, "mod/licenses/WebView/qtwebview-6.11.2-valmax-source.zip", b"new Evergreen source")
        self.planned_file(plan, "mod/licenses/WebView/webview-third-party-notices.zip", b"new Evergreen notices")
        ordinary_notice = self.planned_file(plan, "transfer/licenses/Qt/source-materials/QT-NOTICES.txt",
                                           notice_data["notices/transfer/Qt/source-materials/QT-NOTICES.txt"])
        source_row = {"name": archive_name, "bytes": len(source_bytes), "sha256": digest(source_bytes),
                      "sourcePathsInP16": source_paths, "archiveTest": "passed"}
        notice_rows = [{"path": name, "bytes": len(data), "sha256": digest(data)}
                       for name, data in notice_data.items()]
        public = distribution == "public-beta-asset"
        manifest = {"kind": "qt-source-bundle-public-beta" if public
                    else "local-qt-source-bundle-prototype",
                    "variant": "p16-integrated-slim-no-qtwebengine", "qtVersion": "6.11.2",
                    "distribution": distribution, "legalCompleteness": "not-assessed",
                    "retainedSourceArchives": [source_row], "retainedSourceArchiveCount": 1,
                    "retainedSourceArchiveBytes": len(source_bytes),
                    "retainedNoticeFiles": notice_rows, "retainedNoticeFileCount": len(notice_rows),
                    "retainedNoticeBytes": sum(row["bytes"] for row in notice_rows)}
        if public:
            manifest["publicationStatus"] = "pending-upload"
        manifest_path = self.root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf8")
        zip_path = self.root / "qt-synthetic.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.write(manifest_path, "manifest.json")
            bundle.writestr("SOURCE-AVAILABILITY.txt" if public else "SOURCE-AVAILABILITY-DRAFT.txt",
                            availability if availability is not None else
                            (PUBLIC_BETA_AVAILABILITY if public else b"synthetic draft"))
            bundle.writestr("sources/" + archive_name, source_bytes)
            for name, data in notice_data.items():
                bundle.writestr(name, data)
        return plan, zip_path, manifest_path, ordinary_notice, source_paths

    def test_exact_external_asset_omits_duplicate_sources_and_rejects_notice_drift(self):
        plan, archive, manifest, ordinary_notice, paths = self.source_fixture()
        result, omitted = verify_external_qt_source_asset(plan, archive, manifest)
        self.assertEqual(omitted, set(paths))
        self.assertEqual(result["omittedRuntimeBytes"], 2 * len(b"synthetic Qt source archive bytes"))
        self.assertEqual(result["manifestDistribution"], "local-prototype-only")
        self.assertEqual(len(result["assetNoticeSnapshotDifferences"]), 3)
        ordinary_notice.write_bytes(b"unlisted notice change")
        with self.assertRaisesRegex(ValueError, "unexpected difference"):
            verify_external_qt_source_asset(plan, archive, manifest)

    def test_source_member_must_match_each_planned_copy(self):
        plan, archive, manifest, _, paths = self.source_fixture()
        Path(plan[paths[0].casefold()]["source"]).write_bytes(b"wrong source")
        with self.assertRaisesRegex(ValueError, "asset differs from planned runtime source"):
            verify_external_qt_source_asset(plan, archive, manifest)

    def test_public_beta_asset_requires_final_availability_and_exact_sources(self):
        plan, archive, manifest, _, paths = self.source_fixture("public-beta-asset")
        result, omitted = verify_external_qt_source_asset(plan, archive, manifest)
        self.assertEqual(omitted, set(paths))
        self.assertEqual(result["manifestDistribution"], "public-beta-asset")
        self.assertIs(result["availabilityDraftIncluded"], False)
        self.assertEqual(result["assetSha256"], digest(archive.read_bytes()))
        public = {"mode": "separate-asset", "assetName": "qt-sources.zip",
                  "assetSha256": result["assetSha256"], "manifestDistribution": "public-beta-asset",
                  "availabilityDraftIncluded": False}
        preview = package.preview_information("8.9.0-native.preview.20", "evergreen",
                                              "separate-asset", public).decode("utf-8-sig")
        self.assertIn("Публикация этого файла", preview)
        self.assertNotIn("публикация этого прототипа", preview)
        notice = package.qt_source_availability(public).decode("utf-8-sig")
        self.assertIn("проверьте, что файл опубликован", notice)
        beta = package.beta_information("8.9.0-native.preview.20", "evergreen",
                                        "separate-asset", public).decode("utf-8-sig")
        self.assertIn("должны быть опубликованы", beta)

    def test_public_beta_asset_rejects_draft_or_unapproved_availability(self):
        plan, archive, manifest, _, _ = self.source_fixture("public-beta-asset", b"synthetic draft")
        with self.assertRaisesRegex(ValueError, "final Qt source availability differs"):
            verify_external_qt_source_asset(plan, archive, manifest)
        plan, archive, manifest, _, _ = self.source_fixture("public-beta-asset")
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("SOURCE-AVAILABILITY-DRAFT.txt", b"stale draft")
        with self.assertRaisesRegex(ValueError, "unlisted or missing members"):
            verify_external_qt_source_asset(plan, archive, manifest)

    def test_public_beta_asset_rejects_unbound_or_overstated_manifest(self):
        plan, archive, manifest, _, _ = self.source_fixture("public-beta-asset")
        data = json.loads(manifest.read_text("utf8"))
        data["publicationStatus"] = "published"
        manifest.write_text(json.dumps(data), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "unsupported or overstated"):
            verify_external_qt_source_asset(plan, archive, manifest)
        data["publicationStatus"] = "pending-upload"
        data["kind"] = "local-qt-source-bundle-prototype"
        manifest.write_text(json.dumps(data), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "unsupported or overstated"):
            verify_external_qt_source_asset(plan, archive, manifest)
        data["kind"] = "qt-source-bundle-public-beta"
        data["published"] = True
        manifest.write_text(json.dumps(data), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "unsupported or overstated"):
            verify_external_qt_source_asset(plan, archive, manifest)
        data.pop("published")
        data["retainedNoticeBytes"] += 1
        manifest.write_text(json.dumps(data), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "ZIP manifest differs"):
            verify_external_qt_source_asset(plan, archive, manifest)

    def test_local_prototype_rejects_publication_claim(self):
        plan, archive, manifest, _, _ = self.source_fixture()
        data = json.loads(manifest.read_text("utf8"))
        data["publicationStatus"] = "published"
        manifest.write_text(json.dumps(data), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "unsupported or overstated"):
            verify_external_qt_source_asset(plan, archive, manifest)

    def test_prepare_public_asset_dry_run_then_exact_readback(self):
        plan, archive, manifest, _, paths = self.source_fixture()
        public_archive = self.root / "public-qt-sources.zip"
        public_manifest = self.root / "public-qt-manifest.json"
        dry_run = prepare_public_qt_source_asset(archive, manifest, public_archive, public_manifest)
        self.assertTrue(dry_run["ready"])
        self.assertFalse(dry_run["applied"])
        self.assertFalse(public_archive.exists())
        self.assertFalse(public_manifest.exists())
        receipt = prepare_public_qt_source_asset(archive, manifest, public_archive, public_manifest, True)
        self.assertTrue(receipt["applied"])
        result, omitted = verify_external_qt_source_asset(plan, public_archive, public_manifest)
        self.assertEqual(omitted, set(paths))
        self.assertEqual(result["assetSha256"], receipt["archiveSha256"])
        self.assertEqual(result["manifestSha256"], receipt["manifestSha256"])
        self.assertIs(result["availabilityDraftIncluded"], False)
        with zipfile.ZipFile(public_archive) as bundle:
            self.assertEqual(bundle.read("SOURCE-AVAILABILITY.txt"), PUBLIC_BETA_AVAILABILITY)
            self.assertNotIn("SOURCE-AVAILABILITY-DRAFT.txt", bundle.namelist())

    def test_prepare_public_asset_rejects_source_drift_without_output(self):
        _, archive, manifest, _, _ = self.source_fixture()
        public_archive = self.root / "public-qt-sources.zip"
        public_manifest = self.root / "public-qt-manifest.json"
        with zipfile.ZipFile(archive, "a") as bundle:
            bundle.writestr("unlisted.txt", b"drift")
        with self.assertRaisesRegex(ValueError, "prototype inventory differs"):
            prepare_public_qt_source_asset(archive, manifest, public_archive, public_manifest, True)
        self.assertFalse(public_archive.exists())
        self.assertFalse(public_manifest.exists())

    def test_prototype_asset_cannot_pass_public_beta_materials_gate(self):
        receipt = {"mode": "separate-asset", "manifestDistribution": "local-prototype-only",
                   "availabilityDraftIncluded": True, "requiredTogetherWithInstaller": True,
                   "legalCompleteness": "not-assessed", "assetSha256": "a" * 64}
        with self.assertRaisesRegex(ValueError, "separate Qt source asset identity required"):
            package.beta_information("8.9.0-native.preview.20", "evergreen", "separate-asset",
                                     {**receipt, "assetName": "qt-sources.zip"})
        limitations = package.beta_material_limitations("evergreen", "separate-asset")
        binding = {"status": {"completeForWholeClient": False,
                              "limitations": [item["statusText"] for item in limitations],
                              "qtSourceDistribution": receipt}, "sha256": "b" * 64}
        with self.assertRaisesRegex(ValueError, "prototype-only"):
            package.beta_materials_documentation(binding, [], "c" * 64, {}, None,
                                                 "evergreen", "separate-asset")

    def test_evergreen_notices_name_system_runtime_and_external_asset(self):
        asset = {"mode": "separate-asset", "assetName": "qt-sources.zip", "assetSha256": "a" * 64,
                 "manifestDistribution": "local-prototype-only", "availabilityDraftIncluded": True}
        public = {**asset, "manifestDistribution": "public-beta-asset", "availabilityDraftIncluded": False}
        notice = package.beta_information("8.9.0-native.preview.17", "evergreen", "separate-asset", public).decode("utf-8-sig")
        self.assertIn("Microsoft Edge WebView2 Evergreen Runtime", notice)
        self.assertIn("qt-sources.zip", notice)
        self.assertNotIn("Microsoft Fixed Version Runtime", notice)
        availability = package.qt_source_availability(asset).decode("utf-8-sig")
        self.assertIn("локальная проверка", availability)
        self.assertIn("source-assets", availability)
        preview = package.preview_information("0.0.0-native.preview.999", "evergreen",
                                              "separate-asset", asset).decode("utf-8-sig")
        self.assertIn("Microsoft Edge WebView2 Evergreen Runtime", preview)
        self.assertIn("qt-sources.zip", preview)
        self.assertIn("локальный ZIP", preview)

    def test_launcher_pin_mode_rejects_inline_qt_archive(self):
        runtime = self.root / "runtime"
        for name in REQUIRED | EVERGREEN_REQUIRED:
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"synthetic")
        archive = runtime / "mod/licenses/Qt/sources/qtbase-everywhere-src-6.11.2.tar.xz"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"synthetic")
        with self.assertRaisesRegex(ValueError, "Qt source archive remains"):
            inventory(runtime, "evergreen", "separate-asset")
        archive.unlink()
        archive.parent.rmdir()
        archive.parent.parent.rmdir()
        archive.parent.parent.parent.rmdir()
        receipt_path = self.root / "pins.json"
        generate(runtime, self.root / "pins.h", receipt_path, "evergreen", "separate-asset")
        self.assertEqual(json.loads(receipt_path.read_text("utf8"))["qtSourceMode"], "separate-asset")

    def test_inno_compiler_requires_exact_external_asset_and_notice(self):
        compiler = Path("D:/CodexLabs/max-mod-26.35.1/tools/InnoSetup/ISCC.exe")
        if not compiler.is_file():
            self.skipTest("Inno compiler unavailable")
        payload = self.root / "payload"
        (payload / "runtime/transfer").mkdir(parents=True)
        (payload / "ValMAX.exe").write_bytes(b"inert launcher")
        (payload / "runtime/transfer/valmax-transfer.exe").write_bytes(b"inert transfer")
        package_json = payload / "native-package.json"
        package_json.write_bytes(b'{"synthetic":true}\n')
        preview = payload / "native-preview.txt"
        preview.write_bytes("Local compile-only proof.\n".encode("utf-8-sig"))
        notice = payload / "QT-SOURCE-AVAILABILITY.txt"
        notice.write_bytes(b"synthetic source availability\n")
        asset = self.root / "qt-sources.zip"
        asset.write_bytes(b"synthetic source asset")
        bootstrapper = self.root / "MicrosoftEdgeWebview2Setup.exe"
        bootstrapper.write_bytes(b"inert synthetic WebView2 installer for compile only")
        asset_hash, notice_hash = digest(asset.read_bytes()), digest(notice.read_bytes())
        marker = payload / "native-release-state.iss"
        output = self.root / "compile-only"
        output.mkdir()

        def compile_case(*, browser="evergreen", asset_pin=None, notice_pin=None,
                         bootstrapper_pin=None, include_asset=True, include_notice=True,
                         include_bootstrapper=True, bootstrapper_file=None):
            selected_bootstrapper = bootstrapper_file or bootstrapper
            marker.write_text(
                '#define NativeDistributionMode "local-preview"\n'
                '#define NativeWebView2Mode "' + browser + '"\n'
                '#define NativeReleaseEligible 0\n'
                '#define NativeReleaseBlockerCount 7\n'
                '#define NativePackageManifestSha256 "' + digest(package_json.read_bytes()) + '"\n'
                '#define NativePreviewVersion "8.9.0-native.preview.99"\n'
                '#define NativePreviewInfoSha256 "' + digest(preview.read_bytes()) + '"\n'
                '#define NativeQtSourceMode "separate-asset"\n'
                '#define NativeFFmpegSourceMode "inline"\n'
                '#define NativeQtSourceAssetSha256 "' + (asset_pin or asset_hash) + '"\n'
                '#define NativeQtSourceInfoSha256 "' + (notice_pin or notice_hash) + '"\n'
                '#define NativeWebView2BootstrapperSha256 "' +
                (bootstrapper_pin or digest(selected_bootstrapper.read_bytes())) + '"\n',
                encoding="utf8")
            if not include_notice:
                notice.unlink()
            command = [str(compiler), "/Qp", "/O-", "/DPayload=" + str(payload),
                       "/DOutput=" + str(output), "/DProductVersion=8.9.0-native.preview.99"]
            if include_asset:
                command.append("/DQtSourceAsset=" + str(asset))
            if include_bootstrapper:
                command.append("/DWebView2Bootstrapper=" + str(selected_bootstrapper))
            command.append(str(ROOT / "native/installer/valmax-native.iss"))
            result = subprocess.run(command, capture_output=True, timeout=60)
            if not include_notice:
                notice.write_bytes(b"synthetic source availability\n")
            return result.returncode, (result.stdout + result.stderr).decode("utf8", errors="replace")

        code, output_text = compile_case()
        self.assertEqual(code, 0, output_text)
        microsoft = Path("E:/ValMAXBuild/deps/webview2-evergreen-bootstrapper") / webview2_bootstrapper_pin()["name"]
        if microsoft.is_file():
            code, output_text = compile_case(bootstrapper_file=microsoft)
            self.assertEqual(code, 0, output_text)
        for options, expected in (
            ({"include_asset": False}, "ZIP path must be supplied"),
            ({"asset_pin": "0" * 64}, "ZIP differs from the package receipt"),
            ({"include_notice": False}, "availability notice is missing"),
            ({"notice_pin": "0" * 64}, "availability notice differs from the package receipt"),
            ({"include_bootstrapper": False}, "bootstrapper path must be supplied"),
            ({"bootstrapper_pin": "0" * 64}, "bootstrapper differs from the package receipt"),
            ({"browser": "fixed"}, "requires Evergreen WebView2 mode"),
        ):
            code, output_text = compile_case(**options)
            self.assertNotEqual(code, 0, options)
            self.assertIn(expected, output_text, output_text)
        self.assertFalse(list(output.glob("*.exe")))


if __name__ == "__main__":
    unittest.main()
