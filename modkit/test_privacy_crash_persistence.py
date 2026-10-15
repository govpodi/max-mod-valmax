"""Fatal routing only; no native client, file or credential access."""
import unittest
from unittest.mock import Mock
from privacy_server import Monitor


class FatalRoutingTests(unittest.TestCase):
    def monitor(self):
        monitor = Monitor.__new__(Monitor)
        monitor.observation = Mock()
        monitor.native_crashes = Mock()
        return monitor

    def test_fatal_routes_only_frames_and_verified_process_role(self):
        for role in ("main", "service"):
            monitor = self.monitor()
            frames = [{"module": "ucrtbase.dll", "rva": 676478}]
            monitor._message({"type": "send", "payload": {"kind": "fatal", "frames": frames}},
                             b"SYNTHETIC_UNUSED_BUFFER", role=role, pid=123)
            monitor.observation.fatal.assert_called_once_with(frames)
            monitor.native_crashes.record.assert_called_once_with(frames, role=role, pid=123)

    def test_unknown_role_and_nonfatal_messages_do_not_persist(self):
        for message, role in (({"type": "error"}, "main"),
                              ({"payload": {"kind": "fatal", "frames": []}}, None),
                              ({"payload": {"kind": "fatal", "frames": "bad"}}, "main"),
                              ({"payload": {"kind": "different", "frames": []}}, "main")):
            monitor = self.monitor()
            monitor._message(message, None, role=role, pid=123)
            monitor.native_crashes.record.assert_not_called()

    def test_optional_writer_failure_cannot_replace_fatal_observation(self):
        monitor = self.monitor()
        monitor.native_crashes.record.side_effect = OSError("SYNTHETIC_FAILURE")
        monitor._message({"payload": {"kind": "fatal", "frames": []}}, None, role="main", pid=123)
        monitor.observation.fatal.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
