"""Receipt-chain admission tests; no native DLL or executable is loaded."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "real_core_launcher_runner", Path(__file__).with_name("run_real_core_launcher.py"))
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)


class RealCoreInputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="launcher-proof-inputs-",
                                                dir="D:/ValMAXBuild/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / "build"
        self.reports = self.root / "reports"
        self.pair_root = self.build / "frozen-pair"
        self.pair_root.mkdir(parents=True)
        self.reports.mkdir()
        self.core = self.pair_root / "valmax_core.dll"
        self.library = self.pair_root / "valmax_core.dll.lib"
        self.crt = self.build / "vcruntime140.dll"
        self.core.write_bytes(b"owned synthetic core identity")
        self.library.write_bytes(b"owned synthetic import library identity")
        self.crt.write_bytes(b"owned synthetic CRT identity")
        self.source_path = self.reports / "core-build.json"
        self.pair_path = self.pair_root / "core-pair-proof.json"
        self.runtime_path = self.reports / "qt-runtime.json"
        self.core_sha, self.library_sha = RUNNER.sha(self.core), RUNNER.sha(self.library)
        self.source = {"ok": True, "sourceSnapshotMatches": True,
                       "dll": {"sha256": self.core_sha},
                       "importLibrary": {"sha256": self.library_sha}}
        self.pair = {"verified": True, "sourceSnapshotMatches": True,
                     "importLibrarySymbolsMatch": True, "snapshot": str(self.pair_root),
                     "sourceProof": str(self.source_path),
                     "hashes": {"dll": self.core_sha, "importLibrary": self.library_sha}}
        self.runtime = {"verified": True, "actualMaxStarted": False,
                        "coreSha256": self.core_sha, "corePairProof": str(self.pair_path),
                        "dependencyClosure": [{"path": str(self.crt),
                                               "sha256": RUNNER.sha(self.crt)}]}
        for name, value in [("BUILD_ROOT", self.build), ("REPORT_ROOT", self.reports),
                            ("FROZEN_CORE_PROOFS", {})]:
            item = patch.object(RUNNER, name, value)
            item.start()
            self.addCleanup(item.stop)
        self.repin_fixture()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding="utf8")

    def repin_fixture(self):
        self.write(self.source_path, self.source)
        self.pair["sourceProofSha256"] = RUNNER.sha(self.source_path)
        self.write(self.pair_path, self.pair)
        self.runtime["corePairProofSha256"] = RUNNER.sha(self.pair_path)
        self.write(self.runtime_path, self.runtime)
        RUNNER.FROZEN_CORE_PROOFS.clear()
        RUNNER.FROZEN_CORE_PROOFS[RUNNER.sha(self.runtime_path)] = {
            "corePairProofSha256": RUNNER.sha(self.pair_path),
            "sourceProofSha256": RUNNER.sha(self.source_path),
            "coreSha256": self.core_sha, "importLibrarySha256": self.library_sha}

    def validate(self):
        return RUNNER.verified_core_inputs(self.runtime_path, self.pair_path)

    def test_valid_reviewed_chain(self):
        result = self.validate()
        self.assertEqual(result["core"], self.core)
        self.assertEqual(result["importLibrary"], self.library)
        self.assertEqual(result["dependency"], self.crt)
        self.assertEqual(result, self.validate())

    def test_runtime_not_in_reviewed_allowlist(self):
        self.runtime["verified"] = False
        self.write(self.runtime_path, self.runtime)
        with self.assertRaisesRegex(ValueError, "unreviewed runtime proof"):
            self.validate()

    def test_explicit_pair_must_match_runtime_path(self):
        other = self.pair_root / "another-pair.json"
        other.write_bytes(self.pair_path.read_bytes())
        with self.assertRaisesRegex(ValueError, "explicit pair differs"):
            RUNNER.verified_core_inputs(self.runtime_path, other)

    def test_tampered_pair_receipt(self):
        self.pair_path.write_text("{}", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "core-pair proof identity differs"):
            self.validate()

    def test_unverified_pair_rejected(self):
        self.pair["verified"] = False
        self.repin_fixture()
        with self.assertRaisesRegex(ValueError, "core pair is not verified"):
            self.validate()

    def test_changed_source_receipt(self):
        self.source_path.write_text("{}", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "core source proof identity differs"):
            self.validate()

    def test_unverified_source_receipt(self):
        self.source["sourceSnapshotMatches"] = False
        self.repin_fixture()
        with self.assertRaisesRegex(ValueError, "core build proof is not verified"):
            self.validate()

    def test_changed_core_dll(self):
        self.core.write_bytes(b"changed core")
        with self.assertRaisesRegex(ValueError, "frozen core DLL identity differs"):
            self.validate()

    def test_changed_import_library(self):
        self.library.write_bytes(b"changed import library")
        with self.assertRaisesRegex(ValueError, "frozen core import-library identity differs"):
            self.validate()

    def test_changed_crt(self):
        self.crt.write_bytes(b"changed CRT")
        with self.assertRaisesRegex(ValueError, "recorded VCRuntime identity differs"):
            self.validate()

    def test_duplicate_crt_is_not_admitted(self):
        self.runtime["dependencyClosure"] *= 2
        self.repin_fixture()
        with self.assertRaisesRegex(ValueError, "unique recorded VCRuntime required"):
            self.validate()

    def test_core_identity_not_selected_by_pair_alone(self):
        self.pair["hashes"]["dll"] = "0" * 64
        self.repin_fixture()
        with self.assertRaisesRegex(ValueError, "frozen core DLL identity differs"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
