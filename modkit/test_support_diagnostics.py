"""Synthetic allowlist and new-file export tests; no real profile or network."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import support_diagnostics as target


def sample():
    return target.build_report(
        versions={"valmax": "8.8.2", "max": "26.35.1.80662", "windows": "10.0.26100", "python": "3.13.5"},
        startup={"role": "observe", "stage": "complete", "state": "ready", "code": "none", "operationId": "PRIVATE-SENTINEL"},
        privacy={"verified": True, "observation": {"active": True, "seconds": 6, "blockedCalls": 3,
            "tracerHooks": 2, "transportHooks": 4, "processes": 1},
            "dashboard": {"observedPackets": 5, "requestTypesComplete": True}, "errors": {"hook_failed": 2}},
        ghost={"revision": "PRIVATE-SENTINEL", "settings": {"enabled": False, "hideRead": True}},
        optimization={"settings": {"enabled": True, "historyCache": False}})


class AllowlistTests(unittest.TestCase):
    def test_expected_fields_and_no_operation_or_revision_ids(self):
        report = sample(); raw = target.serialize(report)
        self.assertEqual(report, json.loads(raw))
        self.assertLess(len(raw), target.MAX_BYTES)
        self.assertEqual(report["startup"], {"role": "observe", "stage": "complete", "state": "ready", "code": "none"})
        self.assertEqual(report["counters"]["observation"]["processes"], 1)
        self.assertEqual(report["errors"]["hook_failed"], 2)
        self.assertFalse(report["flags"]["ghost"]["enabled"])
        self.assertTrue(all(value == 0 for value in target.build_report(privacy={"errors": {}})["errors"].values()))
        self.assertTrue(all(value is None for value in target.build_report()["errors"].values()))
        for sentinel in (b"PRIVATE-SENTINEL", b"operationId", b"revision", b"timestamp", b"pid"):
            self.assertNotIn(sentinel, raw)

    def test_secrets_nested_in_broader_snapshot_are_never_serialized(self):
        secret = "PRIVATE-SENTINEL"
        report = target.build_report(
            versions={"valmax": secret, "max": "26.35.1.80662", "machine": secret},
            startup={"role": "observe", "stage": "complete", "state": "error", "code": secret, "operationId": secret},
            privacy={"notes": [secret], "rules": [{"detail": secret}], "connections": [{"host": secret}],
                "requests": [{"fields": [secret]}], "fatalFrames": [{"module": secret}], "accountId": secret,
                "observation": {"active": secret, "seconds": secret, "coverage": secret, "pid": secret},
                "dashboard": {"observedBytes": secret, "externalDestinations": secret}, "errors": {secret: 4}},
            ghost={"settings": {"enabled": secret, "accountId": secret}, "revision": secret},
            optimization={"settings": {"enabled": 1}, "appliedSettings": {"private": secret}})
        raw = target.serialize(report)
        self.assertNotIn(secret.encode(), raw)
        self.assertIsNone(report["startup"])
        self.assertIsNone(report["versions"]["valmax"])
        self.assertIsNone(report["flags"]["optimization"]["enabled"])
        self.assertNotIn("externalDestinations", report["counters"]["dashboard"])

    def test_type_and_size_checks_fail_closed(self):
        for value in (-1, True, target.MAX_COUNT + 1, float("nan"), "4", [4]):
            report = target.build_report(privacy={"observation": {"seconds": value}})
            self.assertIsNone(report["counters"]["observation"]["seconds"])
        for value in ("8.8.2 Beta", "C:/private", "+79999999999", "1.2\n", "1.2.3.4.5", "999999.1", 3):
            report = target.build_report(versions={"valmax": value})
            self.assertIsNone(report["versions"]["valmax"])
        target.serialize(target.build_report())

    def test_serialization_rejects_additional_fields_at_every_level(self):
        paths = ((), ("versions",), ("startup",), ("flags",), ("flags", "ghost"),
            ("flags", "optimization"), ("counters",), ("counters", "dashboard"), ("errors",))
        for path in paths:
            report = copy.deepcopy(sample()); nested = report
            for key in path: nested = nested[key]
            nested["private"] = "PRIVATE-SENTINEL"
            with self.assertRaises(target.SupportDiagnosticsError) as error:
                target.serialize(report)
            self.assertEqual(error.exception.code, "invalid_report")

    def test_serialization_rejects_wrong_scalar_types_and_startup_code(self):
        for path, value in ((("schema",), True), (("versions", "valmax"), "PRIVATE-SENTINEL"),
            (("flags", "verified"), 1), (("flags", "ghost", "enabled"), 0),
            (("counters", "dashboard", "observedBytes"), -1), (("startup", "code"), "PRIVATE-SENTINEL")):
            report = sample(); nested = report
            for key in path[:-1]: nested = nested[key]
            nested[path[-1]] = value
            with self.assertRaises(target.SupportDiagnosticsError): target.serialize(report)

    def test_hostile_values_are_not_formatted_or_traversed(self):
        class Hostile(dict):
            def get(self, *_args): raise AssertionError("read")
            def __str__(self): raise AssertionError("format")
        report = target.build_report(versions=Hostile(), startup=Hostile(), privacy=Hostile(), ghost=Hostile())
        target.serialize(report)
        class HostileError(Exception):
            def __str__(self): raise AssertionError("format")
        def unavailable(): raise HostileError()
        service = target.SupportDiagnosticsService(valmax_version="8.8.2", max_version="26.35.1.80662",
            privacy=unavailable, startup=unavailable, ghost=unavailable, optimization=unavailable)
        report = service.preview()["report"]
        self.assertIsNone(report["flags"]["verified"])
        self.assertIsNone(report["startup"])

    def test_runtime_versions_are_numeric_and_settings_allowlist_tracks_source(self):
        from ghost_store import defaults
        from optimization_store import KEYS
        self.assertEqual(set(target.GHOST_FLAGS), set(defaults()))
        self.assertEqual(set(target.OPTIMIZATION_FLAGS), set(KEYS))
        versions = target.runtime_versions("8.8.2", "26.35.1.80662")
        self.assertEqual(set(versions), set(target.VERSION_KEYS))
        for value in versions.values():
            if value is not None: self.assertRegex(value, r"^[0-9]+(?:\.[0-9]+){1,3}$")


class SaveReportTests(unittest.TestCase):
    def setUp(self):
        # Test files stay on the explicitly requested D: workspace.
        self.temporary = tempfile.TemporaryDirectory(prefix="support-test-", dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_explicit_new_file_only_and_canonical_report(self):
        destination = self.directory / "выбранный отчёт.json"
        result = target.save_report(destination.as_uri(), sample())
        self.assertEqual(result, {"schema": 1, "saved": True})
        self.assertEqual(json.loads(destination.read_bytes()), sample())
        self.assertEqual(list(self.directory.iterdir()), [destination])
        self.assertNotIn(str(destination), json.dumps(result))

    def test_existing_file_is_never_overwritten(self):
        destination = self.directory / "report.json"; destination.write_bytes(b"PRESERVE")
        with self.assertRaises(target.SupportDiagnosticsError) as error:
            target.save_report(destination.as_uri(), sample())
        self.assertEqual(error.exception.code, "file_exists")
        self.assertEqual(destination.read_bytes(), b"PRESERVE")

    def test_unsafe_urls_and_missing_parent_do_not_create_files(self):
        for value in (None, "", "report.json", "https://example.invalid/report.json", "file://remote/share/report.json",
            self.directory.as_uri() + "/report.log", self.directory.as_uri() + "/report.json?private=1",
            self.directory.as_uri() + "/report.json#private", self.directory.as_uri() + "/../report.json"):
            with self.assertRaises(target.SupportDiagnosticsError) as error: target.save_report(value, sample())
            self.assertEqual(error.exception.code, "unsafe_path")
        missing = self.directory / "absent" / "report.json"
        with self.assertRaises(target.SupportDiagnosticsError): target.save_report(missing.as_uri(), sample())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_publish_race_and_io_failure_leave_no_partial_output(self):
        destination = self.directory / "report.json"
        for failure, code in ((FileExistsError("PRIVATE-SENTINEL"), "file_exists"), (OSError("PRIVATE-SENTINEL"), "io_error")):
            with patch.object(target, "_publish_stream", side_effect=failure):
                with self.assertRaises(target.SupportDiagnosticsError) as error: target.save_report(destination.as_uri(), sample())
            self.assertEqual(str(error.exception), code)
            self.assertEqual(list(self.directory.iterdir()), [])

    def test_temporary_replacement_cannot_publish_other_file_contents(self):
        destination = self.directory / "report.json"
        private = self.directory / "synthetic-private.txt"
        private.write_bytes(b"SYNTHETIC-PRIVATE-NEVER-REPORT")
        publish = target._publish_stream
        attempted = []
        def replace_temporary(stream, source, path, data):
            self.assertFalse(stream.closed)
            attempted.append(source)
            if os.name == "nt":
                # The original DELETE-capable stream remains exclusively held.
                with self.assertRaises(PermissionError): source.unlink()
            else:
                source.unlink()
                os.link(private, source)
            return publish(stream, source, path, data)
        with patch.object(target, "_publish_stream", side_effect=replace_temporary):
            if os.name == "nt":
                self.assertEqual(target.save_report(destination.as_uri(), sample()), {"schema": 1, "saved": True})
                self.assertEqual(json.loads(destination.read_bytes()), sample())
            else:
                with self.assertRaises(target.SupportDiagnosticsError) as error:
                    target.save_report(destination.as_uri(), sample())
                self.assertEqual(error.exception.code, "unsafe_path")
                self.assertFalse(destination.exists())
        self.assertEqual(len(attempted), 1)
        self.assertEqual(private.read_bytes(), b"SYNTHETIC-PRIVATE-NEVER-REPORT")
        if os.name != "nt":
            # This is the injected foreign hardlink; production must preserve it.
            attempted[0].unlink()
        self.assertEqual(set(self.directory.iterdir()), {private, destination} if os.name == "nt" else {private})

    def test_destination_created_during_publish_is_preserved(self):
        destination = self.directory / "report.json"
        publish = target._publish_stream
        def create_destination(stream, source, path, data):
            path.write_bytes(b"PRESERVE-RACING-FILE")
            return publish(stream, source, path, data)
        with patch.object(target, "_publish_stream", side_effect=create_destination):
            with self.assertRaises(target.SupportDiagnosticsError) as error:
                target.save_report(destination.as_uri(), sample())
        self.assertEqual(error.exception.code, "file_exists")
        self.assertEqual(destination.read_bytes(), b"PRESERVE-RACING-FILE")
        self.assertEqual(list(self.directory.iterdir()), [destination])

    @unittest.skipIf(os.name == "nt", "POSIX exclusive-write fallback")
    def test_posix_publish_failure_removes_owned_final_output(self):
        from session_transfer import _remove_owned
        destination = self.directory / "report.json"
        def fail_source_cleanup(path, identity):
            if path.name.startswith(".valmax-support-"): raise OSError()
            return _remove_owned(path, identity)
        with patch("session_transfer._remove_owned", side_effect=fail_source_cleanup):
            with self.assertRaises(target.SupportDiagnosticsError) as error:
                target.save_report(destination.as_uri(), sample())
        self.assertEqual(error.exception.code, "io_error")
        self.assertFalse(destination.exists())

    def test_invalid_report_is_rejected_before_output_creation(self):
        report = sample(); report["tokens"] = "PRIVATE-SENTINEL"
        with self.assertRaises(target.SupportDiagnosticsError): target.save_report((self.directory / "report.json").as_uri(), report)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_preview_does_not_write_and_export_refreshes_current_counters(self):
        calls = []
        def privacy():
            calls.append(1)
            return {"observation": {"seconds": len(calls)}}
        service = target.SupportDiagnosticsService(valmax_version="8.8.2", max_version="26.35.1.80662", privacy=privacy)
        service.preview()
        self.assertEqual(list(self.directory.iterdir()), [])
        destination = self.directory / "report.json"; service.export(destination.as_uri())
        self.assertEqual(json.loads(destination.read_bytes())["counters"]["observation"]["seconds"], 2)


if __name__ == "__main__": unittest.main()
