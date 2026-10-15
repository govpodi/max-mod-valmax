"""Pure metadata and temporary synthetic files only; no MAX or real WinCred."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import native_crash_diagnostics as module


SECRET = "SYNTHETIC_TOKEN_LOG_PATH_DO_NOT_PERSIST"


def event(pid=17):
    return {"timestampUTC": "2026-10-13T16:50:54Z", "role": "main", "pid": pid,
            "frames": [{"module": "ucrtbase.dll", "rva": 0xa527e},
                       {"module": "max.exe", "rva": 0x1234}]}


class PureNativeCrashTests(unittest.TestCase):
    def test_serialization_is_bounded_metadata_only(self):
        value = {"schema": 1, "events": [event()]}
        raw = module.serialize(value)
        self.assertEqual(json.loads(raw), value)
        self.assertTrue(module.validate(value))
        self.assertNotIn(SECRET.encode(), raw)
        self.assertEqual(set(json.loads(raw)), {"schema", "events"})

    def test_worst_case_four_by_twenty_four_frames_fits_limit(self):
        item = event(2**32 - 1)
        item["frames"] = [{"module": "m" * 64, "rva": 2**32 - 1} for _ in range(24)]
        raw = module.serialize({"schema": 1, "events": [copy.deepcopy(item) for _ in range(4)]})
        self.assertLessEqual(len(raw), module.MAX_BYTES)

    def test_unknown_fields_types_paths_counts_and_invalid_dates_rejected(self):
        cases = []
        for key, value in (("role", SECRET), ("pid", True), ("pid", 0), ("pid", 2**32),
                           ("timestampUTC", "2026-02-30T00:00:00Z"), ("timestampUTC", SECRET)):
            item = event(); item[key] = value; cases.append(item)
        item = event(); item["token"] = SECRET; cases.append(item)
        for frame in ({"module": "C:\\private\\" + SECRET, "rva": 1},
                      {"module": "../max.exe", "rva": 1},
                      {"module": "max.exe", "rva": True},
                      {"module": "max.exe", "rva": 2**32},
                      {"module": "max.exe", "rva": -1},
                      {"module": "max.exe", "rva": float("nan")},
                      {"module": "max.exe", "rva": 1, "message": SECRET}):
            item = event(); item["frames"] = [frame]; cases.append(item)
        item = event(); item["frames"] *= 13; cases.append(item)
        for item in cases:
            with self.subTest(kind=list(item)):
                value = {"schema": 1, "events": [item]}
                self.assertFalse(module.validate(value))
                with self.assertRaises(ValueError) as error:
                    module.serialize(value)
                self.assertEqual(str(error.exception), "invalid_native_crash_metadata")
        self.assertFalse(module.validate({"schema": True, "events": [event()]}))
        self.assertFalse(module.validate({"schema": 1, "events": [event()] * 5}))

    def test_hostile_subclasses_are_not_inspected_or_formatted(self):
        class Hostile(list):
            def __iter__(self):
                raise AssertionError(SECRET)
            def __str__(self):
                raise AssertionError(SECRET)
        value = {"schema": 1, "events": [event()]}
        value["events"][0]["frames"] = Hostile()
        self.assertFalse(module.validate(value))


class NativeCrashWriterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-crash-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.directory = self.base / ".mxmod-helper-temp"
        self.writer = module.NativeCrashDiagnostics(self.directory)
        self.addCleanup(patch.stopall)
        patch("session_credentials._api", side_effect=AssertionError("WinCred forbidden")).start()

    def prepare(self):
        self.directory.mkdir()

    def record(self, pid=17):
        return self.writer.record(event()["frames"], role="main", pid=pid)

    def report(self):
        return json.loads(self.writer.path.read_bytes())

    def assert_no_temps(self):
        self.assertEqual([], list(self.directory.glob(".native-crash-*.tmp")))

    def test_constructor_is_lazy_and_missing_parent_is_not_created(self):
        with patch.object(module, "_parents", side_effect=AssertionError("filesystem")):
            module.NativeCrashDiagnostics(self.directory)
        self.assertFalse(self.record())
        self.assertFalse(self.directory.exists())

    def test_constructor_refuses_arbitrary_profile_and_relative_destinations(self):
        for path in (self.base / "MXMOD" / ".mxmod-helper-temp", self.base,
                     Path("relative") / ".mxmod-helper-temp", self.base / "ONEME" / ".mxmod-helper-temp"):
            with self.subTest(pathKind=path.name), self.assertRaises(ValueError):
                module.NativeCrashDiagnostics(path)

    def test_atomic_create_append_keeps_last_four_after_new_writer(self):
        self.prepare()
        self.assertTrue(self.record(1))
        self.writer = module.NativeCrashDiagnostics(self.directory)
        for pid in range(2, 7):
            self.assertTrue(self.record(pid))
        value = self.report()
        self.assertEqual([item["pid"] for item in value["events"]], [3, 4, 5, 6])
        self.assertTrue(module.validate(value))
        self.assertLessEqual(self.writer.path.stat().st_size, module.MAX_BYTES)
        self.assert_no_temps()

    def test_invalid_incoming_metadata_creates_no_files(self):
        self.prepare()
        self.assertFalse(self.writer.record([{"module": "max.exe", "rva": 1, "token": SECRET}], role="main"))
        self.assertEqual([], list(self.directory.iterdir()))

    def test_malformed_duplicate_nan_and_oversize_existing_are_preserved(self):
        self.prepare()
        for raw in (b'{"schema":1,"schema":1,"events":[]}', b'{"schema":1,"events":NaN}',
                    b'not-json-' + SECRET.encode(), b'x' * (module.MAX_BYTES + 1)):
            self.writer.path.write_bytes(raw)
            self.assertFalse(self.record())
            self.assertEqual(self.writer.path.read_bytes(), raw)
            self.assert_no_temps()

    def test_existing_hardlink_is_refused_without_mutating_either_name(self):
        self.prepare()
        original = self.base / "synthetic-victim.json"
        raw = module.serialize({"schema": 1, "events": [event()]})
        original.write_bytes(raw)
        os.link(original, self.writer.path)
        self.assertFalse(self.record())
        self.assertEqual(original.read_bytes(), raw)
        self.assertEqual(self.writer.path.read_bytes(), raw)
        self.assert_no_temps()

    def test_existing_symlink_is_refused_without_reading_or_changing_target(self):
        self.prepare()
        target = self.base / "synthetic-victim.json"
        target.write_bytes(SECRET.encode())
        try:
            self.writer.path.symlink_to(target)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        self.assertFalse(self.record())
        self.assertEqual(target.read_bytes(), SECRET.encode())
        self.assert_no_temps()

    def test_wrong_canonical_parent_is_refused(self):
        from contextlib import contextmanager
        self.prepare()
        @contextmanager
        def redirected(*_args, **_kwargs):
            yield self.base
        with patch.object(module, "_parents", redirected):
            self.assertFalse(self.record())
        self.assertFalse(self.writer.path.exists())

    def test_fsync_and_publish_failure_preserve_previous_and_remove_temporary(self):
        self.prepare()
        self.assertTrue(self.record(1))
        previous = self.writer.path.read_bytes()
        for target in ("os.fsync", "_publish"):
            with self.subTest(stage=target), patch.object(module if target == "_publish" else module.os,
                                                        target.rsplit(".", 1)[-1], side_effect=OSError(28, SECRET)):
                self.assertFalse(self.record(2))
            self.assertEqual(self.writer.path.read_bytes(), previous)
            self.assert_no_temps()

    def test_changed_target_before_publish_is_not_overwritten(self):
        self.prepare()
        self.assertTrue(self.record(1))
        original = module._publish
        replacement = module.serialize({"schema": 1, "events": [event(99)]})
        def changed(temporary, path, previous):
            path.unlink()
            path.write_bytes(replacement)
            original(temporary, path, previous)
        with patch.object(module, "_publish", side_effect=changed):
            self.assertFalse(self.record(2))
        self.assertEqual(self.writer.path.read_bytes(), replacement)
        self.assert_no_temps()

    def test_concurrent_cooperating_writers_leave_four_valid_events(self):
        self.prepare()
        barrier = threading.Barrier(6)
        results = []
        def write(pid):
            writer = module.NativeCrashDiagnostics(self.directory)
            barrier.wait(timeout=5)
            results.append(writer.record(event()["frames"], role="service", pid=pid))
        threads = [threading.Thread(target=write, args=(pid,)) for pid in range(1, 7)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(8)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(results, [True] * 6)
        value = self.report()
        self.assertTrue(module.validate(value))
        self.assertEqual(len({item["pid"] for item in value["events"]}), 4)
        self.assert_no_temps()


if __name__ == "__main__":
    unittest.main()
