"""Synthetic desktop broker tests; no native spawn, profiles or credentials."""
import ctypes
import io
import os
import struct
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

import desktop_process as d


PYTHON = r"D:\Fixture\python.exe"
SCRIPT = r"D:\Fixture\helper.py"
FROZEN = r"D:\Fixture\MAX-Mod-Helper.exe"


def put(pointer, kind, value):
    ctypes.cast(pointer, ctypes.POINTER(kind)).contents.value = value


class ApiFixture:
    def __init__(self):
        self.CloseHandle = Mock(return_value=True)
        self.DeleteProcThreadAttributeList = Mock()
        self.UpdateProcThreadAttribute = Mock(return_value=True)
        self.WaitForSingleObject = Mock(return_value=0)
        self.exit_code = 0
        self.created = []
        self.initialize_ok = self.create_ok = True

    def InitializeProcThreadAttributeList(self, storage, _count, _flags, length):
        put(length, ctypes.c_size_t, 128)
        return bool(storage is not None and self.initialize_ok)

    def CreateProcessW(self, executable, command, _pa, _ta, inherit, flags, env, cwd, startup, result):
        self.created.append({"executable": executable, "command": command.value, "inherit": inherit,
                             "flags": flags, "env": env[:], "cwd": cwd,
                             "startup": ctypes.cast(startup, ctypes.POINTER(d._StartupInfoEx)).contents.StartupInfo})
        if not self.create_ok:
            return False
        info = ctypes.cast(result, ctypes.POINTER(d._ProcessInfo)).contents
        info.hProcess, info.hThread, info.dwProcessId, info.dwThreadId = 100, 101, 202, 203
        return True

    def GetExitCodeProcess(self, _handle, result):
        put(result, d.w.DWORD, self.exit_code)
        return True


class SpawnTests(unittest.TestCase):
    def spawn(self, command=None, **kwargs):
        api = ApiFixture()
        with patch.object(d, "_api", return_value=api), patch.object(d, "_verified_parent", return_value=500):
            process = d.popen(command or [PYTHON, "-B", SCRIPT, "--native"], **kwargs)
        return process, api

    def test_python_bootstrap_uses_explicit_executable_and_no_inherited_stdio(self):
        process, api = self.spawn(cwd=r"D:\Fixture", env={"PATH": r"D:\Safe", "ТЕСТ": "значение"})
        value = api.created[0]
        self.assertEqual(value["executable"], PYTHON)
        self.assertIn("--worker -B", value["command"])
        self.assertIn("desktop_process.py", value["command"])
        self.assertIn(SCRIPT, value["command"])
        self.assertFalse(value["inherit"])
        self.assertEqual(value["flags"], 0x08080400)
        self.assertEqual(value["startup"].dwFlags, 0)
        self.assertFalse(value["startup"].hStdOutput)
        self.assertIn("MAX_MOD_DESKTOP_CHILD=1\0", value["env"])
        self.assertIn("ТЕСТ=значение\0", value["env"])
        self.assertTrue(value["env"].endswith("\0\0"))
        self.assertEqual(process.pid, 202)
        api.CloseHandle.assert_any_call(101)
        api.CloseHandle.assert_any_call(500)
        self.assertNotIn(100, [call.args[0] for call in api.CloseHandle.call_args_list])
        self.assertEqual(process.wait(timeout=1), 0)
        self.assertEqual(process.poll(), 0)
        self.assertEqual([call.args[0] for call in api.CloseHandle.call_args_list].count(100), 1)

    def test_frozen_and_native_commands_are_direct(self):
        for command in ([FROZEN, "observe", "--manifest", r"D:\Fixture\mod-manifest.json"],
                        [r"D:\Fixture\max.exe"]):
            process, api = self.spawn(command=command)
            self.assertEqual(api.created[0]["command"], subprocess.list2cmdline(command))
            self.assertNotIn("--worker", api.created[0]["command"])
            process.close()

    def test_input_environment_is_not_mutated_and_marker_cannot_disable_child_loop_control(self):
        original = {"PATH": r"D:\Safe", d.DESKTOP_CHILD_MARKER: "0"}
        process, api = self.spawn(env=original)
        self.assertEqual(original[d.DESKTOP_CHILD_MARKER], "0")
        self.assertIn(d.DESKTOP_CHILD_MARKER + "=1\0", api.created[0]["env"])
        process.close()

    def test_invalid_command_env_and_cwd_fail_before_native_calls(self):
        commands = ["secret command", [], ["python.exe", "-B", SCRIPT],
                    [r"\\server\python.exe", "-B", SCRIPT], [PYTHON, "-c", "SECRET"],
                    [PYTHON, "-B", "relative.py"], [FROZEN, "bad\0argument"], [FROZEN, "a" * 32768]]
        with patch.object(d, "_api") as api:
            for command in commands:
                with self.subTest(command_kind=type(command).__name__), self.assertRaises(d.DesktopProcessError):
                    d.popen(command)
            for env in ([], {"A": "1", "a": "2"}, {"bad=key": "1"}, {"A": "bad\0value"}, {"A": 3}):
                with self.assertRaises(d.DesktopProcessError):
                    d.popen([FROZEN], env=env)
            for cwd in ("relative", r"\\server\share", "D:\\bad\0directory"):
                with self.assertRaises(d.DesktopProcessError):
                    d.popen([FROZEN], cwd=cwd)
            api.assert_not_called()

    def test_create_and_attribute_failures_release_owned_handles_without_fallback(self):
        for stage in ("init", "attribute", "create"):
            api = ApiFixture()
            if stage == "init":
                api.initialize_ok = False
            elif stage == "attribute":
                api.UpdateProcThreadAttribute.return_value = False
            else:
                api.create_ok = False
            with patch.object(d, "_api", return_value=api), patch.object(d, "_verified_parent", return_value=500):
                with self.assertRaises(d.DesktopProcessError):
                    d.popen([FROZEN])
            api.CloseHandle.assert_called_once_with(500)
            self.assertEqual(api.DeleteProcThreadAttributeList.call_count, 0 if stage == "init" else 1)
            self.assertLessEqual(len(api.created), 1)

    def test_parent_verification_failure_never_starts_child(self):
        api = ApiFixture()
        with patch.object(d, "_api", return_value=api), patch.object(d, "_verified_parent", side_effect=d.DesktopProcessError("user_mismatch")):
            with self.assertRaises(d.DesktopProcessError) as result:
                d.popen([FROZEN])
        self.assertEqual(result.exception.code, "user_mismatch")
        self.assertEqual(api.created, [])

    def test_poll_wait_timeout_and_exit_259_are_unambiguous(self):
        process, api = self.spawn()
        api.WaitForSingleObject.return_value = 258
        self.assertIsNone(process.poll())
        with self.assertRaises(subprocess.TimeoutExpired):
            process.wait(timeout=0.001)
        self.assertIsNone(process.returncode)
        api.WaitForSingleObject.assert_called_with(100, 1)
        api.WaitForSingleObject.return_value = 0
        api.exit_code = 259
        self.assertEqual(process.wait(), 259)
        self.assertEqual(process.poll(), 259)

    def test_close_releases_handle_without_terminating_any_process(self):
        process, api = self.spawn()
        process.close()
        process.close()
        self.assertEqual([call.args[0] for call in api.CloseHandle.call_args_list].count(100), 1)
        with self.assertRaises(d.DesktopProcessError):
            process.poll()
        self.assertFalse(hasattr(api, "TerminateProcess"))

    def test_invalid_timeout_and_wait_error_are_fixed(self):
        process, api = self.spawn()
        for timeout in (True, "secret", float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                process.wait(timeout=timeout)
        api.WaitForSingleObject.return_value = 0xFFFFFFFF
        with self.assertRaises(d.DesktopProcessError):
            process.wait(timeout=1)
        process.close()


class ParentTests(unittest.TestCase):
    def api(self, image=r"C:\Windows\explorer.exe", same_session=True):
        api = MagicMock()
        api.GetShellWindow.return_value = 1
        def window(_hwnd, result):
            put(result, d.w.DWORD, 20)
            return 1
        api.GetWindowThreadProcessId.side_effect = window
        api.OpenProcess.return_value = 500
        def windows(buffer, _size):
            buffer.value = r"C:\Windows"
            return len(buffer.value)
        api.GetWindowsDirectoryW.side_effect = windows
        def query(_handle, _flags, buffer, length):
            buffer.value = image
            put(length, d.w.DWORD, len(image))
            return True
        api.QueryFullProcessImageNameW.side_effect = query
        api.GetCurrentProcessId.return_value = 10
        def session(pid, result):
            put(result, d.w.DWORD, 1 if pid == 10 or same_session else 2)
            return True
        api.ProcessIdToSessionId.side_effect = session
        return api

    def test_only_exact_system_explorer_in_same_session_is_accepted(self):
        api = self.api()
        with patch.object(d, "_verify_tokens") as verify:
            self.assertEqual(d._verified_parent(api), 500)
        api.OpenProcess.assert_called_once_with(0x1080, False, 20)
        verify.assert_called_once_with(api, 500)
        api.CloseHandle.assert_not_called()
        for image, same_session, code in ((r"D:\Fake\explorer.exe", True, "untrusted_desktop"),
                                          (r"C:\Windows\explorer.exe", False, "session_mismatch")):
            api = self.api(image, same_session)
            with patch.object(d, "_verify_tokens") as verify, self.assertRaises(d.DesktopProcessError) as result:
                d._verified_parent(api)
            self.assertEqual(result.exception.code, code)
            api.CloseHandle.assert_called_once_with(500)
            verify.assert_not_called()

    def test_desktop_unavailable_never_uses_arbitrary_process(self):
        api = self.api()
        api.GetShellWindow.return_value = 0
        with self.assertRaises(d.DesktopProcessError):
            d._verified_parent(api)
        api.OpenProcess.assert_not_called()

    def test_token_verification_enforces_user_elevation_integrity_and_logon(self):
        for failure, expected in ((None, None), ("user", "user_mismatch"), ("elevation", "elevated_desktop"),
                                  ("integrity", "integrity_mismatch"), ("logon", "logon_mismatch")):
            api = MagicMock()
            api.GetCurrentProcess.return_value = 999
            api.EqualSid.return_value = failure != "user"
            def token(process, _access, output):
                put(output, d.w.HANDLE, 111 if process == 999 else 222)
                return True
            api.OpenProcessToken.side_effect = token
            def token_data(_api, token, kind):
                if kind == 20:
                    return ctypes.create_string_buffer(struct.pack("<I", int(failure == "elevation")))
                if kind == 10:
                    return ctypes.create_string_buffer(b"\0" * 8 + (b"different" if token == 222 and failure == "logon" else b"sameLUID"))
                return ctypes.create_string_buffer(b"synthetic")
            with patch.object(d, "_token_data", side_effect=token_data), patch.object(d, "_sid", return_value=1), patch.object(d, "_integrity", side_effect=[0x2000, 0x3000 if failure == "integrity" else 0x2000]):
                if expected:
                    with self.assertRaises(d.DesktopProcessError) as result:
                        d._verify_tokens(api, 500)
                    self.assertEqual(result.exception.code, expected)
                else:
                    d._verify_tokens(api, 500)
            self.assertEqual({call.args[0] for call in api.CloseHandle.call_args_list}, {111, 222})


class WorkerTests(unittest.TestCase):
    def test_marker_is_public_loop_control_only(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(d.is_desktop_child())
            os.environ[d.DESKTOP_CHILD_MARKER] = "1"
            self.assertTrue(d.is_desktop_child())
            os.environ[d.DESKTOP_CHILD_MARKER] = "0"
            self.assertFalse(d.is_desktop_child())

    def test_python_worker_redirects_before_importing_target_and_preserves_argv(self):
        calls = []
        with patch.object(d, "redirect_stdio_to_null", side_effect=lambda: calls.append("null")), patch.object(d.runpy, "run_path", side_effect=lambda *_args, **_kwargs: calls.append("target")), patch.object(sys, "argv", ["parent"]), patch.object(sys, "path", list(sys.path)), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(d._worker(["-B", SCRIPT, "--native", "--report", r"D:\Fixture\report.json"]), 0)
            self.assertEqual(sys.argv, [SCRIPT, "--native", "--report", r"D:\Fixture\report.json"])
            self.assertTrue(d.is_desktop_child())
        self.assertEqual(calls, ["null", "target"])

    def test_worker_rejects_interpreter_code_without_executing_target(self):
        with patch.object(d, "redirect_stdio_to_null"), patch.object(d.runpy, "run_path") as run:
            self.assertEqual(d._worker(["-c", "SECRET"]), 2)
            run.assert_not_called()

    def test_null_stdio_uses_child_own_descriptors_not_parent_handle_inheritance(self):
        with patch.object(d.os, "open", return_value=5) as opened, patch.object(d.os, "dup2") as duplicate, patch.object(d.os, "close") as close, patch("builtins.open", side_effect=[io.StringIO(), io.StringIO(), io.StringIO()]), patch.object(sys, "stdin", None), patch.object(sys, "stdout", None), patch.object(sys, "stderr", None):
            d.redirect_stdio_to_null()
        opened.assert_called_once_with(os.devnull, os.O_RDWR | getattr(os, "O_BINARY", 0))
        self.assertEqual([call.args for call in duplicate.call_args_list], [(5, 0), (5, 1), (5, 2)])
        self.assertTrue(all(call.kwargs == {"inheritable": False} for call in duplicate.call_args_list))
        close.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()
