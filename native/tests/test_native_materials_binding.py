"""Exact p14 material-binding regression; reads build receipts, never profiles.

The local materials archive is external to Git. On a host without the pinned
p14 inputs these checks skip rather than invent evidence.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "native/tooling"))
import assemble_native_runtime as assembly  # noqa: E402

MOD = Path("E:/ValMAXBuild/build/staging-integrated-api2-p14-20261025-1610/runtime/mod")
BASELINE = ROOT / "analysis/native-migration-v1/baseline-third-party-vc-winsparkle-20261025.json"
GUM = Path("D:/ValMAXBuild/materials/frida-gum-audit-20261021/frida-gum-materials-inventory.json")
RUST = Path("E:/ValMAXBuild/materials/rust-core-p14-20261025/windows-msvc-dependency-sbom.json")
CORE_PROOF = ROOT / "analysis/native-migration-v1/core-integrated-api2-preview14-20261025-1610.json"


class NativeMaterialsBinding(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        required = [MOD / "valmax_core.dll", BASELINE, GUM, RUST, CORE_PROOF]
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("exact local p14 build/materials receipts unavailable")

    def plan(self):
        plan = assembly.Plan()
        names = (assembly.BASELINE_FFMPEG_DLLS | assembly.BASELINE_OPENSSL_DLLS |
                 {"WinSparkle.dll", "valmax_core.dll", "valmax_ghost.dll",
                  "valmax_notifications.dll"})
        for name in sorted(names):
            path = MOD / name
            plan.add("mod/" + name, path, assembly.sha(path), "existing-p14")
        return plan

    def test_exact_p14_notices_and_rust_sources_bind_with_open_limits(self):
        plan = self.plan()
        baseline = assembly.add_baseline_upstream_notices(plan, BASELINE)
        gum = assembly.add_gum_notice(plan, GUM)
        rust = assembly.add_native_rust_materials(
            plan, RUST, CORE_PROOF, assembly.sha(MOD / "valmax_core.dll"))
        self.assertEqual(baseline["uncoveredRuntimeImages"], ["swscale-8.dll"])
        self.assertEqual(baseline["copiedNoticeFiles"], 8)
        self.assertFalse(baseline["ffmpegExactCorrespondingSourceProven"])
        self.assertTrue(gum["taggedNoticeCopied"])
        self.assertFalse(gum["staticObjectAndTransitiveNoticeClosureProven"])
        self.assertEqual((rust["sourceArchives"], rust["licenseTexts"]), (70, 139))
        self.assertFalse(rust["linkedObjectAndBundledCAsmLicenseReviewComplete"])
        materials = [row for row in plan.entries.values()
                     if row["path"].startswith("mod/licenses/")]
        self.assertEqual(len(materials), 8 + 1 + 70 + 139 + 3)

    def test_wrong_planned_baseline_dll_fails_before_notice_copy(self):
        plan = self.plan()
        plan.entries["mod/avcodec-61.dll"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "image differs"):
            assembly.add_baseline_upstream_notices(plan, BASELINE)
        self.assertFalse(any(row["path"].startswith("mod/licenses/BaselineUpstream/")
                             for row in plan.entries.values()))

    def test_changed_gum_receipt_fails_before_notice_copy(self):
        plan = self.plan()
        data = json.loads(GUM.read_text(encoding="utf8"))
        data["receipt"]["sha256"] = "0" * 64
        with tempfile.TemporaryDirectory(prefix="gum-materials-test-",
                                         dir="E:/ValMAXBuild/tmp") as directory:
            altered = Path(directory) / "audit.json"
            altered.write_text(json.dumps(data), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "receipt changed"):
                assembly.add_gum_notice(plan, altered)
        self.assertNotIn("mod/licenses/gum/copying", plan.entries)

    def test_rust_manifest_drift_rejected_even_with_exact_core_dll(self):
        plan = self.plan()
        data = json.loads(RUST.read_text(encoding="utf8"))
        data["sourceInputs"]["cargoLockSha256"] = "0" * 64
        with tempfile.TemporaryDirectory(prefix="rust-materials-test-",
                                         dir="E:/ValMAXBuild/tmp") as directory:
            altered = Path(directory) / "sbom.json"
            altered.write_text(json.dumps(data), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "manifests differ"):
                assembly.add_native_rust_materials(
                    plan, altered, CORE_PROOF, assembly.sha(MOD / "valmax_core.dll"))
        self.assertNotIn("mod/licenses/rustcore/windows-msvc-dependency-sbom.json",
                         plan.entries)


if __name__ == "__main__":
    unittest.main(verbosity=2)
