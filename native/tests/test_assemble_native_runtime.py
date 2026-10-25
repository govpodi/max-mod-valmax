"""Synthetic file-only assembler checks. No MAX/helper/profile/process is run."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "native/tooling"))
import assemble_native_runtime as assembly


class AssemblyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="native-staging-unit-", dir="E:/ValMAXBuild/build"))

    def fresh(self, name):
        value = self.root / (self._testMethodName + "-" + name)
        value.mkdir()
        return value

    def test_relative_rejects_windows_alias_and_private_paths(self):
        for name in ("", "../escape", "a/../b", "/root", "a\\b", "a//b", "a/./b", "C:/file", "a:ads", "a./b",
                     "a /b", "CON.txt", "a/COM1", "MXMOD/config.json", "a/oneme/token", "a/.mxmod-helper-temp/file"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                assembly.relative(name)
        self.assertEqual(assembly.relative("mod/max-service/config.dll").as_posix(), "mod/max-service/config.dll")

    def test_inventory_exact_content_and_extra_file(self):
        root = self.fresh("input")
        (root / "Qt.dll").write_bytes(b"fixture")
        (root / "api.dll").write_bytes(b"two")
        rows = assembly.inventory(root)
        self.assertEqual([row["path"] for row in rows], ["Qt.dll", "api.dll"])
        self.assertEqual(assembly.verify_inventory(root, rows), rows)
        (root / "extra").write_bytes(b"unexpected")
        with self.assertRaisesRegex(ValueError, "differs"):
            assembly.verify_inventory(root, rows)

    def test_manifest_case_duplicate_rejected(self):
        root = self.fresh("input")
        path = root / "test.bin"
        path.write_bytes(b"fixture")
        row = assembly.inventory(root)[0]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assembly.verify_inventory(root, [row, {**row, "path": "TEST.bin"}])

    def test_independent_copy_and_source_not_modified(self):
        root = self.fresh("input")
        source = root / "data.bin"
        source.write_bytes(b"fixture")
        plan = assembly.Plan()
        plan.add("mod/data.bin", source, assembly.sha(source), "synthetic")
        output = self.root / (self._testMethodName + "-runtime")
        plan.copy(output)
        target = output / "mod/data.bin"
        self.assertNotEqual(source.stat().st_ino, target.stat().st_ino)
        self.assertEqual(target.stat().st_nlink, 1)
        target.write_bytes(b"replacement")
        self.assertEqual(source.read_bytes(), b"fixture")
        with self.assertRaisesRegex(ValueError, "new runtime"):
            plan.copy(output)

    def test_mutation_before_copy_fails_closed(self):
        root = self.fresh("input")
        source = root / "data.bin"
        source.write_bytes(b"fixture")
        plan = assembly.Plan()
        plan.add("mod/data.bin", source, assembly.sha(source), "synthetic")
        source.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "changed before copy"):
            plan.copy(self.root / (self._testMethodName + "-runtime"))

    def test_legacy_files_not_admitted(self):
        root = self.fresh("input")
        source = root / "data.bin"
        source.write_bytes(b"fixture")
        for name in ("server.py", "thing.pyc", "python314.dll", "frida.dll", "privacy_monitor.js", "ghost_monitor.js",
                     "notification_monitor.js", "message_retention_monitor.js", "chat_export_end_monitor.js", "a.mdata", "crash.dmp", "a.pdb", "a.lib"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "legacy/private/development"):
                assembly.Plan().add("mod/" + name, source, assembly.sha(source), "synthetic")

    def test_duplicate_destination_and_explicit_replace(self):
        root = self.fresh("input")
        source = root / "data.bin"
        source.write_bytes(b"fixture")
        plan = assembly.Plan()
        plan.add("mod/name", source, assembly.sha(source), "a")
        with self.assertRaisesRegex(ValueError, "collision"):
            plan.add("mod/NAME", source, assembly.sha(source), "b")
        plan.add("mod/name", source, assembly.sha(source), "b", replace=True)
        self.assertEqual(len(plan.entries), 1)
        self.assertEqual(next(iter(plan.entries.values()))["component"], "b")

    def test_literal_patch_extraction_never_executes_module(self):
        root = self.fresh("input")
        path = root / "spec.py"
        path.write_text("raise Exception('must never run')\nPATCH_FILES = [{'relative_path':'test.bin','sha256':'x','patches':[]}]\n", "utf8")
        self.assertEqual(assembly.literal_specs(path)[0]["relative_path"], "test.bin")
        path.write_text("PATCH_FILES = dangerous()", "utf8")
        with self.assertRaises(ValueError):
            assembly.literal_specs(path)

    def test_existing_module_allowlist(self):
        for name in ("launch_mod", "package_mod", "privacy_server", "export_history", "ui_privacy"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "unsupported"):
                assembly.existing_module(name)

    def test_duplicate_json_keys_and_nonfinite_rejected(self):
        root = self.fresh("input")
        path = root / "receipt.json"
        for value in ('{"key":1,"key":2}', '{"value":NaN}'):
            path.write_text(value, "utf8")
            with self.assertRaises(ValueError):
                assembly.read_json(path)

    def test_hardlink_rejected(self):
        root = self.fresh("input")
        path = root / "one"
        path.write_bytes(b"fixture")
        os.link(path, root / "two")
        with self.assertRaisesRegex(ValueError, "hardlinked"):
            assembly.inventory(root)

    def test_unresolved_qml_is_reported_and_preserved(self):
        root = self.fresh("input")
        (root / "icons/inputs/valmax").mkdir(parents=True)
        (root / "ui_native.py").write_text("PATCH_FILES = []", "utf8")
        (root / "icons/icon-pack-plus-valmax.rcc").write_bytes(b"synthetic resource")
        page = root / "Retained.qml"
        page.write_text('import QtQuick\nItem { property string endpoint: "http://127.0.0.1:18973/retention" }', "utf8")
        proof = {"nativeRuntime": True, "generatedArtifacts": {name: assembly.sha(root/name) for name in ("ui_native.py", "icons/icon-pack-plus-valmax.rcc")},
                 "sourceHashes": {":/qt/qml/OneMeUi/Retained.qml": assembly.sha(page)}, "extraResourceHashes": {}}
        (root / "native-qml-proof.json").write_text(json.dumps(proof), "utf8")
        _, unresolved = assembly.generated_sources(root)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]["resource"], ":/qt/qml/OneMeUi/Retained.qml")
        self.assertIn(b"127.0.0.1:18973", page.read_bytes())
        page.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "QML changed"):
            assembly.generated_sources(root)

    def test_no_targets_or_old_launcher_imports_in_builder(self):
        source = Path(assembly.__file__).read_text("utf8")
        for forbidden in ("subprocess.run(", "subprocess.Popen(", "ctypes.WinDLL(", "import launch_mod", "import package_mod", "import privacy_server"):
            self.assertNotIn(forbidden, source)
        self.assertEqual({entry["id"] for entry in assembly.LIMITATIONS},
                         {"message_retention", "early_protection", "installed_full_client"})

    def pe_fixture(self, root, declarations):
        for name in declarations:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"synthetic-not-an-executable")
        def fake(path, fast_load):
            machine, direct, delay = declarations[Path(path).relative_to(root).as_posix()]
            def imports(names):
                return [SimpleNamespace(dll=name.encode("ascii")) for name in names]
            return SimpleNamespace(FILE_HEADER=SimpleNamespace(Machine=machine),
                                   OPTIONAL_HEADER=SimpleNamespace(DATA_DIRECTORY=[SimpleNamespace(VirtualAddress=0, Size=0) for _ in range(14)]),
                                   DIRECTORY_ENTRY_IMPORT=imports(direct), DIRECTORY_ENTRY_DELAY_IMPORT=imports(delay),
                                   parse_data_directories=lambda **_: None, close=lambda: None)
        return fake

    def test_dependency_closure_keeps_service_context_and_delay_edges(self):
        root = self.fresh("runtime")
        fake = self.pe_fixture(root, {
            "mod/max.exe": (0x8664, ["config.dll"], []),
            "mod/config.dll": (0x8664, [], []),
            "mod/max-service/max-service.exe": (0x8664, ["config.dll"], ["service-only.dll"]),
            "mod/max-service/config.dll": (0x8664, [], []),
            "mod/max-service/service-only.dll": (0x8664, [], []),
        })
        with mock.patch.object(assembly.pefile, "PE", side_effect=fake):
            result = assembly.dependency_closure(root, assembly.inventory(root))
        self.assertTrue(result["verified"])
        targets = {(edge["image"], edge["kind"]): edge["target"] for edge in result["edges"]}
        self.assertEqual(targets["mod/max.exe", "direct"], "mod/config.dll")
        self.assertEqual(targets["mod/max-service/max-service.exe", "direct"], "mod/max-service/config.dll")
        self.assertEqual(targets["mod/max-service/max-service.exe", "delay"], "mod/max-service/service-only.dll")

    def test_wrong_architecture_or_other_branch_is_not_closure(self):
        root = self.fresh("runtime")
        fake = self.pe_fixture(root, {
            "mod/max.exe": (0x8664, ["wrong.dll", "elsewhere.dll"], []),
            "mod/wrong.dll": (0x14c, [], []),
            "transfer/elsewhere.dll": (0x8664, [], []),
        })
        with mock.patch.object(assembly.pefile, "PE", side_effect=fake), mock.patch.object(assembly, "system_dependency", return_value=None):
            result = assembly.dependency_closure(root, assembly.inventory(root))
        self.assertFalse(result["verified"])
        self.assertEqual({edge["resolution"] for edge in result["missing"]}, {"unresolved", "architecture-mismatch"})

    def test_system_component_store_hardlinks_are_observed_not_copied(self):
        root = self.fresh("runtime")
        fake = self.pe_fixture(root, {"webview2/browser.dll": (0x8664, ["bthprops.cpl"], [])})
        windows = self.fresh("windows")
        (windows / "System32").mkdir()
        system_file = windows / "System32/bthprops.cpl"
        system_file.write_bytes(b"synthetic-windows-component")
        os.link(system_file, windows / "component-store-link")
        with mock.patch.object(assembly.pefile, "PE", side_effect=fake), mock.patch.object(assembly, "system_dependency", return_value=None), mock.patch.dict(os.environ, {"SystemRoot": str(windows)}):
            result = assembly.dependency_closure(root, assembly.inventory(root))
        self.assertTrue(result["verified"])
        self.assertEqual(result["edges"][0]["resolution"], "windows-system-file-present-on-build-host")
        self.assertEqual(system_file.stat().st_nlink, 2)
        self.assertEqual([row["path"] for row in assembly.inventory(root)], ["webview2/browser.dll"])

    def test_resume_metadata_never_overwrites_different_bytes(self):
        root = self.fresh("runtime")
        path = root / "status.json"
        assembly.write_exact_or_new(path, b"original")
        assembly.write_exact_or_new(path, b"original")
        with self.assertRaisesRegex(ValueError, "differs"):
            assembly.write_exact_or_new(path, b"different")
        self.assertEqual(path.read_bytes(), b"original")

    def test_virtual_plan_closure_runs_before_destination_exists(self):
        source_root = self.fresh("sources")
        fake = self.pe_fixture(source_root, {
            "mod/new.dll": (0x8664, ["dependency.dll"], []),
            "mod/dependency.dll": (0x8664, [], []),
        })
        rows = assembly.inventory(source_root)
        future = self.root / (self._testMethodName + "-not-created")
        source_paths = {row["path"]: source_root / row["path"] for row in rows}
        with mock.patch.object(assembly.pefile, "PE", side_effect=fake):
            result = assembly.dependency_closure(future, rows, source_paths)
        self.assertTrue(result["verified"])
        self.assertFalse(future.exists())
        self.assertEqual(result["edges"][0]["target"], "mod/dependency.dll")

    def test_exact_qt_lottie_omission_retains_custom_module(self):
        root = self.fresh("lottie")
        plan = assembly.Plan()
        selected = {}
        for resource in assembly.QT_LOTTIE_RECORDER_REPLACEMENTS:
            path = root / resource.rsplit("/", 1)[1]
            path.write_text("import QtQuick\nItem { // VALMAX_QTQUICK_RECORDING_PULSE\n}\n", "utf8")
            selected[resource] = assembly.sha(path)
        for destination in sorted(assembly.QT_LOTTIE_RUNTIME_FILES | {assembly.QT_LOTTIE_QMLDIR}):
            path = root / destination.replace("/", "_")
            path.write_bytes(destination.encode())
            plan.add(destination, path, assembly.sha(path), "synthetic")
        keep = root / "custom-qmldir"
        keep.write_text("module LottieModule\n", "utf8")
        plan.add("mod/qml/LottieModule/qmldir", keep, assembly.sha(keep), "synthetic")
        with mock.patch.dict(assembly.QT_LOTTIE_RECORDER_REPLACEMENTS, selected, clear=True):
            result = assembly.omit_qt_lottie(plan, {"sourceHashes": selected}, root)
        self.assertEqual(len(result["omittedRuntimeFiles"]), 7)
        self.assertEqual({row["path"] for row in plan.entries.values()}, {"mod/qml/LottieModule/qmldir"})

    def test_qt_lottie_missing_replacement_or_pe_import_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "exact native Qt Quick"):
            assembly.omit_qt_lottie(assembly.Plan(), {"sourceHashes": {}}, self.fresh("missing"))
        for kind in ("direct", "delay"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "PE import remains"):
                assembly.require_no_lottie_pe_imports({"images": 1, "edges": [{"kind": kind, "import": "Qt6Lottie.dll"}]})


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(AssemblyTests))
    print(json.dumps({"verified": result.wasSuccessful(), "tests": result.testsRun,
                      "fixture": str(AssemblyTests.root), "targetsExecuted": False}))
    raise SystemExit(0 if result.wasSuccessful() else 1)
