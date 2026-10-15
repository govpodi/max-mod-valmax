"""Pure synthetic exception/traceback fixtures; no MAX, files or native API."""
import copy
from datetime import datetime, timezone
import json
import unittest

import mdata_diagnostics as module


OPERATION_ID = "0123456789abcdef0123456789abcdef"
SECRET = "SYNTHETIC_PRIVATE_TOKEN_AND_PATH_938271"
PHASES = frozenset((
    "lock", "wait_closed", "profile_listing", "credential_inventory",
    "credential_read", "storage_validate", "archive_open", "archive_encrypt",
    "source_open", "source_read", "profile_verify", "credential_verify",
    "archive_publish", "staging_create", "archive_decrypt", "incoming_validate",
    "config_prepare", "backup", "recovery_marker", "restore", "add", "cleanup",
    "complete",
))
ROOT_ERRORS = frozenset((
    "other", "OSError", "PermissionError", "FileNotFoundError", "FileExistsError",
    "TimeoutError", "IsADirectoryError", "NotADirectoryError", "BlockingIOError",
    "ValueError", "TypeError", "RuntimeError", "KeyError", "IndexError", "EOFError",
    "MemoryError", "UnicodeError", "UnicodeDecodeError", "UnicodeEncodeError",
    "OverflowError", "ZeroDivisionError", "MDataError", "MDataFileError",
    "MDataArchiveError", "CredentialError", "SessionArchiveError", "TransferError",
    "RecoveryRequired", "QuickReplyStoreError",
))
TRUSTED_MODULES = frozenset((
    "mdata_service.py", "mdata_files.py", "mdata_credentials.py", "mdata_archive.py",
    "mdata_recovery.py", "mdata_recovery_state.py", "session_transfer.py",
    "session_archive.py", "session_credentials.py", "quick_reply_store.py",
    "launch_mod.py", "privacy_server.py",
))
KEYS = frozenset(("schema", "timestamp", "phase", "operationId", "rootError",
                  "errno", "winerror", "frames"))


class SyntheticError(Exception):
    pass


class HostileError(Exception):
    touches = []

    def __str__(self):
        self.touches.append("str")
        raise AssertionError(SECRET)

    def __repr__(self):
        self.touches.append("repr")
        raise AssertionError(SECRET)

    def __getattribute__(self, name):
        if name == "args":
            type(self).touches.append("args")
            raise AssertionError(SECRET)
        return super().__getattribute__(name)


def raised(error, *, filename="/private/" + SECRET + "/mdata_files.py",
           function="read_profile", depth=0):
    """Create a traceback entirely in memory, including forged file paths."""
    namespace = {"fixture_error": error, "fixture_secret": SECRET}
    source = (f"def {function}(depth):\n"
              f"    local_secret = fixture_secret\n"
              f"    if depth:\n"
              f"        return {function}(depth - 1)\n"
              f"    raise fixture_error\n")
    exec(compile(source, filename, "exec"), namespace)
    try:
        namespace[function](depth)
    except BaseException as caught:
        return caught
    raise AssertionError("Fixture did not raise")


class MDataDiagnosticTests(unittest.TestCase):
    def record(self, error=None, phase="profile_listing"):
        return module.failure(error or SyntheticError(SECRET), phase, OPERATION_ID)

    def assert_safe(self, value):
        self.assertIs(type(value), dict)
        self.assertEqual(set(value), KEYS)
        self.assertTrue(module.validate(value))
        self.assertIs(type(value["schema"]), int)
        self.assertEqual(value["schema"], 1)
        self.assertRegex(value["timestamp"], r"\A[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z")
        datetime.strptime(value["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        self.assertIn(value["phase"], PHASES)
        self.assertRegex(value["operationId"], r"\A[0-9a-f]{32}\Z")
        self.assertIn(value["rootError"], ROOT_ERRORS)
        for key in ("errno", "winerror"):
            self.assertTrue(value[key] is None or type(value[key]) is int and 0 <= value[key] <= 65535)
        self.assertIs(type(value["frames"]), list)
        self.assertLessEqual(len(value["frames"]), 4)
        for frame in value["frames"]:
            self.assertIs(type(frame), dict)
            self.assertEqual(set(frame), {"module", "function", "line"})
            self.assertIn(frame["module"], TRUSTED_MODULES)
            self.assertRegex(frame["function"], r"\A[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
            self.assertIs(type(frame["line"]), int)
            self.assertTrue(1 <= frame["line"] <= 1000000)
        encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        self.assertLessEqual(len(encoded), 1200)
        self.assertTrue(json.dumps(value, ensure_ascii=False).isascii())
        self.assertNotIn(SECRET, encoded.decode("ascii"))
        return value

    def test_public_allowlists_are_exact_and_every_phase_produces_valid_record(self):
        self.assertEqual(set(module.PHASES), PHASES)
        self.assertEqual(set(module.ROOT_ERRORS), ROOT_ERRORS)
        self.assertEqual(set(module.TRUSTED_MODULES), TRUSTED_MODULES)
        for phase in PHASES:
            with self.subTest(phase=phase):
                value = self.assert_safe(self.record(phase=phase))
                self.assertEqual(value["phase"], phase)
                self.assertEqual(value["rootError"], "other")
                self.assertEqual(value["frames"], [])

    def test_timestamp_is_current_utc_with_seconds_and_no_local_offset(self):
        before = datetime.now(timezone.utc).replace(microsecond=0)
        value = self.assert_safe(self.record())
        after = datetime.now(timezone.utc).replace(microsecond=0)
        timestamp = datetime.strptime(value["timestamp"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLessEqual(before, timestamp)
        self.assertLessEqual(timestamp, after)

    def test_hostile_exception_is_never_formatted_and_its_args_are_not_read(self):
        HostileError.touches = []
        value = self.assert_safe(self.record(raised(HostileError(SECRET))))
        self.assertEqual(value["rootError"], "other")
        self.assertEqual(HostileError.touches, [])
        self.assertTrue(value["frames"])
        self.assertTrue(all(frame["module"] == "mdata_files.py" for frame in value["frames"]))
        self.assertNotIn("local_secret", json.dumps(value))
        self.assertNotIn("fixture_error", json.dumps(value))

    def test_exception_slot_overrides_cannot_execute_or_hide_real_cause_and_numbers(self):
        class HostileSlots(Exception):
            touches = []

            def __getattribute__(self, name):
                if name in {"__cause__", "__context__", "__traceback__", "__dict__", "errno", "winerror", "args"}:
                    type(self).touches.append(name)
                    raise AssertionError(SECRET)
                return super().__getattribute__(name)

        deepest = HostileSlots(SECRET)
        deepest.errno, deepest.winerror = 5, 32
        outer = HostileSlots(SECRET)
        outer.__cause__ = deepest
        value = self.assert_safe(self.record(outer))
        self.assertEqual((value["errno"], value["winerror"]), (5, 32))
        self.assertEqual(HostileSlots.touches, [])

    def test_exception_rejecting_every_attribute_never_runs_getattribute(self):
        class HostileEveryAttribute(Exception):
            touches = []

            def __getattribute__(self, name):
                type(self).touches.append(name)
                raise AssertionError(SECRET)

        deepest = raised(HostileEveryAttribute(SECRET))
        deepest.errno, deepest.winerror = 5, 32
        outer = HostileEveryAttribute(SECRET)
        outer.__cause__ = deepest
        value = self.assert_safe(self.record(outer))
        self.assertEqual(value["rootError"], "other")
        self.assertEqual((value["errno"], value["winerror"]), (5, 32))
        self.assertTrue(value["frames"])
        self.assertEqual(HostileEveryAttribute.touches, [])

    def test_builtin_categories_require_exact_types_and_project_names_require_known_modules(self):
        cases = [
            (OSError(5, SECRET), "OSError"),
            (PermissionError(13, SECRET), "PermissionError"),
            (FileNotFoundError(2, SECRET, "/" + SECRET), "FileNotFoundError"),
            (ValueError(SECRET), "ValueError"),
            (RuntimeError(SECRET), "RuntimeError"),
            (UnicodeDecodeError("ascii", b"\xff", 0, 1, SECRET), "UnicodeDecodeError"),
            (type("PrivateRuntimeError", (RuntimeError,), {})(SECRET), "other"),
            (type("MDataError", (Exception,), {"__module__": "untrusted"})(SECRET), "other"),
            (type("MDataError", (Exception,), {"__module__": "mdata_service"})(SECRET), "MDataError"),
            (type("CredentialError", (Exception,), {"__module__": "mdata_credentials"})(SECRET), "other"),
            (type("CredentialError", (Exception,), {"__module__": "session_credentials"})(SECRET), "CredentialError"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected, actual_type=type(error).__name__):
                value = self.assert_safe(self.record(error))
                self.assertEqual(value["rootError"], expected)

    def test_cause_precedes_context_and_deepest_node_supplies_category_numbers_and_frames(self):
        deepest = raised(FileNotFoundError(2, SECRET, "/" + SECRET), function="read_archive")
        deepest.winerror = 3
        middle = PermissionError(13, SECRET)
        middle.__context__ = deepest
        outer = raised(RuntimeError(SECRET), filename="/private/mdata_service.py", function="export_data")
        outer.__cause__ = middle
        outer.__context__ = ValueError(SECRET)
        value = self.assert_safe(self.record(outer))
        self.assertEqual((value["rootError"], value["errno"], value["winerror"]), ("FileNotFoundError", 2, 3))
        self.assertTrue(value["frames"])
        self.assertTrue(all(frame["function"] == "read_archive" for frame in value["frames"]))

    def test_context_cycle_is_bounded_and_uses_last_unrepeated_node(self):
        first, second = RuntimeError(SECRET), PermissionError(13, SECRET)
        first.__context__ = second
        second.__context__ = first
        value = self.assert_safe(self.record(first))
        self.assertEqual((value["rootError"], value["errno"]), ("PermissionError", 13))

    def test_chain_limit_includes_initial_exception_and_never_reads_seventh_node(self):
        nodes = [RuntimeError(SECRET) for _ in range(5)]
        nodes += [FileNotFoundError(2, SECRET), PermissionError(13, SECRET)]
        for previous, following in zip(nodes, nodes[1:]):
            previous.__cause__ = following
        value = self.assert_safe(self.record(nodes[0]))
        self.assertEqual((value["rootError"], value["errno"]), ("FileNotFoundError", 2))

    def test_numeric_fields_are_exact_bounded_integers_and_property_failures_are_hidden(self):
        for number in (0, 1, 65535, -1, 65536, True, False, 1.0, "13", None):
            with self.subTest(number=number):
                error = SyntheticError(SECRET)
                error.errno = error.winerror = number
                value = self.assert_safe(self.record(error))
                expected = number if type(number) is int and 0 <= number <= 65535 else None
                self.assertEqual(value["errno"], expected)
                self.assertEqual(value["winerror"], expected)

        class HostileNumbers(Exception):
            @property
            def errno(self):
                raise ValueError(SECRET)

            @property
            def winerror(self):
                raise ValueError(SECRET)

        value = self.assert_safe(self.record(HostileNumbers(SECRET)))
        self.assertIsNone(value["errno"])
        self.assertIsNone(value["winerror"])

    def test_forged_paths_only_emit_whitelisted_basename_and_bounded_frames(self):
        for basename in TRUSTED_MODULES:
            for prefix in ("/" + SECRET + "/", "C:\\" + SECRET + "\\"):
                with self.subTest(module=basename, windows=prefix.startswith("C:")):
                    value = self.assert_safe(self.record(raised(SyntheticError(SECRET), filename=prefix + basename, depth=8)))
                    self.assertTrue(value["frames"])
                    self.assertEqual(len(value["frames"]), 4)
                    self.assertTrue(all(frame["module"] == basename for frame in value["frames"]))
        value = self.assert_safe(self.record(raised(SyntheticError(SECRET), filename="/" + SECRET + "/untrusted.py", depth=8)))
        self.assertEqual(value["frames"], [])

    def test_unicode_long_and_module_function_names_are_omitted(self):
        for name in ("читать", "f" * 65):
            with self.subTest(name=name):
                value = self.assert_safe(self.record(raised(SyntheticError(SECRET), function=name)))
                self.assertEqual(value["frames"], [])
        namespace = {"fixture_error": SyntheticError(SECRET)}
        try:
            exec(compile("raise fixture_error", "/private/mdata_files.py", "exec"), namespace)
        except BaseException as error:
            value = self.assert_safe(self.record(error))
        self.assertEqual(value["frames"], [])

    def test_invalid_failure_phase_or_operation_id_raises_fixed_value_error(self):
        messages = {"phase": [], "operationId": []}
        invalid = [None, True, 1, b"lock", [], {}, "", SECRET, "lock\n", "LOCK", "lock/" + SECRET]
        for phase in invalid:
            with self.subTest(field="phase", kind=type(phase).__name__):
                with self.assertRaises(ValueError) as caught:
                    module.failure(SyntheticError(SECRET), phase, OPERATION_ID)
                messages["phase"].append(caught.exception.args)
        for operation_id in (None, True, 1, b"0" * 32, [], {}, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "a" * 32 + "\n", SECRET):
            with self.subTest(field="operationId", kind=type(operation_id).__name__):
                with self.assertRaises(ValueError) as caught:
                    module.failure(SyntheticError(SECRET), "lock", operation_id)
                messages["operationId"].append(caught.exception.args)
        for field_messages in messages.values():
            self.assertTrue(field_messages)
            self.assertTrue(all(message == field_messages[0] for message in field_messages))
        self.assertNotIn(SECRET, json.dumps(messages))

    def test_validate_rejects_unknown_or_missing_keys_and_non_plain_types(self):
        valid = self.record()
        for key in KEYS:
            value = copy.deepcopy(valid)
            del value[key]
            self.assertFalse(module.validate(value), key)
        value = copy.deepcopy(valid)
        value[SECRET] = SECRET
        self.assertFalse(module.validate(value))
        for value in (None, True, [], (), "{}", 1, type("DerivedDict", (dict,), {})(valid)):
            with self.subTest(kind=type(value).__name__):
                self.assertIs(module.validate(value), False)

    def test_validate_rejects_wrong_scalar_types_ranges_formats_and_unknown_allowlist_values(self):
        invalid = {
            "schema": [True, False, 0, 2, 1.0, "1", None],
            "timestamp": [None, True, "", "2026-02-30T00:00:00Z", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00.000Z", "2026-01-01T00:00:00Z\n"],
            "phase": [None, True, "", SECRET, "lock\n"],
            "operationId": [None, True, "a" * 31, "a" * 33, "A" * 32, "g" * 32, "a" * 32 + "\n"],
            "rootError": [None, True, "", SECRET, "RuntimeError\n"],
            "errno": [True, False, -1, 65536, 1.0, "13", []],
            "winerror": [True, False, -1, 65536, 1.0, "13", []],
            "frames": [None, True, {}, (), "", [None], [True]],
        }
        for key, values in invalid.items():
            for bad in values:
                with self.subTest(field=key, kind=type(bad).__name__):
                    value = self.record()
                    value[key] = bad
                    self.assertIs(module.validate(value), False)

    def test_validate_frame_keys_modules_ascii_identifiers_lines_and_count(self):
        valid_frame = {"module": "mdata_files.py", "function": "_read2", "line": 1}
        value = self.record()
        value["frames"] = [valid_frame]
        self.assertTrue(module.validate(value))
        bad_frames = []
        for key in valid_frame:
            frame = dict(valid_frame)
            del frame[key]
            bad_frames.append(frame)
        bad_frames.append({**valid_frame, "source": SECRET})
        bad_frames.append(type("DerivedDict", (dict,), {})(valid_frame))
        for key, choices in {
            "module": [None, True, "untrusted.py", "/private/mdata_files.py", "MData_Files.py", "mdata_files.py\n"],
            "function": [None, True, "", "<module>", "<lambda>", "читать", "f" * 65, "read.path", "read\n", "2read"],
            "line": [None, True, False, 0, -1, 1000001, 1.0, "1"],
        }.items():
            bad_frames.extend({**valid_frame, key: bad} for bad in choices)
        for frame in bad_frames:
            value = self.record()
            value["frames"] = [frame]
            self.assertIs(module.validate(value), False)
        value = self.record()
        value["frames"] = [dict(valid_frame) for _ in range(5)]
        self.assertIs(module.validate(value), False)
        value["frames"] = [{**valid_frame, "function": "f" * 64, "line": 1000000} for _ in range(4)]
        self.assert_safe(value)


if __name__ == "__main__":
    unittest.main()
