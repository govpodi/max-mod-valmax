"""Synthetic builder/restore tests. Never load or modify a MAX image/profile."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import build_mod as builder


def edit(offset=2, expected=b"cd", replacement=b"XY", name="example"):
    return {"id": name, "offset": offset, "expected": expected.hex(), "replacement": replacement.hex()}


class PatchBytesTests(unittest.TestCase):
    def tail(self, offset=4, value=b"resource"):
        return {"id": "resource", "kind": "append", "offset": offset,
                "expected": "", "replacement": value.hex()}

    def test_appended_resource_reverses_exactly_with_header_patches(self):
        original = b"abcd"
        entries = [self.tail(), edit()]
        result = builder.patch_bytes(original, entries)
        self.assertEqual(result, b"abXYresource")
        self.assertEqual(builder.patch_bytes(result, entries, reverse=True), original)

    def test_appended_resource_requires_exact_eof_and_nonempty_tail(self):
        for entry in (self.tail(3), self.tail(5), self.tail(value=b"")):
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, "tail bounds"):
                builder.patch_bytes(b"abcd", [entry])

    def test_appended_resource_does_not_allow_ordinary_patch_in_tail(self):
        with self.assertRaisesRegex(ValueError, "bounds"):
            builder.patch_bytes(b"abcdresource", [self.tail(), edit(4, b"re", b"QQ")], reverse=True)

    def test_appended_resource_wrong_tail_cannot_be_restored(self):
        with self.assertRaisesRegex(ValueError, "Unexpected bytes"):
            builder.patch_bytes(b"abcdresourcX", [self.tail()], reverse=True)

    def test_multiple_append_and_unknown_kinds_are_rejected(self):
        for entries in ([self.tail(), self.tail()], [dict(edit(), kind="insert")]):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, "patch kind"):
                builder.patch_bytes(b"abcd", entries)

    def test_append_cannot_claim_replacement_source_bytes(self):
        entry = dict(self.tail(), expected="00")
        with self.assertRaisesRegex(ValueError, "tail bounds"):
            builder.patch_bytes(b"abcd", [entry])

    def test_adjacent_changes_reverse_exactly_and_input_is_immutable(self):
        original = b"abcdefghij"
        patches = [edit(), edit(4, b"ef", b"12", "adjacent")]
        changed = builder.patch_bytes(original, patches)
        self.assertEqual(changed, b"abXY12ghij")
        self.assertEqual(builder.patch_bytes(changed, patches, reverse=True), original)
        self.assertEqual(original, b"abcdefghij")

    def test_overlaps_rejected_in_either_order(self):
        patches = [edit(), edit(3, b"de", b"12", "overlap")]
        for entries in (patches, list(reversed(patches))):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValueError, "Overlapping"):
                builder.patch_bytes(b"abcdefgh", entries)

    def test_invalid_bounds_or_size_are_rejected(self):
        cases = [edit(-1), edit(7), edit(2, b"", b""), edit(2, b"cd", b"Z")]
        for entry in cases:
            with self.subTest(entry=entry), self.assertRaisesRegex(ValueError, "bounds"):
                builder.patch_bytes(b"abcdefgh", [entry])

    def test_wrong_original_bytes_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unexpected bytes"):
            builder.patch_bytes(b"abQQefgh", [edit()])

    def test_wrong_modified_bytes_rejected_when_reversing(self):
        with self.assertRaisesRegex(ValueError, "Unexpected bytes"):
            builder.patch_bytes(b"abZZefgh", [edit()], reverse=True)

    def test_patch_at_file_end_is_valid(self):
        self.assertEqual(builder.patch_bytes(b"abcd", [edit()]), b"abXY")


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="max-modkit-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.baseline = self.root / "baseline"
        self.baseline.mkdir()
        (self.baseline / "service").mkdir()
        self.originals = {"client.bin": b"abcdefghij", "service/worker.bin": b"0123456789"}
        for relative, data in self.originals.items():
            (self.baseline / relative).write_bytes(data)
        (self.baseline / "untouched.bin").write_bytes(b"leave this unchanged")
        self.specs = [
            {"relative_path": "client.bin", "sha256": builder.sha(self.originals["client.bin"]), "patches": [edit()]},
            {"relative_path": "service/worker.bin", "sha256": builder.sha(self.originals["service/worker.bin"]),
             "patches": [edit(7, b"78", b"UV", "worker")]},
        ]
        self.output = self.root / "output"

    def make_build(self):
        with patch.object(builder, "specifications", return_value=copy.deepcopy(self.specs)):
            return builder.build(self.baseline, self.output, ["privacy"])

    def assert_baseline_unchanged(self):
        for relative, data in self.originals.items():
            self.assertEqual((self.baseline / relative).read_bytes(), data)

    def test_prepare_guards_entire_source_not_just_patch_window(self):
        (self.baseline / "client.bin").write_bytes(b"abcdefghi!")
        with self.assertRaisesRegex(ValueError, "modified source"):
            builder.prepare(self.baseline, self.specs)

    def test_wrong_source_refused_before_copy_or_destination_creation(self):
        (self.baseline / "service/worker.bin").write_bytes(b"changed worker")
        with patch.object(builder, "specifications", return_value=self.specs), patch.object(builder.shutil, "copytree") as copier:
            with self.assertRaisesRegex(ValueError, "modified source"):
                builder.build(self.baseline, self.output, ["privacy"])
            copier.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_expected_bytes_mismatch_refused_before_copy(self):
        specs = copy.deepcopy(self.specs)
        specs[1]["patches"][0]["expected"] = b"ZZ".hex()
        with patch.object(builder, "specifications", return_value=specs), patch.object(builder.shutil, "copytree") as copier:
            with self.assertRaisesRegex(ValueError, "Unexpected bytes"):
                builder.build(self.baseline, self.output, ["privacy"])
            copier.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_build_verify_restore_and_source_preservation(self):
        manifest = self.make_build()
        self.assertEqual(builder.verify(self.output / "mod-manifest.json"), manifest)
        self.assertEqual((self.output / "client.bin").read_bytes(), b"abXYefghij")
        self.assertEqual((self.output / "service/worker.bin").read_bytes(), b"0123456UV9")
        self.assertEqual((self.output / "untouched.bin").read_bytes(), b"leave this unchanged")
        result = builder.restore(self.output / "mod-manifest.json")
        self.assertTrue(result["restored"])
        self.assertTrue((self.output / "restore-receipt.json").is_file())
        for relative, original in self.originals.items():
            self.assertEqual((self.output / relative).read_bytes(), original)
            self.assertEqual(builder.sha((self.output / relative).read_bytes()), builder.sha(original))
        self.assert_baseline_unchanged()

    def test_existing_destination_is_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with patch.object(builder, "specifications", return_value=self.specs):
            with self.assertRaisesRegex(ValueError, "Destination must be new"):
                builder.build(self.baseline, self.output, ["privacy"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_existing_temporary_names_are_not_overwritten_by_build_or_restore(self):
        marker = self.baseline / "client.bin.modkit-tmp"
        marker.write_bytes(b"unrelated build temporary")
        self.make_build()
        self.assertEqual((self.output / marker.name).read_bytes(), b"unrelated build temporary")
        restore_marker = self.output / "client.bin.restore-tmp"
        restore_marker.write_bytes(b"unrelated restore temporary")
        builder.restore(self.output / "mod-manifest.json")
        self.assertEqual(restore_marker.read_bytes(), b"unrelated restore temporary")
        self.assertEqual(marker.read_bytes(), b"unrelated build temporary")

    def test_atomic_replace_failure_removes_only_its_exclusive_temporary(self):
        target = self.baseline / "client.bin"
        names_before = {item.name for item in self.baseline.iterdir()}
        with patch.object(builder.os, "replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaisesRegex(OSError, "synthetic replace failure"):
                builder.atomic_replace_bytes(target, b"changed", ".restore-tmp")
        self.assertEqual(target.read_bytes(), self.originals["client.bin"])
        self.assertEqual({item.name for item in self.baseline.iterdir()}, names_before)

    def test_destination_within_source_refused_before_copy(self):
        with patch.object(builder.shutil, "copytree") as copier:
            with self.assertRaisesRegex(ValueError, "separate"):
                builder.build(self.baseline, self.baseline / "child", ["privacy"])
            copier.assert_not_called()

    def test_modified_build_bytes_inside_or_outside_patch_fail_full_hash(self):
        self.make_build()
        target = self.output / "client.bin"
        for data in (b"abQQefghij", b"abXYefghi!"):
            with self.subTest(data=data):
                target.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "does not match manifest"):
                    builder.verify(self.output / "mod-manifest.json")

    def test_changing_recorded_patched_hash_cannot_hide_bad_original(self):
        manifest = self.make_build()
        target = self.output / "client.bin"
        target.write_bytes(b"abXYefghi!")
        manifest["files"][0]["patched_sha256"] = builder.sha(target.read_bytes())
        manifest_path = self.output / "mod-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Cannot reproduce original"):
            builder.verify(manifest_path)

    def test_restore_validates_all_files_before_mutating_first_file(self):
        self.make_build()
        first = self.output / "client.bin"
        before = first.read_bytes()
        (self.output / "service/worker.bin").write_bytes(b"corrupt second file")
        with self.assertRaisesRegex(ValueError, "does not match manifest"):
            builder.restore(self.output / "mod-manifest.json")
        self.assertEqual(first.read_bytes(), before)
        self.assertFalse((self.output / "restore-receipt.json").exists())
        self.assert_baseline_unchanged()

    def test_restore_rechecks_changes_between_verify_and_preparation(self):
        manifest = self.make_build()
        first = self.output / "client.bin"
        before = first.read_bytes()
        (self.output / "service/worker.bin").write_bytes(b"corrupt after verify")
        with patch.object(builder, "verify", return_value=manifest):
            with self.assertRaisesRegex(ValueError, "changed before restore"):
                builder.restore(self.output / "mod-manifest.json")
        self.assertEqual(first.read_bytes(), before)

    def test_duplicate_resolved_payload_rejected_by_prepare_and_verify(self):
        specs = [self.specs[0], copy.deepcopy(self.specs[0])]
        with self.assertRaisesRegex(ValueError, "Duplicate payload"):
            builder.prepare(self.baseline, specs)
        manifest = self.make_build()
        manifest["files"].append(copy.deepcopy(manifest["files"][0]))
        path = self.output / "mod-manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Duplicate payload"):
            builder.verify(path)

    def test_symlink_or_reparse_point_cannot_escape_payload_root(self):
        outside_dir = self.root / "outside-payload"
        outside_dir.mkdir()
        outside = outside_dir / "client.bin"
        outside.write_bytes(self.originals["client.bin"])
        link = self.baseline / "escape"
        try:
            link.symlink_to(outside_dir, target_is_directory=True)
        except OSError as error:
            # Windows directory junctions exercise the same resolve boundary
            # and do not require the symbolic-link creation privilege.
            if os.name != "nt":
                self.skipTest(f"Directory symlink creation unavailable: {error}")
            import _winapi
            _winapi.CreateJunction(str(outside_dir), str(link))
        with self.assertRaisesRegex(ValueError, "escapes"):
            builder.payload_file(self.baseline, "escape/client.bin")
        self.assertEqual(outside.read_bytes(), self.originals["client.bin"])

    def test_resolve_containment_check_without_symlink_privileges(self):
        actual_resolve = Path.resolve
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside")

        def emulate_reparse_point(candidate, strict=False):
            if candidate == self.baseline / "client.bin":
                return outside
            return actual_resolve(candidate, strict=strict)

        with patch.object(Path, "resolve", autospec=True, side_effect=emulate_reparse_point):
            with self.assertRaisesRegex(ValueError, "escapes"):
                builder.prepare(self.baseline, self.specs)


class ManifestPathsAndComponentsTests(unittest.TestCase):
    def test_canonical_nested_posix_path_accepted(self):
        self.assertEqual(builder.safe_relative("max-service/config.dll"), Path("max-service") / "config.dll")

    def test_traversal_absolute_windows_unc_and_noncanonical_paths_refused(self):
        values = ("", ".", "../escape.bin", "a/../escape.bin", "/absolute.bin",
                  "..\\escape.bin", "a\\..\\escape.bin", "\\\\server\\share\\payload.bin",
                  "C:/payload.bin", "C:payload.bin", "//server/share/payload.bin", "a//b.bin",
                  "./a.bin", "a/./b.bin", "a.bin/", "a.bin.", "a.bin ")
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                builder.safe_relative(value)

    def test_unknown_component_refused(self):
        with self.assertRaisesRegex(ValueError, "Unknown component"):
            builder.specifications(["not_a_component"])

    def test_component_source_hash_conflict_refused(self):
        first = {"relative_path": "core.dll", "sha256": "a" * 64, "patches": []}
        second = dict(first, sha256="b" * 64)
        modules = [types.SimpleNamespace(PATCH_FILES=[first]), types.SimpleNamespace(PATCH_FILES=[second])]
        with patch.object(builder.importlib, "import_module", side_effect=modules):
            with self.assertRaisesRegex(ValueError, "Conflicting source hashes"):
                builder.specifications(["accounts", "privacy"])

    def test_windows_case_aliases_merge_before_overlap_validation(self):
        if os.name != "nt":
            self.skipTest("Windows path identity check")
        first = {"relative_path": "Core.dll", "sha256": "a" * 64, "patches": [edit()]}
        second = {"relative_path": "core.DLL", "sha256": "a" * 64, "patches": [edit(4, b"ef", b"12")]}
        modules = [types.SimpleNamespace(PATCH_FILES=[first]), types.SimpleNamespace(PATCH_FILES=[second])]
        with patch.object(builder.importlib, "import_module", side_effect=modules):
            specs = builder.specifications(["accounts", "privacy"])
        self.assertEqual(len(specs), 1)
        self.assertEqual(len(specs[0]["patches"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
