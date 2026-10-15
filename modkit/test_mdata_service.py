"""Synthetic MData integration: temp trees, memory credentials, no MAX/WinCred."""
import base64
from contextlib import nullcontext, contextmanager
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import launch_mod
import mdata_archive
import mdata_credentials as creds
import mdata_files
import mdata_service
import session_credentials


PASSWORD = "Synthetic-MData-Password-Only-123"


class FakeMemoryCredentials:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.reads, self.writes, self.inventories = [], [], 0
        self.fail_at = None

    def owned_names(self):
        self.inventories += 1
        return tuple(sorted(self.data))

    def read(self, name):
        if not creds.allowed_name(name):
            raise session_credentials.CredentialError("invalid_payload")
        self.reads.append(name)
        return self.data.get(name)

    def _change(self, name, blob, expected):
        if not creds.allowed_name(name):
            raise session_credentials.CredentialError("invalid_payload")
        if self.data.get(name) != expected:
            raise session_credentials.CredentialError("destination_not_empty")
        if self.fail_at is not None and len(self.writes) == self.fail_at:
            self.fail_at = None  # One-shot fault permits the rollback to run.
            raise session_credentials.CredentialError("credential_io")
        self.writes.append((name, expected, blob))
        if blob is None:
            self.data.pop(name, None)
        else:
            self.data[name] = blob

    def set_owned(self, name, blob, expected):
        self._change(name, blob, expected)

    def delete_owned(self, name, expected):
        self._change(name, None, expected)


def web_name(rid, app=500, key="synthetic-secure"):
    return f"{key}-{app}_{int(rid) ^ app}@MXMOD"


@contextmanager
def synthetic_disk_capacity(base):
    """Isolate tiny owned fixtures from the host volume's changing free space.

    Production reserve calculations remain active. Capacity-specific tests can
    override this patch locally; unexpected paths outside the fixture fail.
    """
    base = Path(base).resolve()

    def capacity(directory):
        if not Path(directory).resolve().is_relative_to(base):
            raise AssertionError("Capacity read outside the synthetic fixture")
        return SimpleNamespace(total=64 * 1024**3, used=32 * 1024**3, free=32 * 1024**3)

    with patch("mdata_service.shutil.disk_usage", side_effect=capacity) as mocked:
        yield mocked


class MDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mdata-synthetic-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # Use a pure lock fixture: never create the real Windows named mutex.
        self.addCleanup(patch.stopall)
        patch("mdata_service.transfer_lock", side_effect=lambda: nullcontext()).start()
        patch("session_credentials._api", side_effect=AssertionError("Real WinCred forbidden")).start()
        self.enterContext(synthetic_disk_capacity(self.base))

    def profile(self, label, ids, *, web=True, unsafe_config=False):
        root = self.base / label / "MXMOD"
        root.mkdir(parents=True)
        config = launch_mod.make_config()
        if unsafe_config:
            config.update(app_folder_name="ONEME", auto_update_enabled=True,
                          single_instance_id="official-synthetic")
        (root / "config.json").write_text(json.dumps(config), encoding="utf8")
        values = {name: (label + ":" + name).encode("ascii") for name in creds.COMMON_NAMES}
        for rid in ids:
            metadata = {"first_name": "Synthetic", "last_name": label,
                        "short_name": "S", "bio": "fixture", "photo_url": "",
                        "photo_id": 0, "options": 0}
            (root / ("auth_" + rid)).write_text(json.dumps(metadata), encoding="utf8")
            for name in creds.ACCOUNT_KEYS:
                target = name + "_" + rid + "@MXMOD"
                values[target] = (label + ":" + name + ":" + rid).encode("ascii") + b"\x00"
            for relative in ["history/fake-id.db", "history/fake-id.db-wal", "settings",
                             "files/image.bin", "video/clip.bin", "webapps/500/data"]:
                path = root / ("user" + rid) / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((label + ":" + rid + ":" + relative).encode("ascii"))
            if web:
                values[web_name(rid)] = (label + ":synthetic-web:" + rid).encode("ascii")
        for relative in ["app/common-id.db", "settings/application", "cache/ordinary.bin"]:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((label + ":" + relative).encode("ascii"))
        return root, FakeMemoryCredentials(values)

    def service(self, root, credentials, activity=None, wait_seconds=3, restart_activity=None):
        return mdata_service.MDataService(root, activity or (lambda: (0, True)),
                                          credentials=credentials, wait_seconds=wait_seconds,
                                          settle_seconds=0, restart_activity=restart_activity)

    def job(self, service, action, path, *, password=PASSWORD, mode="restore", error=None):
        accepted = service.execute(action, path.as_uri(), password, mode)
        self.assertTrue(accepted["accepted"])
        if action == "export":
            self.assertIsNone(service._worker, "Compact export must not create a lifecycle worker")
            self.assertEqual(accepted["state"], "error" if error else "done", accepted)
            self.assertFalse(service.restart_requested())
        else:
            service._worker.join(10)
            self.assertFalse(service._worker.is_alive(), "Synthetic worker did not finish")
        result = service.snapshot()
        self.assertEqual(result["state"], "error" if error else "done", result)
        if isinstance(error, (set, frozenset)):
            self.assertIn(result["error"], error, result)
        else:
            self.assertEqual(result["error"], error, result)
        return result

    def archive(self, ids=("42",), label="source", **kwargs):
        # Historical full-profile container fixture for compatibility/recovery.
        # Public exports now use the separate login-only contract below.
        root, memory = self.profile(label, ids, **kwargs)
        path = self.base / (label + ".mdata")
        self.service(root, memory)._write_archive(path, PASSWORD, login_only=False)
        return path, root, memory

    def custom_archive(self, label, file_values, credential_values):
        manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                    "files": [{"path": name, "size": len(data)} for name, data in file_values.items()],
                    "credentials": [{"name": name, "blob": base64.b64encode(blob).decode("ascii")}
                                    for name, blob in credential_values.items()]}
        path = self.base / (label + ".mdata")
        with path.open("wb") as output:
            mdata_archive.encrypt_stream(manifest, PASSWORD, lambda name: io.BytesIO(file_values[name]),
                                         output, policy=mdata_service._policy())
        return path

    @staticmethod
    def file_values(root):
        return {item["path"]: (root / item["path"]).read_bytes()
                for item in mdata_files.listing(root)}

    @staticmethod
    def decode(path):
        restored = {}
        @contextmanager
        def sink(relative, size):
            output = io.BytesIO()
            yield output
            restored[relative] = output.getvalue()
        with path.open("rb") as source:
            value = mdata_archive.decrypt_stream(source, PASSWORD, sink,
                                                 policy=mdata_service._policy())
        return value, restored

    def test_constructor_and_status_never_read_or_inventory_auth(self):
        root, memory = self.profile("idle", ["42"])
        before = self.file_values(root)
        service = self.service(root, memory)
        self.assertEqual(service.snapshot()["state"], "idle")
        self.assertFalse(service.pending())
        self.assertFalse(service.restart_requested())
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))
        self.assertEqual(before, self.file_values(root))

    def test_done_export_stays_pending_through_persist_without_restart(self):
        from privacy_server import IdleDeadline
        root, memory = self.profile("pending-finalization", ["42"])
        service = self.service(root, memory)
        persist_entered, persist_allowed = threading.Event(), threading.Event()
        release_restart_states = []
        underlying_lock = service._job_lock

        class ObserveRelease:
            def acquire(self, *args, **kwargs):
                return underlying_lock.acquire(*args, **kwargs)

            def locked(self):
                return underlying_lock.locked()

            def release(self):
                release_restart_states.append(service.restart_requested())
                underlying_lock.release()

        service._job_lock = ObserveRelease()
        original_persist = service._persist

        def delayed_persist():
            persist_entered.set()
            if not persist_allowed.wait(5):
                raise RuntimeError("Synthetic persistence barrier timeout")
            original_persist()

        path = self.base / "pending-finalization.mdata"
        responses, failures = [], []
        def execute_export():
            try:
                responses.append(service.execute("export", path.as_uri(), PASSWORD, "restore"))
            except Exception as error:
                failures.append(error)
        caller = threading.Thread(target=execute_export)
        with patch.object(service, "_persist", delayed_persist):
            caller.start()
            try:
                self.assertTrue(persist_entered.wait(3), "Synchronous caller did not reach persistence")
                self.assertTrue(caller.is_alive())
                self.assertEqual(responses, [], "Export returns only after finalization")
                self.assertIsNone(service._worker)
                self.assertEqual(service.snapshot()["state"], "done")
                self.assertTrue(service._job_lock.locked())
                self.assertTrue(service.pending(), "Done is still pending while finalizing")
                self.assertFalse(service.restart_requested())

                # The actual server's idle condition must keep its HTTP loop
                # alive even after the nominal 20-second idle deadline.
                idle = IdleDeadline(20)
                self.assertFalse(idle.expired(0, True, 0))
                self.assertTrue(idle.expired(0, True, 21))
                should_stop_idle = idle.expired(0, True, 21) and not service.pending()
                self.assertFalse(should_stop_idle)
                with self.assertRaises(mdata_service.MDataError) as raised:
                    service.execute("export", (self.base / "too-early.mdata").as_uri(), PASSWORD, "restore")
                self.assertEqual(raised.exception.code, "busy")
            finally:
                persist_allowed.set()
                caller.join(10)

        self.assertFalse(caller.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0]["accepted"])
        self.assertEqual(responses[0]["state"], "done")
        self.assertFalse(service._job_lock.locked())
        self.assertFalse(service.pending())
        self.assertFalse(service.restart_requested())
        self.assertEqual(release_restart_states, [False])
        self.assertEqual(json.loads((root / "mdata-result.json").read_bytes())["result"], "exported")
        self.assertTrue(path.is_file())

    def test_public_export_contains_only_native_login_token_and_auth_metadata(self):
        root, memory = self.profile("login-source", ["42"])
        path = self.base / "login-source.mdata"
        self.job(self.service(root, memory), "export", path)
        value, restored = self.decode(path)
        exported = {item["name"]: base64.b64decode(item["blob"], validate=True)
                    for item in value["credentials"]}
        self.assertEqual(exported, {"access_token_42@MXMOD": memory.data["access_token_42@MXMOD"]})
        self.assertEqual(restored, {"auth_42": (root / "auth_42").read_bytes()})
        self.assertEqual(path.read_bytes()[:len(mdata_archive.MAGIC)], mdata_archive.LOGIN_MAGIC)
        self.assertLess(path.stat().st_size, 1024 * 1024)
        self.assertEqual(memory.inventories, 0)
        self.assertEqual(memory.writes, [])
        raw = path.read_bytes()
        for secret in memory.data.values():
            self.assertNotIn(secret, raw)
        self.assertTrue(all(name == "access_token_42@MXMOD" for name in memory.reads))

    def test_failed_source_records_safe_phase_and_numeric_error_without_secrets(self):
        from mdata_diagnostics import validate
        root, memory = self.profile("diagnostic", ["42"])
        service = self.service(root, memory)
        secret = "SYNTHETIC-PRIVATE-ERROR-MESSAGE-DO-NOT-STORE"
        def denied(*args):
            error = PermissionError(13, secret)
            error.winerror = 32
            raise error
        with patch("mdata_login._read_auth", denied):
            self.job(service, "export", self.base / "failed.mdata", error="io_error")
        raw = (root / "mdata-result.json").read_bytes()
        diagnostic = json.loads(raw)["diagnostic"]
        self.assertTrue(validate(diagnostic))
        self.assertEqual((diagnostic["phase"], diagnostic["errno"], diagnostic["winerror"]),
                         ("credential_read", 13, 32))
        self.assertLessEqual(len(raw), 2048)
        for value in (secret, PASSWORD, *[blob.decode("ascii") for blob in memory.data.values()]):
            self.assertNotIn(value.encode("ascii"), raw)
        fresh_memory = FakeMemoryCredentials()
        loaded = self.service(root, fresh_memory).snapshot()
        self.assertEqual((loaded["state"], loaded["error"]), ("error", "io_error"))
        self.assertEqual((fresh_memory.reads, fresh_memory.writes, fresh_memory.inventories), ([], [], 0))

    def test_diagnostic_failure_cannot_replace_job_error_or_hold_job_lock(self):
        root, memory = self.profile("diagnostic-fallback", ["42"])
        service = self.service(root, memory)
        with patch.object(service, "_write_archive", side_effect=OSError("synthetic-only")), \
                patch("mdata_diagnostics.failure", side_effect=RuntimeError("synthetic diagnostic failure")):
            self.job(service, "export", self.base / "failed.mdata", error="io_error")
        self.assertFalse(service.restart_requested())
        self.assertNotIn("diagnostic", json.loads((root / "mdata-result.json").read_bytes()))
        self.job(service, "export", self.base / "retry.mdata")

    def test_persistence_failure_preserves_primary_error_releases_lock_and_allows_retry(self):
        from session_transfer import TransferError
        root, memory = self.profile("persist-primary", ["42"])
        service = self.service(root, memory)
        with patch.object(service, "_write_archive", side_effect=mdata_service.MDataError("profile_changed")), \
                patch.object(service, "_persist", side_effect=TransferError("rollback_failed")):
            self.job(service, "export", self.base / "failed.mdata", error="profile_changed")
        self.assertFalse(service._job_lock.locked())
        self.assertFalse(service.restart_requested())
        self.job(service, "export", self.base / "retry-after-persist.mdata")

    def test_persistence_failure_cannot_replace_successful_export(self):
        from session_transfer import TransferError
        root, memory = self.profile("persist-success", ["42"])
        service = self.service(root, memory)
        output = self.base / "persist-success.mdata"
        with patch.object(service, "_persist", side_effect=TransferError("unsafe_path")):
            self.job(service, "export", output)
        self.assertTrue(output.is_file())
        self.assertEqual(service.snapshot()["result"], "exported")
        self.assertFalse(service.restart_requested())
        self.assertFalse(service._job_lock.locked())

    def test_hostile_exception_properties_use_fixed_fallback_without_formatting(self):
        secret = "SYNTHETIC-PRIVATE-HOSTILE-ERROR"
        class HostileError(Exception):
            formatted = False
            @property
            def code(self):
                raise RuntimeError(secret)
            def __str__(self):
                type(self).formatted = True
                raise AssertionError(secret)
        root, memory = self.profile("hostile-error", ["42"])
        service = self.service(root, memory)
        def failed(*args):
            raise HostileError(secret)
        with patch.object(service, "_write_archive", failed):
            self.job(service, "export", self.base / "hostile.mdata", error="io_error")
        self.assertFalse(HostileError.formatted)
        self.assertFalse(service._job_lock.locked())
        self.assertNotIn(secret.encode("ascii"), (root / "mdata-result.json").read_bytes())

    def test_cleanup_failure_preserves_primary_error_and_warning_is_reset_on_retry(self):
        from mdata_diagnostics import validate
        path, _, _ = self.archive()
        root, memory = self.profile("cleanup-primary", ["7"])
        service = self.service(root, memory)
        before = dict(memory.data)
        with patch.object(mdata_files, "remove_staging", side_effect=PermissionError(13, "SYNTHETIC-CLEANUP-SECRET")):
            self.job(service, "import", path, password="Wrong-Synthetic-Password-123", error="authentication_failed")
        self.assertEqual(memory.data, before)
        saved = json.loads((root / "mdata-result.json").read_bytes())
        self.assertTrue(validate(saved["diagnostic"]))
        self.assertTrue(validate(saved["cleanupDiagnostic"]))
        self.assertEqual(saved["diagnostic"]["phase"], "archive_decrypt")
        self.assertEqual(saved["cleanupDiagnostic"]["phase"], "cleanup")
        self.assertEqual((saved["cleanupDiagnostic"]["rootError"], saved["cleanupDiagnostic"]["errno"]),
                         ("PermissionError", 13))
        self.assertNotIn("cleanupDiagnostic", service.snapshot())
        self.assertNotIn("SYNTHETIC-CLEANUP-SECRET", json.dumps(saved))
        self.job(service, "export", self.base / "cleanup-retry.mdata")
        self.assertNotIn("cleanupDiagnostic", json.loads((root / "mdata-result.json").read_bytes()))

    def test_cleanup_failure_cannot_replace_committed_add(self):
        from mdata_diagnostics import validate
        from mdata_recovery_state import read
        path, _, _ = self.archive()
        root, memory = self.profile("cleanup-success", ["7"])
        service = self.service(root, memory)
        with patch.object(mdata_files, "remove_staging", side_effect=OSError("SYNTHETIC-CLEANUP-SECRET")):
            result = self.job(service, "import", path, mode="add")
        self.assertEqual((result["result"], result["profiles"]), ("added", 1))
        self.assertIn("access_token_42@MXMOD", memory.data)
        self.assertTrue((root / "auth_42").is_file())
        self.assertIsNone(read(root))
        self.assertTrue(service.restart_requested())
        self.assertFalse(service._job_lock.locked())
        saved = json.loads((root / "mdata-result.json").read_bytes())
        self.assertTrue(validate(saved["cleanupDiagnostic"]))
        self.assertNotIn("diagnostic", saved)

    def test_failed_import_does_not_reuse_count_from_previous_export(self):
        path, _, _ = self.archive(ids=("42", "43"), label="count-incoming")
        root, memory = self.profile("count-current", ["7"])
        service = self.service(root, memory)
        exported = self.job(service, "export", self.base / "count-export.mdata")
        self.assertEqual(exported["profiles"], 1)
        before = dict(memory.data)
        result = self.job(service, "import", path,
                          password="Wrong-Synthetic-Password-123", error="authentication_failed")
        self.assertEqual(result["profiles"], 0)
        self.assertIsNone(result["result"])
        self.assertEqual(memory.data, before)
        reloaded = self.service(root, memory).snapshot()
        self.assertEqual((reloaded["state"], reloaded["profiles"]), ("error", 0))

    def test_two_bounded_diagnostics_fit_saved_result_and_invalid_records_are_discarded(self):
        import mdata_diagnostics as diagnostic
        root, memory = self.profile("diagnostic-bounds", ["42"])
        service = self.service(root, memory)
        maximum = {"schema": 1, "timestamp": "2026-10-13T00:00:00Z",
                   "phase": max(diagnostic.PHASES, key=len), "operationId": "1" * 32,
                   "rootError": max(diagnostic.ROOT_ERRORS, key=len), "errno": 65535, "winerror": 65535,
                   "frames": [{"module": max(diagnostic.TRUSTED_MODULES, key=len),
                               "function": "f" * 64, "line": 1000000} for _ in range(4)]}
        self.assertTrue(diagnostic.validate(maximum))
        service._action = "export"
        service._set("error", error=max(mdata_service._CODES, key=len), profiles=3)
        service._diagnostic = maximum
        service._cleanup_diagnostic = maximum
        service._persist()
        raw = (root / "mdata-result.json").read_bytes()
        self.assertLessEqual(len(raw), 2048)
        saved = json.loads(raw)
        self.assertEqual(saved["diagnostic"], maximum)
        self.assertEqual(saved["cleanupDiagnostic"], maximum)
        service._diagnostic = {"secret": "SYNTHETIC-PRIVATE"}
        service._cleanup_diagnostic = {"secret": "SYNTHETIC-PRIVATE"}
        service._persist()
        raw = (root / "mdata-result.json").read_bytes()
        self.assertNotIn(b"SYNTHETIC-PRIVATE", raw)
        self.assertEqual(json.loads(raw)["error"], saved["error"])

    def test_restore_existing_two_accounts_backups_then_sanitizes_runtime_config(self):
        path, source, incoming = self.archive(unsafe_config=True)
        root, current = self.profile("destination", ["7", "8"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        result = self.job(self.service(root, current), "import", path)
        self.assertEqual((result["result"], result["profiles"], result["backupAvailable"]),
                         ("restored", 1, True))
        self.assertEqual(current.data, incoming.data)
        expected = self.file_values(source)
        expected.pop("config.json")
        actual = self.file_values(root)
        config = json.loads(actual.pop("config.json"))
        self.assertEqual(actual, expected)
        self.assertEqual(config, launch_mod.make_config())
        launch_mod.validate_config(json.dumps(config).encode("utf8"))
        backups = list((root.parent / "MXMOD-MData-Backups").glob("*.mdata"))
        self.assertEqual(len(backups), 1)
        backup, files = self.decode(backups[0])
        self.assertEqual(files, before_files)
        self.assertEqual({item["name"]: base64.b64decode(item["blob"])
                          for item in backup["credentials"]}, before_creds)
        from mdata_recovery_state import ensure_clear
        ensure_clear(root)

    def test_add_preserves_existing_common_global_and_adds_new_account_history(self):
        path, source, incoming = self.archive()
        root, current = self.profile("destination", ["7", "8"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        result = self.job(self.service(root, current), "import", path, mode="add")
        self.assertEqual((result["result"], result["profiles"]), ("added", 1))
        actual = self.file_values(root)
        for relative, data in before_files.items():
            self.assertEqual(actual[relative], data)
        for name, blob in before_creds.items():
            self.assertEqual(current.data[name], blob)
        for name, blob in incoming.data.items():
            if creds.owner_id(name) == "42":
                self.assertEqual(current.data[name], blob)
        for relative, data in self.file_values(source).items():
            if relative == "auth_42" or relative.startswith("user42/"):
                self.assertEqual(actual[relative], data)
        self.assertEqual(current.data["app_data_storage_key_Common@MXMOD"],
                         before_creds["app_data_storage_key_Common@MXMOD"])
        self.assertEqual(actual["app/common-id.db"], before_files["app/common-id.db"])

    def test_duplicate_add_is_noop_without_backup_or_credential_writes(self):
        path, _, _ = self.archive()
        root, current = self.profile("destination", ["42"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        result = self.job(self.service(root, current), "import", path, mode="add")
        self.assertEqual((result["result"], result["profiles"], result["backupAvailable"]),
                         ("already_present", 0, False))
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(current.data, before_creds)
        self.assertEqual(current.writes, [])

    def test_add_refuses_fourth_account_without_changing_destination(self):
        path, _, _ = self.archive()
        root, current = self.profile("destination", ["7", "8", "9"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        self.job(self.service(root, current), "import", path, mode="add", error="account_limit")
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(current.data, before_creds)
        self.assertEqual(current.writes, [])

    def test_wrong_password_and_terminal_tamper_do_not_read_or_write_credentials(self):
        path, _, _ = self.archive()
        root, current = self.profile("destination", ["7"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        self.job(self.service(root, current), "import", path,
                 password="Wrong-Synthetic-Password-123", error="authentication_failed")
        tampered = self.base / "tampered.mdata"
        damaged = bytearray(path.read_bytes())
        damaged[-1] ^= 1
        tampered.write_bytes(damaged)
        self.job(self.service(root, current), "import", tampered, error="authentication_failed")
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(current.data, before_creds)
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))

    def test_running_client_blocks_import_until_closed(self):
        path, _, _ = self.archive(label="waiting-input")
        original_archive = path.read_bytes()
        root, current = self.profile("waiting", ["7"])
        live = threading.Event()
        live.set()
        service = self.service(root, current, lambda: (int(live.is_set()), True))
        accepted = service.execute("import", path.as_uri(), PASSWORD, "restore")
        self.assertTrue(accepted["accepted"])
        try:
            time.sleep(.12)
            self.assertEqual(service.snapshot()["state"], "waiting_for_exit")
            self.assertEqual((current.reads, current.inventories), ([], 0))
            self.assertEqual(path.read_bytes(), original_archive)
        finally:
            live.clear()
            service._worker.join(10)
        self.assertFalse(service._worker.is_alive())
        self.assertEqual(service.snapshot()["result"], "restored")
        self.assertTrue(service.restart_requested())

    def test_client_timeout_does_not_enter_import(self):
        path, _, _ = self.archive(label="timeout-input")
        original_archive = path.read_bytes()
        root, current = self.profile("timeout", ["42"])
        service = self.service(root, current, lambda: (1, True), wait_seconds=.1)
        with patch.object(service, "_import", side_effect=AssertionError("Import entered")) as restore:
            self.job(service, "import", path, error="client_still_running")
        restore.assert_not_called()
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))
        self.assertEqual(path.read_bytes(), original_archive)
        self.assertFalse(service.restart_requested(), "Default callers keep the prior closed-client requirement")

    def test_wait_timeout_returns_ui_only_after_complete_all_main_scan(self):
        path, _, _ = self.archive(label="return-ui-input")
        original_archive = path.read_bytes()
        outcomes = [(0, True), (1, True), (0, False), (False, True),
                    (0, 1), RuntimeError("synthetic scan failure")]
        for index, outcome in enumerate(outcomes):
            with self.subTest(outcome=index):
                root, current = self.profile("return-ui-" + str(index), ["42"])
                before_files, before_keys = self.file_values(root), dict(current.data)
                scan = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
                service = self.service(root, current, lambda: (1, True), wait_seconds=.01,
                                       restart_activity=scan)
                with patch.object(service, "_write_archive", side_effect=AssertionError("Archive entered")) as export, \
                        patch.object(service, "_import", side_effect=AssertionError("Import entered")) as restore:
                    self.job(service, "import", path, error="client_still_running")
                self.assertEqual(service.restart_requested(), index == 0)
                export.assert_not_called()
                restore.assert_not_called()
                scan.assert_called_once_with()
                self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))
                self.assertEqual(self.file_values(root), before_files)
                self.assertEqual(current.data, before_keys)
                self.assertEqual(path.read_bytes(), original_archive)
                self.assertFalse(service.pending())
                self.assertEqual(json.loads((root / "mdata-result.json").read_bytes())["error"], "client_still_running")

    def test_wait_timeout_rechecks_main_absence_before_restart(self):
        path, _, _ = self.archive(label="return-ui-race-input")
        root, current = self.profile("return-ui-race", ["42"])
        scan = Mock(side_effect=[(0, True), (1, True)])
        service = self.service(root, current, lambda: (1, True), wait_seconds=.01,
                               restart_activity=scan)
        self.job(service, "import", path, error="client_still_running")
        self.assertTrue(service.restart_requested())
        self.assertFalse(service.restart_requested(recheck=True), "A newly appeared main must prevent a duplicate")
        self.assertEqual(scan.call_count, 2)
        self.assertEqual(service.snapshot()["error"], "client_still_running")

    def test_wait_timeout_recovery_guard_failure_forbids_ui_restart(self):
        from mdata_recovery_state import RecoveryRequired
        path, _, _ = self.archive(label="return-ui-recovery-input")
        root, current = self.profile("return-ui-recovery", ["42"])
        scan = Mock(return_value=(0, True))
        service = self.service(root, current, lambda: (1, True), wait_seconds=.01,
                               restart_activity=scan)
        # Request validation can succeed, then a recovery latch appears before
        # the failed wait is finalized. Never open a half-restored profile.
        with patch("mdata_recovery_state.ensure_clear", side_effect=[None, RecoveryRequired()]):
            self.job(service, "import", path, error="client_still_running")
        self.assertFalse(service.restart_requested())
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))
        self.assertFalse(service.pending())

    def test_wait_timeout_rechecks_recovery_guard_and_keeps_lock_through_decision(self):
        from mdata_recovery_state import RecoveryRequired
        path, _, _ = self.archive(label="return-ui-finalization-input")
        root, current = self.profile("return-ui-finalization", ["42"])
        observed_pending = []
        service = None
        def scan():
            observed_pending.append(service.pending())
            return 0, True
        service = self.service(root, current, lambda: (1, True), wait_seconds=.01,
                               restart_activity=scan)
        self.job(service, "import", path, error="client_still_running")
        self.assertEqual(observed_pending, [True])
        with patch("mdata_recovery_state.ensure_clear", side_effect=RecoveryRequired()):
            self.assertFalse(service.restart_requested(recheck=True))
        self.assertEqual(observed_pending, [True, False])

    def test_other_wait_error_does_not_enable_return_ui_fallback(self):
        path, _, _ = self.archive(label="return-ui-other-error-input")
        root, current = self.profile("return-ui-other-error", ["42"])
        scan = Mock(return_value=(0, True))
        service = self.service(root, current, restart_activity=scan)
        def fail_after_ack():
            time.sleep(.05)
            raise mdata_service.MDataError("io_error")
        with patch.object(service, "_wait_closed", side_effect=fail_after_ack):
            self.job(service, "import", path, error="io_error")
        self.assertFalse(service.restart_requested())
        scan.assert_not_called()
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))

    def test_success_and_post_wait_error_keep_existing_restart_path(self):
        path, _, _ = self.archive(label="return-ui-success-input")
        root, current = self.profile("return-ui-success", ["42"])
        scan = Mock(side_effect=AssertionError("Successful-path policy changed"))
        service = self.service(root, current, restart_activity=scan)
        self.job(service, "import", path, mode="add")
        self.assertTrue(service.restart_requested(recheck=True))
        with patch.object(service, "_import", side_effect=mdata_service.MDataError("client_still_running")):
            self.job(service, "import", path, error="client_still_running")
        self.assertTrue(service.restart_requested(recheck=True))
        scan.assert_not_called()

    def test_full_restore_credential_failure_rolls_back_prior_writes(self):
        path, _, _ = self.archive()
        root, current = self.profile("destination", ["7", "8"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        current.fail_at = 2
        self.job(self.service(root, current), "import", path, error="credential_io")
        self.assertEqual(current.data, before_creds)
        self.assertEqual(self.file_values(root), before_files)
        self.assertGreaterEqual(len(current.writes), 4)

    def test_add_file_publish_failure_rolls_back_credentials_and_files(self):
        path, _, _ = self.archive()
        root, current = self.profile("destination", ["7"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        real_rename = Path.rename
        fired = [False]
        def rename(path_object, target):
            if (not fired[0] and path_object.name == "fake-id.db"
                    and ".MXMOD-MData-stage-" in str(path_object)
                    and Path(target).is_relative_to(root)):
                fired[0] = True
                raise OSError("Synthetic publish failure")
            return real_rename(path_object, target)
        with patch.object(Path, "rename", rename):
            self.job(self.service(root, current), "import", path, mode="add", error="io_error")
        self.assertTrue(fired[0])
        self.assertEqual(current.data, before_creds)
        self.assertEqual(self.file_values(root), before_files)

    def test_authenticated_foreign_namespace_archive_rejected_before_native_io(self):
        name = "access_token_42@ONEME"
        manifest = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                    "files": [], "credentials": [{"name": name,
                     "blob": base64.b64encode(b"SYNTHETIC-FOREIGN-ONLY").decode("ascii")}]}
        from session_archive import ArchivePolicy
        policy = mdata_archive.MDataPolicy(ArchivePolicy(credential_names=(name,)), lambda _: True)
        path = self.base / "foreign.mdata"
        with path.open("wb") as output:
            mdata_archive.encrypt_stream(manifest, PASSWORD, lambda _: io.BytesIO(), output, policy=policy)
        root, current = self.profile("destination", ["7"])
        before_files, before_creds = self.file_values(root), dict(current.data)
        self.job(self.service(root, current), "import", path, error="policy_violation")
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))
        self.assertEqual(current.data, before_creds)
        self.assertEqual(self.file_values(root), before_files)

    def test_secure_key_named_access_token_does_not_count_as_native_authorization(self):
        # A mini-app is permitted to call its arbitrary key 'access_token'.
        # That must not turn its XOR owner into an authenticated native account.
        path = self.custom_archive("web-only", {}, {web_name("42", key="access_token"): b"FAKE-WEB"})
        root, current = self.profile("destination", ["7"])
        before = dict(current.data)
        self.assertEqual(mdata_service._active({web_name("42", key="access_token"): b"FAKE-WEB"}), set())
        self.job(self.service(root, current), "import", path, mode="add",
                 error={"no_accounts", "invalid_payload"})
        self.assertEqual(current.data, before)
        self.assertEqual(current.writes, [])

    def test_history_database_requires_matching_per_account_key_before_native_io(self):
        path = self.custom_archive("missing-history-key", {"user42/history/example.db": b"FAKE-DB"},
                                   {"access_token_42@MXMOD": b"FAKE-TOKEN"})
        root, current = self.profile("destination", ["7"])
        before = self.file_values(root)
        self.job(self.service(root, current), "import", path, mode="add",
                 error={"invalid_payload", "invalid_profile", "policy_violation"})
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))
        self.assertEqual(self.file_values(root), before)

    def test_shared_database_requires_common_key_before_native_io(self):
        path = self.custom_archive("missing-common-key", {"app/example.db": b"FAKE-COMMON-DB"},
                                   {"access_token_42@MXMOD": b"FAKE-TOKEN"})
        root, current = self.profile("destination", ["7"])
        self.job(self.service(root, current), "import", path,
                 error={"invalid_payload", "invalid_profile", "policy_violation"})
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))

    def test_web_xor_owner_must_belong_to_source_profile(self):
        path = self.custom_archive("foreign-web-owner", {},
                                   {"access_token_42@MXMOD": b"FAKE-TOKEN",
                                    web_name("99"): b"FAKE-ORPHAN-WEB"})
        root, current = self.profile("destination", ["7"])
        self.job(self.service(root, current), "import", path,
                 error={"invalid_payload", "invalid_profile", "policy_violation"})
        self.assertEqual((current.reads, current.writes, current.inventories), ([], [], 0))

    def test_add_refuses_stale_account_files_without_token_before_writes(self):
        path, _, _ = self.archive()
        for stale_kind in ("auth", "user"):
            with self.subTest(stale_kind=stale_kind):
                root, current = self.profile("collision-" + stale_kind, ["7"])
                if stale_kind == "auth":
                    (root / "auth_42").write_bytes(b'{"first_name":"EXISTING-STALE-FIXTURE"}')
                else:
                    stale = root / "user42" / "history" / "stale.db"
                    stale.parent.mkdir(parents=True)
                    stale.write_bytes(b"EXISTING-STALE-FIXTURE")
                    current.data["history_storage_key_42@MXMOD"] = b"EXISTING-STALE-HISTORY-KEY"
                before_files, before_creds = self.file_values(root), dict(current.data)
                self.job(self.service(root, current), "import", path, mode="add", error="profile_collision")
                self.assertEqual(current.writes, [])
                self.assertEqual(current.data, before_creds)
                self.assertEqual(self.file_values(root), before_files)

    def test_failed_rollback_preserves_staging_and_forbids_restart(self):
        path, _, _ = self.archive()
        root, current = self.profile("destination", ["7", "8"])
        current.fail_at = 2
        original = current.set_owned
        def fail_rollback(name, blob, expected):
            if name == "access_token_7@MXMOD" and expected is None:
                raise session_credentials.CredentialError("credential_io")
            return original(name, blob, expected)
        current.set_owned = fail_rollback
        service = self.service(root, current)
        result = self.job(service, "import", path, error="rollback_failed")
        self.assertFalse(service.restart_requested())
        self.assertTrue(result["backupAvailable"])
        stages = list(root.parent.glob(".MXMOD-MData-stage-*"))
        self.assertTrue(stages, "Failed rollback must retain recovery staging")
        self.assertTrue(any((stage / "auth_42").is_file() for stage in stages))
        from mdata_recovery_state import read, ensure_clear, RecoveryRequired
        latch = read(root)
        self.assertEqual(latch["profileRoot"], str(root))
        self.assertTrue(Path(latch["backupFile"]).is_file())
        with self.assertRaises(RecoveryRequired):
            ensure_clear(root)


    def test_insufficient_stage_space_opens_zero_plaintext_sinks_and_preserves_current(self):
        archive, _, _ = self.archive()
        root, memory = self.profile("space-current", ["7"])
        before_files, before_keys = self.file_values(root), dict(memory.data)
        with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=0)), \
                patch("mdata_files.sink", side_effect=AssertionError("Plaintext sink opened")) as sink:
            self.job(self.service(root, memory), "import", archive, error="insufficient_space")
        sink.assert_not_called()
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(memory.data, before_keys)
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))
        self.assertFalse(list(root.parent.glob(".MXMOD-MData-stage-*")))

    def test_staging_preflight_also_reserves_complete_current_backup(self):
        archive, _, _ = self.archive()
        manifest, _ = self.decode(archive)
        root, memory = self.profile("space-backup-budget", ["7"])
        stage_bytes = sum(item["size"] for item in manifest["files"]) + len(manifest["files"]) * mdata_service.STAGING_FILE_OVERHEAD
        backup_bytes = mdata_service._archive_space_bound(sum(item["size"] for item in mdata_files.listing(root)))
        # Staging alone fits; staging plus the future complete backup does not.
        free = mdata_service.SPACE_RESERVE + stage_bytes + backup_bytes - 1
        self.assertGreater(free, mdata_service.SPACE_RESERVE + stage_bytes)
        before_files, before_keys = self.file_values(root), dict(memory.data)
        with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=free)), \
                patch("mdata_files.sink", side_effect=AssertionError("Plaintext sink opened")) as sink:
            self.job(self.service(root, memory), "import", archive, error="insufficient_space")
        sink.assert_not_called()
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(memory.data, before_keys)
        self.assertFalse(memory.writes)

    def test_insufficient_export_space_does_not_open_or_publish_archive(self):
        root, memory = self.profile("space-export", ["42"])
        output = self.base / "space-export.mdata"
        before_files, before_keys = self.file_values(root), dict(memory.data)
        service = self.service(root, memory)
        with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=0)) as capacity, \
                patch("mdata_files.atomic_output", side_effect=AssertionError("Archive output opened")) as publish:
            with self.assertRaises(mdata_service.MDataError) as caught:
                service.execute("export", output.as_uri(), PASSWORD, "restore")
        self.assertEqual(caught.exception.code, "insufficient_space")
        self.assertIsNone(service._worker)
        self.assertFalse(service.pending())
        publish.assert_not_called()
        self.assertEqual(Path(capacity.call_args.args[0]), root)
        self.assertFalse(output.exists())
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(memory.data, before_keys)
        self.assertEqual((memory.reads, memory.inventories), ([], 0))
        self.assertFalse(memory.writes)

    def test_wrong_password_wins_before_disk_capacity_read(self):
        archive, _, _ = self.archive()
        root, memory = self.profile("space-wrong-password", ["7"])
        with patch("mdata_service.shutil.disk_usage", side_effect=AssertionError("Space checked before authentication")) as capacity, \
                patch("mdata_files.sink", side_effect=AssertionError("Plaintext sink opened")) as sink:
            self.job(self.service(root, memory), "import", archive,
                     password="wrong-synthetic-password", error="authentication_failed")
        capacity.assert_not_called()
        sink.assert_not_called()
        self.assertFalse(memory.writes)

    def test_backup_capacity_is_rechecked_after_staging_before_any_profile_write(self):
        archive, _, _ = self.archive()
        root, memory = self.profile("space-late-backup", ["7"])
        before_files, before_keys = self.file_values(root), dict(memory.data)
        capacities = [SimpleNamespace(free=10 * 1024**3), SimpleNamespace(free=0)]
        with patch("mdata_service.shutil.disk_usage", side_effect=capacities) as capacity, \
                patch("mdata_files.atomic_output", side_effect=AssertionError("Backup opened before capacity guard")) as publish:
            self.job(self.service(root, memory), "import", archive, error="insufficient_space")
        self.assertEqual(capacity.call_count, 2)
        publish.assert_not_called()
        self.assertEqual(self.file_values(root), before_files)
        self.assertEqual(memory.data, before_keys)
        self.assertFalse(memory.writes)
        self.assertFalse(list(root.parent.glob(".MXMOD-MData-stage-*")))

    def test_capacity_boundary_and_unavailable_capacity_fail_closed(self):
        needed = 123456
        with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=needed + mdata_service.SPACE_RESERVE)):
            mdata_service._require_space(self.base, needed)
        for free in (needed + mdata_service.SPACE_RESERVE - 1, 0):
            with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=free)), \
                    self.assertRaises(mdata_service.MDataError) as caught:
                mdata_service._require_space(self.base, needed)
            self.assertEqual(caught.exception.code, "insufficient_space")
        for free in (-1, None, True, 1.5):
            with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=free)), \
                    self.assertRaises(mdata_service.MDataError) as caught:
                mdata_service._require_space(self.base, needed)
            self.assertEqual(caught.exception.code, "io_error")

    def test_archive_capacity_bound_exceeds_real_container_without_changing_format(self):
        archive, root, _ = self.archive()
        file_bytes = sum(item["size"] for item in mdata_files.listing(root))
        self.assertGreaterEqual(mdata_service._archive_space_bound(file_bytes), archive.stat().st_size)
        # Existing unmodified streaming callers still decrypt with no callback.
        manifest, content = self.decode(archive)
        self.assertEqual(len(manifest["files"]), len(content))

    def test_legacy_capacity_check_follows_password_authentication_before_any_sink(self):
        from session_archive import encrypt
        from session_transfer import policy as legacy_policy
        payload = {"schema": 1, "sourceClient": "MAX Desktop", "sourceVersion": "26.35.1",
                   "credentials": [{"name": "access_token_42@MXMOD", "blob": base64.b64encode(b"synthetic-only").decode()}],
                   "files": [{"path": "auth_42", "data": base64.b64encode(b"{}").decode()}]}
        archive = self.base / "legacy-space.maxsession"
        archive.write_bytes(encrypt(payload, PASSWORD, policy=legacy_policy()))
        root, memory = self.profile("legacy-space-current", ["7"])
        before = self.file_values(root), dict(memory.data)
        with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=0)), \
                patch("mdata_files.sink", side_effect=AssertionError("Legacy plaintext sink opened")) as sink:
            self.job(self.service(root, memory), "import", archive, mode="add", error="insufficient_space")
        sink.assert_not_called()
        with patch("mdata_service.shutil.disk_usage", side_effect=AssertionError("Space checked before legacy authentication")) as capacity:
            self.job(self.service(root, memory), "import", archive, mode="add",
                     password="wrong-synthetic-password", error="authentication_failed")
        capacity.assert_not_called()
        self.assertEqual((self.file_values(root), memory.data), before)
        self.assertFalse(memory.writes)


if __name__ == "__main__":
    unittest.main()
