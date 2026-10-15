"""Synthetic HTTP support-export authorization and exact-shape contracts."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock

from privacy_server import PORT, handler_factory
from support_diagnostics import SupportDiagnosticsError


class SupportHTTPTests(unittest.TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.preview.return_value = {"schema": 1, "report": {"schema": 1}}
        self.service.export.return_value = {"schema": 1, "saved": True}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(lambda: {}, support_diagnostics=self.service))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)

    def request(self, *, method="POST", raw=None, extra=None, path="/support-diagnostics"):
        headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json", "X-Max-Mod-Local-Write": "support-diagnostics-v1"}
        headers.update(extra or {})
        if raw is None and method == "POST": raw = b'{"schema":1,"file":"file:///D:/synthetic-report.json"}'
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally: connection.close()

    def test_preview_never_exports_and_returns_no_cache_headers(self):
        code, headers, body = self.request(method="GET")
        self.assertEqual(code, 200); self.assertEqual(json.loads(body), self.service.preview.return_value)
        self.service.export.assert_not_called()
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request(method="GET", path="/support-diagnostics?file=private")[0], 404)

    def test_only_explicit_post_exports_chosen_file(self):
        self.assertEqual(self.request()[0], 200)
        self.service.export.assert_called_once_with("file:///D:/synthetic-report.json")

    def test_origins_and_missing_native_write_marker_never_export(self):
        for extra in ({"Origin": "null"}, {"Origin": "https://example.invalid"}, {"Host": "example.invalid"},
            {"X-Max-Mod-Local-Write": ""}, {"Content-Type": "text/plain"}, {"Expect": "100-continue"}, {"Transfer-Encoding": "chunked"}):
            self.assertEqual(self.request(extra=extra)[0], 403)
        self.assertEqual(self.request(method="OPTIONS")[0], 405)
        self.service.export.assert_not_called()

    def test_duplicate_unknown_keys_and_wrong_schema_never_export(self):
        for raw in (b'[]', b'{"schema":true,"file":"x"}', b'{"schema":1,"schema":1,"file":"x"}',
            b'{"schema":1,"file":"x","private":"PRIVATE-SENTINEL"}', b'{"schema":1}', b'{"schema":NaN,"file":"x"}'):
            code, _, body = self.request(raw=raw)
            self.assertEqual(code, 400); self.assertNotIn(b"PRIVATE-SENTINEL", body)
        self.service.export.assert_not_called()

    def test_failure_codes_do_not_echo_file_path_or_exception_message(self):
        for code, expected in (("file_exists", 409), ("unsafe_path", 400), ("io_error", 503), ("PRIVATE-SENTINEL", 503)):
            self.service.export.side_effect = SupportDiagnosticsError(code)
            status, _, body = self.request()
            self.assertEqual(status, expected); self.assertNotIn(b"PRIVATE-SENTINEL", body)
            self.assertNotIn(b"synthetic-report", body)


if __name__ == "__main__": unittest.main()
