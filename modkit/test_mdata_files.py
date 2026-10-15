"""Synthetic filesystem regression tests; never access MAX/WinCred/user data."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import mdata_files as files
import quick_reply_store as guards
import session_credentials


class MDataFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="maxmod-mdata-files-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "MXMOD"
        self.root.mkdir()
        self.credentials = patch.object(session_credentials, "_api",
                                        side_effect=AssertionError("WinCred is forbidden in filesystem tests"))
        self.credentials.start()
        self.addCleanup(self.credentials.stop)

    def put(self, relative, data=b"synthetic-only"):
        path = self.root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def stage(self, suffix="synthetic"):
        root = self.base / (".MXMOD-MData-stage-" + suffix)
        root.mkdir()
        return root

    @contextmanager
    def junction(self, destination, target):
        if os.name != "nt":
            self.skipTest("Windows junction semantics")
        result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(destination), str(target)],
                                capture_output=True, check=False, timeout=10)
        if result.returncode:
            self.skipTest("Cannot create synthetic junction")
        try:
            yield destination
        finally:
            if destination.exists():
                os.rmdir(destination)  # Remove only the synthetic junction itself.

    def test_profile_root_must_be_own_namespace(self):
        self.assertEqual(self.root, files.profile_path(self.root))
        for target in (self.base / "ONEME", self.base / "other",
                       self.base / "ONEME" / "MXMOD"):
            with self.subTest(target=target.name):
                with self.assertRaises(files.MDataFileError):
                    files.profile_path(target)

    def test_listing_keeps_unicode_persistent_files_and_database_sidecars(self):
        fixture = {"auth_42": b"{}", "user42/history/synthetic.db": b"database",
                   "user42/history/synthetic.db-wal": b"WAL",
                   "user42/history/synthetic.db-shm": b"SHM",
                   "user42/history/synthetic.db-journal": b"journal",
                   "user42/webapps/7/данные.log": b"leveldb journal",
                   "user42/files/Фото 1.png": b"image", "user42/settings": b"[settings]"}
        for path, data in fixture.items():
            self.put(path, data)
        self.assertEqual({p: len(data) for p, data in fixture.items()},
                         {entry["path"]: entry["size"] for entry in files.listing(self.root)})

    def test_listing_uses_physical_directory_yielded_by_trusted_guard(self):
        self.put("cache/alias-only", b"must-not-be-listed")
        alias = self.root / "cache"
        physical = self.base / "synthetic-mapped-cache"
        kept = physical / "synthetic" / "ONEME" / "keep.bin"
        kept.parent.mkdir(parents=True)
        kept.write_bytes(b"physical-only")
        real_directory = files._directory

        @contextmanager
        def trusted_directory(path):
            if path == alias:
                # Model a previously validated exact MSIX mapping, not a link.
                with real_directory(physical) as actual:
                    yield actual
            else:
                with real_directory(path) as actual:
                    yield actual

        with patch.object(files, "_directory", trusted_directory):
            result = files.listing(self.root)
        self.assertEqual([{"path": "cache/synthetic/ONEME/keep.bin", "size": len(b"physical-only")}], result)

    def test_known_diagnostics_and_transient_archives_are_excluded(self):
        for relative in ("logs/events.txt", "user42/CrashDumps/crash.bin", "mdata-result.json",
                         "session-transfer-result.json", ".mdata-stage.tmp", ".mxsession-synthetic.tmp",
                         "backup.mdata", "backup.maxsession"):
            self.put(relative)
        self.put("config.json", b"{}")
        self.assertEqual([{"path": "config.json", "size": 2}], files.listing(self.root))

    def test_file_count_and_byte_caps_fail_without_large_allocation(self):
        self.put("one", b"1")
        self.put("two", b"2")
        for option, value in (("MAX_FILES", 1), ("MAX_BYTES", 1)):
            with self.subTest(option=option), patch.object(files, option, value):
                with self.assertRaises(files.MDataFileError) as caught:
                    files.listing(self.root)
                self.assertEqual("too_large", caught.exception.code)

    def test_hardlinked_regular_file_is_refused_by_listing_and_source(self):
        source = self.put("original")
        try:
            os.link(source, self.root / "second")
        except OSError:
            self.skipTest("Filesystem does not support synthetic hardlink")
        with self.assertRaises(files.MDataFileError):
            files.listing(self.root)
        with self.assertRaises((ValueError, OSError)):
            with files.source(self.root, "original"):
                self.fail("Hardlinked source was opened")

    def test_directory_junction_cannot_escape_listing(self):
        outside = self.base / "synthetic-outside"
        outside.mkdir()
        marker = outside / "keep.txt"
        marker.write_bytes(b"outside-only")
        with self.junction(self.root / "escape", outside):
            with self.assertRaises(files.MDataFileError):
                files.listing(self.root)
            self.assertEqual(b"outside-only", marker.read_bytes())

    def test_sink_refuses_traversal_and_unsupported_names_before_creation(self):
        stage = self.stage()
        for relative in ("../escape", "/absolute", "a\\escape", "a:stream", "CON", "a/../b",
                         "a/trailing.", "a/trailing ", "logs/output.txt"):
            with self.subTest(path=relative):
                with self.assertRaises(files.MDataFileError):
                    with files.sink(stage, relative, 1):
                        self.fail("Unsafe sink was opened")
        self.assertEqual([], list(stage.iterdir()))

    def test_sink_uses_exclusive_creation_and_cleans_failed_new_file(self):
        stage = self.stage()
        with files.sink(stage, "каталог/файл", 3) as output:
            output.write(b"one")
        with self.assertRaises(FileExistsError):
            with files.sink(stage, "каталог/файл", 3):
                self.fail("Existing staging file overwritten")
        self.assertEqual(b"one", (stage / "каталог" / "файл").read_bytes())
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with files.sink(stage, "new", 3) as output:
                output.write(b"one")
                raise RuntimeError("synthetic failure")
        self.assertFalse((stage / "new").exists())

    def test_sink_parent_junction_is_refused(self):
        stage = self.stage()
        outside = self.base / "synthetic-outside"
        outside.mkdir()
        with self.junction(stage / "escape", outside):
            with self.assertRaises((ValueError, OSError)):
                with files.sink(stage, "escape/file", 1):
                    self.fail("Junction sink was opened")
        self.assertEqual([], list(outside.iterdir()))

    def test_source_is_readonly_and_blocks_other_writers_while_open_on_windows(self):
        path = self.put("source", b"abc")
        with files.source(self.root, "source") as stream:
            self.assertEqual(b"abc", stream.read())
            self.assertFalse(stream.writable())
            if os.name == "nt":
                with self.assertRaises(OSError):
                    with path.open("ab"):
                        self.fail("Pinned source permits concurrent writer")

    def test_source_opens_the_pinned_physical_parent_from_directory_guard(self):
        relative = "cache/synthetic/ONEME/empty.synthetic"
        alias_parent = self.root / "cache" / "synthetic" / "ONEME"
        alias_parent.mkdir(parents=True)
        physical_parent = self.base / "synthetic-mapped-cache" / "synthetic" / "ONEME"
        physical_parent.mkdir(parents=True)
        kept = physical_parent / "empty.synthetic"
        kept.touch()
        real_directory = guards._directory

        @contextmanager
        def trusted_directory(path):
            mapped = physical_parent if path == alias_parent else path
            with real_directory(mapped) as actual:
                yield actual

        self.assertFalse((alias_parent / "empty.synthetic").exists())
        with patch.object(guards, "_directory", trusted_directory):
            with files.source(self.root, relative) as source:
                self.assertEqual(b"", source.read(0))
                self.assertFalse(source.writable())
                self.assertEqual(files._identity(kept.stat()), files._identity(os.fstat(source.fileno())))

    def test_atomic_output_refuses_existing_and_cleans_failed_stage(self):
        output = self.base / "synthetic.mdata"
        output.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            with files.atomic_output(output):
                self.fail("Existing export overwritten")
        self.assertEqual(b"existing", output.read_bytes())
        failed = self.base / "failed.mdata"
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            with files.atomic_output(failed) as stream:
                stream.write(b"partial")
                raise RuntimeError("synthetic failure")
        self.assertFalse(failed.exists())
        self.assertEqual([], list(self.base.glob(".mdata-*.tmp")))

    def test_atomic_output_publication_does_not_replace_racing_existing_file(self):
        destination = self.base / "race.mdata"
        with self.assertRaises(FileExistsError):
            with files.atomic_output(destination) as output:
                output.write(b"encrypted-synthetic")
                destination.write_bytes(b"other-writer")
        self.assertEqual(b"other-writer", destination.read_bytes())
        self.assertEqual([], list(self.base.glob(".mdata-*.tmp")))

    def test_move_checks_containment_collision_and_expected_identity(self):
        source = self.stage()
        (source / "keep").write_bytes(b"data")
        destination = self.base / "MXMOD-restored"
        identity = files._identity(source.lstat())
        with self.assertRaises(files.MDataFileError):
            files.move_owned(source, self.base / "sub" / "outside", self.base)
        with self.assertRaises(files.MDataFileError):
            files.move_owned(source, destination, self.base, expected=(0, 0))
        destination.mkdir()
        with self.assertRaises(files.MDataFileError):
            files.move_owned(source, destination, self.base, expected=identity)
        destination.rmdir()
        files.move_owned(source, destination, self.base, expected=identity)
        self.assertFalse(source.exists())
        self.assertEqual(b"data", (destination / "keep").read_bytes())

    def test_cleanup_only_accepts_private_sibling_staging_tree(self):
        self.put("keep")
        with self.assertRaises(files.MDataFileError):
            files.remove_staging(self.root, self.base)
        self.assertTrue((self.root / "keep").exists())
        stage = self.stage()
        (stage / "sub").mkdir()
        (stage / "sub" / "file").write_bytes(b"synthetic")
        files.remove_staging(stage, self.base)
        self.assertFalse(stage.exists())

    def test_cleanup_refuses_junction_without_deleting_outside_file(self):
        stage = self.stage()
        outside = self.base / "synthetic-outside"
        outside.mkdir()
        marker = outside / "keep"
        marker.write_bytes(b"outside-only")
        with self.junction(stage / "escape", outside):
            with self.assertRaises((ValueError, OSError)):
                files.remove_staging(stage, self.base)
            self.assertEqual(b"outside-only", marker.read_bytes())


if __name__ == "__main__":
    unittest.main()
