"""Native packaging regressions: server leftovers, stale UI, dependency gaps."""
import hashlib
import io
import json
import copy
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import tarfile
import unittest
import zipfile
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
import os

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "native/tooling"))
import package_native_client as package
import verify_public_stable_core as stable_publication


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

    def evidence(self, subject=None):
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        timestamp = self.now.strftime("%Y-%m-%dT%H:%M:%SZ")
        subject = subject or self.subject()
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

    def channel_proof(self, channel):
        path = self.root / "native-qml-proof.json"
        path.write_text(json.dumps({"schema": 1, "nativeRuntime": True,
                                    "uiReleaseChannel": channel}), encoding="utf8")
        subject = self.subject()
        subject["qmlProofSha256"] = package.sha(path)
        return path, subject

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
        self.assertEqual({key for key, _ in package.CURRENT_RELEASE_BLOCKERS}
                         | {package.MAX_RIGHTS_ATTESTATION_BLOCKER[0], "whole_client_materials", "stable_ui_channel"},
                         {value["id"] for value in report["blockers"]})

    def test_publisher_attestation_clears_only_the_rights_blocker(self):
        manifest = self.evidence()
        subject = self.subject()
        without = package.release_preflight(subject, manifest, now=self.now)
        with_attestation = package.release_preflight(
            subject, manifest, now=self.now, max_rights_attested=True)
        self.assertFalse(without["maxRightsAttested"])
        self.assertTrue(with_attestation["maxRightsAttested"])
        self.assertIn("max_redistribution_attestation", {item["id"] for item in without["blockers"]})
        self.assertEqual({item["id"] for item in without["blockers"]}
                         - {"max_redistribution_attestation"},
                         {item["id"] for item in with_attestation["blockers"]})
        self.assertFalse(with_attestation["releaseEligible"])
        self.assertFalse(with_attestation["wholeClientMaterialsVerified"])

    def test_user_release_cli_records_attestation_without_claiming_readiness(self):
        destination = self.root / "attested-preflight.json"
        argv = ["package_native_client.py", "--version", "8.9.0",
                "--distribution", "user-release", "--max-rights-attested",
                "--preflight-only", "--preflight-report", str(destination)]
        with patch.object(sys, "argv", argv):
            package.main()
        report = json.loads(destination.read_text("utf8"))
        self.assertTrue(report["maxRightsAttested"])
        self.assertFalse(report["releaseEligible"])
        self.assertNotIn("max_redistribution_attestation",
                         {item["id"] for item in report["blockers"]})
        self.assertIn("whole_client_materials", {item["id"] for item in report["blockers"]})

    def test_user_release_cli_reads_channel_from_bound_qml_proof(self):
        inputs = self.staging_inputs()
        qml = inputs["qml"]
        qml.mkdir()
        proof = qml / "native-qml-proof.json"
        proof.write_text(json.dumps({"schema": 1, "nativeRuntime": True,
                                     "uiReleaseChannel": "beta"}), encoding="utf8")
        inputs["qmlResult"]["proofSha256"] = package.sha(proof)
        destination = self.root / "channel-preflight.json"
        argv = ["package_native_client.py", "--version", "8.9.0", "--preflight-only",
                "--preflight-report", str(destination), "--runtime", str(inputs["runtime"]),
                "--qml-build", str(qml), "--launcher-proof", str(self.root / "launcher-proof.json")]
        with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs", return_value=inputs):
            package.main()
        report = json.loads(destination.read_text("utf8"))
        self.assertEqual(report["subject"]["qmlProofSha256"], package.sha(proof))
        self.assertFalse(report["stableUiChannelVerified"])
        self.assertEqual([item["id"] for item in report["blockers"]].count("stable_ui_channel"), 1)
        self.assertIn("stable-candidate native QML release channel required",
                      next(item["reason"] for item in report["blockers"] if item["id"] == "stable_ui_channel"))

    def test_complete_matching_evidence_contract_after_actual_blockers_resolved(self):
        qml_proof, subject = self.channel_proof("stable-candidate")
        manifest = self.evidence(subject)
        # Schema behavior only. Mock receipts do not prove a released client,
        # and this test does not package anything or change product statuses.
        with patch.object(package, "CURRENT_RELEASE_BLOCKERS", ()):
            report = package.release_preflight(subject, manifest, now=self.now,
                                               materials={"completeForWholeClient": True, "limitations": []},
                                               max_rights_attested=True, qml_proof_path=qml_proof)
        self.assertTrue(report["releaseEligible"])
        self.assertTrue(report["stableUiChannelVerified"])
        self.assertTrue(report["wholeClientMaterialsVerified"])
        self.assertTrue(report["featureRuntimeTested"])
        self.assertEqual(report["blockers"], [])

    def test_stable_ui_channel_and_exact_qml_subject_are_required(self):
        qml_proof, subject = self.channel_proof("beta")
        manifest = self.evidence(subject)
        options = {"now": self.now, "materials": {"completeForWholeClient": True, "limitations": []},
                   "max_rights_attested": True, "qml_proof_path": qml_proof}
        with patch.object(package, "CURRENT_RELEASE_BLOCKERS", ()):
            beta = package.release_preflight(subject, manifest, **options)
            self.assertTrue(beta["evidenceVerified"])
            self.assertFalse(beta["releaseEligible"])
            self.assertFalse(beta["stableUiChannelVerified"])
            self.assertEqual([item["id"] for item in beta["blockers"]], ["stable_ui_channel"])

            qml_proof, stable_subject = self.channel_proof("stable-candidate")
            stale_subject = package.release_preflight(subject, manifest, **options)
            self.assertEqual([item["id"] for item in stale_subject["blockers"]], ["stable_ui_channel"])
            self.assertIn("differs from exact release subject", stale_subject["blockers"][0]["reason"])

            stable_manifest = self.evidence(stable_subject)
            stable = package.release_preflight(stable_subject, stable_manifest, **options)
            self.assertTrue(stable["stableUiChannelVerified"])
            self.assertTrue(stable["releaseEligible"])
            self.assertEqual(stable["blockers"], [])

            qml_proof.write_text("[]", encoding="utf8")
            malformed_subject = self.subject()
            malformed_subject["qmlProofSha256"] = package.sha(qml_proof)
            malformed_manifest = self.evidence(malformed_subject)
            malformed = package.release_preflight(malformed_subject, malformed_manifest, **options)
            self.assertEqual([item["id"] for item in malformed["blockers"]], ["stable_ui_channel"])

    def test_complete_installed_evidence_cannot_override_missing_materials(self):
        manifest = self.evidence()
        with patch.object(package, "CURRENT_RELEASE_BLOCKERS", ()):
            for materials in (None, {"completeForWholeClient": False, "limitations": []},
                              {"completeForWholeClient": True, "limitations": ["unresolved"]}):
                report = package.release_preflight(self.subject(), manifest, now=self.now,
                                                   materials=materials, max_rights_attested=True)
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

    def beta_subject(self, inputs=None):
        if inputs is None:
            self.beta_launcher = self.root / "ValMAX.exe"
            self.beta_launcher.write_bytes(b"owned installed outer launcher fixture")
            return {"version": "8.9.0-native.preview.1", "launcherSha256": package.sha(self.beta_launcher),
                    "runtimeInventorySha256": "b" * 64, "qmlProofSha256": "c" * 64}
        self.beta_launcher = inputs["launcher"]
        return {"version": "8.9.0-native.preview.1", "launcherSha256": inputs["launcherHash"],
                "runtimeInventorySha256": package.inventory_digest(inputs["rows"]),
                "qmlProofSha256": inputs["qmlResult"]["proofSha256"]}

    def beta_smoke(self, subject):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        smoke = self.root / "installed-beta-smoke.json"
        smoke.write_text(json.dumps({"schema": 1, "kind": "native-public-beta-installed-smoke",
            "status": "passed", "subject": subject,
            "testedAt": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clientExecuted": True, "scope": "installed-native-client",
            "launchMode": "outer-installed-launcher",
            "installedLauncher": {"path": str(self.beta_launcher), "sha256": subject["launcherSha256"]},
            "scenarios": dict.fromkeys(package.PUBLIC_BETA_SCENARIOS, True),
            "unresolvedCritical": []}), encoding="utf8")
        return smoke

    def beta_material_summary(self):
        return {"verified": True, "materialsReceiptSha256": "d" * 64,
                "assemblyReceiptSha256": "e" * 64, "evidenceSha256": "f" * 64,
                "ffmpegReferenceBundleManifestSha256": "a" * 64,
                "ffmpegAuditSha256": "b" * 64,
                "ffmpegRuntimeManifestSha256": "c" * 64,
                "ffmpegSignatureVerificationSha256": "d" * 64,
                "completeForWholeClient": False, "exactCorrespondingSourceProven": False,
                "knownLimitations": [{"id": item["id"], "reason": item["statusText"]}
                                     for item in package.PUBLIC_BETA_MATERIAL_LIMITATIONS],
                "sourceBundles": [{"version": version,
                    "path": "materials/FFmpeg/ffmpeg-" + version + ".tar.xz",
                    "size": 1, "sha256": "1" * 64,
                    "signature": {"path": "materials/FFmpeg/ffmpeg-" + version + ".tar.xz.asc",
                                  "size": 1, "sha256": "3" * 64}}
                    for version in ("7.1.3", "7.1.5")],
                "buildConfigurations": {"path": "materials/FFmpeg/build-configurations.json",
                    "size": 1, "sha256": "2" * 64}}

    def beta_material_fixture(self, inputs):
        runtime = inputs["runtime"]
        for name in sorted(package.PUBLIC_BETA_REQUIRED_NOTICE_PATHS):
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(("owned notice: " + name).encode())
            inputs["rows"].append({"path": name, "size": target.stat().st_size, "sha256": package.sha(target)})
        images, probes, matches = {}, {}, {}
        for location, prefix in (("p14Main", "mod/"), ("p14Service", "mod/max-service/")):
            images[location], matches[location] = {}, {}
            libraries = {}
            for name in sorted(package.PUBLIC_BETA_FFMPEG_NAMES):
                target = runtime / prefix / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(("owned DLL fixture: " + prefix + name).encode())
                row = {"path": prefix + name, "size": target.stat().st_size, "sha256": package.sha(target)}
                inputs["rows"].append(row)
                images[location][name] = {"sha256": row["sha256"], "sizeBytes": row["size"],
                                          "productVersion": "n7.1.3" if location == "p14Main" else "7.1.5"}
                libraries[name] = {"configuration": "--disable-gpl --disable-nonfree --enable-shared",
                                   "license": "LGPL version 2.1 or later"}
                matches[location][name] = True
            probes[location] = {"libraries": libraries}
        audit = self.root / "ffmpeg-audit.json"
        audit.write_text(json.dumps({"originalMax": {"p14ImagesMatchCab": matches},
                                     "images": images, "capabilityProbes": probes}), encoding="utf8")
        bundle = self.root / "ffmpeg-bundle"
        (bundle / "sources").mkdir(parents=True)
        (bundle / "manifests").mkdir()
        (bundle / "verification").mkdir()
        (bundle / "manifests/ffmpeg-audit.json").write_bytes(audit.read_bytes())
        source_refs = []
        fingerprint = "FCF986EA15E6E293A5644F10B4322F04D67658D8"
        for version in ("7.1.3", "7.1.5"):
            source = self.root / ("ffmpeg-" + version + ".tar.xz")
            with tarfile.open(source, "w:xz") as archive:
                data = ("#define FF_VERSION \"" + version + "\"\n").encode()
                info = tarfile.TarInfo("ffmpeg-" + version + "/libavcodec/version.h")
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            signature = self.root / (source.name + ".asc")
            signature.write_bytes(("owned synthetic signature " + version).encode())
            (bundle / "sources" / source.name).write_bytes(source.read_bytes())
            (bundle / "sources" / signature.name).write_bytes(signature.read_bytes())
            source_refs.append({"version": version, "officialUrl": "https://ffmpeg.org/releases/" + source.name,
                                "path": str(source), "bundleRelativePath": "sources/" + source.name,
                                "bytes": source.stat().st_size, "sha256": package.sha(source),
                                "signatureOfficialUrl": "https://ffmpeg.org/releases/" + source.name + ".asc",
                                "signaturePath": str(signature), "signatureSha256": package.sha(signature),
                                "signatureVerified": True, "signingKeyFingerprint": fingerprint,
                                "correspondenceToValMAXDllsProven": False})
        runtime_images = []
        for location, prefix in (("p14Main", "mod/"), ("p14Service", "mod/max-service/")):
            for name in sorted(package.PUBLIC_BETA_FFMPEG_NAMES):
                runtime_path = prefix + name
                row = next(row for row in inputs["rows"] if row["path"] == runtime_path)
                runtime_images.append({"runtimePath": runtime_path, "bytes": row["size"],
                    "sha256": row["sha256"], "productVersion": images[location][name]["productVersion"],
                    "configuration": probes[location]["libraries"][name]["configuration"],
                    "reportedLicense": probes[location]["libraries"][name]["license"],
                    "matchesLocalMaxMsiCab": True, "exactCorrespondingSourceProven": False})
        runtime_manifest = bundle / "manifests/valmax-ffmpeg-runtime.json"
        runtime_manifest.write_text(json.dumps({"schema": "valmax.ffmpeg-runtime-reference.v1",
            "imageCount": 10, "exactCorrespondingSourceProven": False, "images": runtime_images}), encoding="utf8")
        signature_record = bundle / "verification/gpg-verification.json"
        signature_record.write_text(json.dumps({"schema": "valmax.ffmpeg-upstream-signatures.v1",
            "expectedFingerprintFromOfficialDownloadPage": fingerprint,
            "signatures": [{"version": version, "signingKeyFingerprint": fingerprint,
                            "validSigStatus": "[GNUPG:] VALIDSIG " + fingerprint}
                           for version in ("7.1.3", "7.1.5")]}), encoding="utf8")
        bundle_manifest = bundle / "manifest.json"
        bundle_manifest.write_text(json.dumps({
            "schema": "valmax.ffmpeg-public-beta-reference-bundle.v1",
            "completeForWholeClient": False, "ffmpegExactCorrespondingSourceProven": False,
            "ffmpegSourceProvenanceUnverified": True,
            "knownLimitationIds": ["ffmpeg_source_provenance_unverified",
                                   "ffmpeg_exact_patches_unconfirmed", "whole_client_materials_incomplete"],
            "sourceBundles": source_refs,
            "ffmpegAudit": {"path": str(audit), "sha256": package.sha(audit),
                            "bundleRelativePath": "manifests/ffmpeg-audit.json", "copySha256": package.sha(audit)},
            "runtimeManifest": {"bundleRelativePath": "manifests/valmax-ffmpeg-runtime.json",
                                "sha256": package.sha(runtime_manifest), "imageCount": 10},
            "signatureVerification": {"bundleRelativePath": "verification/gpg-verification.json",
                                      "sha256": package.sha(signature_record)}}), encoding="utf8")
        materials = {"completeForWholeClient": False,
            "limitations": [item["statusText"] for item in package.PUBLIC_BETA_MATERIAL_LIMITATIONS],
            "helperMaterialsKeptIndependent": True,
            "additionalQtMaterialProof": {"runtimeImagesMatched": 12, "licenseTexts": 48, "runtimeDllsMatched": True},
            "additionalQtImageFormatsMaterialProof": {"runtimeImagesMatched": 5, "copiedMaterialFiles": 13,
                                                      "wholeClientMaterialsComplete": False},
            "baselineUpstreamNoticeProof": {"copiedNoticeFiles": 8,
                "uncoveredRuntimeImages": ["swscale-8.dll"], "ffmpegExactCorrespondingSourceProven": False},
            "gumTaggedNoticeProof": {"taggedNoticeCopied": True},
            "nativeRustSourceGraphProof": {"sourceArchives": 70, "licenseTexts": 139,
                "resolvedDependencyGraphBoundToCoreProof": True, "wholeClientMaterialsComplete": False},
            "qtLottieRuntimeOmission": {"omittedRuntimeFiles": ["fixture-" + str(i) for i in range(7)]},
            "fixedBrowserFilesUnmodified": True}
        inputs["materialBinding"] = {"status": materials, "sha256": "d" * 64}
        subject = self.beta_subject(inputs)
        evidence = self.root / "beta-materials-evidence.json"
        evidence.write_text(json.dumps({"schema": 1, "kind": "native-public-beta-materials",
            "subject": subject, "materialsReceiptSha256": "d" * 64,
            "assemblyReceiptSha256": inputs["hookTargetIdentity"]["assemblyReceiptSha256"],
            "knownLimitationIds": [item["id"] for item in package.PUBLIC_BETA_MATERIAL_LIMITATIONS],
            "ffmpegReferenceBundle": {"path": str(bundle_manifest), "sha256": package.sha(bundle_manifest)},
            "exactCorrespondingSourceProven": False,
            "completeForWholeClient": False}), encoding="utf8")
        return subject, evidence

    def beta_report(self, subject, smoke, *, materials=None, rights=True,
                    artifact_verified=True, qml_verified=True, identity=None, beta_materials=None):
        identity = identity if identity is not None else {key: subject[key] for key in package.ARTIFACT_IDENTITY_KEYS}
        materials = materials if materials is not None else {"completeForWholeClient": False,
            "limitations": [item["statusText"] for item in package.PUBLIC_BETA_MATERIAL_LIMITATIONS]}
        beta_materials = beta_materials if beta_materials is not None else self.beta_material_summary()
        report = package.release_preflight(subject, smoke, mode="public-beta", now=self.now,
                    artifact_identity=identity, materials=materials,
                    artifact_verified=artifact_verified, qml_compatibility_verified=qml_verified,
                    max_rights_attested=rights, beta_materials=beta_materials)
        report["artifactValidation"] = {"verified": artifact_verified, "identity": identity,
            "materialsReceiptSha256": beta_materials["materialsReceiptSha256"],
            "hookTargetIdentity": {"assemblyReceiptSha256": beta_materials["assemblyReceiptSha256"]}}
        report["nativeQmlCompatibility"] = {"verifiedNoLegacyReferences": qml_verified}
        return report

    def test_public_beta_installed_core_evidence_is_distinct_from_stable_regression(self):
        subject = self.beta_subject()
        smoke = self.beta_smoke(subject)
        report = self.beta_report(subject, smoke)
        self.assertTrue(report["releaseEligible"])
        self.assertTrue(report["betaCoreTested"])
        self.assertTrue(report["evidenceVerified"])
        self.assertFalse(report["featureRuntimeTested"])
        self.assertFalse(report["wholeClientMaterialsVerified"])
        self.assertTrue(report["materialsDocumentedForBeta"])
        self.assertEqual(report["blockers"], [])
        self.assertEqual(report["installedSmokeSha256"], package.sha(smoke))
        self.assertEqual({item["id"] for item in report["knownLimitations"]},
                         {"retention_scope", "chat_export_history", "early_protection", "full_feature_regression"}
                         | {item["id"] for item in package.PUBLIC_BETA_MATERIAL_LIMITATIONS})
        package.require_distribution("public-beta", report, installer=True)

    def candidate_receipt_fixture(self, installed_package, version):
        folder = self.root / "candidate-package"
        payload = folder / "payload"
        release = folder / "release"
        payload.mkdir(parents=True)
        release.mkdir()
        (payload / "native-package.json").write_bytes(installed_package.read_bytes())
        setup = release / ("ValMAX-" + version + "-Setup.exe")
        setup.write_bytes(b"owned synthetic Inno Setup fixture")
        receipt = folder / "build-receipt.json"
        receipt.write_text(json.dumps({"installerCompiled": True,
            "distributionMode": "local-preview", "releaseEligible": False,
            "manifestSha256": package.sha(installed_package), "payload": str(payload),
            "installer": str(setup), "installerSha256": package.sha(setup),
            "packagedAt": (self.now - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")}),
            encoding="utf8")
        return {"path": str(receipt), "sha256": package.sha(receipt)}

    def stable_idle_health_fixture(self):
        process = {"pid": 4242,
                   "createdAt": (self.now - timedelta(minutes=55)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "path": str(self.root / "runtime/mod/max.exe")}
        expected = {"error": "ok", "ready": True, "captureActive": True,
                    "failures": 0, "process": process}
        return {"startedAt": (self.now - timedelta(minutes=50)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "checkedAt": (self.now - timedelta(minutes=39)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "start": expected, "end": {**expected, "process": dict(process)}}

    def stable_final_health_fixture(self):
        process = self.stable_idle_health_fixture()["start"]["process"]
        status = {"error": "ok", "ready": True, "captureActive": True,
                  "failures": 0, "queued": 0, "inFlight": 0, "pending": 0,
                  "captured": 4, "marked": 0, "missing": 0, "missed": 1,
                  "routeFailures": 0, "lastScopeFailureReason": 0,
                  "lastInvalidSenderReason": 0, "boundedSkips": 1}
        return {"startedAt": (self.now - timedelta(minutes=37)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "trafficAt": (self.now - timedelta(minutes=36)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "checkedAt": (self.now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "before": {**status, "process": dict(process)},
                "traffic": {**status, "captured": 6, "process": dict(process)},
                "end": {**status, "captured": 6, "process": dict(process)}}

    def stable_core_fixture(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        launcher = self.root / "ValMAX.exe"
        launcher.write_bytes(b"owned installed outer launcher fixture")
        runtime = self.root / "runtime"
        for name in sorted(package.pins.REQUIRED | package.pins.FIXED_REQUIRED):
            target = runtime / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(("owned installed runtime fixture: " + name).encode())
        rows = package.pins.inventory(runtime)
        qml_folder = self.root / "qml-proof"
        qml_folder.mkdir()
        proof_path = qml_folder / "native-qml-proof.json"
        proof_path.write_text(json.dumps({"schema": 1, "nativeRuntime": True,
                                          "uiReleaseChannel": "beta"}), encoding="utf8")
        subject = {"version": "8.9.0", "launcherSha256": package.sha(launcher),
                   "runtimeInventorySha256": package.inventory_digest(rows),
                   "qmlProofSha256": package.sha(proof_path)}
        installed_package = self.root / "native-package.json"
        installed_package.write_text(json.dumps({"version": "8.9.0-native.preview.24",
            "distributionMode": "local-preview", "launcherSha256": subject["launcherSha256"],
            "runtimeFiles": rows, "qml": {"proofSha256": subject["qmlProofSha256"]}}),
            encoding="utf8")
        smoke = self.root / "installed-stable-core-smoke.json"
        smoke.write_text(json.dumps({"schema": 1,
            "kind": "native-public-stable-core-installed-smoke", "status": "passed",
            "subject": subject, "testedAt": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clientExecuted": True, "scope": "installed-native-client",
            "launchMode": "outer-installed-launcher",
            "installedLauncher": {"path": str(launcher), "sha256": subject["launcherSha256"]},
            "installedPackage": {"path": str(installed_package),
                                 "sha256": package.sha(installed_package),
                                 "version": "8.9.0-native.preview.24",
                                 "distributionMode": "local-preview"},
            "candidatePackage": self.candidate_receipt_fixture(
                installed_package, "8.9.0-native.preview.24"),
            "retentionIdleHealth": self.stable_idle_health_fixture(),
            "retentionFinalHealth": self.stable_final_health_fixture(),
            "scenarios": dict.fromkeys(package.PUBLIC_STABLE_CORE_SCENARIOS, True),
            "unresolvedCritical": []}), encoding="utf8")
        return subject, smoke, proof_path, runtime

    def stable_core_report(self, subject, smoke, qml_proof_path, *, rights=True,
                           registration=None):
        summary = self.beta_material_summary()
        identity = {key: subject[key] for key in package.ARTIFACT_IDENTITY_KEYS}
        registration = registration if registration is not None else {
            "path": str(self.root), "version": "8.9.0-native.preview.24"}
        with patch.object(package, "registered_installation", return_value=registration):
            report = package.release_preflight(subject, smoke, mode="public-stable-core",
                now=self.now, artifact_identity=identity, artifact_verified=True,
                qml_compatibility_verified=True, max_rights_attested=rights,
                materials={"completeForWholeClient": False,
                           "limitations": [item["statusText"] for item in package.PUBLIC_BETA_MATERIAL_LIMITATIONS]},
                beta_materials=summary, qml_proof_path=qml_proof_path)
        report["artifactValidation"] = {"verified": True, "identity": identity,
            "materialsReceiptSha256": summary["materialsReceiptSha256"],
            "hookTargetIdentity": {"assemblyReceiptSha256": summary["assemblyReceiptSha256"]}}
        report["nativeQmlCompatibility"] = {"verifiedNoLegacyReferences": True}
        return report

    def test_stable_core_binds_installed_preview_bytes_and_keeps_beta_channel(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertTrue(report["releaseEligible"])
        self.assertTrue(report["stableCoreTested"])
        self.assertTrue(report["betaUiChannelVerified"])
        self.assertFalse(report["featureRuntimeTested"])
        self.assertFalse(report["wholeClientMaterialsVerified"])
        self.assertFalse(report["betaCoreTested"])
        self.assertEqual(report["blockers"], [])
        self.assertIn("advanced_beta_features",
                      {item["id"] for item in report["knownLimitations"]})
        package.require_distribution("public-stable-core", report, installer=True)
        with self.assertRaises(ValueError):
            package.require_distribution("user-release", report, installer=True)

    def test_stable_core_rejects_old_p29_smoke_without_final_health(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        data = json.loads(smoke.read_text("utf8"))
        data.pop("retentionFinalHealth")
        smoke.write_text(json.dumps(data), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke",
                      {item["id"] for item in report["blockers"]})

    def test_stable_core_rejects_post_smoke_route_failure_or_short_dwell(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        original = json.loads(smoke.read_text("utf8"))
        incident = json.loads(json.dumps(original))
        incident["retentionFinalHealth"]["end"].update({
            "error": "route", "ready": False, "captureActive": False,
            "failures": 1, "routeFailures": 1,
            "lastScopeFailureReason": 8, "lastInvalidSenderReason": 2})
        smoke.write_text(json.dumps(incident), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke",
                      {item["id"] for item in report["blockers"]})
        short = json.loads(json.dumps(original))
        short["retentionFinalHealth"]["checkedAt"] = (
            self.now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
        smoke.write_text(json.dumps(short), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke",
                      {item["id"] for item in report["blockers"]})

    def test_stable_core_rejects_changed_installed_bytes_or_missing_scenario(self):
        subject, smoke, proof_path, runtime = self.stable_core_fixture()
        original_smoke = smoke.read_bytes()
        report = self.stable_core_report(subject, smoke, proof_path,
            registration={"path": str(self.root), "version": "8.9.0-native.preview.23"})
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})
        (runtime / "mod/max.exe").write_bytes(b"changed installed runtime")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})
        (runtime / "mod/max.exe").write_bytes(b"owned installed runtime fixture: mod/max.exe")
        data = json.loads(original_smoke)
        data["scenarios"].pop("retention_restart_persistence")
        smoke.write_text(json.dumps(data), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})
        data = json.loads(original_smoke)
        data["retentionIdleHealth"]["startedAt"] = data["retentionIdleHealth"]["checkedAt"]
        smoke.write_text(json.dumps(data), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})
        data = json.loads(original_smoke)
        data["retentionIdleHealth"]["end"]["error"] = "route"
        data["retentionIdleHealth"]["end"]["ready"] = False
        data["retentionIdleHealth"]["end"]["captureActive"] = False
        data["retentionIdleHealth"]["end"]["failures"] = 1
        smoke.write_text(json.dumps(data), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})
        smoke.write_bytes(original_smoke)
        candidate_ref = json.loads(original_smoke)["candidatePackage"]
        candidate = json.loads(Path(candidate_ref["path"]).read_text("utf8"))
        Path(candidate["installer"]).write_bytes(b"changed candidate Setup")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})

    def test_stable_core_rejects_repackaged_candidate_without_reinstall(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        data = json.loads(smoke.read_text("utf8"))
        candidate_path = Path(data["candidatePackage"]["path"])
        candidate = json.loads(candidate_path.read_text("utf8"))
        candidate["packagedAt"] = (self.now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidate_path.write_text(json.dumps(candidate), encoding="utf8")
        data["candidatePackage"]["sha256"] = package.sha(candidate_path)
        smoke.write_text(json.dumps(data), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})

    def test_stable_core_rejects_future_dated_idle_finish(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        data = json.loads(smoke.read_text("utf8"))
        idle = data["retentionIdleHealth"]
        idle["startedAt"] = (self.now - timedelta(minutes=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
        idle["checkedAt"] = (self.now + timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%SZ")
        data["testedAt"] = idle["checkedAt"]
        smoke.write_text(json.dumps(data), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertFalse(report["releaseEligible"])
        self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})
        self.assertTrue(any("retention idle-health window is too short or detached" in item["reason"]
                            for item in report["blockers"] if item["id"] == "invalid_installed_smoke"))

    def test_stable_core_rejects_process_restart_during_idle_health(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        original = json.loads(smoke.read_text("utf8"))
        for change in ("pid", "creation", "future_creation", "other_path"):
            with self.subTest(change=change):
                data = json.loads(json.dumps(original))
                idle = data["retentionIdleHealth"]
                if change == "pid":
                    idle["end"]["process"]["pid"] += 1
                elif change == "creation":
                    idle["end"]["process"]["createdAt"] = (
                        self.now - timedelta(minutes=11, seconds=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
                elif change == "future_creation":
                    stamp = (self.now - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    idle["start"]["process"]["createdAt"] = stamp
                    idle["end"]["process"]["createdAt"] = stamp
                else:
                    idle["end"]["process"]["path"] = str(self.root / "other/max.exe")
                smoke.write_text(json.dumps(data), encoding="utf8")
                report = self.stable_core_report(subject, smoke, proof_path)
                self.assertFalse(report["releaseEligible"])
                self.assertIn("invalid_installed_smoke",
                              {item["id"] for item in report["blockers"]})

    def test_stable_core_rejects_preview_version_missing_rights_and_removed_beta_badges(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        report = self.stable_core_report(subject, smoke, proof_path, rights=False)
        self.assertIn("max_redistribution_attestation", {item["id"] for item in report["blockers"]})
        proof_path.write_text(json.dumps({"schema": 1, "nativeRuntime": True,
                                          "uiReleaseChannel": "stable-candidate"}), encoding="utf8")
        report = self.stable_core_report(subject, smoke, proof_path)
        self.assertIn("beta_ui_channel", {item["id"] for item in report["blockers"]})
        preview = {**subject, "version": "8.9.0-native.preview.24"}
        report = self.stable_core_report(preview, smoke, proof_path)
        self.assertIn("invalid_payload_subject", {item["id"] for item in report["blockers"]})

    def test_stable_core_marker_binds_notice_materials_and_beta_ui(self):
        subject, smoke, proof_path, _ = self.stable_core_fixture()
        report = self.stable_core_report(subject, smoke, proof_path)
        summary = report["betaMaterials"]
        payload = self.root / "stable-core-payload"
        payload.mkdir()
        for row in (*summary["sourceBundles"],
                    *(item["signature"] for item in summary["sourceBundles"]),
                    summary["buildConfigurations"]):
            data = ("owned source fixture: " + row["path"]).encode()
            target = payload / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            row["size"] = len(data)
            row["sha256"] = digest(data)
        materials_path = payload / package.PUBLIC_STABLE_CORE_MATERIAL_INFO
        materials_path.write_text(json.dumps(summary), encoding="utf8")
        information_path = payload / package.PUBLIC_STABLE_CORE_INFO
        information_path.write_bytes(package.stable_core_information(subject["version"]))
        self.assertIn("BETA", information_path.read_text("utf-8-sig"))
        manifest = {"distributionMode": "public-stable-core", "version": subject["version"],
            "webview2Mode": "fixed", "webview2Bootstrapper": None,
            "qtSourceMode": "inline", "qtSourceDistribution": None,
            "qtSourceAvailability": None, "ffmpegSourceMode": "inline",
            "ffmpegSourceDistribution": None, "ffmpegSourceAvailability": None,
            "publicStableCore": True, "stableCoreTested": True,
            "betaCoreTested": False,
            "betaUiChannelVerified": True, "featureRuntimeTested": False,
            "wholeClientMaterialsVerified": False, "materialsDocumentedForBeta": True,
            "maxRightsAttested": True, "knownLimitations": report["knownLimitations"],
            "installedSmokeSha256": report["installedSmokeSha256"],
            "betaMaterials": summary,
            "betaMaterialsInformation": {"path": materials_path.name,
                                          "sha256": package.sha(materials_path)},
            "coreInformation": {"path": information_path.name,
                                "sha256": package.sha(information_path)}}
        (payload / "native-package.json").write_text(json.dumps(manifest), encoding="utf8")
        state = package.write_release_state(payload, manifest, report)
        marker = (payload / "native-release-state.iss").read_text("utf8")
        self.assertIn('#define NativeDistributionMode "public-stable-core"', marker)
        self.assertIn('#define NativeStableCoreVersion "8.9.0"', marker)
        self.assertIn('#define NativeStableCoreTested 1', marker)
        self.assertIn('#define NativeBetaUiChannelVerified 1', marker)
        self.assertEqual(state["manifestSha256"], package.sha(payload / "native-package.json"))
        information_path.write_bytes(b"changed notice")
        with self.assertRaisesRegex(ValueError, "stable core evidence or information changed"):
            package.write_release_state(payload, manifest, report)

    def test_stable_core_packaging_keeps_ffmpeg_external_and_full_feature_claim_false(self):
        inputs = self.staging_inputs()
        _, material_evidence = self.beta_material_fixture(inputs)
        qml = self.root / "qml"
        qml.mkdir()
        qml_proof = qml / "native-qml-proof.json"
        qml_proof.write_text(json.dumps({"schema": 1, "nativeRuntime": True,
                                         "uiReleaseChannel": "beta"}), encoding="utf8")
        inputs["qmlResult"]["proofSha256"] = package.sha(qml_proof)
        for name in sorted(package.pins.REQUIRED | package.pins.FIXED_REQUIRED):
            target = inputs["runtime"] / name
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(("owned runtime fixture: " + name).encode())
        inputs["rows"] = package.pins.inventory(inputs["runtime"])
        subject = {"version": "8.9.0", "launcherSha256": inputs["launcherHash"],
                   "runtimeInventorySha256": package.inventory_digest(inputs["rows"]),
                   "qmlProofSha256": inputs["qmlResult"]["proofSha256"]}
        material_data = json.loads(material_evidence.read_text("utf8"))
        material_data["kind"] = "native-public-stable-core-materials"
        material_data["subject"] = subject
        material_evidence.write_text(json.dumps(material_data), encoding="utf8")
        installed_package = self.root / "native-package.json"
        installed_package.write_text(json.dumps({"version": "8.9.0-native.preview.24",
            "distributionMode": "local-preview", "launcherSha256": subject["launcherSha256"],
            "runtimeFiles": inputs["rows"], "qml": {"proofSha256": subject["qmlProofSha256"]}}),
            encoding="utf8")
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        smoke = self.root / "stable-core-smoke.json"
        smoke.write_text(json.dumps({"schema": 1,
            "kind": "native-public-stable-core-installed-smoke", "status": "passed",
            "subject": subject, "testedAt": self.now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clientExecuted": True, "scope": "installed-native-client",
            "launchMode": "outer-installed-launcher",
            "installedLauncher": {"path": str(inputs["launcher"]),
                                  "sha256": subject["launcherSha256"]},
            "installedPackage": {"path": str(installed_package),
                                 "sha256": package.sha(installed_package),
                                 "version": "8.9.0-native.preview.24",
                                 "distributionMode": "local-preview"},
            "candidatePackage": self.candidate_receipt_fixture(
                installed_package, "8.9.0-native.preview.24"),
            "retentionIdleHealth": self.stable_idle_health_fixture(),
            "retentionFinalHealth": self.stable_final_health_fixture(),
            "scenarios": dict.fromkeys(package.PUBLIC_STABLE_CORE_SCENARIOS, True),
            "unresolvedCritical": []}), encoding="utf8")
        output = self.root / "public-stable-core"
        argv = ["package_native_client.py", "--version", subject["version"],
                "--runtime", str(inputs["runtime"]), "--qml-build", str(qml),
                "--launcher-proof", str(self.root / "launcher-proof.json"),
                "--output", str(output), "--distribution", "public-stable-core",
                "--release-evidence", str(smoke), "--public-materials-evidence",
                str(material_evidence), "--max-rights-attested",
                "--ffmpeg-source-mode", "separate-asset"]
        with patch.object(sys, "argv", argv), \
             patch.object(package, "packaging_inputs", return_value=inputs), \
             patch.object(package, "registered_installation",
                          return_value={"path": str(self.root), "version": "8.9.0-native.preview.24"}), \
             patch.object(package.subprocess, "run") as compiler, redirect_stdout(io.StringIO()):
            package.main()
            compiler.assert_not_called()
        payload = output / "payload"
        manifest = json.loads((payload / "native-package.json").read_text("utf8"))
        receipt = json.loads((output / "build-receipt.json").read_text("utf8"))
        self.assertTrue(manifest["releaseEligible"])
        self.assertTrue(manifest["publicStableCore"])
        self.assertTrue(manifest["stableCoreTested"])
        self.assertTrue(manifest["betaUiChannelVerified"])
        self.assertFalse(manifest["featureRuntimeTested"])
        self.assertFalse(manifest["wholeClientMaterialsVerified"])
        self.assertEqual(manifest["betaMaterialsInformation"]["path"],
                         "native-stable-core-materials.json")
        self.assertFalse((payload / "materials/FFmpeg").exists())
        asset = output / "source-assets" / manifest["ffmpegSourceDistribution"]["assetName"]
        self.assertTrue(package.verify_ffmpeg_source_asset(
            asset, manifest["ffmpegSourceDistribution"], manifest["betaMaterials"]))
        self.assertEqual(receipt["ffmpegSourceAsset"]["sha256"],
                         manifest["ffmpegSourceDistribution"]["assetSha256"])
        self.assertIn("стабильный основной клиент",
                      (payload / "native-stable-core.txt").read_text("utf-8-sig"))
        setup_folder = output / "release"
        setup_folder.mkdir()
        setup = setup_folder / "ValMAX-8.9.0-Setup.exe"
        setup.write_bytes(b"owned final Setup fixture")
        receipt["installerCompiled"] = True
        receipt["installer"] = str(setup)
        receipt["installerSha256"] = package.sha(setup)
        receipt["packagedAt"] = (self.now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        build_receipt = output / "build-receipt.json"
        build_receipt.write_text(json.dumps(receipt), encoding="utf8")
        installed_package.write_bytes((payload / "native-package.json").read_bytes())
        final_data = json.loads(smoke.read_text("utf8"))
        final_data["installedPackage"] = {"path": str(installed_package),
            "sha256": package.sha(installed_package), "version": "8.9.0",
            "distributionMode": "public-stable-core"}
        final_data["candidatePackage"] = {"path": str(build_receipt),
                                          "sha256": package.sha(build_receipt)}
        final_smoke = self.root / "final-installed-stable-core-smoke.json"
        final_smoke.write_text(json.dumps(final_data), encoding="utf8")
        with patch.object(package, "registered_installation",
                          return_value={"path": str(self.root), "version": "8.9.0"}):
            with self.assertRaisesRegex(ValueError, "final installed smoke must postdate"):
                stable_publication.verify(build_receipt, final_smoke, qml_proof)
            receipt["packagedAt"] = (self.now - timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
            build_receipt.write_text(json.dumps(receipt), encoding="utf8")
            final_data["candidatePackage"]["sha256"] = package.sha(build_receipt)
            final_smoke.write_text(json.dumps(final_data), encoding="utf8")
            publication = stable_publication.verify(build_receipt, final_smoke, qml_proof)
            self.assertTrue(publication["publicationEligible"])
            self.assertEqual(publication["installerSha256"], package.sha(setup))
            original_receipt = build_receipt.read_bytes()
            original_asset = asset.read_bytes()
            replacement = b"owned coherently replaced source ZIP fixture"
            asset.write_bytes(replacement)
            changed = json.loads(original_receipt)
            changed["ffmpegSourceAsset"]["size"] = len(replacement)
            changed["ffmpegSourceAsset"]["sha256"] = package.sha(asset)
            changed["ffmpegSourceDistribution"]["assetBytes"] = len(replacement)
            changed["ffmpegSourceDistribution"]["assetSha256"] = package.sha(asset)
            build_receipt.write_text(json.dumps(changed), encoding="utf8")
            final_data["candidatePackage"]["sha256"] = package.sha(build_receipt)
            final_smoke.write_text(json.dumps(final_data), encoding="utf8")
            with self.assertRaisesRegex(ValueError, "source asset distribution differs"):
                stable_publication.verify(build_receipt, final_smoke, qml_proof)
            asset.write_bytes(original_asset)
            build_receipt.write_bytes(original_receipt)
            final_data["candidatePackage"]["sha256"] = package.sha(build_receipt)
            final_smoke.write_text(json.dumps(final_data), encoding="utf8")
            setup.write_bytes(b"changed final Setup")
            with self.assertRaisesRegex(ValueError, "final Setup bytes changed"):
                stable_publication.verify(build_receipt, final_smoke, qml_proof)

    def test_public_beta_refuses_missing_rights_materials_or_exact_artifacts(self):
        subject = self.beta_subject()
        smoke = self.beta_smoke(subject)
        cases = (
            ({"rights": False}, "max_redistribution_attestation"),
            ({"materials": {"completeForWholeClient": False, "limitations": []}}, "beta_materials"),
            ({"materials": {"completeForWholeClient": True, "limitations": []}}, "beta_materials"),
            ({"materials": {"completeForWholeClient": False, "limitations": ["unknown gap"]}}, "beta_materials"),
            ({"artifact_verified": False}, "artifact_integrity"),
            ({"qml_verified": False}, "artifact_integrity"),
            ({"identity": {**{key: subject[key] for key in package.ARTIFACT_IDENTITY_KEYS},
                           "qmlProofSha256": "e" * 64}}, "artifact_integrity"),
        )
        for changes, blocker in cases:
            with self.subTest(changes=changes):
                report = self.beta_report(subject, smoke, **changes)
                self.assertFalse(report["releaseEligible"])
                self.assertIn(blocker, {item["id"] for item in report["blockers"]})
                with self.assertRaises(ValueError):
                    package.require_distribution("public-beta", report, installer=True)

    def test_public_beta_refuses_stale_other_subject_and_synthetic_smoke(self):
        subject = self.beta_subject()
        smoke = self.beta_smoke(subject)
        original = json.loads(smoke.read_text("utf8"))
        changes = (
            lambda value: value.update(subject={**subject, "launcherSha256": "e" * 64}),
            lambda value: value.update(testedAt=(self.now - timedelta(days=8)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            lambda value: value.update(testedAt=(self.now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")),
            lambda value: value.update(clientExecuted=False),
            lambda value: value.update(scope="standalone-qt"),
            lambda value: value.update(launchMode="direct-inner-max"),
            lambda value: value["installedLauncher"].update(path=str(self.root / "runtime/mod/max.exe")),
            lambda value: value["installedLauncher"].update(sha256="e" * 64),
            lambda value: value["scenarios"].pop("deleted_message_bubble"),
            lambda value: value["scenarios"].update(chat_send_receive=1),
            lambda value: value.update(unresolvedCritical=["startup crash"]),
            lambda value: value.update(status="failed"),
        )
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                value = copy.deepcopy(original)
                change(value)
                smoke.write_text(json.dumps(value), encoding="utf8")
                report = self.beta_report(subject, smoke)
                self.assertFalse(report["releaseEligible"])
                self.assertFalse(report["betaCoreTested"])
                self.assertIn("invalid_installed_smoke", {item["id"] for item in report["blockers"]})

    def test_public_beta_cli_without_smoke_or_rights_stops_before_payload(self):
        inputs = self.staging_inputs()
        subject, material_evidence = self.beta_material_fixture(inputs)
        smoke = self.beta_smoke(subject)
        for index, extras in enumerate((("--max-rights-attested",),
                                        ("--release-evidence", str(smoke)))):
            output = self.root / ("blocked-public-beta-" + str(index))
            argv = ["package_native_client.py", "--version", subject["version"],
                    "--runtime", str(inputs["runtime"]), "--qml-build", str(inputs["qml"]),
                    "--launcher-proof", str(self.root / "launcher-proof.json"),
                    "--output", str(output), "--distribution", "public-beta",
                    "--beta-materials-evidence", str(material_evidence), *extras]
            with self.subTest(extras=extras), patch.object(sys, "argv", argv), \
                 patch.object(package, "packaging_inputs", return_value=inputs), \
                 patch.object(package.shutil, "copyfile") as copy_file, \
                 patch.object(package.subprocess, "run") as compiler:
                with self.assertRaisesRegex(ValueError, "public BETA requires"):
                    package.main()
                copy_file.assert_not_called()
                compiler.assert_not_called()
            self.assertTrue((output / "packaging-preflight.json").is_file())
            self.assertFalse((output / "payload").exists())

    def test_public_beta_materials_bind_notices_sources_config_and_exact_dlls(self):
        inputs = self.staging_inputs()
        subject, evidence = self.beta_material_fixture(inputs)
        binding = inputs["materialBinding"]
        assembly = inputs["hookTargetIdentity"]["assemblyReceiptSha256"]
        plan = package.beta_materials_documentation(binding, inputs["rows"], assembly, subject, evidence)
        self.assertTrue(plan["summary"]["verified"])
        self.assertFalse(plan["summary"]["completeForWholeClient"])
        self.assertFalse(plan["summary"]["exactCorrespondingSourceProven"])
        self.assertEqual([row["version"] for row in plan["summary"]["sourceBundles"]], ["7.1.3", "7.1.5"])
        original_evidence = evidence.read_bytes()
        original_rows = copy.deepcopy(inputs["rows"])
        original_binding = copy.deepcopy(binding)
        source715 = self.root / "ffmpeg-7.1.5.tar.xz"
        original_source715 = source715.read_bytes()
        audit = self.root / "ffmpeg-audit.json"
        original_audit = audit.read_bytes()
        cases = (
            (lambda: binding["status"]["limitations"].append("unknown materials gap"), "unknown, missing"),
            (lambda: inputs["rows"].pop(next(i for i, row in enumerate(inputs["rows"])
                                          if row["path"] == "mod/licenses/Gum/COPYING")), "notice is missing"),
            (lambda: binding["status"].update(additionalQtMaterialProof=None), "source or notice proof"),
            (lambda: evidence.write_text(original_evidence.decode().replace('"materialsReceiptSha256": "' + "d" * 64,
                '"materialsReceiptSha256": "' + "0" * 64), encoding="utf8"), "exact incomplete assembly"),
            (lambda: source715.write_bytes(b"tampered source"), "source bundle bytes differ"),
            (lambda: audit.write_bytes(b"tampered audit"), "FFmpeg audit changed"),
            (lambda: inputs["rows"][next(i for i, row in enumerate(inputs["rows"])
                                     if row["path"] == "mod/avcodec-61.dll")].update(sha256="0" * 64),
             "staged FFmpeg DLL differs"),
        )
        for index, (change, reason) in enumerate(cases):
            binding.clear(); binding.update(copy.deepcopy(original_binding))
            inputs["rows"][:] = copy.deepcopy(original_rows)
            evidence.write_bytes(original_evidence)
            source715.write_bytes(original_source715)
            audit.write_bytes(original_audit)
            change()
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, reason):
                package.beta_materials_documentation(binding, inputs["rows"], assembly, subject, evidence)

    def test_public_beta_materials_reject_changed_reference_and_signature(self):
        inputs = self.staging_inputs()
        subject, evidence = self.beta_material_fixture(inputs)
        binding = inputs["materialBinding"]
        assembly = inputs["hookTargetIdentity"]["assemblyReceiptSha256"]
        bundle = self.root / "ffmpeg-bundle"
        targets = (
            (bundle / "manifest.json", "reference bundle manifest changed"),
            (bundle / "manifests/valmax-ffmpeg-runtime.json", "runtime manifest changed"),
            (bundle / "verification/gpg-verification.json", "signature verification record changed"),
            (self.root / "ffmpeg-7.1.5.tar.xz.asc", "source bundle bytes differ"),
        )
        for target, reason in targets:
            with self.subTest(target=target.name):
                original = target.read_bytes()
                target.write_bytes(b"tampered owned fixture")
                try:
                    with self.assertRaisesRegex(ValueError, reason):
                        package.beta_materials_documentation(binding, inputs["rows"], assembly, subject, evidence)
                finally:
                    target.write_bytes(original)

    def test_public_beta_packaging_pins_notice_and_preserves_stable_claim_boundary(self):
        inputs = self.staging_inputs()
        subject, material_evidence = self.beta_material_fixture(inputs)
        smoke = self.beta_smoke(subject)
        output = self.root / "public-beta"
        argv = ["package_native_client.py", "--version", subject["version"], "--runtime", str(inputs["runtime"]),
                "--qml-build", str(inputs["qml"]), "--launcher-proof", str(self.root / "launcher-proof.json"),
                "--output", str(output), "--distribution", "public-beta", "--release-evidence", str(smoke),
                "--beta-materials-evidence", str(material_evidence), "--max-rights-attested"]
        with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs", return_value=inputs), \
             patch.object(package.pins, "inventory", return_value=inputs["rows"]), \
             patch.object(package.subprocess, "run") as compiler:
            package.main()
            compiler.assert_not_called()
        payload = output / "payload"
        manifest = json.loads((payload / "native-package.json").read_text("utf8"))
        self.assertTrue(manifest["releaseEligible"])
        self.assertTrue(manifest["publicBeta"])
        self.assertTrue(manifest["betaCoreTested"])
        self.assertFalse(manifest["featureRuntimeTested"])
        self.assertFalse(manifest["wholeClientMaterialsVerified"])
        self.assertTrue(manifest["materialsDocumentedForBeta"])
        self.assertFalse(manifest["betaMaterials"]["exactCorrespondingSourceProven"])
        self.assertEqual(manifest["betaMaterials"]["materialsReceiptSha256"], "d" * 64)
        self.assertTrue(manifest["maxRightsAttested"])
        self.assertEqual(manifest["installedSmokeSha256"], package.sha(smoke))
        self.assertEqual(manifest["betaInformation"], {"path": "native-beta.txt",
            "sha256": package.sha(payload / "native-beta.txt")})
        warning = (payload / "native-beta.txt").read_text("utf-8-sig")
        for phrase in ("публичная BETA", "история может быть неполной", "Фото, видео и голосовые",
                       "первого фонового действия", "public BETA", "FFmpeg", "WinSparkle"):
            self.assertIn(phrase, warning)
        for item in package.PUBLIC_BETA_MATERIAL_LIMITATIONS:
            self.assertIn(item["noticeRu"], warning)
            self.assertIn(item["noticeEn"], warning)
        for row in (*manifest["betaMaterials"]["sourceBundles"],
                    *(item["signature"] for item in manifest["betaMaterials"]["sourceBundles"]),
                    manifest["betaMaterials"]["buildConfigurations"]):
            self.assertEqual(package.sha(payload / row["path"]), row["sha256"])
        self.assertEqual(package.sha(payload / "native-beta-materials.json"),
                         manifest["betaMaterialsInformation"]["sha256"])
        marker = (payload / "native-release-state.iss").read_text("utf8")
        self.assertIn('#define NativeDistributionMode "public-beta"', marker)
        self.assertIn('#define NativeReleaseEligible 1', marker)
        self.assertIn('#define NativeBetaCoreTested 1', marker)
        self.assertIn('#define NativeBetaInfoSha256 "' + package.sha(payload / "native-beta.txt") + '"', marker)
        self.assertIn('#define NativeBetaMaterialsInfoSha256 "' +
                      package.sha(payload / "native-beta-materials.json") + '"', marker)
        self.assertIn('#define NativeBetaSource713SignatureSha256 "' +
                      manifest["betaMaterials"]["sourceBundles"][0]["signature"]["sha256"] + '"', marker)

    def test_public_beta_separate_ffmpeg_asset_is_bound_and_required(self):
        inputs = self.staging_inputs()
        subject, material_evidence = self.beta_material_fixture(inputs)
        smoke = self.beta_smoke(subject)
        plan = package.beta_materials_documentation(
            inputs["materialBinding"], inputs["rows"],
            inputs["hookTargetIdentity"]["assemblyReceiptSha256"],
            subject, material_evidence, ffmpeg_source_mode="separate-asset")
        repeated = package.beta_materials_documentation(
            inputs["materialBinding"], inputs["rows"],
            inputs["hookTargetIdentity"]["assemblyReceiptSha256"],
            subject, material_evidence, ffmpeg_source_mode="separate-asset")
        self.assertEqual(plan["ffmpegSourceAsset"]["bytes"], repeated["ffmpegSourceAsset"]["bytes"])
        output = self.root / "public-beta-external-ffmpeg"
        argv = ["package_native_client.py", "--version", subject["version"],
                "--runtime", str(inputs["runtime"]), "--qml-build", str(inputs["qml"]),
                "--launcher-proof", str(self.root / "launcher-proof.json"),
                "--output", str(output), "--distribution", "public-beta",
                "--release-evidence", str(smoke), "--beta-materials-evidence", str(material_evidence),
                "--max-rights-attested", "--ffmpeg-source-mode", "separate-asset"]
        with patch.object(sys, "argv", argv), patch.object(package, "packaging_inputs", return_value=inputs), \
             patch.object(package.pins, "inventory", return_value=inputs["rows"]), \
             patch.object(package.subprocess, "run") as compiler, redirect_stdout(io.StringIO()):
            package.main()
            compiler.assert_not_called()
        payload = output / "payload"
        manifest = json.loads((payload / "native-package.json").read_text("utf8"))
        report = json.loads((output / "packaging-preflight.json").read_text("utf8"))
        receipt = json.loads((output / "build-receipt.json").read_text("utf8"))
        asset = manifest["ffmpegSourceDistribution"]
        asset_path = output / "source-assets" / asset["assetName"]
        self.assertTrue(manifest["releaseEligible"])
        self.assertEqual(manifest["ffmpegSourceMode"], "separate-asset")
        self.assertEqual(receipt["ffmpegSourceAsset"]["sha256"], asset["assetSha256"])
        self.assertFalse((payload / "materials/FFmpeg").exists())
        self.assertTrue(package.verify_ffmpeg_source_asset(asset_path, asset, manifest["betaMaterials"]))
        with zipfile.ZipFile(asset_path) as archive:
            self.assertEqual(archive.read("build-configurations.json"), plan["configBytes"])
            self.assertEqual(len(archive.namelist()), 6)
        notice = (payload / package.FFMPEG_SOURCE_INFO).read_text("utf-8-sig")
        self.assertIn(asset["assetSha256"], notice)
        self.assertIn(asset["assetName"], (payload / "native-beta.txt").read_text("utf-8-sig"))
        marker = (payload / "native-release-state.iss").read_text("utf8")
        self.assertIn('#define NativeFFmpegSourceMode "separate-asset"', marker)
        self.assertIn('#define NativeFFmpegSourceAssetSha256 "' + asset["assetSha256"] + '"', marker)
        original = asset_path.read_bytes()
        asset_path.write_bytes(b"changed synthetic source asset")
        with self.assertRaisesRegex(ValueError, "separate FFmpeg source asset is missing or changed"):
            package.write_release_state(payload, manifest, report)
        asset_path.unlink()
        with self.assertRaises((ValueError, OSError)):
            package.write_release_state(payload, manifest, report)
        asset_path.write_bytes(original)
        self.assertTrue(package.verify_ffmpeg_source_asset(asset_path, asset, manifest["betaMaterials"]))
        for unexpected in (payload / "materials/FFmpeg", payload / "source-assets"):
            with self.subTest(unexpected=unexpected.name):
                unexpected.mkdir(parents=True)
                try:
                    with self.assertRaisesRegex(ValueError, "separate FFmpeg source notice or byte binding changed"):
                        package.write_release_state(payload, manifest, report)
                finally:
                    unexpected.rmdir()
        (payload / "materials").rmdir()

    def test_public_beta_marker_rejects_changed_notice_and_forged_claim(self):
        inputs = self.staging_inputs()
        subject, material_evidence = self.beta_material_fixture(inputs)
        plan = package.beta_materials_documentation(inputs["materialBinding"], inputs["rows"],
            inputs["hookTargetIdentity"]["assemblyReceiptSha256"], subject, material_evidence)
        smoke = self.beta_smoke(subject)
        report = self.beta_report(subject, smoke, materials=inputs["materialBinding"]["status"],
                                  beta_materials=plan["summary"])
        payload = self.root / "beta-marker"
        payload.mkdir()
        material_info = package.copy_beta_materials(payload, plan)
        notice = payload / "native-beta.txt"
        notice.write_bytes(package.beta_information(subject["version"]))
        manifest = {"distributionMode": "public-beta", "version": subject["version"], "publicBeta": True,
                    "betaCoreTested": True, "featureRuntimeTested": False,
                    "wholeClientMaterialsVerified": False, "materialsDocumentedForBeta": True,
                    "betaMaterials": plan["summary"], "betaMaterialsInformation": material_info,
                    "maxRightsAttested": True,
                    "installedSmokeSha256": package.sha(smoke), "knownLimitations": report["knownLimitations"],
                    "betaInformation": {"path": "native-beta.txt", "sha256": package.sha(notice)}}
        (payload / "native-package.json").write_text(json.dumps(manifest), encoding="utf8")
        notice.write_bytes(b"changed unit-only notice")
        with self.assertRaisesRegex(ValueError, "public BETA evidence or information changed"):
            package.write_release_state(payload, manifest, report)
        notice.write_bytes(package.beta_information(subject["version"]))
        manifest["featureRuntimeTested"] = True
        with self.assertRaisesRegex(ValueError, "public BETA evidence or information changed"):
            package.write_release_state(payload, manifest, report)
        manifest["featureRuntimeTested"] = False
        source = payload / manifest["betaMaterials"]["sourceBundles"][0]["path"]
        source_original = source.read_bytes()
        source.write_bytes(b"changed unit-only source")
        with self.assertRaisesRegex(ValueError, "source or configuration changed"):
            package.write_release_state(payload, manifest, report)
        source.write_bytes(source_original)
        signature = payload / manifest["betaMaterials"]["sourceBundles"][0]["signature"]["path"]
        signature.write_bytes(b"changed unit-only signature")
        with self.assertRaisesRegex(ValueError, "source or configuration changed"):
            package.write_release_state(payload, manifest, report)
        self.assertFalse((payload / "native-release-state.iss").exists())

    def test_local_preview_accepts_only_explicit_known_product_gaps(self):
        report = self.preview_report()
        package.require_distribution("local-preview", report, installer=True)
        self.assertFalse(report["releaseEligible"])
        self.assertFalse(report["featureRuntimeTested"])
        self.assertEqual({item["id"] for item in report["blockers"]},
                         {key for key, _ in package.CURRENT_RELEASE_BLOCKERS}
                         | {package.MAX_RIGHTS_ATTESTATION_BLOCKER[0],
                            "whole_client_materials", "release_evidence_missing"})
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
        self.assertEqual(len(manifest["releaseBlockers"]), len(package.CURRENT_RELEASE_BLOCKERS) + 3)

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
