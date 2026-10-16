"""Owned fixtures and mocked HTTP only: provider limits never touch Wikimedia."""
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from email.utils import formatdate
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import inline_media as media


ORIGINAL = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Synthetic.png"
THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Synthetic.png/240px-Synthetic.png"
PNG = b"\x89PNG\r\n\x1a\nsynthetic"


def headers(**values):
    result = Message()
    for name, value in values.items():
        result[name.replace("_", "-")] = value
    return result


class Response:
    def __init__(self, url, data=PNG, mime="image/png"):
        self.url = url
        self.data = data
        self.headers = headers(Content_Type=mime, Content_Length=str(len(data)))
        self.closed = False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.data[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.closed = True


class RetryDelayTests(unittest.TestCase):
    def test_delta_and_future_http_date(self):
        wall = 1_700_000_000.0
        self.assertEqual(10, media._retry_delay("10", wall))
        self.assertEqual(37, media._retry_delay(formatdate(wall + 37, usegmt=True), wall))

    def test_invalid_missing_past_and_short_values_wait_at_least_five_seconds(self):
        wall = 1_700_000_000.0
        for value in (None, "", "0", "1", "-10", "nonsense", "1.5", "NaN",
                      formatdate(wall - 60, usegmt=True)):
            with self.subTest(value=value):
                self.assertGreaterEqual(media._retry_delay(value, wall), 5)

    def test_valid_long_delay_is_not_clamped_to_the_ui_limit(self):
        self.assertGreaterEqual(media._retry_delay("100000", 1_700_000_000.0), 100000)

    def test_only_valid_bounded_integer_delay_can_be_exposed_in_local_error(self):
        for value in (0, -1, True, 1.5, "10", 86401):
            with self.subTest(value=value), self.assertRaises(ValueError):
                media.InlineMediaError("provider_busy", 503, retry_after_seconds=value)
        error = media.InlineMediaError("provider_busy", 503, retry_after_seconds=10)
        self.assertEqual("provider_busy", str(error))
        self.assertEqual(10, error.retry_after_seconds)


class ProviderGateTests(unittest.TestCase):
    def test_cooldown_blocks_all_priorities_and_only_manual_request_after_deadline_enters(self):
        now = [10.0]
        gate = media._ProviderGate(clock=lambda: now[0], wall_clock=lambda: 1_700_000_000.0)
        gate.defer("10")
        for preview in (False, True, False):
            with self.subTest(preview=preview), self.assertRaises(media.InlineMediaError) as caught:
                with gate.request(preview=preview):
                    self.fail("Cooldown must prevent provider entry")
            self.assertEqual("provider_busy", caught.exception.code)
            self.assertEqual(10, caught.exception.retry_after_seconds)
        now[0] += 9.1
        with self.assertRaises(media.InlineMediaError) as caught:
            with gate.request():
                self.fail("The last fraction of a second is still a cooldown")
        self.assertEqual(1, caught.exception.retry_after_seconds)
        now[0] += 0.9
        with gate.request():
            pass

    def test_second_shorter_limit_does_not_shorten_an_existing_cooldown(self):
        now = [0.0]
        gate = media._ProviderGate(clock=lambda: now[0], wall_clock=lambda: 1_700_000_000.0)
        gate.defer("30")
        now[0] = 1.0
        gate.defer("5")
        with self.assertRaises(media.InlineMediaError) as caught:
            with gate.request():
                self.fail("A second response must not reset a longer wait")
        self.assertEqual(29, caught.exception.retry_after_seconds)

    def test_long_cooldown_is_preserved_beyond_one_day_without_misleading_short_hint(self):
        now = [0.0]
        gate = media._ProviderGate(clock=lambda: now[0], wall_clock=lambda: 1_700_000_000.0)
        gate.defer("100000")
        with self.assertRaises(media.InlineMediaError) as caught:
            with gate.request():
                self.fail("Long provider delay remains in force")
        self.assertIsNone(caught.exception.retry_after_seconds)
        now[0] = 86401.0
        with self.assertRaises(media.InlineMediaError) as caught:
            with gate.request():
                self.fail("UI bounds must not shorten the actual provider delay")
        self.assertEqual(13599, caught.exception.retry_after_seconds)

    def test_already_queued_request_rechecks_cooldown_before_entry(self):
        gate = media._ProviderGate(max_parallel=1, queue_seconds=1)
        attempted = threading.Event()
        entered = threading.Event()

        def queued():
            attempted.set()
            try:
                with gate.request(preview=True):
                    entered.set()
            except media.InlineMediaError as error:
                return error.code
            return "entered"

        with ThreadPoolExecutor(max_workers=1) as pool:
            with gate.request():
                future = pool.submit(queued)
                self.assertTrue(attempted.wait(1))
                # The worker reaches the full gate while the first request owns it.
                time.sleep(0.03)
                gate.defer("10")
            self.assertEqual("provider_busy", future.result(timeout=2))
        self.assertFalse(entered.is_set())

    def test_full_queue_returns_promptly_without_entering_provider(self):
        gate = media._ProviderGate(max_parallel=1, queue_seconds=0.1)
        with gate.request():
            started = time.monotonic()
            with self.assertRaises(media.InlineMediaError):
                with gate.request(preview=True):
                    self.fail("The bounded full gate must not be bypassed")
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.0)

    def test_foreground_download_has_priority_over_an_older_queued_preview(self):
        gate = media._ProviderGate(max_parallel=1, queue_seconds=2)
        attempted = {True: threading.Event(), False: threading.Event()}
        order = []

        def queued(preview):
            attempted[preview].set()
            with gate.request(preview=preview):
                order.append("preview" if preview else "foreground")

        with ThreadPoolExecutor(max_workers=2) as pool:
            with gate.request():
                preview = pool.submit(queued, True)
                self.assertTrue(attempted[True].wait(1))
                time.sleep(0.03)
                foreground = pool.submit(queued, False)
                self.assertTrue(attempted[False].wait(1))
                time.sleep(0.03)
            foreground.result(timeout=3)
            preview.result(timeout=3)
        self.assertEqual(["foreground", "preview"], order)


class FetchTests(unittest.TestCase):
    def test_identified_user_agent_and_identity_encoding_on_api_and_media(self):
        calls = []

        def opening(request, timeout):
            calls.append(request)
            api = request.full_url.startswith(media.API)
            return Response(request.full_url, b"{}" if api else PNG,
                            "application/json" if api else "image/png")

        opener = Mock()
        opener.open.side_effect = opening
        with patch.object(media, "_PROVIDER_GATE", media._ProviderGate()), patch.object(media, "build_opener", return_value=opener):
            media.fetch(media.API + "?action=query", 100, "api")
            media.fetch(ORIGINAL, 100, "media")
        self.assertEqual(2, len(calls))
        for request in calls:
            self.assertEqual(media.MEDIA_USER_AGENT, request.get_header("User-agent"))
            self.assertIn("https://github.com/govpodi/max-mod-valmax", request.get_header("User-agent"))
            self.assertEqual("identity", request.get_header("Accept-encoding"))

    def test_429_and_503_share_cooldown_across_hosts_without_automatic_retry(self):
        for status in (429, 503):
            with self.subTest(status=status):
                now = [0.0]
                gate = media._ProviderGate(clock=lambda: now[0], wall_clock=lambda: 1_700_000_000.0)
                body = io.BytesIO(b"synthetic upstream response must not leak")
                failure = HTTPError(ORIGINAL, status, "synthetic", headers(Retry_After="10"), body)
                opener = Mock()
                opener.open.side_effect = failure
                with patch.object(media, "_PROVIDER_GATE", gate), patch.object(media, "build_opener", return_value=opener):
                    with self.assertRaises(media.InlineMediaError) as caught:
                        media.fetch(ORIGINAL, 100, "media")
                    self.assertEqual("provider_busy", caught.exception.code)
                    self.assertEqual(10, caught.exception.retry_after_seconds)
                    self.assertEqual("provider_busy", str(caught.exception))
                    self.assertTrue(body.closed)
                    for url, kind in ((media.API + "?action=query", "api"), (THUMB, "media"), (ORIGINAL, "media")):
                        with self.assertRaises(media.InlineMediaError):
                            media.fetch(url, 100, kind)
                    self.assertEqual(1, opener.open.call_count)
                    now[0] = 10.0
                    opener.open.side_effect = None
                    opener.open.return_value = Response(ORIGINAL)
                    self.assertEqual((PNG, "image/png"), media.fetch(ORIGINAL, 100, "media"))
                    self.assertEqual(2, opener.open.call_count)

    def test_missing_retry_after_still_gates_new_requests(self):
        now = [0.0]
        gate = media._ProviderGate(clock=lambda: now[0], wall_clock=lambda: 1_700_000_000.0)
        opener = Mock()
        opener.open.side_effect = HTTPError(ORIGINAL, 429, "synthetic", headers(), io.BytesIO())
        with patch.object(media, "_PROVIDER_GATE", gate), patch.object(media, "build_opener", return_value=opener):
            with self.assertRaises(media.InlineMediaError) as caught:
                media.fetch(ORIGINAL, 100, "media")
            self.assertGreaterEqual(caught.exception.retry_after_seconds, 5)
            with self.assertRaises(media.InlineMediaError):
                media.fetch(ORIGINAL, 100, "media")
            self.assertEqual(1, opener.open.call_count)

    def test_other_http_error_does_not_read_or_leak_body_or_automatically_retry(self):
        body = io.BytesIO(b"synthetic-private-looking-provider-body")
        opener = Mock()
        opener.open.side_effect = HTTPError(ORIGINAL, 404, "synthetic", headers(), body)
        with patch.object(media, "_PROVIDER_GATE", media._ProviderGate()), patch.object(media, "build_opener", return_value=opener):
            with self.assertRaises(media.InlineMediaError) as caught:
                media.fetch(ORIGINAL, 100, "media")
        self.assertEqual("provider_unavailable", str(caught.exception))
        self.assertTrue(body.closed)
        self.assertEqual(1, opener.open.call_count)

    def test_real_threads_share_two_request_cap_for_api_thumbs_and_original(self):
        lock = threading.Lock()
        active = 0
        peak = 0
        pair_entered = threading.Event()
        release = threading.Event()

        def opening(request, timeout):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                if active == 2:
                    pair_entered.set()
            try:
                if not release.wait(2):
                    raise AssertionError("Owned concurrency fixture timed out")
                api = request.full_url.startswith(media.API)
                return Response(request.full_url, b"{}" if api else PNG,
                                "application/json" if api else "image/png")
            finally:
                with lock:
                    active -= 1

        opener = Mock()
        opener.open.side_effect = opening
        jobs = [(media.API + "?action=query", "api"), (THUMB, "media"), (ORIGINAL, "media")] * 2
        with patch.object(media, "_PROVIDER_GATE", media._ProviderGate(max_parallel=2, queue_seconds=2)), patch.object(media, "build_opener", return_value=opener):
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                futures = [pool.submit(media.fetch, url, 100, kind) for url, kind in jobs]
                try:
                    self.assertTrue(pair_entered.wait(1))
                    time.sleep(0.04)
                    with lock:
                        self.assertEqual(2, peak)
                finally:
                    release.set()
                for future in futures:
                    self.assertTrue(future.result(timeout=3)[0])
        self.assertEqual(len(jobs), opener.open.call_count)


def search_body():
    return json.dumps({"query": {"pages": [{"title": "File:Synthetic.png", "imageinfo": [{
        "mime": "image/png", "size": len(PNG), "width": 320, "height": 240,
        "url": ORIGINAL, "thumburl": THUMB,
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Synthetic.png",
        "extmetadata": {"Artist": {"value": "Synthetic author"},
                        "LicenseShortName": {"value": "CC BY 4.0"}}}]}]}}).encode()


class PreviewCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [0.0]

    def test_same_canonical_thumb_across_searches_is_fetched_once(self):
        calls = []

        def transport(url, limit, kind):
            calls.append((url, kind))
            return (search_body(), "application/json") if kind == "api" else (PNG, "image/png")

        service = media.InlineMediaService(Path(self.tmp.name), transport=transport, clock=lambda: self.now[0])
        first = service.search({"mode": "pic", "query": "synthetic"})["results"][0]
        self.assertEqual((PNG, "image/png"), service.preview(first["id"]))
        self.now[0] += 3
        second = service.search({"mode": "pic", "query": "different synthetic"})["results"][0]
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual((PNG, "image/png"), service.preview(second["id"]))
        self.assertEqual(1, sum(kind == "media" for url, kind in calls))

    def test_concurrent_same_preview_has_one_upstream_fetch(self):
        entered = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        calls = 0

        def transport(url, limit, kind):
            nonlocal calls
            if kind == "api":
                return search_body(), "application/json"
            with lock:
                calls += 1
            entered.set()
            if not release.wait(2):
                raise AssertionError("Owned preview fixture timed out")
            return PNG, "image/png"

        service = media.InlineMediaService(Path(self.tmp.name), transport=transport, clock=lambda: self.now[0])
        result_id = service.search({"mode": "pic", "query": "synthetic"})["results"][0]["id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(service.preview, result_id)
            self.assertTrue(entered.wait(1))
            rest = [pool.submit(service.preview, result_id) for unused in range(3)]
            time.sleep(0.03)
            release.set()
            for future in [first, *rest]:
                self.assertEqual((PNG, "image/png"), future.result(timeout=3))
        self.assertEqual(1, calls)

    def test_failed_preview_is_negative_cached_but_does_not_prevent_selecting_original(self):
        preview_calls = 0

        def transport(url, limit, kind):
            nonlocal preview_calls
            if kind == "api":
                return search_body(), "application/json"
            if url == THUMB:
                preview_calls += 1
                raise media.InlineMediaError("provider_unavailable", 503)
            return PNG, "image/png"

        service = media.InlineMediaService(Path(self.tmp.name), transport=transport, clock=lambda: self.now[0])
        result_id = service.search({"mode": "pic", "query": "synthetic"})["results"][0]["id"]
        for unused in range(3):
            with self.assertRaises(media.InlineMediaError):
                service.preview(result_id)
        self.assertEqual(1, preview_calls)
        selected = service.select({"id": result_id})
        self.assertEqual("image/png", selected["mime"])
        self.assertEqual(PNG, next(Path(self.tmp.name).iterdir()).read_bytes())
        self.now[0] += 60
        with self.assertRaises(media.InlineMediaError):
            service.preview(result_id)
        self.assertEqual(2, preview_calls)

    def test_failed_single_flight_releases_every_waiter_and_uses_negative_cache(self):
        entered = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        calls = 0

        def transport(url, limit, kind):
            nonlocal calls
            if kind == "api":
                return search_body(), "application/json"
            with lock:
                calls += 1
            entered.set()
            if not release.wait(2):
                raise AssertionError("Owned failed-preview fixture timed out")
            raise media.InlineMediaError("provider_busy", 503, retry_after_seconds=10)

        service = media.InlineMediaService(Path(self.tmp.name), transport=transport, clock=lambda: self.now[0])
        result_id = service.search({"mode": "pic", "query": "synthetic"})["results"][0]["id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(service.preview, result_id)
            self.assertTrue(entered.wait(1))
            rest = [pool.submit(service.preview, result_id) for unused in range(3)]
            time.sleep(0.03)
            release.set()
            for future in [first, *rest]:
                with self.assertRaises(media.InlineMediaError) as caught:
                    future.result(timeout=3)
                self.assertEqual("provider_busy", caught.exception.code)
        self.assertEqual(1, calls)
        with self.assertRaises(media.InlineMediaError):
            service.preview(result_id)
        self.assertEqual(1, calls)


if __name__ == "__main__":
    unittest.main()
