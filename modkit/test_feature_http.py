"""Synthetic local HTTP boundaries; no accounts, messenger or external calls."""
import http.client
from email.message import Message
from http.server import ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

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
        handler = handler_factory(lambda: {"schema": 1}, chat_locks=self.locks, inline_media=self.media)
        class ObservedHandler(handler):
            def discard_rejected_body(self, max_body):
                self.server.discard_started.set()
                try:
                    super().discard_rejected_body(max_body)
                finally:
                    self.server.discard_finished.set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), ObservedHandler)
        self.server.discard_started = threading.Event()
        self.server.discard_finished = threading.Event()
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

    def rejected_headers(self, connection, length):
        connection.putrequest("POST", "/inline-media/search", skip_host=True)
        for name, value in {"Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json",
                            "X-Max-Mod-Local-Write": "inline-media-v1", "Origin": "https://example.invalid",
                            "Content-Length": str(length)}.items():
            connection.putheader(name, value)
        connection.endheaders()

    def test_delayed_rejected_body_returns_403_without_parsing_or_store(self):
        body = b"PRIVATE-SENTINEL\x00\xff" + b"x" * 32768
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            with patch("privacy_server.json.loads", side_effect=AssertionError("rejected body parsed")):
                self.rejected_headers(connection, len(body))
                self.assertTrue(self.server.discard_started.wait(1))
                connection.send(body[:17])
                threading.Event().wait(0.03)
                connection.send(body[17:])
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                result = response.read()
            self.assertTrue(self.server.discard_finished.wait(1))
            self.assertEqual(json.loads(result), {"schema": 1, "error": "local_write_required"})
            self.assertNotIn(b"PRIVATE-SENTINEL", result)
        finally:
            connection.close()
        self.locks.execute.assert_not_called()
        self.media.search.assert_not_called()

    def test_incomplete_rejected_body_has_a_short_total_deadline(self):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=2)
        try:
            started = time.monotonic()
            self.rejected_headers(connection, 65536)
            self.assertTrue(self.server.discard_started.wait(1))
            connection.send(b"x")
            response = connection.getresponse()
            self.assertEqual(response.status, 403)
            response.read()
            self.assertTrue(self.server.discard_finished.wait(1))
            self.assertLess(time.monotonic() - started, 1)
        finally:
            connection.close()
        self.media.search.assert_not_called()

    def test_rejection_never_consumes_ambiguous_chunked_or_oversized_body(self):
        handler = handler_factory(lambda: {})
        for fields in ([], [("Content-Length", "1"), ("Content-Length", "1")],
                       [("Content-Length", "1"), ("Transfer-Encoding", "chunked")],
                       [("Content-Length", "32769")], [("Content-Length", "-1")],
                       [("Content-Length", "999999999999999999999999")]):
            headers = Message()
            for key, value in fields: headers[key] = value
            fixture = SimpleNamespace(headers=headers, connection=Mock(), rfile=Mock())
            fixture.rfile.read1.side_effect = AssertionError("unsafe rejected framing consumed")
            handler.discard_rejected_body(fixture, 32768)
            fixture.rfile.read1.assert_not_called()
            fixture.connection.gettimeout.assert_not_called()

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

    def test_media_cooldown_metadata_is_finite_on_search_select_and_preview(self):
        routes = (("/inline-media/search", "POST", {"mode": "pic", "query": "PRIVATE-SENTINEL"}, self.media.search),
                  ("/inline-media/select", "POST", {"id": "a" * 32}, self.media.select),
                  ("/inline-media/preview?id=" + "a" * 32, "GET", None, self.media.preview))
        for path, method, payload, operation in routes:
            for delay in (10, None):
                with self.subTest(path=path, delay=delay):
                    operation.side_effect = InlineMediaError("provider_busy", 503, retry_after_seconds=delay)
                    status, headers, body = self.request(path, payload, method=method)
                    self.assertEqual(503, status)
                    expected = {"schema": 1, "error": "provider_busy"}
                    if delay is not None:
                        expected["retryAfterSeconds"] = delay
                        self.assertEqual(str(delay), headers["Retry-After"])
                    else:
                        self.assertNotIn("Retry-After", headers)
                    self.assertEqual(expected, json.loads(body))
                    self.assertEqual("no-store", headers["Cache-Control"])
                    self.assertNotIn(b"PRIVATE-SENTINEL", body)
                    self.assertNotIn("Access-Control-Allow-Origin", headers)
            operation.side_effect = None

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
