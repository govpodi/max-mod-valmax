"""Synthetic tests only: no process spawn, attach, profile or credentials."""
import json
import threading
import time
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import privacy_monitor_probe as target
from privacy_status import Observation


def observer():
    probe=target.Probe.__new__(target.Probe)
    probe.lock=threading.RLock()
    probe.records=[]
    probe.started=time.monotonic()
    probe.stopping=threading.Event()
    probe.guard_failed=False
    probe.observation=Observation()
    probe.output_bytes=0
    return probe


class PrivacyMonitorProbeTest(unittest.TestCase):
    def test_rejected_valid_session_header_is_still_counted_as_header_only(self):
        probe = observer()
        frame = b"\x00\x00\x00\x00\x00\x06\x00\x00\x00\x01\x80"
        probe.monitor_message(123, {"payload": {"kind": "session-init"}}, frame)
        value = probe.observation.snapshot()
        self.assertEqual(value["dashboard"]["observedPackets"], 1)
        self.assertEqual(value["dashboard"]["headerOnlyPackets"], 1)
        self.assertEqual(value["dashboard"]["parsedPackets"], 0)
        self.assertEqual(value["requestGroups"][0]["fields"], [])
    def test_aggregated_header_batches_and_omissions_reach_dashboard(self):
        probe = observer()
        probe.sessions = {123: {"service": False}}
        probe.consume_snapshot(123, {
            "stats": {"tracerHooks": 0, "transportHooks": 1},
            "tracerCounts": {}, "connections": {}, "failures": 0,
            "packets": [{"metadata": {"protocol": "msgpack", "opcode": 27,
                                      "fields": [], "bytes": 10, "token": "SECRET"},
                         "count": 5, "minBytes": 10, "maxBytes": 20, "bytesTotal": 80,
                         "body": "SECRET"}],
            "omittedPackets": 2, "omittedBytes": 48,
        })
        report = probe.observation.snapshot()
        self.assertEqual(report["dashboard"]["observedPackets"], 7)
        self.assertEqual(report["dashboard"]["observedBytes"], 128)
        self.assertEqual(report["dashboard"]["excludedPackets"], 2)
        self.assertEqual(report["requestGroups"][0]["count"], 5)
        self.assertEqual(report["requestGroups"][0]["maxBytes"], 20)
        self.assertNotIn("SECRET", json.dumps(report))

    def test_guard_text_redaction_preserves_offline_configuration(self):
        source=target.redacted_guard_source({"offline":True,"profile":"D:/synthetic","hostProfile":"C:/synthetic"})
        self.assertIn('"offline": true',source)
        self.assertNotIn(target.GUARD_QT_BRANCH,source)
        self.assertIn("count('qt_messages_omitted');return;",source)
        self.assertIn("GUARD.offline && name === 'ws2_32.dll'",source)
        self.assertIn("protectedPath(path)",source)
        self.assertIn("recv('privacy-snapshot'",source)
        self.assertIn("rpc.exports.selftest()",source)
        self.assertNotIn("exports_sync",source)

    def test_redaction_fails_before_launch_if_guard_anchor_changes(self):
        with patch.object(target.Path,"read_text",return_value="no expected anchor"):
            with self.assertRaises(ValueError):
                target.redacted_guard_source({"offline":True})

    def test_guard_summary_strict_labels_and_values(self):
        raw={"pid":123,"counters":{"credential_read_denied":1,"SECRET_TOKEN":999,"host_profile_file_denied":"SECRET"},
             "hooks":20,"namespaceValues":{"appFolder":"MXMOD","instance":"SECRET","pipe":"mxmod_local_pipe"},
             "configValues":{"maskLogs":1,"sendErrorLogs":0,"autoUpdate":"SECRET"},
             "sdkObjectPresent":True,"text":"SECRET"}
        safe=target.safe_guard_summary(raw)
        self.assertEqual(safe["counters"],{"credential_read_denied":1})
        self.assertFalse(safe["namespaceMatches"]["instance"])
        self.assertNotIn("SECRET",json.dumps(safe))

    def test_guard_errors_store_no_message_or_stack(self):
        probe=observer()
        probe.guard_message(123,{"type":"error","stack":"SECRET","description":"SECRET"},b"SECRET")
        self.assertTrue(probe.guard_failed)
        self.assertTrue(probe.stopping.is_set())
        self.assertNotIn("SECRET",json.dumps(probe.records))

    def test_qt_text_and_unknown_guard_events_are_discarded(self):
        probe=observer()
        for kind in ("qt-message","namespace-path","namespace-value","unknown"):
            probe.guard_message(123,{"payload":{"type":kind,"text":"SECRET","path":"SECRET"}},b"SECRET")
        self.assertEqual(probe.records,[])

    def test_raw_packet_and_exception_messages_do_not_reach_records(self):
        probe=observer()
        probe.monitor_message(123,{"payload":{"kind":"session-init"}},b"SECRET_AUTH_MESSAGE")
        probe.monitor_message(123,{"type":"error","description":"SECRET"},b"SECRET")
        probe.monitor_message(123,{"payload":{"kind":"unknown","value":"SECRET"}},b"SECRET")
        serialized=json.dumps({"records":probe.records,"status":probe.observation.snapshot()})
        self.assertNotIn("SECRET",serialized)
        self.assertEqual(probe.observation.snapshot()["requests"],[])

    def test_fatal_record_has_only_valid_module_offsets(self):
        probe=observer()
        probe.monitor_message(123,{"payload":{"kind":"fatal","frames":[
            {"module":"Qt6Core.dll","rva":100,"argument":"SECRET"},
            {"module":"C:/SECRET/profile","rva":1},
            {"module":"core.dll","rva":-1}]}},None)
        self.assertEqual(probe.observation.snapshot()["fatalFrames"],[{"module":"Qt6Core.dll","rva":100}])
        self.assertNotIn("SECRET",json.dumps(probe.records))

    def test_stdout_is_counted_without_decoding(self):
        probe=observer()
        probe.on_output(123,2,b"SECRET\xff\xfe")
        self.assertEqual(probe.output_bytes,8)
        self.assertEqual(probe.records,[])

    def test_child_cleanup_catches_public_frida_error_without_logging_values(self):
        probe=observer()
        probe.retain=Mock(side_effect=OSError("SYNTHETIC_PRIVATE_VALUE"))
        probe.device=Mock()
        probe.device.kill.side_effect=target.frida.ProcessNotFoundError("SYNTHETIC_PRIVATE_VALUE")
        probe.on_child(SimpleNamespace(pid=123))
        self.assertTrue(probe.guard_failed)
        probe.device.kill.assert_called_once_with(123)
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE",json.dumps(probe.records))

    def test_run_failure_still_closes_owned_handles_when_detach_raises(self):
        probe=observer()
        probe.device=Mock()
        probe.device.spawn.return_value=123
        probe.executable=Path("D:/synthetic/max.exe")
        probe.specs={False:{"tracerCallsites":[]},True:{"tracerCallsites":[]}}
        probe.detached={}
        owned=Mock()
        probe.owned={123:owned}
        probe.retain=Mock()
        session=Mock()
        session.detach.side_effect=target.frida.InvalidOperationError("SYNTHETIC_PRIVATE_VALUE")
        probe.sessions={123:{"session":session,"service":False}}
        probe.instrument=Mock(side_effect=target.frida.ProcessNotFoundError("SYNTHETIC_PRIVATE_VALUE"))
        with tempfile.TemporaryDirectory() as temporary:
            probe.output=Path(temporary)
            probe.profile=probe.output/"profile"
            result=probe.run(1)
            self.assertTrue(result["guardFailed"])
            for filename in ("events.json","summary.json"):
                self.assertNotIn("SYNTHETIC_PRIVATE_VALUE",(probe.output/filename).read_text(encoding="utf8"))
        owned.stop.assert_called_once_with()
        owned.close.assert_called_once_with()
        session.detach.assert_called_once_with()


if __name__=="__main__":
    unittest.main()
