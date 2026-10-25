"""Pure packaging-policy checks and read-only pinned PE inspection; no deploy."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[2] / "tooling/package_transfer_helper.py"
spec = importlib.util.spec_from_file_location("transfer_package", SOURCE)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackagingPolicy(unittest.TestCase):
    def test_paths_and_relative_config(self):
        for drive in ("C", "D"):
            owned = Path(drive + ":/ValMAXBuild/build/owned/helper.exe")
            self.assertEqual(package.owned_build(owned, "test"), owned.resolve())
        for bad in (Path("D:/other/helper.exe"), Path("D:/ValMAXBuild/build"), Path("D:/ValMAXBuild/build/../other/helper.exe")):
            with self.assertRaises(ValueError):
                package.owned_build(bad, "test")
        self.assertEqual(package.below(Path("C:/ValMAXBuild/build/owned/helper.exe"), package.BUILD_ROOT, "test"),
                         Path("C:/ValMAXBuild/build/owned/helper.exe").resolve())
        for bad in (Path("relative.exe"), Path("C:/Windows/helper.exe"), Path("C:/ValMAXBuild"),
                    Path("C:/ValMAXBuild/build/file.exe:stream"), Path("C:/ValMAXBuild/build/trailing. /app.exe")):
            with self.assertRaises(ValueError):
                package.below(bad, package.BUILD_ROOT, "test")
        self.assertNotIn(b":", package.QT_CONF)
        self.assertNotIn(b"..", package.QT_CONF)
        self.assertIn(b"Plugins=plugins", package.QT_CONF)
        self.assertIn(b"QmlImports=qml", package.QT_CONF)

    def test_deterministic_serialization(self):
        self.assertEqual(package.canonical({"z": 1, "a": ["x"]}), package.canonical({"a": ["x"], "z": 1}))
        self.assertEqual(package.canonical({"name": "Перенос"}).decode(), '{"name":"Перенос"}')

    def test_dependencies_do_not_use_path_or_system_crt(self):
        inventory = {"qt6core.dll": "Qt6Core.dll", "plugins/own.dll": "plugins/own.dll"}
        qt = {"qt6gui.dll": Path("C:/ValMAXBuild/Qt/bin/Qt6Gui.dll")}
        crt = {"vcruntime140.dll": Path("C:/ValMAXBuild/msvc/VC/Redist/vcruntime140.dll")}
        self.assertEqual(package.resolve_name("qt6core.dll", "plugins/own.dll", inventory, qt, crt), ("package", "Qt6Core.dll"))
        self.assertEqual(package.resolve_name("own.dll", "plugins/consumer.dll", inventory, qt, crt), ("package", "plugins/own.dll"))
        self.assertEqual(package.resolve_name("qt6gui.dll", "app.exe", inventory, qt, crt), ("qt-bin", qt["qt6gui.dll"]))
        self.assertEqual(package.resolve_name("vcruntime140.dll", "app.exe", inventory, qt, crt), ("vc-redist", crt["vcruntime140.dll"]))
        for missing in ("synthetic-missing.dll", "vcruntime140.dll", "api-ms-valmax-synthetic-l1-0-0.dll"):
            with self.assertRaises(ValueError):
                package.resolve_name(missing, "app.exe", {}, {}, {})

    def test_api_contracts_are_explicit_os_boundary(self):
        self.assertEqual(package.system_dependency("icuuc.dll"), "windows-system-file-present-on-build-host")
        self.assertEqual(package.system_dependency("api-ms-win-crt-runtime-l1-1-0.dll"), "windows-api-contract-present-on-build-host")
        self.assertIsNone(package.system_dependency("api-ms-valmax-does-not-exist-l1-1-0.dll"))
        self.assertIsNone(package.system_dependency("vcruntime140.dll"))

    def test_real_frozen_core_parses_without_loading(self):
        # Historical immutable PE fixture only; current packaging must select
        # its EXE/core pair from the new native build receipt.
        expected = "dc64035b71fcbfb863ce7a7a23cacce65c61e4198692da449ea3a672d064d5ba"
        fixture = package.BUILD_ROOT / ("build/transfer-inputs-" + expected) / "valmax_core.dll"
        self.assertEqual(package.sha(fixture), expected)
        imports = package.pe_imports(fixture)
        self.assertTrue(imports)
        self.assertTrue(any(entry["name"] == "advapi32.dll" for entry in imports))
        self.assertFalse(any("python" in entry["name"] or "frida" in entry["name"] for entry in imports))

    def test_build_receipt_selects_exact_pair_and_rejects_stale_inputs(self):
        parent = Path("D:/ValMAXBuild/build").resolve()
        parent.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="transfer-pair-test-", dir=parent)).resolve()
        self.assertEqual(directory.parent, parent)
        try:
            executable, core = directory / "valmax-transfer.exe", directory / "valmax_core.dll"
            executable.write_bytes(b"SYNTHETIC-EXE")
            core.write_bytes(b"SYNTHETIC-CORE")
            source = "modkit/config.defaults.json"
            proof = {"schema": 1, "compiled": True, "build": str(directory),
                     "coreDll": str(core), "coreSha256": package.sha(core),
                     "artifacts": {str(executable): package.sha(executable), str(core): package.sha(core)},
                     "sourceHashes": {source: package.sha(package.ROOT / source)}}
            receipt = directory / "synthetic-build.json"
            receipt.write_text(json.dumps(proof), encoding="utf8")
            self.assertEqual(package.build_inputs(executable, receipt), (core, package.sha(core)))
            with self.assertRaisesRegex(ValueError, "input mismatch"):
                package.build_inputs(directory / "another.exe", receipt)
            for change in ("core", "exe", "source", "compiled", "unrecorded"):
                changed = json.loads(json.dumps(proof))
                if change == "core": core.write_bytes(b"DIFFERENT-CORE")
                elif change == "exe": executable.write_bytes(b"DIFFERENT-EXE")
                elif change == "source": changed["sourceHashes"][source] = "0" * 64
                elif change == "compiled": changed["compiled"] = False
                else: del changed["artifacts"][str(core)]
                receipt.write_text(json.dumps(changed), encoding="utf8")
                with self.subTest(change=change), self.assertRaises(ValueError):
                    package.build_inputs(executable, receipt)
                executable.write_bytes(b"SYNTHETIC-EXE")
                core.write_bytes(b"SYNTHETIC-CORE")
        finally:
            if directory.resolve().parent != parent or not directory.name.startswith("transfer-pair-test-"):
                raise RuntimeError("unsafe fixture cleanup")
            shutil.rmtree(directory)

    def test_direct_and_delay_imports_and_malformed_finite(self):
        class FakePE:
            def __init__(self):
                self.FILE_HEADER = NS(Machine=0x8664)
                self.OPTIONAL_HEADER = NS(DATA_DIRECTORY=[NS(VirtualAddress=0, Size=0) for _ in range(16)])
                self.OPTIONAL_HEADER.DATA_DIRECTORY[1] = NS(VirtualAddress=100, Size=20)
                self.OPTIONAL_HEADER.DATA_DIRECTORY[13] = NS(VirtualAddress=200, Size=20)
                self.DIRECTORY_ENTRY_IMPORT = [NS(dll=b"NORMAL.dll", imports=[])]
                self.DIRECTORY_ENTRY_DELAY_IMPORT = [NS(dll=b"DELAY.dll", imports=[])]
                self.closed = False
            def parse_data_directories(self, directories):
                self.directories = directories
            def close(self):
                self.closed = True
        pe = FakePE()
        with patch.object(package, "ordinary", return_value=NS(st_size=100)), patch.object(package.pefile, "PE", return_value=pe):
            self.assertEqual(package.pe_imports(Path("synthetic.exe")),
                             [{"kind": "delay", "name": "delay.dll"}, {"kind": "direct", "name": "normal.dll"}])
            self.assertTrue(pe.closed)
            self.assertEqual(pe.directories, [1, 13])
            pe.DIRECTORY_ENTRY_DELAY_IMPORT = []
            with self.assertRaisesRegex(ValueError, "unparsed"):
                package.pe_imports(Path("synthetic.exe"))
            pe.DIRECTORY_ENTRY_DELAY_IMPORT = [NS(dll=b"../bad.dll", imports=[])]
            with self.assertRaisesRegex(ValueError, "invalid import"):
                package.pe_imports(Path("synthetic.exe"))
            pe.FILE_HEADER.Machine = 0x14c
            with self.assertRaisesRegex(ValueError, "non-x64"):
                package.pe_imports(Path("synthetic.exe"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
