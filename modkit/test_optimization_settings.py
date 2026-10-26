"""Synthetic preferences/startup/HTTP tests; no real profile or MAX process."""
import http.client
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from optimization_store import (ACTIVE_FILENAME, KEYS, OptimizationSettingsError,
                                OptimizationSettingsStore, canonical,
                                child_environment, defaults, parse)
from package_mod import verify_program_tree


class SettingsTests(unittest.TestCase):
    def setUp(self):
        scratch = Path("E:/ValMAXBuild/tmp/optimization-settings-tests")
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch, prefix="prefs-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "profile" / "optimization-settings.json"
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.store = OptimizationSettingsStore(self.path)

    def test_default_read_is_lazy_and_preserves_current_features(self):
        value = self.store.snapshot()
        self.assertEqual(value["settings"], {key: True for key in KEYS})
        self.assertFalse(value["restartRequired"])
        self.assertFalse(self.path.exists())

    def test_save_is_pending_until_a_new_launch_and_never_mutates_active(self):
        active_path = self.runtime / ACTIVE_FILENAME
        self.store.activate(self.runtime)
        original = active_path.read_bytes()
        initial = self.store.snapshot()
        changed = {**initial["settings"], "enabled": False, "listPrefetch": False}
        saved = self.store.replace(initial["revision"], changed)
        self.assertTrue(saved["restartRequired"])
        self.assertEqual(saved["appliedSettings"], defaults())
        self.assertEqual(active_path.read_bytes(), original)
        self.store.activate(self.runtime)
        self.assertEqual(active_path.read_bytes(), original)
        next_launch = OptimizationSettingsStore(self.path)
        next_launch.activate(self.runtime)
        self.assertEqual(parse(active_path.read_bytes()), changed)
        self.assertFalse(next_launch.snapshot()["restartRequired"])

    def test_reattaching_helper_adopts_active_and_does_not_apply_pending(self):
        self.store.activate(self.runtime)
        initial = self.store.snapshot()
        self.store.replace(initial["revision"], {**defaults(), "smoothWheel": False})
        recovered = OptimizationSettingsStore(self.path)
        recovered.activate(self.runtime, existing_client=True)
        self.assertEqual(recovered.snapshot()["appliedSettings"], defaults())
        self.assertTrue(recovered.snapshot()["restartRequired"])

    def test_existing_client_recovers_saved_disabled_flags_when_active_is_missing(self):
        active_path = self.runtime / ACTIVE_FILENAME
        changed = {**defaults(), "enabled": False, "photoDecodeResize": False}
        self.store.replace(self.store.snapshot()["revision"], changed)
        self.assertFalse(active_path.exists())
        recovered = OptimizationSettingsStore(self.path)
        self.assertEqual(recovered.activate(self.runtime, existing_client=True), changed)
        self.assertEqual(active_path.read_bytes(), canonical(changed))
        self.assertFalse(recovered.snapshot()["restartRequired"])
        self.assertEqual(recovered.snapshot()["appliedSettings"], changed)

    def test_existing_client_missing_profile_and_active_writes_defaults(self):
        active_path = self.runtime / ACTIVE_FILENAME
        self.assertEqual(self.store.activate(self.runtime, existing_client=True), defaults())
        self.assertEqual(active_path.read_bytes(), canonical(defaults()))
        self.assertFalse(self.path.exists())

    def test_existing_client_preserves_malformed_active(self):
        active_path = self.runtime / ACTIVE_FILENAME
        malformed = b'{"schema":1,"settings":{}}'
        active_path.write_bytes(malformed)
        with self.assertRaises(OptimizationSettingsError) as raised:
            self.store.activate(self.runtime, existing_client=True)
        self.assertEqual(raised.exception.code, "invalid_store")
        self.assertEqual(active_path.read_bytes(), malformed)
        self.assertFalse(self.path.exists())

    def test_existing_client_does_not_overwrite_active_created_during_recovery(self):
        if os.name != "nt":
            self.skipTest("Windows exclusive rename behavior")
        desired = {**defaults(), "enabled": False}
        competing = {**defaults(), "historyCache": False}
        self.store.replace(self.store.snapshot()["revision"], desired)
        active_path = self.runtime / ACTIVE_FILENAME
        original_rename = os.rename

        def create_competing_active(source, destination):
            self.assertEqual(Path(destination), active_path)
            active_path.write_bytes(canonical(competing))
            original_rename(source, destination)

        recovered = OptimizationSettingsStore(self.path)
        with patch("optimization_store.os.rename", side_effect=create_competing_active):
            self.assertEqual(recovered.activate(self.runtime, existing_client=True), competing)
        self.assertEqual(active_path.read_bytes(), canonical(competing))
        self.assertTrue(recovered.snapshot()["restartRequired"])

    def test_revisions_prevent_stale_overwrite(self):
        initial = self.store.snapshot()
        self.store.replace(initial["revision"], {**defaults(), "profileRows": False})
        with self.assertRaises(OptimizationSettingsError) as raised:
            self.store.replace(initial["revision"], defaults())
        self.assertEqual(raised.exception.code, "revision_conflict")
        self.assertFalse(self.store.snapshot()["settings"]["profileRows"])

    def test_exact_boolean_shape_is_required(self):
        initial = self.store.snapshot()
        for value in ({}, {**defaults(), "enabled": 1}, {**defaults(), "extra": True},
                      {key: value for key, value in defaults().items() if key != "readyCache"}):
            with self.subTest(value=value), self.assertRaises(OptimizationSettingsError) as raised:
                self.store.replace(initial["revision"], value)
            self.assertEqual(raised.exception.code, "invalid_settings")
        self.assertFalse(self.path.exists())

    def test_corrupt_store_is_preserved_and_error_is_finite(self):
        self.path.parent.mkdir()
        for data in (b'{"schema":1,"schema":1}', b'[]', b'{"schema":NaN}', b'x' * 4097):
            self.path.write_bytes(data)
            with self.subTest(data=data[:20]), self.assertRaises(OptimizationSettingsError) as raised:
                self.store.snapshot()
            self.assertEqual(raised.exception.code, "invalid_store")
            self.assertEqual(self.path.read_bytes(), data)

    def test_linked_active_file_is_rejected(self):
        source = self.root / "source.json"
        source.write_bytes(canonical(defaults()))
        os.link(source, self.runtime / ACTIVE_FILENAME)
        for existing_client in (False, True):
            with self.subTest(existing_client=existing_client), self.assertRaises(OptimizationSettingsError) as raised:
                self.store.activate(self.runtime, existing_client=existing_client)
            self.assertEqual(raised.exception.code, "unsafe_path")
        self.assertEqual(source.read_bytes(), canonical(defaults()))

    def test_active_state_is_excluded_from_package_inventory(self):
        active = self.runtime / ACTIVE_FILENAME
        active.write_bytes(canonical({**defaults(), "enabled": False}))
        verify_program_tree(self.runtime, {}, allow_optimization_state=True)
        with self.assertRaises(ValueError):
            verify_program_tree(self.runtime, {})
        active.write_bytes(b'{"secret":"OWN-SENTINEL"}')
        with self.assertRaises(OptimizationSettingsError):
            verify_program_tree(self.runtime, {}, allow_optimization_state=True)

    def test_child_environment_never_modifies_parent(self):
        original = {"PATH": "fixture", "QML_XHR_ALLOW_FILE_READ": "0"}
        result = child_environment(original)
        self.assertEqual(result["QML_XHR_ALLOW_FILE_READ"], "1")
        self.assertEqual(original["QML_XHR_ALLOW_FILE_READ"], "0")
        self.assertEqual(result["PATH"], "fixture")


class HTTPTests(unittest.TestCase):
    def setUp(self):
        from privacy_server import PORT, handler_factory
        SettingsTests.setUp(self)
        self.host_port = PORT
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(
            lambda: {"schema": 1}, optimization_settings=self.store))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, value=None, *, method="GET", extra=None, raw=None):
        headers = {"Host": f"127.0.0.1:{self.host_port}"}
        if method == "POST":
            headers.update({"Content-Type": "application/json;charset=UTF-8",
                            "X-Max-Mod-Local-Write": "optimization-settings-v1"})
            raw = json.dumps(value).encode() if raw is None else raw
        headers.update(extra or {})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, "/optimization-settings", body=raw, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def test_endpoint_returns_requested_and_active_without_writing_on_get(self):
        code, headers, body = self.request()
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertFalse(self.path.exists())
        initial = json.loads(body)
        payload = {"schema": 1, "revision": initial["revision"], "settings": {**defaults(), "enabled": False}}
        code, _, body = self.request(payload, method="POST")
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)["restartRequired"])
        self.assertEqual(json.loads(body)["appliedSettings"], defaults())

    def test_endpoint_rejects_web_writes_and_reflects_no_body(self):
        value = {"schema": 1, "revision": self.store.snapshot()["revision"], "settings": defaults()}
        for extra in ({"Origin": "null"}, {"Origin": "https://example.invalid"},
                      {"Host": "example.invalid"}, {"X-Max-Mod-Local-Write": ""},
                      {"Content-Type": "text/plain"}):
            with self.subTest(extra=extra):
                self.assertEqual(self.request(value, method="POST", extra=extra)[0], 403)
        for raw in (b'{"schema":1,"schema":1}', b'{"settings":"OWN-SENTINEL"}', b'{"schema":true}'):
            code, _, body = self.request(method="POST", raw=raw)
            self.assertEqual(code, 400)
            self.assertNotIn(b"OWN-SENTINEL", body)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
