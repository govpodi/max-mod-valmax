"""Synthetic local HTTP boundaries; no accounts, messenger or external calls."""
import http.client
from http.server import ThreadingHTTPServer
import json
import threading
import unittest
from unittest.mock import Mock

from privacy_server import PORT, handler_factory
from chat_lock_service import ChatLockError
from inline_media import InlineMediaError


class FeatureHTTPTests(unittest.TestCase):
    def setUp(self):
        self.locks = Mock()
        self.locks.catalog.return_value = {"schema": 1, "ready": True, "protected": []}
        self.locks.execute.return_value = {"schema": 1, "ready": True, "protected": True, "locked": True}
        self.media = Mock()
        self.media.search.return_value = {"schema": 1, "results": []}
        self.media.select.return_value = {"schema": 1, "url": "file:///synthetic.jpg"}
        self.media.preview.return_value = (b"OWN-IMAGE-FIXTURE", "image/jpeg")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(
            lambda: {"schema": 1}, chat_locks=self.locks, inline_media=self.media))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, value=None, *, method="POST", raw=None, extra=None):
        headers = {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json;charset=UTF-8",
                   "X-Max-Mod-Local-Write": "chat-locks-v1" if path.startswith("/chat-locks") else "inline-media-v1"}
        headers.update(extra or {})
        if raw is None and method == "POST":
            raw = json.dumps(value).encode()
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=4)
        try:
            connection.request(method, path, body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def test_pin_catalog_and_exact_scope_routing(self):
        self.assertEqual(self.request("/chat-locks", method="GET")[0], 200)
        value = {"schema": 1, "accountId": "9007199254740993", "chatId": "-77", "pin": "681439"}
        captured = []
        self.locks.execute.side_effect = lambda action, received: captured.append((action, dict(received))) or {"schema": 1, "ready": True, "protected": True, "locked": True}
        self.assertEqual(self.request("/chat-locks/unlock", value)[0], 200)
        action, received = captured[0]
        self.assertEqual(action, "unlock")
        self.assertEqual(received, value)
        self.assertEqual(self.request("/chat-locks/lock-all", {"schema": 1})[0], 200)
        self.assertEqual(self.request("/chat-locks/unlock", method="GET")[0], 404)

    def test_pin_finite_errors_and_no_secret_reflection(self):
        value = {"schema": 1, "accountId": "42", "chatId": "-77", "pin": "PRIVATE-SENTINEL"}
        for error, expected in (("wrong_pin", 403), ("rate_limited", 429), ("invalid_pin", 400),
                                ("already_protected", 409), ("invalid_store", 503), ("PRIVATE-SENTINEL", 503)):
            self.locks.execute.side_effect = ChatLockError(error)
            status, headers, body = self.request("/chat-locks/unlock", value)
            self.assertEqual(status, expected)
            self.assertNotIn(b"PRIVATE-SENTINEL", body)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_local_writes_require_native_headers_and_no_origin(self):
        routes = (("/chat-locks/lock-all", {"schema": 1}),
                  ("/inline-media/search", {"mode": "pic", "query": "fixture"}))
        for path, value in routes:
            for extra in ({"Origin": "null"}, {"Origin": "https://example.invalid"},
                          {"Host": "example.invalid"}, {"X-Max-Mod-Local-Write": ""},
                          {"Content-Type": "text/plain"}, {"Transfer-Encoding": "chunked"}):
                self.assertEqual(self.request(path, value, extra=extra)[0], 403)
            self.assertEqual(self.request(path, method="OPTIONS")[0], 405)
        self.locks.execute.assert_not_called()
        self.media.search.assert_not_called()

    def test_strict_schema_and_duplicate_keys(self):
        requests = (("/chat-locks/lock-all", b'{"schema":true}'),
                    ("/chat-locks/lock-all", b'{"schema":1,"schema":1}'),
                    ("/inline-media/search", b'{"mode":"pic","query":"PRIVATE-SENTINEL","extra":0}'),
                    ("/inline-media/search", b'{"mode":"pic","query":"x","query":"y"}'),
                    ("/inline-media/select", b'{"id":NaN}'),
                    ("/inline-media/select", b'[]'))
        for path, raw in requests:
            code, _, body = self.request(path, raw=raw)
            self.assertEqual(code, 400)
            self.assertNotIn(b"PRIVATE-SENTINEL", body)
        self.locks.execute.assert_not_called()
        self.media.search.assert_not_called()
        self.media.select.assert_not_called()

    def test_media_explicit_search_selection_and_finite_errors(self):
        self.assertEqual(self.request("/inline-media/search", {"mode": "pic", "query": "fixture"})[0], 200)
        self.assertEqual(self.request("/inline-media/select", {"id": "a" * 32})[0], 200)
        self.assertEqual(self.media.search.call_count, 1)
        self.assertEqual(self.media.select.call_count, 1)
        for code, status in (("provider_unavailable", 503), ("search_too_fast", 429), ("result_expired", 410)):
            self.media.search.side_effect = InlineMediaError(code, status)
            result, _, body = self.request("/inline-media/search", {"mode": "pic", "query": "PRIVATE-SENTINEL"})
            self.assertEqual(result, status)
            self.assertEqual(json.loads(body)["error"], code)
            self.assertNotIn(b"PRIVATE-SENTINEL", body)

    def test_binary_preview_allowlist_and_missing_service(self):
        path = "/inline-media/preview?id=" + "a" * 32
        code, headers, body = self.request(path, method="GET")
        self.assertEqual((code, headers["Content-Type"], body), (200, "image/jpeg", b"OWN-IMAGE-FIXTURE"))
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Cache-Control"], "no-store")
        for mime, data in (("text/html", b"PRIVATE-SENTINEL"), ("image/jpeg", bytes(2 * 1024 * 1024 + 1))):
            self.media.preview.return_value = (data, mime)
            code, _, body = self.request(path, method="GET")
            self.assertEqual(code, 503)
            self.assertNotIn(b"PRIVATE-SENTINEL", body)
        self.media.preview.side_effect = InlineMediaError("result_expired", 410)
        self.assertEqual(self.request(path, method="GET")[0], 410)


if __name__ == "__main__":
    unittest.main()
