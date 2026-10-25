"""Verify static audit evidence and resolver edge cases; no target code loading."""
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
OUT = Path("C:/ValMAXBuild/fixtures/payload-dependencies-v1")
spec = importlib.util.spec_from_file_location("audit_payload", Path(__file__).with_name("audit_payload_dependencies.py"))
audit_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_module)


class StaticAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((ROOT / "analysis/native-migration-v1/payload-dependency-audit.json").read_text(encoding="utf-8"))
        cls.pe = json.loads((OUT / "pe-closure.json").read_text(encoding="utf-8"))
        cls.qml = json.loads((OUT / "qml-dependencies.json").read_text(encoding="utf-8"))
        cls.req = json.loads((OUT / "package-requirements.json").read_text(encoding="utf-8"))

    def test_artifact_hashes(self):
        for artifact in self.report["evidence"].values():
            raw = Path(artifact["path"]).read_bytes()
            self.assertEqual(len(raw), artifact["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), artifact["sha256"])

    def test_audit_source_hash(self):
        self.assertEqual(hashlib.sha256(Path(audit_module.__file__).read_bytes()).hexdigest(), self.report["scriptSha256"])

    def test_three_physical_gaps(self):
        self.assertEqual(set(self.report["nonSystemMissingImports"]),
                         {"qt6serialport.dll", "qt6webenginequick.dll", "qt6webenginequickdelegatesqml.dll"})

    def test_no_non_system_symbol_gaps(self):
        self.assertEqual(self.report["nonSystemSymbolIssueEdges"], 0)

    def test_exact_proof_pins(self):
        self.assertEqual(self.report["nativeProofMismatches"], [])
        self.assertEqual(self.report["proofsChangedDuringAudit"], [])

    def test_complete_rcc_decode(self):
        self.assertEqual(self.report["qmlDecodeErrors"], [])
        self.assertEqual(self.report["qmlDecoded"], 1061)
        self.assertEqual(self.qml["tables"], 107)

    def test_delay_imports_included(self):
        self.assertTrue(any(e["kind"] == "delay" and e["from"] == "webview-vendor:msedge.dll" for e in self.pe["edges"]))

    def test_large_export_tables_not_truncated(self):
        self.assertGreater(self.pe["nodes"]["old-payload:Qt6Gui.dll"]["exports"], 20000)
        self.assertGreater(self.pe["nodes"]["old-payload:Qt6Core.dll"]["exports"], 16900)

    def test_icu_resolves_as_os_dependency(self):
        edges = self.report["specialImporters"]["icuuc.dll"]
        self.assertEqual(len(edges), 2)
        self.assertTrue(all(e["to"] == "system64:icuuc.dll" and e["symbolCheck"] == "passed" for e in edges))

    def test_retention_not_shipping(self):
        self.assertNotIn("valmax_retention_queue.dll", self.pe["nativePlan"])
        self.assertIn("native:valmax_retention_queue.dll", self.pe["nodes"])

    def test_combined_candidate_uses_its_own_frozen_core(self):
        edge = next(e for e in self.pe["edges"] if e["from"] == "all-feature:qml/ValMAX/Native/valmaxnativeplugin.dll" and e["import"] == "valmax_core.dll")
        self.assertEqual(edge["to"], "all-feature:valmax_core.dll")
        self.assertEqual(self.pe["nodes"][edge["to"]]["sha256"], "f29f113fd1c0c82daa73ec21c8cf83b1030ebc69eec8d8847fcd93cc5ea015bf")
        self.assertNotIn("valmax_retention_queue.dll", self.pe["contextPlans"]["all-features"])

    def test_loader_closure_contains_privacy_dependencies(self):
        paths = {n["path"] for n in self.req["normalAppLocalClosures"]["old-payload:max.exe"]}
        self.assertTrue({"tracer_crash_reporter.dll", "tracernative.dll", "updater_oneme.dll", "WinSparkle.dll", "WebView2Loader.dll"} <= paths)

    def test_business_qml_is_webview(self):
        uses = [s for s in self.qml["specialTerms"] if s["source"].endswith("native\\qt\\BusinessPanel.qml")]
        self.assertEqual(len(uses), 1)
        self.assertEqual(uses[0]["terms"], ["QtWebView"])
        self.assertFalse(any("QtWebEngine" in s["terms"] for s in self.qml["specialTerms"]))

    def test_scanner_dependency_not_implicit_runtime_import(self):
        q = next(v for v in self.qml["qmldirs"] if v["path"] == "qml/QtWebView/qmldir")
        self.assertIn("depends QtWebEngine auto", [d["text"] for d in q["directives"]])
        self.assertFalse(any(d["text"].startswith("import QtWebEngine") for d in q["directives"]))

    def test_no_release_or_runtime_claim(self):
        self.assertFalse(self.report["targetCodeExecuted"])
        self.assertFalse(self.report["fullReleaseClosureVerified"])
        self.assertFalse(self.req["isFinalShippingAllowlist"])

    def test_api_exact_and_prefix_candidates_stay_distinct(self):
        a = audit_module.Audit.__new__(audit_module.Audit)
        a.api = {"api-ms-synthetic-l1-1-1.dll": [
            {"alias": "", "host": "kernelbase.dll", "schemaHashPrefix": "api-ms-synthetic-l1-1"}]}
        self.assertEqual(a.api_host("api-ms-synthetic-l1-1-1.dll", "app.exe"), ("kernelbase.dll", "exact_schema_name"))
        self.assertEqual(a.api_host("api-ms-synthetic-l1-1-0.dll", "app.exe"),
                         ("kernelbase.dll", "schema_hash_prefix_candidate_not_loader_verified"))
        self.assertEqual(a.api_host("api-ms-missing-l1-1-0.dll", "app.exe"), (None, "unknown_api_contract"))

    def test_ambiguous_or_empty_api_mapping_stays_unknown(self):
        a = audit_module.Audit.__new__(audit_module.Audit)
        a.api = {"api-ms-synthetic-l1-1-1.dll": [
            {"alias": "", "host": "", "schemaHashPrefix": "api-ms-synthetic-l1-1"}]}
        self.assertEqual(a.api_host("api-ms-synthetic-l1-1-1.dll", "app.exe"), (None, "unknown_api_contract"))
        a.api["api-ms-synthetic-l1-1-2.dll"] = [
            {"alias": "", "host": "another.dll", "schemaHashPrefix": "api-ms-synthetic-l1-1"}]
        self.assertEqual(a.api_host("api-ms-synthetic-l1-1-0.dll", "app.exe"), (None, "unknown_api_contract"))


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StaticAudit))
    summary = {"schema": 1, "tests": result.testsRun, "passed": result.wasSuccessful(),
               "targetCodeExecuted": False, "auditSourceSha256": hashlib.sha256(Path(audit_module.__file__).read_bytes()).hexdigest(),
               "testSourceSha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (ROOT / "analysis/native-migration-v1/payload-dependency-audit-checks.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if result.wasSuccessful() else 1)
