"""Build-time evidence tests. All files are synthetic; no executable is run."""
from contextlib import ExitStack
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import package_mod as package

FIXTURES = Path("C:/ValMAXBuild/fixtures/native-transfer-package")


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


class EvidenceFixture:
    def __init__(self, base):
        self.base = base
        self.repo = base / "repo"
        self.reports = self.repo / "analysis/native-migration-v1"
        self.build_root = base / "build-root"
        self.packages = self.build_root / "packages"
        self.artifacts = self.build_root / "build/fixture"
        for directory in (self.reports, self.packages, self.artifacts, self.repo / "native"):
            directory.mkdir(parents=True)
        self.source = self.repo / "native/synthetic.cpp"
        self.source.write_bytes(b"// synthetic source only\n")
        source_hashes = {"native/synthetic.cpp": package.digest(self.source)}
        self.contents = {"valmax-transfer.exe": b"synthetic executable; never executed",
                         "valmax_core.dll": b"synthetic library; never loaded",
                         "qt.conf": package.TRANSFER_QT_CONF,
                         "licenses/qt.txt": b"synthetic Qt materials",
                         "licenses/msvc.txt": b"synthetic VC materials"}
        for name in ("valmax-transfer.exe", "valmax_core.dll"):
            (self.artifacts / name).write_bytes(self.contents[name])
        files = [{"path": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                 for name, data in self.contents.items()]
        hashes = {row["path"]: row["sha256"] for row in files}
        core = hashes["valmax_core.dll"]
        self.manifest = {"schema": 1, "product": "ValMAX native transfer helper", "coreSha256": core,
                         "files": files, "distributionBlockers": []}
        manifest_data = encoded(self.manifest)
        self.manifest_hash = hashlib.sha256(manifest_data).hexdigest()
        self.root = self.packages / ("valmax-transfer-" + self.manifest_hash)
        self.root.mkdir()
        for name, data in self.contents.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (self.root / "package-manifest.json").write_bytes(manifest_data)
        hashes["package-manifest.json"] = self.manifest_hash
        self.documents = {
            "build": {"schema": 1, "compiled": True, "coreSha256": core, "sourceHashes": source_hashes,
                      "artifacts": {str(self.artifacts / name): hashes[name] for name in ("valmax-transfer.exe", "valmax_core.dll")}},
            "package": {"schema": 1, "packaged": True, "verifiedBundle": True, "distributionReady": True,
                        "distributable": True, "distributionBlockers": [], "missingNonSystemDependencies": [],
                        "package": str(self.root), "manifestSha256": self.manifest_hash, "files": len(files),
                        "bytes": sum(len(data) for data in self.contents.values()), "fileHashes": hashes},
            "runtime": {"schema": 1, "verified": True, "syntheticOnly": True, "coreSha256": core,
                        "sourceHashes": source_hashes,
                        "results": {name: {"result": {"ok": True}, "process": {"exitCode": 0, "failure": None,
                                                                                 "remainingProcesses": 0, "timedOut": False}}
                                    for name in ("preview", "controller")}},
            "startup": {"schema": 1, "verified": True, "noSdkOrPythonInPath": True, "unrelatedWorkingDirectory": True,
                        "sourceHashes": source_hashes, "fileSelected": False, "importRequested": False,
                        "process": {"exitCode": 0, "failure": None, "remainingProcesses": 0,
                                    "windowReady": True, "closeOwnerVerified": True}},
            "materials": {"schema": 1, "kind": "valmax-transfer-materials", "components": ["qt", "msvc"], "gaps": [],
                          "sourcePackageManifestSha256": "1" * 64,
                          "packagedMaterials": [row for row in files if row["path"].startswith("licenses/")]},
            "distribution": {"schema": 1, "kind": "valmax-transfer-distribution", "distributionReady": True,
                             "packageManifestSha256": self.manifest_hash, "sourcePackageManifestSha256": "1" * 64, "gaps": []},
            "summary": {"schema": 1, "verified": True, "distributionReady": True, "productionIdleStartupVerified": True,
                        "nativeCompactImportUiImplemented": True, "noPythonRuntimeInHelper": True,
                        "persistentHttpServerInHelper": False, "package": str(self.root),
                        "packageManifestSha256": self.manifest_hash, "coreSha256": core,
                        "helperSha256": hashes["valmax-transfer.exe"], "packagedFilesExcludingManifest": len(files),
                        "packageBytesExcludingManifest": sum(len(data) for data in self.contents.values())}}
        self.documents["distribution"]["packagedMaterials"] = copy.deepcopy(self.documents["materials"]["packagedMaterials"])
        self.write()

    def path(self, name):
        return self.reports / (name + ".json")

    def write_one(self, name):
        self.path(name).write_bytes(encoded(self.documents[name]))
        return {"path": str(self.path(name)), "sha256": package.digest(self.path(name))}

    def write(self):
        refs = {name: self.write_one(name) for name in ("build", "package", "materials")}
        for name in ("runtime", "startup"):
            self.documents[name].update(packageProof=refs["package"]["path"], packageProofSha256=refs["package"]["sha256"])
        self.documents["runtime"].update(buildProof=refs["build"]["path"], buildProofSha256=refs["build"]["sha256"])
        self.documents["distribution"]["materialsProof"] = refs["materials"]
        refs.update({name: self.write_one(name) for name in ("runtime", "startup", "distribution")})
        self.documents["summary"]["proofs"] = {key: value for key, value in refs.items() if key != "materials"}
        self.write_one("summary")

    def patch(self, stack):
        for key, value in (("TRANSFER_REPO", self.repo), ("TRANSFER_REPORTS", self.reports),
                           ("TRANSFER_BUILD", self.build_root), ("TRANSFER_PACKAGES", self.packages)):
            stack.enter_context(mock.patch.object(package, key, value))


class NativeTransferPackageTests(unittest.TestCase):
    def setUp(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=FIXTURES)
        self.addCleanup(self.temporary.cleanup)
        self.fixture = EvidenceFixture(Path(self.temporary.name))
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.fixture.patch(self.stack)

    def verify(self):
        return package.verify_native_transfer(self.fixture.path("summary"))

    def run_main(self, *, expected_failure=False, output_under_helper=False):
        root = self.fixture.base
        original = root / "original"
        original.mkdir()
        (original / "max.exe").write_bytes(b"synthetic program")
        manifest = original / "mod-manifest.json"
        manifest.write_text('{"files":[]}', encoding="utf-8")
        compiler = root / "ISCC.exe"
        compiler.write_bytes(b"synthetic, never run")
        output = (self.fixture.root if output_under_helper else root) / "build-output"
        commands = []
        def run(command, **_):
            commands.append(command)
            if "PyInstaller" in command:
                payload = output / "dist/ValMAX"
                payload.mkdir(parents=True)
                (payload / "ValMAX.exe").write_bytes(b"synthetic")
            else:
                # Inventory must already include the complete helper before ISCC.
                inventory = json.loads((output / "dist/ValMAX/package-files.json").read_bytes())
                self.assertIn("runtime/transfer/valmax-transfer.exe", {row["path"] for row in inventory["files"]})
                release = output / "release"
                release.mkdir()
                (release / "ValMAX-26.35.1-Setup.exe").write_bytes(b"synthetic setup, never run")
        with ExitStack() as stack:
            for target, replacement in (("launch_mod.verified_executable", lambda _: original / "max.exe"),
                                         ("program_hashes", lambda *_: {"max.exe": None, "mod-manifest.json": None}),
                                         ("verify_program_tree", lambda *_a, **_k: None), ("notices", lambda _: None),
                                         ("subprocess.run", run), ("importlib.metadata.version", lambda _: "test")):
                stack.enter_context(mock.patch("package_mod." + target, replacement))
            stack.enter_context(mock.patch("builtins.print"))
            arguments = ["--manifest", str(manifest), "--output", str(output), "--iscc", str(compiler),
                         "--native-transfer-proof", str(self.fixture.path("summary"))]
            if expected_failure:
                with self.assertRaises(ValueError):
                    package.main(arguments)
                self.assertFalse(output.exists())
                self.assertEqual(commands, [])
            else:
                self.assertEqual(package.main(arguments), 0)
                self.assertIn("/DNativeTransfer=1", commands[-1])
                report = json.loads((output / "build-report.json").read_bytes())
                self.assertEqual(report["nativeTransfer"]["manifestSha256"], self.fixture.manifest_hash)
                self.assertTrue(report["nativeTransfer"]["freshHelperOnly"])

    def test_opt_in_main_copies_before_inventory_and_sets_only_native_define(self):
        self.run_main()

    def test_not_ready_main_fails_before_creating_output_or_invoking_builder(self):
        self.fixture.documents["summary"]["distributionReady"] = False
        self.fixture.write()
        self.run_main(expected_failure=True)

    def test_native_output_cannot_mutate_the_verified_helper_input(self):
        self.run_main(expected_failure=True, output_under_helper=True)
        self.verify()

    def test_verified_copy_is_exclusive_independent_and_in_payload_inventory(self):
        verified = self.verify()
        payload = self.fixture.base / "payload"
        (payload / "runtime").mkdir(parents=True)
        target = package.copy_native_transfer(verified, payload)
        package.payload_inventory(payload)
        inventory = json.loads((payload / "package-files.json").read_bytes())
        self.assertIn("runtime/transfer/valmax-transfer.exe", {row["path"] for row in inventory["files"]})
        self.assertEqual((target / "valmax-transfer.exe").stat().st_nlink, 1)
        self.assertEqual(package.digest(target / "valmax_core.dll"), self.fixture.documents["summary"]["coreSha256"])
        with self.assertRaises(FileExistsError):
            package.copy_native_transfer(verified, payload)

    def test_current_actual_not_ready_summary_is_rejected_without_copy(self):
        # The real receipt is read-only and refuses before any package access.
        actual = Path(__file__).resolve().parents[1] / "analysis/native-migration-v1/native-transfer-helper-proof.json"
        with mock.patch.object(package, "TRANSFER_REPORTS", actual.parent), self.assertRaisesRegex(ValueError, "not distribution-ready"):
            package.verify_native_transfer(actual)

    def test_readiness_requires_full_distribution_and_materials_chain(self):
        baseline = copy.deepcopy(self.fixture.documents)
        for name, key, value in (("summary", "distributionReady", 1), ("package", "distributionReady", False),
                                 ("package", "distributionBlockers", ["missing license"]),
                                 ("distribution", "gaps", ["missing VC"]), ("materials", "components", ["qt"]),
                                 ("materials", "gaps", ["incomplete source"]),
                                 ("distribution", "sourcePackageManifestSha256", "2" * 64)):
            with self.subTest(name=name, key=key):
                self.fixture.documents = copy.deepcopy(baseline)
                self.fixture.documents[name][key] = value
                self.fixture.write()
                with self.assertRaises(ValueError):
                    self.verify()
        self.fixture.documents = copy.deepcopy(baseline)
        self.fixture.write()
        self.fixture.documents["summary"]["proofs"].pop("distribution")
        self.fixture.write_one("summary")
        with self.assertRaises(ValueError):
            self.verify()

    def test_wrong_or_missing_material_bytes_cannot_be_cleared_by_boolean(self):
        for mutate in (lambda: (self.fixture.root / "licenses/qt.txt").write_bytes(b"tampered"),
                       lambda: (self.fixture.root / "licenses/qt.txt").unlink()):
            mutate()
            with self.assertRaises((ValueError, FileNotFoundError)):
                self.verify()
            (self.fixture.root / "licenses/qt.txt").write_bytes(self.fixture.contents["licenses/qt.txt"])

    def test_extra_empty_directory_file_and_hardlink_are_rejected(self):
        extra = self.fixture.root / "unexpected"
        extra.mkdir()
        with self.assertRaises(ValueError):
            self.verify()
        extra.rmdir()
        extra.write_bytes(b"extra")
        with self.assertRaises(ValueError):
            self.verify()
        extra.unlink()
        os.link(self.fixture.root / "valmax_core.dll", self.fixture.base / "hardlink")
        with self.assertRaises(ValueError):
            self.verify()

    def test_changed_sources_artifacts_and_post_validation_source_rejected(self):
        verified = self.verify()
        artifact = self.fixture.artifacts / "valmax-transfer.exe"
        original = artifact.read_bytes()
        artifact.write_bytes(b"changed")
        with self.assertRaises(ValueError):
            self.verify()
        artifact.write_bytes(original)
        self.fixture.source.write_bytes(b"changed source")
        with self.assertRaises(ValueError):
            self.verify()
        payload = self.fixture.base / "payload"
        (payload / "runtime").mkdir(parents=True)
        (self.fixture.root / "valmax-transfer.exe").write_bytes(b"changed package")
        with self.assertRaises(ValueError):
            package.copy_native_transfer(verified, payload)
        self.assertFalse((payload / "runtime/transfer").exists())

    def test_stale_proof_reference_and_test_failure_are_rejected(self):
        self.fixture.path("build").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.verify()
        self.fixture.write()
        self.fixture.documents["runtime"]["results"]["controller"]["process"]["exitCode"] = 1
        self.fixture.write()
        with self.assertRaisesRegex(ValueError, "test failed"):
            self.verify()

    def test_strict_json_no_duplicate_nonfinite_bom_or_oversize(self):
        for data in (b'{"schema":1,"schema":1}', b'{"schema":true}', b'{"schema":1,"n":NaN}',
                     b'\xef\xbb\xbf{"schema":1}', b'{"schema":1}\xff'):
            with self.subTest(data=data):
                self.fixture.path("summary").write_bytes(data)
                with self.assertRaises(ValueError):
                    self.verify()
        self.fixture.write()
        with mock.patch.object(package, "TRANSFER_JSON_BYTES", 8):
            # Explicit limit is also tested directly (default args are frozen).
            with self.assertRaises(ValueError):
                package._transfer_read(self.fixture.path("summary"), 8)

    def test_relative_paths_and_case_parent_aliases(self):
        for name in ("../outside", "/root", "C:/outside", "a\\b", "a/../b", "a//b", "a./b", "NUL.txt", "a:x", "a/", "a\x00b"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package._transfer_records([{"path": name, "bytes": 1, "sha256": "0" * 64}])
        for names in (("A/x", "a/y"), ("A", "a"), ("a", "a/b")):
            with self.subTest(names=names), self.assertRaises(ValueError):
                package._transfer_records([{"path": name, "bytes": 1, "sha256": "0" * 64} for name in names])

    def test_arbitrary_proof_path_is_not_read(self):
        self.fixture.documents["summary"]["proofs"]["build"]["path"] = str(self.fixture.base / "outside.json")
        self.fixture.write_one("summary")
        with self.assertRaisesRegex(ValueError, "escapes allowed roots"):
            self.verify()

    def test_reparse_package_and_ancestor_are_rejected(self):
        # Junction creation works without Developer Mode and never targets user data.
        target = self.fixture.base / "owned-junction-target"
        target.mkdir()
        junction = self.fixture.root / "linked"
        result = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True,
                                creationflags=subprocess.CREATE_NO_WINDOW, check=False)
        self.assertEqual(result.returncode, 0, "own synthetic junction must be created")
        try:
            with self.assertRaises(ValueError):
                self.verify()
            with self.assertRaises(ValueError):
                package._transfer_path(junction, [self.fixture.root])
        finally:
            junction.rmdir()  # Exact junction only, not its target.


class LegacyAndInstallerTests(unittest.TestCase):
    def test_default_main_never_reads_native_proof_and_keeps_legacy_command(self):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=FIXTURES) as directory, ExitStack() as stack:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            (runtime / "max.exe").write_bytes(b"synthetic")
            manifest = runtime / "mod-manifest.json"
            manifest.write_text('{"files":[]}', encoding="utf-8")
            iscc = root / "ISCC.exe"
            iscc.write_bytes(b"synthetic; never executed")
            output = root / "out"
            commands = []
            def run(command, **_):
                commands.append(command)
                if "PyInstaller" in command:
                    payload = output / "dist/ValMAX"
                    payload.mkdir(parents=True)
                    (payload / "ValMAX.exe").write_bytes(b"synthetic")
                else:
                    release = output / "release"
                    release.mkdir()
                    (release / "ValMAX-26.35.1-Setup.exe").write_bytes(b"synthetic installer; never executed")
            for target, replacement in (("launch_mod.verified_executable", lambda _: runtime / "max.exe"),
                                         ("program_hashes", lambda *_: {"max.exe": None, "mod-manifest.json": None}),
                                         ("verify_program_tree", lambda *_a, **_k: None), ("notices", lambda _: None),
                                         ("subprocess.run", run), ("importlib.metadata.version", lambda _: "test")):
                stack.enter_context(mock.patch("package_mod." + target, replacement))
            native = stack.enter_context(mock.patch.object(package, "verify_native_transfer", side_effect=AssertionError("must not read")))
            stack.enter_context(mock.patch("builtins.print"))
            self.assertEqual(package.main(["--manifest", str(manifest), "--output", str(output), "--iscc", str(iscc)]), 0)
            native.assert_not_called()
            self.assertFalse(any("NativeTransfer" in item for item in commands[-1]))
            self.assertFalse((output / "dist/ValMAX/runtime/transfer").exists())
            self.assertNotIn("nativeTransfer", json.loads((output / "build-report.json").read_bytes()))

    def test_inno_native_shortcut_is_isolated_recovery_and_appid_preserved(self):
        text = (Path(__file__).parent / "max-mod-setup.iss").read_text(encoding="utf-8")
        self.assertIn('Filename: "{app}\\runtime\\transfer\\valmax-transfer.exe"; WorkingDir: "{app}\\runtime\\transfer"', text)
        self.assertIn('Parameters: "recover"; WorkingDir: "{app}"', text)
        self.assertIn('Parameters: "mdata"; WorkingDir: "{app}"', text)
        self.assertIn('AppId={{419706B9-6FC7-46F8-B9FB-D7212FC91BAC}', text)
        self.assertIn("OR Name = ''valmax-transfer.exe''", text)
        self.assertNotIn("[InstallDelete]", text)
        self.assertIn("NativeTransferPreflight", text)


if __name__ == "__main__":
    unittest.main()
