"""Native packaging regressions: server leftovers, stale UI, dependency gaps."""
import hashlib
import json
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
import os

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
import package_native_client as package


def digest(data):
    return hashlib.sha256(data).hexdigest()


class NativePackaging(unittest.TestCase):
    def setUp(self):
        parent = Path("E:/ValMAXBuild/tmp")
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="native-packaging-test-", dir=parent)
        self.root = Path(self.temporary.name).resolve()
        self.assertEqual(self.root.parent, parent.resolve())
        # This suite owns non-PE target bytes. The separate verifier suite tests
        # real PE/header parsing; here an explicit static mock exercises how the
        # packager binds that result to the complete assembly and staged files.
        self.verifier_source = self.root / "synthetic-hook-verifier.py"
        self.verifier_source.write_bytes(b"# owned unit-only static verifier fixture\n")
        self.source_patch = patch.object(package, "HOOK_VERIFIER", self.verifier_source)
        self.source_patch.start()
        self.verifier_patch = patch.object(package, "recompute_hook_targets", side_effect=self.synthetic_hook_verifier)
        self.mock_verifier = self.verifier_patch.start()
        self.addCleanup(self.verifier_patch.stop)
        self.addCleanup(self.source_patch.stop)

    def tearDown(self):
        self.assertEqual(self.root.parent, Path("E:/ValMAXBuild/tmp").resolve())
        self.temporary.cleanup()

    def generated(self, legacy=False):
        output = self.root / "qml"
        output.mkdir()
        source = b"import QtQuick\nimport ValMAX.Native 1.0\nItem {}\n"
        extra = b'Item { property string endpoint: "http://127.0.0.1:18973/message-retention/lookup" }' if legacy else b"Item {}"
        (output / "Window.qml").write_bytes(source)
        extra_path = output / "icons/inputs/valmax/Controller.qml"
        extra_path.parent.mkdir(parents=True)
        extra_path.write_bytes(extra)
        proof = {
            "nativeRuntime": True,
            "sourceHashes": {":/qt/qml/Window.qml": digest(source)},
            "extraResourceHashes": {":/valmax/Controller.qml": digest(extra)},
        }
        (output / "ui_native.py").write_text("PATCH_FILES = " + repr([{"patches": [
            {"offset": 2, "replacement": b"native-resource".hex()}
        ]}]), encoding="utf8")
        (output / "icons/icon-pack-plus-valmax.rcc").write_bytes(b"synthetic-resource-bundle")
        proof["generatedArtifacts"] = {name: package.sha(output / name) for name in ("ui_native.py", "icons/icon-pack-plus-valmax.rcc")}
        (output / "native-qml-proof.json").write_text(json.dumps(proof), encoding="utf8")
        client = self.root / "max.exe"
        client.write_bytes(b"MZnative-resource")
        return output, client

    def test_ui_matches_actual_client_bytes(self):
        output, client = self.generated()
        self.assertEqual(package.check_generated_qml(output, client)["resources"], 2)
        client.write_bytes(b"MZold-resource")
        with self.assertRaisesRegex(ValueError, "runtime UI differs"):
            package.check_generated_qml(output, client)

    def test_retention_leftover_blocks_native_package(self):
        output, client = self.generated(legacy=True)
        with self.assertRaisesRegex(ValueError, "legacy local helper remains.*Controller.qml"):
            package.check_generated_qml(output, client)

    def test_preflight_inspection_checks_embedded_bytes_even_with_legacy_reference(self):
        output, client = self.generated(legacy=True)
        result = package.inspect_generated_qml(output, client)
        self.assertEqual(result["legacyResources"], [":/valmax/Controller.qml"])
        self.assertEqual(result["resources"], 2)
        client.write_bytes(b"MZold-resource")
        with self.assertRaisesRegex(ValueError, "runtime UI differs"):
            package.inspect_generated_qml(output, client)

    def full_inputs(self, legacy=False):
        qml, client = self.generated(legacy=legacy)
        runtime = self.root / "runtime"
        (runtime / "mod").mkdir(parents=True)
        client.rename(runtime / "mod/max.exe")
        (runtime / "mod/valmax_core.dll").write_bytes(b"synthetic-core-not-loaded")
        target_names = ["max.exe", "network.dll", "storage.dll", "calls.dll", "analytics.dll", "notifications-base.dll", "core.dll", "notifications.dll"]
        for name in target_names[1:]:
            (runtime / "mod" / name).write_bytes(("owned synthetic target " + name).encode())
        hook_root = self.root / "frozen-hook-inputs"
        hook_root.mkdir()
        hooks = {}
        for family in ("privacy", "ghost", "notification"):
            dll = "valmax_" + family + ".dll"
            data = ("owned synthetic compiled guard " + family).encode()
            (hook_root / dll).write_bytes(data)
            (runtime / "mod" / dll).write_bytes(data)
            pins_name, header = family + "-pins.json", family + "-pins.h"
            (hook_root / pins_name).write_bytes(("owned static pins " + family).encode())
            (hook_root / header).write_bytes(("owned static header " + family).encode())
            hooks[family] = {"dll": dll, "dllSha256": digest(data), "dllBytes": len(data),
                "pinsFile": pins_name, "pinsSha256": package.sha(hook_root / pins_name),
                "headerFile": header, "headerSha256": package.sha(hook_root / header)}
        targets = []
        for family, names in (("privacy", target_names[:6]), ("ghost", ["core.dll", "max.exe"]), ("notification", ["max.exe", "notifications.dll"])):
            for name in names:
                path = runtime / "mod" / name
                targets.append({"hook": family, "relative": name, "sha256": package.sha(path),
                    "size": path.stat().st_size, "imageSize": 4096})
        self.hook_identity = {"schema": 1, "verified": True, "activationVerified": False,
            "verifierSha256": package.sha(self.verifier_source), "targetCount": 10,
            "uniqueTargetCount": 8, "hooks": hooks, "targets": targets}
        rows = [{"path": "mod/" + path.name, "size": path.stat().st_size, "sha256": package.sha(path)}
                for path in sorted((runtime / "mod").iterdir())]
        value = json.loads((qml / "native-qml-proof.json").read_text("utf8"))
        value["requiredNativeFiles"] = ["valmax_core.dll"]
        (qml / "native-qml-proof.json").write_text(json.dumps(value), encoding="utf8")
        launcher = self.root / "ValMAX.exe"
        launcher.write_bytes(b"synthetic-compiled-launcher-not-executed")
        pins = self.root / "launcher-pins.json"
        pins.write_text(json.dumps({"files": rows}), encoding="utf8")
        proof = self.root / "launcher-proof.json"
        proof.write_text(json.dumps({"compiled": True, "artifacts": {str(launcher): package.sha(launcher)},
            "sourceHashes": {"native/tooling/package_native_client.py": package.sha(ROOT / "native/tooling/package_native_client.py")},
            "pinsReceipt": {"path": str(pins), "sha256": package.sha(pins)}}), encoding="utf8")
        inventory = [{"path": row["path"], "bytes": row["size"], "sha256": row["sha256"]} for row in rows]
        (self.root / "assembly-manifest.json").write_text(json.dumps({"schema": 1, "assembled": True,
            "runtime": str(runtime), "inventory": inventory, "runtimeInventorySha256": package.inventory_digest(inventory),
            "hookInputsRoot": str(hook_root), "hookTargetIdentity": self.hook_identity}), encoding="utf8")
        return runtime, qml, proof, rows, launcher, pins

    def synthetic_hook_verifier(self, hook_root, read_target):
        self.assertEqual(hook_root, self.root / "frozen-hook-inputs")
        for row in self.hook_identity["targets"]:
            data = read_target(row["relative"])
            if len(data) != row["size"] or digest(data) != row["sha256"]:
                raise ValueError("synthetic verifier target changed")
        return copy.deepcopy(self.hook_identity)

    def rewrite_assembly(self, change):
        path = self.root / "assembly-manifest.json"
        value = json.loads(path.read_text("utf8"))
        change(value)
        path.write_text(json.dumps(value), encoding="utf8")

    def refresh_general_inventory(self, runtime, proof, rows, pins):
        """Refresh general file pins only, deliberately retaining old guard pins."""
        rows[:] = [{"path": "mod/" + path.name, "size": path.stat().st_size, "sha256": package.sha(path)}
                   for path in sorted((runtime / "mod").iterdir())]
        pins.write_text(json.dumps({"files": rows}), encoding="utf8")
        receipt = json.loads(proof.read_text("utf8"))
        receipt["pinsReceipt"]["sha256"] = package.sha(pins)
        proof.write_text(json.dumps(receipt), encoding="utf8")
        inventory = [{"path": row["path"], "bytes": row["size"], "sha256": row["sha256"]} for row in rows]
        self.rewrite_assembly(lambda value: value.update(inventory=inventory, runtimeInventorySha256=package.inventory_digest(inventory)))

    def test_legacy_preflight_completes_launcher_pins_and_closure_but_cannot_package(self):
        runtime, qml, proof, rows, launcher, _ = self.full_inputs(legacy=True)
        output = self.root / "legacy-preflight.json"
        argv = ["package_native_client.py", "--runtime", str(runtime), "--qml-build", str(qml),
                "--launcher-proof", str(proof), "--preflight-only", "--preflight-report", str(output)]
        with patch.object(sys, "argv", argv), patch.object(package.pins, "inventory", return_value=rows), \
             patch.object(package, "dependency_closure", return_value=[]) as closure, \
             patch.object(package.shutil, "copyfile") as copy, patch.object(package.subprocess, "run") as compiler:
            package.main()
            closure.assert_called_once_with(runtime, rows, launcher)
            copy.assert_not_called()
            compiler.assert_not_called()
        report = json.loads(output.read_text("utf8"))
        self.assertTrue(report["artifactValidation"]["verified"])
        self.assertIn("clean-machine compatibility are not verified", report["artifactValidation"]["scope"])
        self.assertEqual(report["artifactValidation"]["identity"], {"launcherSha256": package.sha(launcher),
            "runtimeInventorySha256": package.inventory_digest(rows), "qmlProofSha256": package.sha(qml / "native-qml-proof.json")})
        self.assertEqual(report["artifactValidation"]["immutableRuntimeFiles"], len(rows))
        self.assertEqual(report["artifactValidation"]["hookTargetIdentity"]["identity"], self.hook_identity)
        self.assertFalse(report["artifactValidation"]["hookTargetIdentity"]["identity"]["activationVerified"])
        self.assertEqual(report["nativeQmlCompatibility"]["legacyResources"], [":/valmax/Controller.qml"])
        ids = {item["id"] for item in report["blockers"]}
        self.assertTrue({"legacy_local_helper", "product_version_missing"} <= ids)
        self.assertNotIn("exact_payload_missing", ids)
        self.assertFalse(report["releaseEligible"])
        with patch.object(package.pins, "inventory", return_value=rows), patch.object(package, "dependency_closure") as closure:
            with self.assertRaisesRegex(ValueError, "legacy local helper remains"):
                package.packaging_inputs(runtime, qml, proof)
            closure.assert_not_called()

    def test_legacy_preflight_still_rejects_mismatched_launcher_pins(self):
        runtime, qml, proof, rows, _, pins = self.full_inputs(legacy=True)
        pins.write_text(json.dumps({"files": []}), encoding="utf8")
        receipt = json.loads(proof.read_text("utf8"))
        receipt["pinsReceipt"]["sha256"] = package.sha(pins)
        proof.write_text(json.dumps(receipt), encoding="utf8")
        with patch.object(package.pins, "inventory", return_value=rows), patch.object(package, "dependency_closure") as closure:
            with self.assertRaisesRegex(ValueError, "launcher and runtime inventory differ"):
                package.packaging_inputs(runtime, qml, proof, preflight_only=True)
            closure.assert_not_called()

    def test_legacy_preflight_still_rejects_stale_launcher_source(self):
        runtime, qml, proof, rows, _, _ = self.full_inputs(legacy=True)
        receipt = json.loads(proof.read_text("utf8"))
        receipt["sourceHashes"]["native/tooling/package_native_client.py"] = "d" * 64
        proof.write_text(json.dumps(receipt), encoding="utf8")
        with patch.object(package.pins, "inventory", return_value=rows), patch.object(package, "dependency_closure") as closure:
            with self.assertRaisesRegex(ValueError, "launcher source changed"):
                package.packaging_inputs(runtime, qml, proof, preflight_only=True)
            closure.assert_not_called()

    def hook_inputs(self):
        runtime, qml, proof, rows, launcher, pins = self.full_inputs()
        return runtime, proof, rows, pins

    def test_static_guard_identity_is_recomputed_without_activation_claim(self):
        runtime, _, rows, _ = self.hook_inputs()
        result = package.hook_target_identity(runtime, rows)
        self.assertEqual(result["identity"], self.hook_identity)
        self.assertFalse(result["identity"]["activationVerified"])
        self.assertEqual(result["assemblyReceiptSha256"], package.sha(self.root / "assembly-manifest.json"))
        self.mock_verifier.assert_called_once()
        self.assertIn("Guard activation", package.ARTIFACT_AUDIT_SCOPE)

    def test_materials_are_bound_to_assembly_and_actual_runtime_bytes(self):
        runtime, _, rows, _ = self.hook_inputs()
        assembly = self.root / "assembly-manifest.json"
        self.assertIsNone(package.bound_materials(runtime, rows, package.sha(assembly)))
        status = {"completeForWholeClient": False, "limitations": ["synthetic-unresolved"]}
        marker = runtime / "mod/licenses/STAGING-MATERIALS.json"
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps(status), encoding="utf8")
        rows.append({"path": "mod/licenses/STAGING-MATERIALS.json",
                     "size": marker.stat().st_size, "sha256": package.sha(marker)})
        inventory = [{"path": row["path"], "bytes": row["size"], "sha256": row["sha256"]}
                     for row in rows]
        self.rewrite_assembly(lambda value: value.update(
            materials=status, inventory=inventory,
            runtimeInventorySha256=package.inventory_digest(inventory)))
        bound = package.bound_materials(runtime, rows, package.sha(assembly))
        self.assertEqual(bound, {"status": status, "sha256": package.sha(marker)})
        marker.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "inventoried materials file changed"):
            package.bound_materials(runtime, rows, package.sha(assembly))
        marker.write_text(json.dumps(status), encoding="utf8")
        self.rewrite_assembly(lambda value: value["materials"].update(completeForWholeClient=True))
        with self.assertRaisesRegex(ValueError, "assembly and runtime materials status differ"):
            package.bound_materials(runtime, rows, package.sha(assembly))

    def test_missing_assembly_receipt_rejects_static_guard_identity(self):
        runtime, _, rows, _ = self.hook_inputs()
        (self.root / "assembly-manifest.json").unlink()
        with self.assertRaises(FileNotFoundError):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_missing_or_false_static_guard_snapshot_is_rejected(self):
        runtime, _, rows, _ = self.hook_inputs()
        path = self.root / "assembly-manifest.json"
        original = path.read_bytes()
        changes = [lambda value: value.pop("hookTargetIdentity"),
                   lambda value: value["hookTargetIdentity"].update(verified=False),
                   lambda value: value["hookTargetIdentity"].update(verified=1),
                   lambda value: value["hookTargetIdentity"].update(activationVerified=True)]
        for change in changes:
            path.write_bytes(original)
            self.rewrite_assembly(change)
            with self.assertRaisesRegex(ValueError, "verified static guard target identity required"):
                package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_altered_static_guard_target_record_is_rejected(self):
        runtime, _, rows, _ = self.hook_inputs()
        self.rewrite_assembly(lambda value: value["hookTargetIdentity"]["targets"][0].update(sha256="e" * 64))
        with self.assertRaisesRegex(ValueError, "guard target differs from actual runtime inventory"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_changed_target_with_current_general_pins_still_fails_guard_pins(self):
        runtime, proof, rows, pins = self.hook_inputs()
        (runtime / "mod/core.dll").write_bytes(b"new target despite refreshed general inventory")
        self.refresh_general_inventory(runtime, proof, rows, pins)
        with self.assertRaisesRegex(ValueError, "guard target differs from actual runtime inventory"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_altered_image_size_snapshot_is_rejected_by_recomputation(self):
        runtime, _, rows, _ = self.hook_inputs()
        self.rewrite_assembly(lambda value: value["hookTargetIdentity"]["targets"][0].update(imageSize=8192))
        with self.assertRaisesRegex(ValueError, "recomputed static guard target identity differs"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_called_once()

    def test_changed_guard_dll_with_current_general_pins_is_rejected(self):
        runtime, proof, rows, pins = self.hook_inputs()
        (runtime / "mod/valmax_ghost.dll").write_bytes(b"different guard build")
        self.refresh_general_inventory(runtime, proof, rows, pins)
        with self.assertRaisesRegex(ValueError, "staged compiled guard identity differs"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_changed_frozen_guard_sidecar_is_rejected(self):
        runtime, _, rows, _ = self.hook_inputs()
        (self.root / "frozen-hook-inputs/privacy-pins.h").write_bytes(b"modified header")
        with self.assertRaisesRegex(ValueError, "frozen guard sidecar or DLL changed"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_changed_guard_verifier_source_is_rejected(self):
        runtime, _, rows, _ = self.hook_inputs()
        self.verifier_source.write_bytes(b"changed verifier source")
        with self.assertRaisesRegex(ValueError, "guard target verifier source changed"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_assembly_changed_during_recompute_is_rejected(self):
        runtime, _, rows, _ = self.hook_inputs()
        def changed(hook_root, read_target):
            result = self.synthetic_hook_verifier(hook_root, read_target)
            self.rewrite_assembly(lambda value: value.update(assembled=False))
            return result
        self.mock_verifier.side_effect = changed
        with self.assertRaisesRegex(ValueError, "assembly receipt changed during guard verification"):
            package.hook_target_identity(runtime, rows)

    def test_nested_guard_sidecar_is_rejected_before_read(self):
        runtime, _, rows, _ = self.hook_inputs()
        self.rewrite_assembly(lambda value: value["hookTargetIdentity"]["hooks"]["privacy"].update(pinsFile="nested/pins.json"))
        with self.assertRaisesRegex(ValueError, "guard sidecar must be a filename"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_incomplete_or_wrong_assembly_inventory_is_rejected(self):
        runtime, _, rows, _ = self.hook_inputs()
        path = self.root / "assembly-manifest.json"
        original = path.read_bytes()
        for change in (lambda value: value["inventory"].pop(),
                       lambda value: value["inventory"][0].update(bytes=0),
                       lambda value: value.update(runtimeInventorySha256="a" * 64),
                       lambda value: value.update(runtime=str(self.root / "other-runtime"))):
            path.write_bytes(original)
            self.rewrite_assembly(change)
            with self.assertRaisesRegex(ValueError, "assembly .* (differ|changed)"):
                package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_duplicate_or_traversal_guard_target_is_rejected(self):
        runtime, _, rows, _ = self.hook_inputs()
        path = self.root / "assembly-manifest.json"
        original = path.read_bytes()
        for name in ("../max.exe", "C:/max.exe", "./max.exe", "mod\\max.exe"):
            path.write_bytes(original)
            self.rewrite_assembly(lambda value: value["hookTargetIdentity"]["targets"][0].update(relative=name))
            with self.assertRaisesRegex(ValueError, "invalid guard identity relative path"):
                package.hook_target_identity(runtime, rows)
        path.write_bytes(original)
        self.rewrite_assembly(lambda value: value["hookTargetIdentity"]["targets"].__setitem__(1, copy.deepcopy(value["hookTargetIdentity"]["targets"][0])))
        with self.assertRaisesRegex(ValueError, "ten distinct family records and eight files"):
            package.hook_target_identity(runtime, rows)
        self.mock_verifier.assert_not_called()

    def test_stale_generated_qml_rejected(self):
        output, client = self.generated()
        (output / "Window.qml").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "QML changed"):
            package.check_generated_qml(output, client)

    def test_patch_spec_is_data_not_executed(self):
        output, client = self.generated()
        marker = self.root / "unexpected-execution.txt"
        (output / "ui_native.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('bad')\nPATCH_FILES = []\n", encoding="utf8")
        proof = json.loads((output / "native-qml-proof.json").read_text("utf8"))
        proof["generatedArtifacts"]["ui_native.py"] = package.sha(output / "ui_native.py")
        (output / "native-qml-proof.json").write_text(json.dumps(proof), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "unexpected resource patch spec"):
            package.check_generated_qml(output, client)
        self.assertFalse(marker.exists())

    def test_mixed_old_patch_spec_or_rcc_is_rejected(self):
        output, client = self.generated()
        spec = output / "ui_native.py"
        original = spec.read_bytes()
        spec.write_bytes(b"PATCH_FILES = []")
        with self.assertRaisesRegex(ValueError, "generated artifact changed: ui_native.py"):
            package.check_generated_qml(output, client)
        spec.write_bytes(original)
        (output / "icons/icon-pack-plus-valmax.rcc").write_bytes(b"old-resource-bundle")
        with self.assertRaisesRegex(ValueError, "generated artifact changed: icons/"):
            package.check_generated_qml(output, client)

    def test_imports_use_only_package_or_explicit_system_boundary(self):
        runtime = self.root / "runtime"
        mod = runtime / "mod"
        mod.mkdir(parents=True)
        (mod / "max.exe").write_bytes(b"synthetic")
        (mod / "Qt6Core.dll").write_bytes(b"synthetic")
        launcher = self.root / "ValMAX.exe"
        launcher.write_bytes(b"synthetic")
        rows = [{"path": "mod/max.exe"}]
        graph = {"verified": True, "missing": [], "edges": [{"image": "mod/max.exe", "kind": "delay",
            "import": "qt6core.dll", "resolution": "bundled", "target": "mod/Qt6Core.dll"}]}
        with patch.object(package, "runtime_dependency_closure", return_value=graph), \
             patch.object(package, "pe_imports", return_value=[{"name": "qt6core.dll", "kind": "direct"}]), \
             patch.object(package, "system_dependency", return_value=None):
            with self.assertRaisesRegex(ValueError, "missing bundled dependency.*ValMAX.exe"):
                package.dependency_closure(runtime, rows, launcher)
        with patch.object(package, "runtime_dependency_closure", return_value=graph) as closure, patch.object(package, "pe_imports", return_value=[]):
            edges = package.dependency_closure(runtime, rows, launcher)
            closure.assert_called_once_with(runtime, rows)
            self.assertEqual(edges[0]["kind"], "delay")
            self.assertIn("Qt6Core.dll".lower(), edges[0]["resolution"].lower())

    def test_python_dependency_rejected(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        launcher = self.root / "ValMAX.exe"
        launcher.write_bytes(b"synthetic")
        with patch.object(package, "runtime_dependency_closure", return_value={"verified": True, "missing": [], "edges": []}), \
             patch.object(package, "pe_imports", return_value=[{"name": "python314.dll", "kind": "delay"}]):
            with self.assertRaisesRegex(ValueError, "legacy runtime dependency"):
                package.dependency_closure(runtime, [], launcher)

    def test_runtime_dependency_errors_are_never_converted_to_passing_preflight(self):
        runtime, launcher = self.root / "runtime", self.root / "ValMAX.exe"
        for resolution in ("unresolved", "architecture-mismatch"):
            graph = {"verified": False, "edges": [], "missing": [{"image": "webview2/support.exe",
                "import": "dependency.dll", "resolution": resolution}]}
            with patch.object(package, "runtime_dependency_closure", return_value=graph), patch.object(package, "pe_imports") as launcher_imports:
                with self.assertRaisesRegex(ValueError, resolution):
                    package.dependency_closure(runtime, [], launcher)
                launcher_imports.assert_not_called()

    def subject(self):
        return {"version": "8.9.0", "launcherSha256": "a" * 64,
                "runtimeInventorySha256": "b" * 64, "qmlProofSha256": "c" * 64}

    def evidence(self):
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        timestamp = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        subject = self.subject()
        self.feature_files = {}
        entries = []
        for feature, scenarios in package.RELEASE_SCENARIOS.items():
            value = {"schema": 1, "kind": "native-client-release-functional", "feature": feature,
                     "status": "passed", "complete": True, "testedAt": timestamp, "subject": subject,
                     "clientExecuted": True, "scope": "installed-native-client",
                     "scenarios": dict.fromkeys(scenarios, True), "unresolved": []}
            path = self.root / (feature + ".json")
            path.write_text(json.dumps(value), encoding="utf8")
            self.feature_files[feature] = path
            entries.append({"id": feature, "path": str(path), "sha256": package.sha(path)})
        manifest = self.root / "release-evidence.json"
        manifest.write_text(json.dumps({"schema": 1, "status": "passed", "complete": True, "subject": subject,
            "generatedAt": timestamp, "evidence": entries, "unresolved": []}), encoding="utf8")
        return manifest

    def rewrite_proof(self, manifest, feature, change):
        path = self.feature_files[feature]
        value = json.loads(path.read_text("utf8"))
        change(value)
        path.write_text(json.dumps(value), encoding="utf8")
        references = json.loads(manifest.read_text("utf8"))
        for item in references["evidence"]:
            if item["id"] == feature:
                item["sha256"] = package.sha(path)
        manifest.write_text(json.dumps(references), encoding="utf8")

    def test_compiled_components_and_clean_qml_do_not_imply_full_functionality(self):
        report = package.release_preflight(self.subject())
        self.assertFalse(report["releaseEligible"])
        self.assertFalse(report["featureRuntimeTested"])
        self.assertTrue(all(not value["verified"] for value in report["features"].values()))
        ids = {value["id"] for value in report["blockers"]}
        self.assertTrue({"message_retention", "early_protection", "installed_full_client", "release_evidence_missing"} <= ids)
        self.assertNotIn("chat_export_history", ids)
        self.assertEqual(package.RELEASE_SCENARIOS["chat_export"],
                         ("messages_and_media", "beta_partial_history_disclosed", "progress_and_pacing", "cancel_and_errors"))

    def test_green_references_cannot_clear_current_unresolved_work(self):
        manifest = self.evidence()
        report = package.release_preflight(self.subject(), manifest, now=self.now)
        self.assertTrue(report["evidenceVerified"])
        self.assertFalse(report["releaseEligible"])
        self.assertFalse(report["featureRuntimeTested"])
        self.assertEqual({key for key, _ in package.CURRENT_RELEASE_BLOCKERS} | {"whole_client_materials"},
                         {value["id"] for value in report["blockers"]})

    def test_complete_matching_evidence_contract_after_actual_blockers_resolved(self):
        manifest = self.evidence()
        # Schema behavior only. Mock receipts do not prove a released client,
        # and this test does not package anything or change product statuses.
        with patch.object(package, "CURRENT_RELEASE_BLOCKERS", ()):
            report = package.release_preflight(self.subject(), manifest, now=self.now,
                                               materials={"completeForWholeClient": True, "limitations": []})
        self.assertTrue(report["releaseEligible"])
        self.assertTrue(report["wholeClientMaterialsVerified"])
        self.assertTrue(report["featureRuntimeTested"])
        self.assertEqual(report["blockers"], [])

    def test_complete_installed_evidence_cannot_override_missing_materials(self):
        manifest = self.evidence()
        with patch.object(package, "CURRENT_RELEASE_BLOCKERS", ()):
            for materials in (None, {"completeForWholeClient": False, "limitations": []},
                              {"completeForWholeClient": True, "limitations": ["unresolved"]}):
                report = package.release_preflight(self.subject(), manifest, now=self.now,
                                                   materials=materials)
                self.assertTrue(report["evidenceVerified"])
                self.assertFalse(report["wholeClientMaterialsVerified"])
                self.assertFalse(report["releaseEligible"])
                self.assertIn("whole_client_materials", {item["id"] for item in report["blockers"]})
                with self.assertRaisesRegex(ValueError, "whole_client_materials"):
                    package.require_distribution("user-release", report, installer=True)

    def test_component_proofs_are_not_installed_client_evidence(self):
        manifest = self.evidence()
        self.rewrite_proof(manifest, "chat_export", lambda proof: proof.update(scope="standalone-qt", clientExecuted=False))
        report = package.release_preflight(self.subject(), manifest, now=self.now)
        self.assertFalse(report["evidenceVerified"])
        self.assertFalse(report["features"]["chat_export"]["verified"])
        self.assertTrue(any("component/synthetic" in value["reason"] for value in report["blockers"]))

    def test_missing_duplicate_and_unknown_feature_references_rejected(self):
        manifest = self.evidence()
        original = manifest.read_text("utf8")
        for change in (lambda value: value["evidence"].pop(),
                       lambda value: value["evidence"].__setitem__(1, value["evidence"][0]),
                       lambda value: value["evidence"][0].update(id="ALL_FEATURES")):
            value = json.loads(original)
            change(value)
            manifest.write_text(json.dumps(value), encoding="utf8")
            report = package.release_preflight(self.subject(), manifest, now=self.now)
            self.assertFalse(report["evidenceVerified"])
            self.assertIn("invalid_release_evidence", [item["id"] for item in report["blockers"]])

    def test_changed_and_missing_referenced_proofs_rejected(self):
        manifest = self.evidence()
        path = self.feature_files["mdata"]
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        report = package.release_preflight(self.subject(), manifest, now=self.now)
        self.assertFalse(report["features"]["mdata"]["verified"])
        self.assertTrue(any(item["reason"] == "feature evidence changed" for item in report["blockers"]))
        path.unlink()
        self.assertFalse(package.release_preflight(self.subject(), manifest, now=self.now)["evidenceVerified"])

    def test_exact_payload_hashes_and_version_must_match(self):
        manifest = self.evidence()
        for key in self.subject():
            subject = self.subject()
            subject[key] = "9.0.0" if key == "version" else "d" * 64
            report = package.release_preflight(subject, manifest, now=self.now)
            self.assertFalse(report["evidenceVerified"])
            self.assertTrue(any("does not match this payload" in item["reason"] for item in report["blockers"]))

    def test_individual_feature_evidence_cannot_target_other_payload(self):
        manifest = self.evidence()
        self.rewrite_proof(manifest, "optimization", lambda proof: proof["subject"].update(runtimeInventorySha256="d" * 64))
        report = package.release_preflight(self.subject(), manifest, now=self.now)
        self.assertFalse(report["features"]["optimization"]["verified"])
        self.assertTrue(any("another payload" in item["reason"] for item in report["blockers"]))

    def test_stale_and_future_receipts_rejected(self):
        manifest = self.evidence()
        for offset in (-8, 1):
            stamp = (self.now + timedelta(days=offset)).strftime("%Y-%m-%dT%H:%M:%SZ")
            self.rewrite_proof(manifest, "ghost", lambda proof: proof.update(testedAt=stamp))
            report = package.release_preflight(self.subject(), manifest, now=self.now)
            self.assertFalse(report["features"]["ghost"]["verified"])
            self.assertTrue(any(("stale evidence" if offset < 0 else "future evidence") in item["reason"] for item in report["blockers"]))

    def test_false_and_numeric_scenario_results_do_not_pass(self):
        manifest = self.evidence()
        for value in (False, 1, "passed"):
            self.rewrite_proof(manifest, "notifications", lambda proof: proof["scenarios"].update(pin_chat_redaction=value))
            report = package.release_preflight(self.subject(), manifest, now=self.now)
            self.assertFalse(report["features"]["notifications"]["verified"])

    def test_unresolved_and_partial_feature_proofs_rejected(self):
        manifest = self.evidence()
        original = self.feature_files["retention"].read_bytes()
        for change in (lambda proof: proof.update(complete=False),
                       lambda proof: proof.update(status="partial"),
                       lambda proof: proof.update(unresolved=["not tested"])):
            self.feature_files["retention"].write_bytes(original)
            self.rewrite_proof(manifest, "retention", change)
            self.assertFalse(package.release_preflight(self.subject(), manifest, now=self.now)["features"]["retention"]["verified"])

    def test_duplicate_json_keys_and_nonfinite_values_are_not_receipts(self):
        path = self.root / "invalid.json"
        for content in ('{"complete":false,"complete":true}', '{"result":NaN}'):
            path.write_text(content, encoding="utf8")
            with self.assertRaises(ValueError):
                package.read_json(path)

    def test_hardlinked_evidence_rejected(self):
        manifest = self.evidence()
        path = self.feature_files["mdata"]
        os.link(path, self.root / "mdata-alias.json")
        self.assertFalse(package.release_preflight(self.subject(), manifest, now=self.now)["features"]["mdata"]["verified"])

    def test_preflight_lists_current_blockers_without_runtime_or_packaging(self):
        output = self.root / "preflight.json"
        argv = ["package_native_client.py", "--preflight-only", "--preflight-report", str(output)]
        with patch.object(sys, "argv", argv), patch.object(package.shutil, "copyfile") as copy, \
             patch.object(package.subprocess, "run") as compile_installer:
            package.main()
            copy.assert_not_called()
            compile_installer.assert_not_called()
        report = json.loads(output.read_text("utf8"))
        self.assertEqual(report["distributionMode"], "user-release")
        self.assertFalse(report["releaseEligible"])
        self.assertFalse(report["artifactValidation"]["verified"])
        self.assertIn("message_retention", [item["id"] for item in report["blockers"]])
        self.assertIn("product_version_missing", [item["id"] for item in report["blockers"]])
        self.assertIsNone(report["requestedVersion"])

    def test_versionless_preflight_still_audits_artifacts(self):
        inputs = self.staging_inputs()
        output = self.root / "no-version.json"
        argv = ["package_native_client.py", "--preflight-only", "--preflight-report", str(output),
                "--runtime", str(inputs["runtime"]), "--qml-build", str(self.root / "qml"),
                "--launcher-proof", str(self.root / "launcher-proof.json")]
        with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs", return_value=inputs) as audit, \
             patch.object(package.shutil, "copyfile") as copy, patch.object(package.subprocess, "run") as compiler:
            package.main()
            audit.assert_called_once()
            copy.assert_not_called()
            compiler.assert_not_called()
        report = json.loads(output.read_text("utf8"))
        self.assertTrue(report["artifactValidation"]["verified"])
        self.assertEqual(report["artifactValidation"]["identity"]["launcherSha256"], inputs["launcherHash"])
        self.assertEqual(report["artifactValidation"]["identity"]["runtimeInventorySha256"], package.inventory_digest(inputs["rows"]))
        self.assertFalse(report["releaseEligible"])
        self.assertIsNone(report["subject"])
        self.assertIn("product_version_missing", [item["id"] for item in report["blockers"]])
        self.assertNotIn("exact_payload_missing", [item["id"] for item in report["blockers"]])

    def test_packaging_requires_version_and_developer_mode_never_compiles_installer(self):
        argv = ["package_native_client.py", "--distribution", "developer-staging", "--output", str(self.root / "stage")]
        with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs") as audit, \
             patch.object(package.subprocess, "run") as compiler:
            with self.assertRaisesRegex(ValueError, "product version is required"):
                package.main()
            audit.assert_not_called()
            compiler.assert_not_called()
        with self.assertRaisesRegex(ValueError, "developer staging cannot compile"):
            package.require_distribution("developer-staging", {"releaseEligible": True, "blockers": []}, installer=True)

    def staging_inputs(self):
        runtime = self.root / "runtime"
        (runtime / "mod").mkdir(parents=True)
        client = runtime / "mod/max.exe"
        client.write_bytes(b"synthetic-native-client")
        launcher = self.root / "ValMAX.exe"
        launcher.write_bytes(b"synthetic-native-launcher")
        return {"runtime": runtime, "qml": self.root / "qml", "rows": [{"path": "mod/max.exe", "size": client.stat().st_size, "sha256": package.sha(client)}],
                "launcher": launcher, "launcherHash": package.sha(launcher), "qmlResult": {"proofSha256": "c" * 64}, "closure": [],
                "hookTargetIdentity": {"assemblyReceiptSha256": "d" * 64,
                    "identity": {"verified": True, "activationVerified": False, "unitFixtureOnly": True}}}

    def test_default_user_release_and_unready_installer_fail_before_copying(self):
        inputs = self.staging_inputs()
        for index, distribution in enumerate(("user-release", "developer-staging")):
            output = self.root / ("blocked-" + str(index))
            argv = ["package_native_client.py", "--version", "8.9.0", "--runtime", str(inputs["runtime"]),
                    "--qml-build", str(self.root / "qml"), "--launcher-proof", str(self.root / "launcher-proof.json"), "--output", str(output)]
            if distribution == "developer-staging":
                argv += ["--distribution", distribution, "--iscc", str(self.root / "ISCC.exe")]
            with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs", return_value=inputs), \
                 patch.object(package.shutil, "copyfile") as copy, patch.object(package.subprocess, "run") as compiler:
                with self.assertRaisesRegex(ValueError, "complete user release is blocked"):
                    package.main()
                copy.assert_not_called()
                compiler.assert_not_called()
            self.assertTrue((output / "packaging-preflight.json").is_file())
            self.assertFalse((output / "payload").exists())

    def test_explicit_developer_staging_is_honest_and_not_an_installer(self):
        inputs = self.staging_inputs()
        output = self.root / "developer-stage"
        argv = ["package_native_client.py", "--version", "8.9.0", "--runtime", str(inputs["runtime"]),
                "--qml-build", str(self.root / "qml"), "--launcher-proof", str(self.root / "launcher-proof.json"),
                "--output", str(output), "--distribution", "developer-staging"]
        with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs", return_value=inputs), \
             patch.object(package.pins, "inventory", return_value=inputs["rows"]), patch.object(package.subprocess, "run") as compiler:
            package.main()
            compiler.assert_not_called()
        payload = output / "payload"
        manifest = json.loads((payload / "native-package.json").read_text("utf8"))
        self.assertEqual(manifest["distributionMode"], "developer-staging")
        self.assertFalse(manifest["releaseEligible"])
        self.assertFalse(manifest["featureRuntimeTested"])
        self.assertFalse(manifest["wholeClientMaterialsVerified"])
        self.assertIsNone(manifest["materialsReceiptSha256"])
        self.assertEqual(manifest["hookTargetIdentity"], inputs["hookTargetIdentity"])
        self.assertTrue(manifest["releaseBlockers"])
        self.assertEqual({row["path"] for row in manifest["ancillaryFiles"]}, {"ValMAX-инструкция.txt", "LICENSE", "THIRD_PARTY_NOTICES.md"})
        for row in manifest["ancillaryFiles"]:
            self.assertEqual((payload / row["path"]).read_bytes(), package.ANCILLARY_FILES[row["path"]].read_bytes())
            self.assertEqual((payload / row["path"]).stat().st_size, row["size"])
            self.assertEqual(package.sha(payload / row["path"]), row["sha256"])
        marker = (payload / "native-release-state.iss").read_text("utf8")
        self.assertIn('#define NativeDistributionMode "developer-staging"', marker)
        self.assertIn('#define NativeReleaseEligible 0', marker)
        self.assertIn('#define NativePackageManifestSha256 "' + package.sha(payload / "native-package.json") + '"', marker)
        receipt = json.loads((output / "build-receipt.json").read_text("utf8"))
        self.assertFalse(receipt["installerCompiled"])
        self.assertEqual(receipt["ancillaryFiles"], manifest["ancillaryFiles"])

    def preview_report(self):
        subject = {"version": "8.9.0-native.preview.1", "launcherSha256": "a" * 64,
                   "runtimeInventorySha256": "b" * 64, "qmlProofSha256": "c" * 64}
        report = package.release_preflight(subject, mode="local-preview")
        report["artifactValidation"] = {"verified": True}
        report["nativeQmlCompatibility"] = {"verifiedNoLegacyReferences": True}
        return report

    def test_local_preview_accepts_only_explicit_known_product_gaps(self):
        report = self.preview_report()
        package.require_distribution("local-preview", report, installer=True)
        self.assertFalse(report["releaseEligible"])
        self.assertFalse(report["featureRuntimeTested"])
        self.assertEqual({item["id"] for item in report["blockers"]},
                         {key for key, _ in package.CURRENT_RELEASE_BLOCKERS}
                         | {"whole_client_materials", "release_evidence_missing"})
        for bad in ("artifact_validation", "legacy_local_helper", "invalid_release_evidence", "arbitrary_gap"):
            changed = copy.deepcopy(report)
            changed["blockers"].append({"id": bad, "reason": "owned unit-only rejection"})
            with self.subTest(blocker=bad), self.assertRaisesRegex(ValueError, "known unresolved product work"):
                package.require_distribution("local-preview", changed, installer=True)

    def test_local_preview_refuses_false_readiness_stale_artifacts_and_cleared_gaps(self):
        changes = [lambda r: r.update(releaseEligible=True), lambda r: r.update(featureRuntimeTested=True),
                   lambda r: r["artifactValidation"].update(verified=False),
                   lambda r: r["nativeQmlCompatibility"].update(verifiedNoLegacyReferences=False),
                   lambda r: r.update(blockers=r["blockers"][1:]),
                   lambda r: r["blockers"].append(dict(r["blockers"][0])),
                   lambda r: r["subject"].update(version="8.9.0")]
        for index, change in enumerate(changes):
            report = self.preview_report()
            change(report)
            with self.subTest(index=index), self.assertRaises(ValueError):
                package.require_distribution("local-preview", report, installer=True)

    def test_local_preview_requires_visible_version_and_no_full_release_evidence(self):
        base = ["package_native_client.py", "--distribution", "local-preview", "--output", str(self.root / "preview")]
        for args in (["--version", "8.9.0"],
                     ["--version", "8.9.0-native.preview.1", "--release-evidence", str(self.root / "unused.json")]):
            with self.subTest(args=args), patch.object(sys, "argv", base + args), \
                 patch.object(package, "packaging_inputs") as audit, self.assertRaises(ValueError):
                package.main()
            audit.assert_not_called()

    def test_local_preview_manifest_and_warning_preserve_the_unfinished_scope(self):
        inputs = self.staging_inputs()
        output = self.root / "local-preview"
        argv = ["package_native_client.py", "--version", "8.9.0-native.preview.1", "--runtime", str(inputs["runtime"]),
                "--qml-build", str(self.root / "qml"), "--launcher-proof", str(self.root / "launcher-proof.json"),
                "--output", str(output), "--distribution", "local-preview"]
        with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs", return_value=inputs), \
             patch.object(package.pins, "inventory", return_value=inputs["rows"]), patch.object(package.subprocess, "run") as compiler:
            package.main()
            compiler.assert_not_called()
        payload = output / "payload"
        manifest = json.loads((payload / "native-package.json").read_text("utf8"))
        self.assertEqual(manifest["distributionMode"], "local-preview")
        self.assertTrue(manifest["localPreview"])
        self.assertFalse(manifest["releaseEligible"])
        self.assertFalse(manifest["featureRuntimeTested"])
        self.assertEqual(manifest["previewInformation"], {
            "path": "native-preview.txt", "sha256": package.sha(payload / "native-preview.txt")})
        warning = (payload / "native-preview.txt").read_text("utf-8-sig")
        for text in ("8.9.0-native.preview.1", "Сохранение удалённых", "Экспорт чата", "Режим призрака", "local native preview"):
            self.assertIn(text, warning)
        self.assertIn("исходное время", warning)
        self.assertIn("старые записи без него", warning)
        marker = (payload / "native-release-state.iss").read_text("utf8")
        self.assertIn('#define NativeDistributionMode "local-preview"', marker)
        self.assertIn('#define NativeReleaseEligible 0', marker)
        self.assertIn('#define NativePreviewVersion "8.9.0-native.preview.1"', marker)
        self.assertIn('#define NativePreviewInfoSha256 "' + package.sha(payload / "native-preview.txt") + '"', marker)
        self.assertEqual(len(manifest["releaseBlockers"]), len(package.CURRENT_RELEASE_BLOCKERS) + 2)

    def test_local_preview_marker_refuses_changed_information(self):
        payload = self.root / "preview-marker"
        payload.mkdir()
        info = payload / "native-preview.txt"
        info.write_bytes(package.preview_information("8.9.0-native.preview.1"))
        manifest = {"distributionMode": "local-preview", "version": "8.9.0-native.preview.1", "localPreview": True,
                    "previewInformation": {"path": "native-preview.txt", "sha256": package.sha(info)}}
        (payload / "native-package.json").write_text(json.dumps(manifest))
        info.write_bytes(b"changed unit-only warning")
        with self.assertRaisesRegex(ValueError, "preview information changed"):
            package.write_release_state(payload, manifest, self.preview_report())
        self.assertFalse((payload / "native-release-state.iss").exists())

    def test_ancillary_changed_since_inventory_is_not_shipped(self):
        source = self.root / "guide.txt"
        source.write_bytes(b"initial document")
        payload = self.root / "payload"
        payload.mkdir()
        with patch.object(package, "ANCILLARY_FILES", {"guide.txt": source}):
            rows = package.ancillary_inventory()
            source.write_bytes(b"unexpected changed document")
            with self.assertRaisesRegex(ValueError, "ancillary document changed"):
                package.copy_ancillary(payload, rows)
        self.assertEqual(list(payload.iterdir()), [])

    def test_ancillary_file_modified_during_copy_is_rejected(self):
        source = self.root / "guide.txt"
        source.write_bytes(b"initial document")
        payload = self.root / "payload"
        payload.mkdir()
        real_copy = package.shutil.copyfile
        def changed_copy(source, target):
            real_copy(source, target)
            target.write_bytes(b"damaged output")
        with patch.object(package, "ANCILLARY_FILES", {"guide.txt": source}):
            rows = package.ancillary_inventory()
            with patch.object(package.shutil, "copyfile", side_effect=changed_copy):
                with self.assertRaisesRegex(ValueError, "ancillary copy mismatch"):
                    package.copy_ancillary(payload, rows)


if __name__ == "__main__":
    unittest.main(verbosity=2)
