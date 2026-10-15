"""Loopback MData route tests, mock service, synthetic passwords only."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock, patch

from privacy_server import PORT, handler_factory
from mdata_service import MDataError
from test_http_transport import CoalescedHTTPConnection


class MDataHTTPTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.snapshot.return_value = {"schema": 1, "available": True, "state": "idle",
                                            "action": None, "error": None, "result": None,
                                            "profiles": 0, "backupAvailable": False}
        self.store.execute.return_value = {"schema": 1, "state": "waiting_for_exit",
                                           "accepted": True, "action": "import"}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(lambda: {}, mdata=self.store))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)
        self.value = {"schema": 1, "action": "import", "file": "file:///C:/synthetic-private.mdata",
                      "password": "SYNTHETIC-PRIVATE-PASSWORD-123", "mode": "add"}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method="GET", path="/mdata", value=None, raw=None, headers=None):
        connection = CoalescedHTTPConnection(*self.server.server_address, timeout=4)
        body = raw if raw is not None else (json.dumps(value).encode("utf8") if value is not None else None)
        connection.request(method, path, body=body,
                           headers={"Host": f"127.0.0.1:{PORT}", **(headers or {})})
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def post(self, value=None, raw=None, **headers):
        return self.request("POST", value=self.value if value is None else value, raw=raw,
                            headers={"Content-Type": "application/json;charset=UTF-8",
                                     "X-Max-Mod-Local-Write": "mdata-v1", **headers})

    def test_get_returns_only_status_and_never_submits_auth_operation(self):
        code, headers, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), self.store.snapshot.return_value)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.store.snapshot.assert_called_once_with()
        self.store.execute.assert_not_called()

    def test_exact_five_key_post_returns_shutdown_ack_without_password_or_path(self):
        code, headers, body = self.post()
        self.assertEqual(code, 200)
        self.store.execute.assert_called_once_with("import", self.value["file"], self.value["password"], "add")
        self.assertEqual(json.loads(body), self.store.execute.return_value)
        self.assertEqual(headers["Cache-Control"], "no-store")
        for secret in (self.value["password"].encode(), self.value["file"].encode(), b"private.mdata"):
            self.assertNotIn(secret, body)

    def test_preview_and_selected_export_use_separate_explicit_routes(self):
        headers = {"Content-Type": "application/json", "X-Max-Mod-Local-Write": "mdata-v1"}
        value = {key: self.value[key] for key in ("schema", "file", "password")}
        preview = {"schema": 1, "kind": "login", "profiles": 1, "accounts": [{"accountId": "42", "displayName": "Test", "shortName": "T"}]}
        with patch("mdata_manager.inspect", return_value=preview) as inspect:
            result = self.request("POST", "/mdata/inspect", value=value, headers=headers)
            self.assertEqual(result[0], 200); self.assertEqual(json.loads(result[2]), preview)
            inspect.assert_called_once_with(value["file"], value["password"])
            self.assertEqual(self.request("POST", "/mdata/inspect", value=value, headers={**headers, "Origin": "null"})[0], 403)
            self.assertEqual(self.request("POST", "/mdata/inspect", value={**value, "extra": True}, headers=headers)[0], 400)
            self.assertEqual(inspect.call_count, 1)
        self.store.execute_selected_export.return_value = {"schema": 1, "state": "done", "result": "exported", "profiles": 1}
        result = self.request("POST", "/mdata/export-selected", value={**value, "accountIds": ["42"]}, headers=headers)
        self.assertEqual(result[0], 200)
        self.store.execute_selected_export.assert_called_once_with(value["file"], value["password"], ["42"])
        self.store.execute_confirmed_import.return_value = self.store.execute.return_value
        result = self.request("POST", "/mdata/import-confirmed", value={**value, "fingerprint": "a" * 64}, headers=headers)
        self.assertEqual(result[0], 200)
        self.store.execute_confirmed_import.assert_called_once_with(value["file"], value["password"], "a" * 64)
        self.store.execute.assert_not_called()

    def test_origin_wrong_marker_wrong_host_content_type_and_transfer_encoding_refused(self):
        for headers in ({"Origin": "null"}, {"Origin": "https://untrusted.invalid"},
                        {"Host": "untrusted.invalid"}, {"X-Max-Mod-Local-Write": "session-transfer-v1"},
                        {"X-Max-Mod-Local-Write": ""}, {"Content-Type": "text/plain"},
                        {"Content-Type": "application/json; charset=iso-8859-1"},
                        {"Transfer-Encoding": "chunked"}, {"Expect": "100-continue"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(**headers)[0], 403)
        self.assertEqual(self.request("OPTIONS")[0], 405)
        self.store.execute.assert_not_called()

    def test_extra_missing_bool_duplicate_and_malformed_json_refused(self):
        for value in ({**self.value, "schema": True}, {**self.value, "extra": "PRIVATE"},
                      {key: value for key, value in self.value.items() if key != "mode"}):
            with self.subTest(value=value):
                self.assertEqual(self.post(value)[0], 400)
        for raw in (b"[]", b"not-json", b"\xff", b'{"schema":NaN}',
                    b'{"schema":1,"schema":1,"action":"import","file":"x","password":"y","mode":"add"}'):
            with self.subTest(raw=raw[:20]):
                self.assertEqual(self.post(raw=raw)[0], 400)
        self.store.execute.assert_not_called()

    def test_body_bounds_checked_before_read_or_service_call(self):
        # Headers alone must be rejected without waiting for the claimed body.
        # Unread extra bytes can otherwise make Windows reset the TCP socket.
        self.assertEqual(self.post(raw=b"", **{"Content-Length": "262145"})[0], 413)
        self.assertEqual(self.post(raw=b"", **{"Content-Length": "999999999999999"})[0], 400)
        self.assertEqual(self.post(raw=b"", **{"Content-Length": "0"})[0], 413)
        self.store.execute.assert_not_called()

    def test_failure_responses_have_fixed_codes_and_no_private_detail(self):
        for error, expected in [("authentication_failed", 400), ("invalid_payload", 400),
                                ("busy", 409), ("profile_collision", 409),
                                ("account_limit", 409), ("recovery_required", 409)]:
            with self.subTest(error=error):
                self.store.execute.side_effect = MDataError(error)
                status, _, body = self.post()
                self.assertEqual(status, expected)
                self.assertEqual(json.loads(body), {"schema": 1, "state": "error", "error": error})
                self.assertNotIn(self.value["password"].encode(), body)
                self.assertNotIn(self.value["file"].encode(), body)

    def test_route_matching_is_exact(self):
        for path in ("/mdata/", "/mdata?schema=1", "/MDATA"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
                self.assertEqual(self.request("POST", path, value=self.value)[0], 405)
        self.store.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
