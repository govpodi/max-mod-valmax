"""Synthetic fail-closed checks for the build-host Rust core proof emitter."""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "tooling/prove_core_release.py"
SPEC = importlib.util.spec_from_file_location("prove_core_release", SOURCE)
tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tool)


class CoreProofValidation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="core-proof-test-", dir="E:/ValMAXBuild/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = {"passed": 346, "ignored": 4,
                          "dll": {"exports": ["valmax_" + str(i) for i in range(20)]}}

    def log(self, body):
        path = self.root / "cargo.log"
        path.write_text(body, encoding="utf8")
        return path

    def test_one_complete_real_shape_cargo_summary_is_accepted(self):
        log = self.log("Finished `release` profile [optimized] target(s)\n"
                       "running 350 tests\n"
                       "test result: ok. 346 passed; 0 failed; 4 ignored; "
                       "0 measured; 0 filtered out; finished in 1.0s\n")
        result = tool.test_result(log, time.time() - 1, self.reference)
        self.assertEqual((result["passed"], result["ignored"]), (346, 4))
        self.assertEqual(result["sha256"], tool.sha(log))

    def test_stale_filtered_or_shrunk_cargo_result_is_rejected(self):
        valid = ("Finished `release` profile [optimized] target(s)\n"
                 "running 350 tests\n"
                 "test result: ok. 346 passed; 0 failed; 4 ignored; "
                 "0 measured; 0 filtered out; finished in 1.0s\n")
        log = self.log(valid)
        os.utime(log, (time.time() - 30, time.time() - 30))
        with self.assertRaisesRegex(ValueError, "stale_cargo_test_log"):
            tool.test_result(log, time.time(), self.reference)
        for changed in (valid.replace("346 passed", "345 passed").replace("running 350", "running 349"),
                        valid.replace("0 filtered out", "1 filtered out"),
                        valid.replace("test result: ok.", "test result: FAILED.")):
            log.write_text(changed, encoding="utf8")
            with self.assertRaises(ValueError):
                tool.test_result(log, time.time() - 1, self.reference)

    def test_changed_source_or_unsafe_snapshot_path_is_rejected(self):
        source = self.root / "native/core/src/lib.rs"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"synthetic old source")
        old = hashlib.sha256(source.read_bytes()).hexdigest()
        tool.verify_source_bytes(self.root, {"native/core/src/lib.rs": old})
        source.write_bytes(b"synthetic new source")
        with self.assertRaisesRegex(ValueError, "core_source_changed"):
            tool.verify_source_bytes(self.root, {"native/core/src/lib.rs": old})
        with self.assertRaisesRegex(ValueError, "unsafe_source_snapshot"):
            tool.verify_source_bytes(self.root, {"../outside.rs": old})

    def test_embedded_external_file_is_frozen_and_mutation_is_rejected(self):
        source = self.root / "native/core/src/launcher_config.rs"
        source.parent.mkdir(parents=True)
        source.write_text('const T: &[u8] = include_bytes!("../../../modkit/config.defaults.json");',
                          encoding="utf8")
        template = self.root / "modkit/config.defaults.json"
        template.parent.mkdir()
        template.write_bytes(b'{"fixture":true}')
        source_name = "native/core/src/launcher_config.rs"
        template_name = "modkit/config.defaults.json"
        with self.assertRaisesRegex(ValueError, "core_include_not_frozen"):
            tool.verify_embedded_inputs(self.root, {source_name})
        snapshot = {source_name: tool.sha(source), template_name: tool.sha(template)}
        tool.verify_source_bytes(self.root, snapshot)
        template.write_bytes(b'{"fixture":false}')
        with self.assertRaisesRegex(ValueError, "core_source_changed:modkit/config.defaults.json"):
            tool.verify_source_bytes(self.root, snapshot)

    def test_dynamic_include_and_untracked_core_file_are_rejected(self):
        source = self.root / "native/core/src/lib.rs"
        source.parent.mkdir(parents=True)
        source.write_text('include!(concat!("generated", ".rs"));', encoding="utf8")
        with self.assertRaisesRegex(ValueError, "nonliteral_core_include"):
            tool.verify_embedded_inputs(self.root, {"native/core/src/lib.rs"})
        source.write_text("pub fn fixed() {}", encoding="utf8")
        tool.verify_core_inventory(self.root, {"native/core/src/lib.rs"})
        (source.parent / "untracked.rs").write_text("pub fn unfrozen() {}", encoding="utf8")
        with self.assertRaisesRegex(ValueError, "core_tree_inventory_changed"):
            tool.verify_core_inventory(self.root, {"native/core/src/lib.rs"})

    def test_external_cargo_path_dependency_is_rejected(self):
        native = self.root / "native"
        core = native / "core"
        core.mkdir(parents=True)
        (native / "Cargo.toml").write_text('[workspace]\nmembers=["core"]\n', encoding="utf8")
        manifest = core / "Cargo.toml"
        manifest.write_text('[package]\nname="valmax-core"\nversion="0.1.0"\n'
                            '[dependencies]\nlocal={path="../../unfrozen"}\n', encoding="utf8")
        with self.assertRaisesRegex(ValueError, "unaudited_cargo_path_dependency"):
            tool.verify_manifest_inputs(self.root, {"native/core/Cargo.toml"})
        manifest.write_text('[package]\nname="valmax-core"\nversion="0.1.0"\n'
                            '[dependencies.bin]\npath="../../unfrozen"\n', encoding="utf8")
        with self.assertRaisesRegex(ValueError, "unaudited_cargo_path_dependency"):
            tool.verify_manifest_inputs(self.root, {"native/core/Cargo.toml"})

    def test_cargo_binary_path_must_resolve_inside_frozen_core_tree(self):
        native = self.root / "native"
        core = native / "core"
        core.mkdir(parents=True)
        (native / "Cargo.toml").write_text('[workspace]\nmembers=["core"]\n', encoding="utf8")
        manifest = core / "Cargo.toml"
        external = self.root / "unfrozen.rs"
        external.write_text("fn main() {}", encoding="utf8")
        manifest.write_text('[package]\nname="valmax-core"\nversion="0.1.0"\n'
                            '[[bin]]\nname="external"\npath="../../unfrozen.rs"\n',
                            encoding="utf8")
        with self.assertRaisesRegex(ValueError, "core_binary_path_outside_tree"):
            tool.verify_manifest_inputs(self.root, {"native/core/Cargo.toml"})
        inside = core / "src/bin/test_driver.rs"
        inside.parent.mkdir(parents=True)
        inside.write_text("fn main() {}", encoding="utf8")
        manifest.write_text('[package]\nname="valmax-core"\nversion="0.1.0"\n'
                            '[[bin]]\nname="inside"\npath="src/bin/test_driver.rs"\n',
                            encoding="utf8")
        with self.assertRaisesRegex(ValueError, "core_binary_path_not_frozen"):
            tool.verify_manifest_inputs(self.root, {"native/core/Cargo.toml"})
        tool.verify_manifest_inputs(self.root,
                                    {"native/core/Cargo.toml", "native/core/src/bin/test_driver.rs"})

    def test_retention_proof_requires_complete_named_integration_run(self):
        names = "".join(f"test {name} ... ok\n" for name in tool.REQUIRED_RETENTION_TESTS)
        body = ("Finished `release` profile [optimized] target(s)\n"
                "running 7 tests\n" + names + "test result: ok. 7 passed; 0 failed; "
                "0 ignored; 0 measured; 0 filtered out; finished in 1.0s\n")
        log = self.log(body)
        self.reference["testResults"] = {"retentionInline": {"passed": 6}}
        result = tool.retention_result(log, time.time() - 1, self.reference)
        self.assertEqual(result["passed"], 7)
        log.write_text(body.replace(names, ""), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "required_retention_test_missing"):
            tool.retention_result(log, time.time() - 1, self.reference)
        log.write_text(body.replace("7 passed", "6 passed"), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "retention_test_inventory_or_result_regressed"):
            tool.retention_result(log, time.time() - 1, self.reference)

    def test_disk_floor_and_untrusted_cargo_flags_fail_before_build(self):
        with mock.patch.object(tool.shutil, "disk_usage", return_value=mock.Mock(free=11 << 30)):
            with self.assertRaisesRegex(ValueError, "insufficient_E_space_for_core_preflight"):
                tool.require_free_space(12, "insufficient_E_space_for_core_preflight")
        for variable in ("RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS",
                         "CARGO_PROFILE_RELEASE_OPT_LEVEL"):
            with mock.patch.dict(os.environ, {variable: "synthetic override"}):
                with self.assertRaisesRegex(ValueError, "untrusted_cargo_environment"):
                    tool.build_environment(self.root)

    def test_current_source_inventory_contains_toolchain_and_embedded_template(self):
        current = tool.source_snapshot(tool.core_source_paths())
        self.assertTrue(tool.CORE_REQUIRED_INPUTS <= current.keys())
        tool.verify_source_bytes(tool.ROOT, current)

    def test_exact_baseline_sha_and_export_abi_are_required(self):
        baseline = self.root / "baseline.json"
        baseline.write_text('{"ok":true,"passed":346,"failed":0,"ignored":4,'
                            '"dll":{"exports":['
                            + ",".join('"valmax_%02d"' % index for index in range(20))
                            + ']}}', encoding="utf8")
        with self.assertRaisesRegex(ValueError, "baseline_proof_changed"):
            tool.baseline(baseline, "0" * 64)
        self.assertEqual(tool.baseline(baseline, tool.sha(baseline))["passed"], 346)
        target = self.root / "mutable-target"
        release = target / "release"
        release.mkdir(parents=True)
        (release / "valmax_core.dll").write_bytes(b"synthetic DLL")
        (release / "valmax_core.dll.lib").write_bytes(b"synthetic LIB")
        with mock.patch.object(tool, "read_exports", return_value=["valmax_changed"]):
            with self.assertRaisesRegex(ValueError, "core_export_abi_changed"):
                tool.release_pair(target, time.time() - 1,
                                  tool.baseline(baseline, tool.sha(baseline)))


if __name__ == "__main__":
    unittest.main()
