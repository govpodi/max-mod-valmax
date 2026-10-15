"""Synthetic loopback contract tests; never reads real settings or starts MAX."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock

from privacy_server import PORT, handler_factory
from quick_reply_store import QuickReplyStoreError
from test_http_transport import CoalescedHTTPConnection


class QuickReplyHTTPTests(unittest.TestCase):
    def setUp(self):
        self.value = {"schema": 1, "revision": "a" * 64, "replies": []}
        self.store = Mock()
        self.store.snapshot.return_value = self.value
        self.store.replace.return_value = self.value
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(
            lambda: {"schema": 1, "safe": True}, None, self.store))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method="GET", path="/quick-replies", body=None, headers=None):
        connection = CoalescedHTTPConnection(*self.server.server_address, timeout=4)
        actual = {"Host": f"127.0.0.1:{PORT}", **(headers or {})}
        connection.request(method, path, body=body, headers=actual)
        response = connection.getresponse()
        result = response.status, dict(response.headers), response.read()
        connection.close()
        return result

    def post(self, value=None, *, body=None, **headers):
        if body is None:
            body = json.dumps(value if value is not None else self.value).encode()
        return self.request("POST", body=body, headers={
            "Content-Type": "application/json", "X-Max-Mod-Local-Write": "quick-replies-v1",
            **headers})

    def test_get_is_not_cacheable_and_never_mutates(self):
        code, headers, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), self.value)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.store.replace.assert_not_called()
        self.assertEqual(json.loads(self.request(path="/status")[2]), {"schema": 1, "safe": True})

    def test_explicit_save_passes_only_revision_and_replies(self):
        code, _, body = self.post()
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), self.value)
        self.store.replace.assert_called_once_with("a" * 64, [])

    def test_qt_utf8_content_type_parameter_is_accepted(self):
        for content_type in ("application/json;charset=UTF-8", "application/json; charset=utf-8",
                             'Application/JSON; charset="UTF-8"'):
            with self.subTest(content_type=content_type):
                self.assertEqual(self.post(**{"Content-Type": content_type})[0], 200)
        self.assertEqual(self.post(**{"Content-Type": "application/json; charset=iso-8859-1"})[0], 403)

    def test_web_origins_wrong_host_header_and_simple_form_are_rejected(self):
        for headers in ({"Origin": "https://untrusted.invalid"}, {"Origin": "null"},
                        {"Host": "untrusted.invalid"}, {"X-Max-Mod-Local-Write": ""},
                        {"Content-Type": "text/plain"}, {"Transfer-Encoding": "chunked"}):
            with self.subTest(headers=headers):
                self.assertEqual(self.post(**headers)[0], 403)
        self.assertEqual(self.request("OPTIONS")[0], 405)
        self.store.replace.assert_not_called()

    def test_invalid_json_schema_extra_keys_and_duplicates_do_not_write(self):
        for body in (b"not-json", b"\xff", b'{"schema":NaN}', b"[]",
                     b'{"schema":1,"schema":1,"revision":"a","replies":[]}',
                     json.dumps({**self.value, "extra": "PRIVATE_VALUE"}).encode(),
                     json.dumps({**self.value, "schema": True}).encode()):
            with self.subTest(body=body[:20]):
                self.assertEqual(self.post(body=body)[0], 400)
        self.store.replace.assert_not_called()

    def test_body_size_is_bounded_before_read(self):
        self.assertEqual(self.post(body=b"{}", **{"Content-Length": "262145"})[0], 413)
        self.assertEqual(self.post(body=b"{}", **{"Content-Length": "999999999999999"})[0], 400)
        self.store.replace.assert_not_called()

    def test_store_failures_are_fixed_codes_and_revision_conflict_is_409(self):
        for code, status in (("revision_conflict", 409), ("invalid_replies", 400),
                             ("unsafe_path", 503), ("invalid_store", 503)):
            with self.subTest(code=code):
                self.store.replace.side_effect = QuickReplyStoreError(code)
                actual, _, body = self.post()
                self.assertEqual(actual, status)
                self.assertEqual(json.loads(body), {"schema": 1, "error": code})
        self.store.snapshot.side_effect = QuickReplyStoreError("invalid_store")
        self.assertEqual(self.request()[0], 503)


if __name__ == "__main__":
    unittest.main()
