"""Synthetic host routing and HTTP boundaries; no MAX or profile access."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock

from privacy_server import Monitor, PORT, handler_factory
from message_retention_service import RetentionServiceError


def event():
    return {"kind": "retained-delete", "schema": 1, "accountId": "10", "chatId": "20", "messageIds": ["9007199254740993"]}


class RetentionHostTests(unittest.TestCase):
    def monitor(self):
        monitor = Monitor.__new__(Monitor)
        monitor.message_retention = Mock()
        monitor.observation = Mock()
        monitor.sessions = {123: (Mock(), Mock(), Mock())}
        return monitor

    def test_marker_events_are_never_forwarded_to_diagnostics_or_service_process(self):
        monitor = self.monitor()
        monitor._message({"type": "send", "payload": event()}, None, role="main", pid=123)
        monitor.message_retention.record.assert_called_once_with(123, event())
        self.assertEqual(monitor.observation.mock_calls, [])
        monitor.message_retention.record.reset_mock()
        monitor._message({"type": "send", "payload": event()}, None, role="service", pid=123)
        monitor.message_retention.record.assert_not_called()
        self.assertEqual(monitor.observation.mock_calls, [])

    def test_storage_failure_disables_future_native_suppression_without_payload_log(self):
        monitor = self.monitor()
        monitor.message_retention.record.side_effect = RetentionServiceError("io_error")
        monitor._message({"type": "send", "payload": event()}, None, role="main", pid=123)
        monitor.message_retention.invalidate.assert_called_once_with(123)
        monitor.sessions[123][1].post.assert_called_once_with({"type": "valmax-retention-storage", "available": False})
        self.assertEqual(monitor.observation.mock_calls, [])

    def test_received_id_batches_use_feature_storage_without_diagnostic_forwarding(self):
        monitor = self.monitor()
        payload = dict(event(), kind="received-messages")
        monitor._message({"type": "send", "payload": payload}, None, role="main", pid=123)
        monitor.message_retention.record_received.assert_called_once_with(123, payload)
        monitor.message_retention.record.assert_not_called()
        self.assertEqual(monitor.observation.mock_calls, [])
        monitor.message_retention.record_received.reset_mock()
        monitor._message({"type": "send", "payload": payload}, None, role="service", pid=123)
        monitor.message_retention.record_received.assert_not_called()


class RetentionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.lookup.return_value = {"schema": 1, "accountId": "10", "chatId": "20", "deletedIds": [], "revision": "a" * 64, "enabled": False, "ready": True}
        self.service.snapshot.return_value = {"schema": 1, "revision": "a" * 64, "enabled": False, "runtime": {"ready": False, "applied": False, "enabled": False, "retainedEvents": 0, "retainedMessages": 0, "error": None, "historySyncVerified": False}}
        self.service.configure.return_value = self.service.snapshot.return_value
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(lambda: {"schema": 1}, message_retention=self.service))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, action="lookup", *, method="POST", value=None, raw=None, **extra):
        if raw is None:
            value = value if value is not None else {"schema": 1, "accountId": "10", "chatId": "20", "messageIds": ["9007199254740993"]}
            raw = json.dumps(value).encode()
        headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json", "X-Max-Mod-Local-Write": "message-retention-v1", **extra}
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        connection.request(method, "/message-retention/" + action, body=raw, headers=headers)
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def test_lookup_settings_status_routes_and_no_cors_cache(self):
        code, headers, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["accountId"], "10")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("settings", value={"schema": 1, "revision": "a" * 64, "enabled": True})[0], 200)
        self.assertEqual(self.request("status", method="GET")[0], 200)
        self.service.configure.assert_called_once()
        self.service.snapshot.assert_called_once()

    def test_web_origin_wrong_header_wrong_host_and_large_body_are_rejected(self):
        for headers in ({"Origin": "null"}, {"Origin": "https://example.invalid"}, {"Host": "example.invalid"},
                        {"X-Max-Mod-Local-Write": ""}, {"Content-Type": "text/plain"}, {"Transfer-Encoding": "chunked"}):
            self.assertEqual(self.request(**headers)[0], 403)
        self.assertEqual(self.request(raw=b"{}", **{"Content-Length": "32769"})[0], 413)
        self.assertEqual(self.request(method="OPTIONS")[0], 405)
        self.assertEqual(self.request(method="GET")[0], 404)
        self.service.lookup.assert_not_called()

    def test_duplicate_keys_and_unexpected_secret_fields_are_never_reflected(self):
        for raw in (b'[]', b'{"schema":true}', b'{"schema":1,"schema":1}',
                    b'{"schema":1,"accountId":"10","chatId":"20","messageIds":[],"token":"PRIVATE_VALUE"}'):
            status, _, body = self.request(raw=raw)
            self.assertEqual(status, 400)
            self.assertNotIn(b"PRIVATE_VALUE", body)
        self.service.lookup.assert_not_called()

    def test_service_error_is_finite_and_cas_conflict_is409(self):
        self.service.configure.side_effect = RetentionServiceError("revision_conflict")
        status, _, body = self.request("settings", value={"schema": 1, "revision": "a" * 64, "enabled": True})
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(body), {"schema": 1, "error": "revision_conflict"})
        self.service.lookup.side_effect = RetentionServiceError("io_error")
        self.assertEqual(self.request()[0], 503)


if __name__ == "__main__":
    unittest.main()
