"""Synthetic image streams/processes only: no MAX, profile or WinCred access."""
from contextlib import contextmanager
import ctypes
import hashlib
import io
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import max_client_policy as policy


EXE = b"synthetic-original-executable"
CONFIG = b"synthetic-original-config"
EXEPATH = r"C:\SyntheticOfficial\max.exe"
CONFIGPATH = r"C:\SyntheticOfficial\config.dll"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Image:
    def __init__(self, data, index, owner):
        self.data, self.index, self.owner = data, index, owner
        self.change, self.write, self.reads = 1, 1, 0
        self.identity_calls, self.mutate_after, self.truncate = 0, False, False
        self.extra = False

    def identity(self):
        self.identity_calls += 1
        change = self.change + (1 if self.mutate_after and self.identity_calls % 2 == 0 else 0)
        return (1, 0, self.index, len(self.data), self.write, change, 0, 1)

    def read(self, size):
        self.reads += 1
        if self.owner.require_both:
            if len(self.owner.held) != 2:
                raise AssertionError("Both image handles must be held")
        if self.truncate:
            return b""
        if self.extra:
            return b"x" * (size + 1)
        return self.stream.read(size)


class Images:
    def __init__(self):
        self.values, self.held, self.opens = {}, set(), []
        self.require_both = True
        self.add(EXEPATH, EXE)
        self.add(CONFIGPATH, CONFIG)

    def add(self, path, data):
        image = Image(data, len(self.values) + 1, self)
        self.values[path] = image
        return image

    @contextmanager
    def open(self, path):
        self.opens.append(path)
        if path not in self.values:
            raise OSError("SYNTHETIC_SECRET_MUST_NOT_APPEAR")
        image = self.values[path]
        image.stream = io.BytesIO(image.data)
        self.held.add(path)
        try:
            yield image
        finally:
            self.held.remove(path)

    def checker(self, **kwargs):
        pins = {"max.exe": (len(EXE), sha(EXE), len(CONFIG), sha(CONFIG))}
        return policy.OriginalImagePolicy(opener=self.open, pins=pins, **kwargs)


class OriginalImageTests(unittest.TestCase):
    def setUp(self):
        self.images = Images()
        self.checker = self.images.checker()

    def test_only_original_executable_and_config_pair_is_excluded(self):
        self.assertTrue(self.checker.original(EXEPATH, "MAX.EXE"))
        self.assertFalse(self.images.held)
        self.images.values[CONFIGPATH].data = b"modified-config-same-length!!"
        self.images.values[CONFIGPATH].change += 1
        self.assertFalse(self.checker.original(EXEPATH, "max.exe"))

    def test_mod_at_an_arbitrary_root_is_never_excluded(self):
        for path in (r"D:\CodexLabs\runtime\mod\max.exe", r"C:\Installed\runtime\mod\max.exe",
                     r"E:\Portable Copy\max.exe"):
            self.images.add(path, b"modified-executable")
            self.images.add(str(Path(path).with_name("config.dll")), CONFIG)
            self.assertFalse(self.checker.original(path, "max.exe"))

    def test_missing_unreadable_or_invalid_paths_fail_closed(self):
        for path, name in ((None, "max.exe"), ("relative\\max.exe", "max.exe"),
                           (r"\\server\share\max.exe", "max.exe"), (EXEPATH, "notepad.exe"),
                           (r"C:\Missing\max.exe", "max.exe"), (EXEPATH + "\0", "max.exe")):
            self.assertFalse(self.checker.original(path, name))
        self.assertFalse(self.images.held)

    def test_cached_pair_still_opens_and_checks_both_identities_without_reading(self):
        self.assertTrue(self.checker.original(EXEPATH, "max.exe"))
        before = [item.reads for item in self.images.values.values()]
        self.assertTrue(self.checker.original(EXEPATH, "max.exe"))
        self.assertEqual(before, [item.reads for item in self.images.values.values()])
        self.assertEqual(len(self.images.opens), 4)
        self.assertFalse(self.images.held)

    def test_change_time_invalidates_cache_even_when_size_and_write_time_match(self):
        self.assertTrue(self.checker.original(EXEPATH, "max.exe"))
        image = self.images.values[EXEPATH]
        image.data = b"x" * len(image.data)
        image.change += 1
        self.assertFalse(self.checker.original(EXEPATH, "max.exe"))

    def test_replacement_object_invalidates_cache(self):
        self.assertTrue(self.checker.original(EXEPATH, "max.exe"))
        image = self.images.values[EXEPATH]
        image.index += 100
        image.data = b"x" * len(image.data)
        self.assertFalse(self.checker.original(EXEPATH, "max.exe"))

    def test_identity_change_during_hash_is_not_cached(self):
        self.images.values[EXEPATH].mutate_after = True
        self.assertFalse(self.checker.original(EXEPATH, "max.exe"))
        self.assertEqual(len(self.checker.cache), 0)

    def test_short_oversized_and_wrong_length_streams_fail_closed(self):
        for flag in ("truncate", "extra"):
            images = Images()
            setattr(images.values[EXEPATH], flag, True)
            self.assertFalse(images.checker().original(EXEPATH, "max.exe"))
        self.images.values[EXEPATH].data += b"extra"
        self.assertFalse(self.checker.original(EXEPATH, "max.exe"))

    def test_missing_config_closes_executable_and_never_excludes_it(self):
        self.images.values.pop(CONFIGPATH)
        self.assertFalse(self.checker.original(EXEPATH, "max.exe"))
        self.assertFalse(self.images.held)

    def test_cache_is_bounded(self):
        checker = self.images.checker(cache_limit=1)
        self.assertTrue(checker.original(EXEPATH, "max.exe"))
        self.assertEqual(len(checker.cache), 1)
        self.assertTrue(checker.original(EXEPATH, "max.exe"))
        self.assertEqual(len(checker.cache), 1)

    def test_windows_file_information_layout(self):
        self.assertEqual(ctypes.sizeof(policy._FileInfo), 52)
        self.assertEqual(ctypes.sizeof(policy._BasicInfo), 40)


class CrossRootTests(unittest.TestCase):
    def setUp(self):
        self.paths = {
            1: Path(r"D:\CodexLabs\runtime\mod-mdata-v6\max.exe"),
            2: Path(r"C:\Installed\runtime\mod\max.exe"),
            3: Path(r"E:\Portable\max-service\max-service.exe"),
            4: Path(EXEPATH),
        }
        self.processes = [SimpleNamespace(pid=pid, name="max-service.exe" if pid == 3 else "max.exe")
                          for pid in self.paths]

    def original(self, path, name):
        return str(path) == EXEPATH

    def test_scan_includes_all_mod_roots_and_excludes_only_verified_original(self):
        with patch.object(policy, "original_image", side_effect=self.original):
            self.assertEqual(policy.scan_clients(self.processes, self.paths.get), (3, True))

    def test_unknown_path_and_failed_enumeration_fail_closed(self):
        self.paths[2] = None
        with patch.object(policy, "original_image", side_effect=self.original):
            self.assertEqual(policy.scan_clients(self.processes, self.paths.get), (2, False))
            def broken():
                yield self.processes[0]
                raise RuntimeError("SYNTHETIC")
            self.assertEqual(policy.scan_clients(broken(), self.paths.get), (1, False))

    def test_non_max_processes_are_never_queried(self):
        lookup = Mock(side_effect=AssertionError("Unexpected process query"))
        self.assertEqual(policy.scan_clients([SimpleNamespace(pid=8, name="notepad.exe")], lookup), (0, True))

    def test_monitor_blocks_crossroot_but_attaches_only_current_runtime(self):
        from privacy_server import Monitor
        from privacy_status import Observation
        monitor = Monitor.__new__(Monitor)
        monitor.device = Mock()
        monitor.device.enumerate_processes.return_value = self.processes
        monitor.sessions, monitor.roles, monitor.failed = {}, {}, {}
        monitor.observation, monitor.activity_lock = Observation(), threading.Lock()
        monitor.client_pids, monitor.main_client_pids, monitor.profile_pids = set(), set(), set()
        monitor.scan_completed = False
        monitor.allowed = {os.path.normcase(str(self.paths[2])): False}
        monitor.attach = Mock()
        with patch("privacy_server.process_path", side_effect=self.paths.get), \
                patch("privacy_server.current_tcp_endpoints", return_value=[]), \
                patch("privacy_server.original_image", side_effect=self.original):
            monitor.tick()
        self.assertEqual(monitor.profile_activity(), (3, True))
        self.assertEqual(monitor.activity(), (1, True))
        monitor.attach.assert_called_once_with(2, service=False)

    def test_restart_scan_is_fresh_all_root_main_only_and_never_attaches(self):
        from privacy_server import Monitor
        monitor = Monitor.__new__(Monitor)
        monitor.device = Mock(side_effect=AssertionError("Frida must not be used"))
        monitor.main_client_pids, monitor.scan_completed = set(), True
        monitor.activity_lock = threading.Lock()
        mains = [process for process in self.processes if process.name == "max.exe"]
        with patch("privacy_server._main_processes", return_value=mains) as snapshot, \
                patch("privacy_server.process_path", side_effect=self.paths.get), \
                patch.object(policy, "original_image", side_effect=self.original):
            self.assertEqual(monitor.main_activity(), (0, True))
            self.assertEqual(monitor.restart_activity(), (2, True))
            self.paths[2] = None
            self.assertEqual(monitor.restart_activity(), (1, False))
        self.assertEqual(snapshot.call_count, 2)
        monitor.device.enumerate_processes.assert_not_called()
        monitor.device.attach.assert_not_called()

    def test_restart_scan_failure_is_incomplete_and_service_only_scan_is_empty(self):
        from privacy_server import Monitor
        monitor = Monitor.__new__(Monitor)
        with patch("privacy_server._main_processes", side_effect=ValueError("synthetic snapshot failure")):
            self.assertEqual(monitor.restart_activity(), (0, False))
        with patch("privacy_server._main_processes", return_value=[]), \
                patch("privacy_server.process_path", side_effect=AssertionError("No main path expected")):
            self.assertEqual(monitor.restart_activity(), (0, True))

    def test_recovery_uses_shared_policy_without_runtime_prefix_exception(self):
        import mdata_recovery
        with patch.object(policy, "original_image", side_effect=self.original):
            self.assertEqual(mdata_recovery.scan_owned_clients(self.processes, self.paths.get,
                                                               r"C:\Installed\runtime"), (3, True))

    def test_service_offline_recovery_uses_same_policy_and_bounded_enumeration(self):
        import mdata_service
        device = Mock()
        device.enumerate_processes.return_value = self.processes
        with patch("frida.get_local_device", return_value=device), \
                patch("privacy_server.bounded_rpc", side_effect=lambda call: call()) as bounded, \
                patch("privacy_server.process_path", side_effect=self.paths.get), \
                patch.object(policy, "original_image", side_effect=self.original):
            self.assertEqual(mdata_service.owned_client_activity(), (3, True))
        bounded.assert_called_once()
        device.attach.assert_not_called()

    def test_source_recovery_routes_to_launcher_before_gui_or_marker(self):
        import mdata_recovery
        with patch("launch_mod.main", return_value=17) as launcher, \
                patch.object(mdata_recovery, "run_gui", side_effect=AssertionError("Direct GUI")), \
                patch.object(mdata_recovery, "load_recovery_marker", side_effect=AssertionError("Marker read")):
            self.assertEqual(mdata_recovery.main(["--recover-mdata"]), 17)
        launcher.assert_called_once_with(["--recover-mdata"])


class NativeMainSnapshotTests(unittest.TestCase):
    def kernel(self, entries, *, terminal_error=18):
        import privacy_server as target
        api, position = Mock(), iter(entries)
        api.CreateToolhelp32Snapshot.return_value = 123
        def next_entry(handle, pointer):
            try:
                pid, name = next(position)
            except StopIteration:
                ctypes.set_last_error(terminal_error)
                return False
            entry = ctypes.cast(pointer, ctypes.POINTER(target._ProcessEntry32)).contents
            self.assertEqual(entry.dwSize, ctypes.sizeof(target._ProcessEntry32))
            entry.th32ProcessID, entry.szExeFile = pid, name
            return True
        api.Process32FirstW.side_effect = api.Process32NextW.side_effect = next_entry
        return api

    def test_native_snapshot_filters_names_and_closes_its_own_handle(self):
        import privacy_server as target
        api = self.kernel([(1, "notepad.exe"), (2, "max.exe"), (3, "MAX.EXE"), (4, "max-service.exe")])
        with patch.object(target.ctypes, "WinDLL", return_value=api):
            entries = target._main_processes()
        self.assertEqual([(item.pid, item.name) for item in entries], [(2, "max.exe"), (3, "max.exe")])
        api.CreateToolhelp32Snapshot.assert_called_once_with(2, 0)
        api.CloseHandle.assert_called_once_with(123)
        self.assertEqual(ctypes.sizeof(target._ProcessEntry32), 568)
        self.assertEqual(target._ProcessEntry32.szExeFile.offset, 44)

    def test_native_empty_snapshot_and_partial_error_are_distinguished(self):
        import privacy_server as target
        for entries, terminal, succeeds in [([], 18, True), ([(2, "max.exe")], 5, False), ([], 5, False)]:
            with self.subTest(entries=len(entries), terminal=terminal):
                api = self.kernel(entries, terminal_error=terminal)
                with patch.object(target.ctypes, "WinDLL", return_value=api):
                    if succeeds:
                        self.assertEqual(target._main_processes(), [])
                    else:
                        with self.assertRaises(ValueError):
                            target._main_processes()
                api.CloseHandle.assert_called_once_with(123)

    def test_failed_snapshot_creation_does_not_close_an_invalid_handle(self):
        import privacy_server as target
        for invalid in (None, 0, ctypes.c_void_p(-1).value):
            with self.subTest(handle=invalid):
                api = self.kernel([])
                api.CreateToolhelp32Snapshot.return_value = invalid
                with patch.object(target.ctypes, "WinDLL", return_value=api):
                    with self.assertRaises(ValueError):
                        target._main_processes()
                api.CloseHandle.assert_not_called()


if __name__ == "__main__":
    unittest.main()
