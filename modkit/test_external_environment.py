import ctypes
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from external_environment import _DllDirectory, clean_environment, native_environment


class DllApi:
    def __init__(self):
        self.value = "private-dll-directory"
        self.calls = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.calls.append(value)


class ExternalEnvironmentTests(unittest.TestCase):
    def test_removes_bundle_and_descendants_but_retains_neighbor_and_other_drive(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "bundle"
            safe = str(bundle) + "-neighbor"
            value = os.pathsep.join((str(bundle), str(bundle / "bin"), safe, "", '"' + str(bundle) + '"'))
            original = {"Path": value, "OTHER": "preserved"}
            result = clean_environment(original, bundle)
            self.assertEqual(result["Path"], safe + os.pathsep)
            self.assertEqual(result["OTHER"], "preserved")
            self.assertEqual(original["Path"], value)

    def test_frozen_spawn_resets_and_restores_directory_even_on_failure(self):
        api = DllApi()
        with self.assertRaisesRegex(RuntimeError, "spawn failed"):
            with native_environment(frozen=True, bundle_directory="bundle", environment={"X": "Y"}, dll_api=api) as environment:
                self.assertIsNone(api.value)
                self.assertEqual(environment, {"X": "Y"})
                raise RuntimeError("spawn failed")
        self.assertEqual(api.calls, [None, "private-dll-directory"])
        self.assertEqual(api.value, "private-dll-directory")

    def test_source_spawn_does_not_change_dll_search_path(self):
        api = DllApi()
        original = {"PATH": "unchanged"}
        with native_environment(frozen=False, environment=original, dll_api=api) as environment:
            environment["PATH"] = "local-copy"
        self.assertEqual(api.calls, [])
        self.assertEqual(original["PATH"], "unchanged")


@unittest.skipUnless(os.name == "nt", "Windows DLL search API")
class DllDirectoryTests(unittest.TestCase):
    def fake(self, replies):
        calls = []
        replies = iter(replies)

        def get(length, buffer):
            value, error, contents = next(replies)
            calls.append(length)
            if buffer is not None and contents is not None:
                buffer.value = contents
            if error is not None:
                ctypes.set_last_error(error)
            return value

        result = _DllDirectory.__new__(_DllDirectory)
        result.api = SimpleNamespace(GetDllDirectoryW=get)
        return result, calls

    def test_empty_success_is_distinct_from_windows_failure(self):
        obj, calls = self.fake([(1, 0, None), (0, 0, "")])
        self.assertIsNone(obj.get())
        self.assertEqual(calls, [0, 2])

    def test_zero_query_success_is_default_path(self):
        obj, calls = self.fake([(0, 0, None)])
        self.assertIsNone(obj.get())
        self.assertEqual(calls, [0])

    def test_empty_query_with_windows_error_is_not_success(self):
        obj, _ = self.fake([(0, 87, None)])
        with self.assertRaises(OSError) as raised:
            obj.get()
        self.assertEqual(raised.exception.winerror, 87)

    def test_empty_copy_with_windows_error_is_not_success(self):
        obj, _ = self.fake([(1, 0, None), (0, 5, "")])
        with self.assertRaises(OSError) as raised:
            obj.get()
        self.assertEqual(raised.exception.winerror, 5)

    def test_path_is_returned_without_normalization(self):
        value = "D:/SYNTHETIC-only"
        obj, _ = self.fake([(len(value) + 1, 0, None), (len(value), 0, value)])
        self.assertEqual(obj.get(), value)

    def test_size_growth_and_unbounded_query_are_rejected(self):
        for replies in ([(2, 0, None), (5, 0, "")], [(32768, 0, None)]):
            obj, _ = self.fake(replies)
            with self.assertRaises(OSError):
                obj.get()

    def test_stale_last_error_cannot_turn_valid_empty_state_into_failure(self):
        obj, _ = self.fake([(1, None, None), (0, None, "")])
        ctypes.set_last_error(5)
        self.assertIsNone(obj.get())

    def test_actual_windows_default_empty_and_nonempty_paths(self):
        api = _DllDirectory()
        original = api.get()
        try:
            api.set(None)
            self.assertIsNone(api.get())
            api.set("")
            self.assertIsNone(api.get())
            with tempfile.TemporaryDirectory(prefix="owned-dll-directory-") as temporary:
                api.set(temporary)
                self.assertEqual(api.get(), temporary)
                api.set(None)
        finally:
            # Only the disposable test process's DLL state is touched.
            api.set(original)

    def test_native_environment_restores_default_state_when_spawn_fails(self):
        api = _DllDirectory()
        original = api.get()
        try:
            api.set(None)
            with self.assertRaisesRegex(RuntimeError, "synthetic-failure"):
                with native_environment(frozen=True, bundle_directory=Path(__file__).parent,
                                        environment={"OWNED": "1"}, dll_api=api):
                    raise RuntimeError("synthetic-failure")
            self.assertIsNone(api.get())
        finally:
            api.set(original)


if __name__ == "__main__":
    unittest.main()
