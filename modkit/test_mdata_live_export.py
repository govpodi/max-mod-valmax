"""Live export protocol with memory tokens; never start MAX or read WinCred."""
import http.client
import json
import threading
import unittest
from unittest.mock import Mock, patch
from http.server import ThreadingHTTPServer

import mdata_login
import mdata_service
from privacy_server import PORT, handler_factory
import test_mdata_service as fixtures


class LiveExportTests(unittest.TestCase):
    setUp = fixtures.MDataServiceTests.setUp
    profile = fixtures.MDataServiceTests.profile
    service = fixtures.MDataServiceTests.service
    decode = staticmethod(fixtures.MDataServiceTests.decode)

    def live_service(self, label):
        root, memory = self.profile(label, ["42"])
        activity = Mock(side_effect=AssertionError("Export inspected native process state"))
        service = self.service(root, memory, activity=activity, restart_activity=activity)
        service._wait_closed = Mock(side_effect=AssertionError("Export waited for native shutdown"))
        return root, memory, service, activity

    def export(self, service, path):
        result = service.execute("export", path.as_uri(), fixtures.PASSWORD, "restore")
        self.assertIsNone(service._worker)
        self.assertFalse(service.pending())
        self.assertFalse(service.restart_requested(recheck=True))
        service._wait_closed.assert_not_called()
        self.assertTrue(result["accepted"])
        self.assertEqual(result["action"], "export")
        return result

    def test_live_http_returns_published_result_without_shutdown_ack(self):
        root, memory, service, activity = self.live_service("live-http")
        path = self.base / "live.mdata"
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(lambda: {}, mdata=service))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(*server.server_address, timeout=10)
            request = {"schema": 1, "action": "export", "file": path.as_uri(),
                       "password": fixtures.PASSWORD, "mode": "restore"}
            connection.request("POST", "/mdata", json.dumps(request).encode(), headers={
                "Host": f"127.0.0.1:{PORT}", "Content-Type": "application/json",
                "X-Max-Mod-Local-Write": "mdata-v1"})
            response = connection.getresponse()
            payload = response.read()
            connection.close()
            self.assertEqual(response.status, 200)
            value = json.loads(payload)
            self.assertEqual((value["state"], value["action"], value["result"], value["profiles"]),
                             ("done", "export", "exported", 1))
            self.assertTrue(value["accepted"])
            self.assertNotIn(fixtures.PASSWORD.encode(), payload)
            self.assertTrue(path.is_file())
            self.assertLess(path.stat().st_size, 16 * 1024)
            self.assertFalse(service.restart_requested())
            self.assertIsNone(service._worker)
            self.assertFalse(memory.writes)
            activity.assert_not_called()
            service._wait_closed.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)

    def test_single_token_refresh_retries_unpublished_snapshot(self):
        root, memory, service, activity = self.live_service("refresh-once")
        read, calls = memory.read, 0
        def refresh(name):
            nonlocal calls
            calls += 1
            if calls == 2:
                memory.data[name] = b"synthetic-refreshed-token"
            return read(name)
        path = self.base / "refreshed.mdata"
        with patch.object(memory, "read", side_effect=refresh):
            result = self.export(service, path)
        self.assertEqual(result["result"], "exported")
        manifest, auth = self.decode(path)
        import base64
        self.assertEqual(base64.b64decode(manifest["credentials"][0]["blob"]),
                         b"synthetic-refreshed-token")
        self.assertEqual(auth, {"auth_42": (root / "auth_42").read_bytes()})
        self.assertFalse(memory.writes)
        activity.assert_not_called()

    def test_continuous_refresh_stops_after_three_attempts_without_archive(self):
        root, memory, service, activity = self.live_service("continuous-refresh")
        read, calls = memory.read, 0
        def refresh(name):
            nonlocal calls
            calls += 1
            memory.data[name] = ("synthetic-refresh-" + str(calls)).encode()
            return read(name)
        path = self.base / "unstable.mdata"
        with patch.object(memory, "read", side_effect=refresh), \
                patch.object(service, "_write_archive", wraps=service._write_archive) as writer:
            result = self.export(service, path)
        self.assertEqual((result["state"], result["error"]), ("error", "profile_changed"))
        self.assertEqual(writer.call_count, 3)
        self.assertFalse(path.exists())
        self.assertFalse(list(path.parent.glob(".mdata-*.tmp")))
        activity.assert_not_called()

    def test_transient_file_sharing_violation_retries_without_closing_client(self):
        root, memory, service, activity = self.live_service("sharing-once")
        read, calls = mdata_login._read_auth, 0
        def locked(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                error = PermissionError(13, "synthetic-sharing-error")
                error.winerror = 32
                raise error
            return read(*args, **kwargs)
        with patch("mdata_login._read_auth", side_effect=locked):
            result = self.export(service, self.base / "shared.mdata")
        self.assertEqual(result["result"], "exported")
        self.assertGreater(calls, 1)
        activity.assert_not_called()

    def test_invalid_snapshot_never_retried_or_published(self):
        root, memory, service, activity = self.live_service("unsafe-snapshot")
        path = self.base / "unsafe.mdata"
        with patch("mdata_login.capture", side_effect=mdata_service.MDataError("unsafe_path")) as capture:
            result = self.export(service, path)
        self.assertEqual((result["state"], result["error"]), ("error", "unsafe_path"))
        self.assertEqual(capture.call_count, 1)
        self.assertFalse(path.exists())
        self.assertFalse(memory.reads)
        activity.assert_not_called()

    def test_export_response_is_its_own_result_when_next_job_changes_shared_status(self):
        root, memory, service, activity = self.live_service("response-race")
        underlying = service._job_lock
        class NextCallerOnRelease:
            def locked(self):
                return underlying.locked()
            def acquire(self, *args):
                return underlying.acquire(*args)
            def release(self):
                underlying.release()
                # Deterministically represent the next HTTP caller taking the
                # lock before this caller has returned to the HTTP handler.
                service._action = "import"
                service._set("waiting_for_exit")
        service._job_lock = NextCallerOnRelease()
        result = service.execute("export", (self.base / "race.mdata").as_uri(), fixtures.PASSWORD, "restore")
        self.assertEqual((result["action"], result["state"], result["result"]),
                         ("export", "done", "exported"))
        self.assertEqual((service.snapshot()["action"], service.snapshot()["state"]),
                         ("import", "waiting_for_exit"))
        self.assertFalse(service.restart_requested())
        activity.assert_not_called()


if __name__ == "__main__":
    unittest.main()
