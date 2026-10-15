"""Synthetic HTTP boundary tests; no MAX, accounts or native requests."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock

from privacy_server import PORT, handler_factory
from chat_export_service import ChatExportError


class ChatExportHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.execute.return_value = {"schema": 1, "state": "exporting"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(lambda: {"schema": 1}, chat_export=self.store))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, method="POST", action="status", value=None, *, raw=None, **extra):
        if raw is None:
            raw = json.dumps(value if value is not None else {"schema": 1, "jobId": "a" * 32, "jobToken": "b" * 64}).encode()
        headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json;charset=UTF-8", "X-Max-Mod-Local-Write": "chat-export-v1", **extra}
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        connection.request(method, "/chat-export/" + action, body=raw, headers=headers)
        response = connection.getresponse(); result = response.status, dict(response.headers), response.read()
        connection.close(); return result

    def test_explicit_local_post_only(self):
        self.assertEqual(self.request()[0], 200)
        self.store.execute.assert_called_once()
        self.assertEqual(self.store.execute.call_args.args[0], "status")
        for header in ({"Origin": "null"}, {"Origin": "https://example.invalid"}, {"Host": "example.invalid"},
                       {"X-Max-Mod-Local-Write": ""}, {"Content-Type": "text/plain"}, {"Transfer-Encoding": "chunked"}):
            self.assertEqual(self.request(**header)[0], 403)
        self.assertEqual(self.request(method="GET")[0], 404)
        self.assertEqual(self.request(method="OPTIONS")[0], 405)
        self.assertEqual(self.store.execute.call_count, 1)

    def test_strict_schema_duplicate_keys_invalid_json_and_no_reflection(self):
        for raw in (b"[]", b"bad", b'{"schema":NaN}', b'{"schema":true}',
                    b'{"schema":1,"schema":1,"jobId":"a","jobToken":"b"}',
                    b'{"schema":1,"jobId":"PRIVATE_VALUE","jobToken":"b","extra":0}'):
            code, _, body = self.request(raw=raw)
            self.assertEqual(code, 400); self.assertNotIn(b"PRIVATE_VALUE", body)
        self.store.execute.assert_not_called()

    def test_bounded_page_size_and_no_cors_or_cache(self):
        code, headers, _ = self.request()
        self.assertEqual(code, 200); self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request(raw=b"{}", **{"Content-Length": "2097153"})[0], 413)
        self.assertEqual(self.request(raw=b"{}", **{"Content-Length": "99999999"})[0], 400)

    def test_fixed_service_errors(self):
        for error, status in (("busy", 409), ("sequence_conflict", 409), ("not_found", 404), ("invalid_request", 400), ("io_error", 503)):
            self.store.execute.side_effect = ChatExportError(error)
            code, _, body = self.request()
            self.assertEqual(code, status); self.assertEqual(json.loads(body)["error"], error)


if __name__ == "__main__": unittest.main()
