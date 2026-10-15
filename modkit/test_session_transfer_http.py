"""Loopback API tests with mocks and synthetic passwords only."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock

from privacy_server import PORT, handler_factory
from session_transfer import TransferError


class SessionHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.snapshot.return_value = {"schema": 1, "available": True, "state": "idle", "error": None}
        self.store.execute.return_value = {"schema": 1, "state": "done", "action": "export", "credentials": 1}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(lambda: {}, session_transfer=self.store))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method="GET", value=None, headers=None, raw=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        body = raw if raw is not None else (json.dumps(value).encode() if value is not None else None)
        actual = {"Host": f"127.0.0.1:{PORT}", **(headers or {})}
        connection.request(method, "/session-transfer", body=body, headers=actual)
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def post(self, value=None, **headers):
        return self.request("POST", value if value is not None else {
            "schema": 1, "action": "export", "file": "file:///C:/synthetic.maxsession",
            "password": "SYNTHETIC-PRIVATE-PASSWORD"}, headers={"Content-Type": "application/json;charset=UTF-8",
            "X-Max-Mod-Local-Write": "session-transfer-v1", **headers})

    def test_get_returns_status_without_opening_auth(self):
        code, headers, data = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(data), self.store.snapshot.return_value)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.store.execute.assert_not_called()

    def test_explicit_post_and_password_never_in_response(self):
        code, headers, data = self.post()
        self.assertEqual(code, 200)
        self.store.execute.assert_called_once_with("export", "file:///C:/synthetic.maxsession", "SYNTHETIC-PRIVATE-PASSWORD")
        self.assertNotIn(b"PRIVATE", data)
        self.assertNotIn(b"synthetic.maxsession", data)

    def test_origin_wrong_marker_form_and_wrong_host_rejected(self):
        for headers in ({"Origin": "null"}, {"Origin": "https://invalid.test"},
                        {"X-Max-Mod-Local-Write": "quick-replies-v1"},
                        {"Content-Type": "text/plain"}, {"Host": "invalid.test"},
                        {"Transfer-Encoding": "chunked"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(**headers)[0], 403)
        self.assertEqual(self.request("OPTIONS")[0], 405)
        self.store.execute.assert_not_called()

    def test_strict_schema_duplicates_extra_fields_bool_and_oversize(self):
        good = {"schema": 1, "action": "export", "file": "file:///C:/synthetic.maxsession", "password": "synthetic"}
        for value in ({**good, "schema": True}, {**good, "extra": "PRIVATE"}, {"schema": 1}):
            self.assertEqual(self.post(value)[0], 400)
        headers = {"Content-Type": "application/json", "X-Max-Mod-Local-Write": "session-transfer-v1"}
        self.assertEqual(self.request("POST", headers=headers, raw=b'{"schema":1,"schema":1}')[0], 400)
        self.assertEqual(self.post(**{"Content-Length": "262145"})[0], 413)
        self.store.execute.assert_not_called()

    def test_failure_is_only_fixed_code_and_allows_explicit_retry(self):
        for error, expected in (("authentication_failed", 400), ("destination_not_empty", 409), ("busy", 409)):
            self.store.execute.side_effect = TransferError(error)
            code, _, data = self.post()
            self.assertEqual(code, expected)
            self.assertEqual(json.loads(data), {"schema": 1, "state": "error", "error": error})
            self.assertNotIn(b"PRIVATE", data)


if __name__ == "__main__":
    unittest.main()
