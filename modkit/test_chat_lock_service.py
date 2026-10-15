"""Own temporary stores and fake protectors; never opens messenger data."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chat_lock_service import ChatLockService, ChatLockError, WindowsProtector


class FakeProtector:
    def protect(self, data):
        return b"OWN-TEST-PROTECTED:" + data

    def unprotect(self, data):
        if not data.startswith(b"OWN-TEST-PROTECTED:"):
            raise ChatLockError("protector_unavailable")
        return data[len(b"OWN-TEST-PROTECTED:"):]


class ChatLockServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="chat-pin-", dir="D:/ValMAX/analysis/test-temp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "profile"
        self.now = 1000.0
        self.protector = FakeProtector()
        self.service = self.fresh()

    def fresh(self):
        return ChatLockService(self.root, protector=self.protector, clock=lambda: self.now, monotonic=lambda: self.now)

    def call(self, action, account="42", chat="-77", **fields):
        return self.service.execute(action, {"schema": 1, "accountId": account, "chatId": chat, **fields})

    def set_pin(self, account="42", chat="-77"):
        return self.call("set", account, chat, pin="681439", confirmPin="681439")

    def error(self, expected, action, **fields):
        with self.assertRaises(ChatLockError) as caught:
            self.call(action, **fields)
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected)

    def test_absent_store_read_does_not_create_profile(self):
        self.assertEqual(self.service.catalog(), {"schema": 1, "ready": True, "protected": []})
        self.assertFalse(self.root.exists())

    def test_notification_mask_is_confirmed_before_new_pin_is_persisted(self):
        called = []
        def prepare(catalog):
            self.assertFalse((self.root / 'chat-locks.json').exists())
            self.assertEqual({'schema': 1, 'ready': True, 'protected': [
                {'accountId': '42', 'chatId': '-77', 'locked': True}]}, catalog)
            self.assertNotIn('protected', catalog['protected'][0])
            called.append(True)
        self.service.before_protect = prepare
        self.assertTrue(self.set_pin()['protected'])
        self.assertEqual([True], called)
        self.assertTrue((self.root / 'chat-locks.json').is_file())

    def test_notification_failure_refuses_new_pin_without_changing_existing_pin(self):
        self.set_pin()
        before = (self.root / 'chat-locks.json').read_bytes()
        def fail(_catalog):
            raise RuntimeError('synthetic native filter unavailable')
        self.service.before_protect = fail
        self.error('notification_unavailable', 'set', account='43', chat='88',
                   pin='318642', confirmPin='318642')
        self.assertEqual(before, (self.root / 'chat-locks.json').read_bytes())
        self.assertEqual(1, len(self.service.catalog()['protected']))
        self.assertFalse(self.call('unlock', pin='681439')['locked'])

    def test_notification_guard_remains_held_through_persistence_and_grant(self):
        events, active = [], [False]
        original_write = self.service._write

        @contextmanager
        def guard():
            events.append('enter')
            active[0] = True
            try:
                yield
            finally:
                self.assertTrue((self.root / 'chat-locks.json').is_file())
                self.assertIn('42:-77', self.service.grants)
                events.append('exit')
                active[0] = False

        def prepare(catalog):
            events.append('ack')
            self.assertEqual('42', catalog['protected'][0]['accountId'])
            self.assertFalse((self.root / 'chat-locks.json').exists())
            return guard()

        def write(path, state):
            self.assertTrue(active[0])
            self.assertNotIn('42:-77', self.service.grants)
            events.append('write')
            return original_write(path, state)

        self.service.before_protect = prepare
        with patch.object(self.service, '_write', side_effect=write):
            self.assertTrue(self.set_pin()['protected'])
        self.assertEqual(['ack', 'enter', 'write', 'exit'], events)
        self.assertFalse(active[0])
        self.assertFalse(self.call('status')['locked'])

    def test_write_failure_after_ack_preserves_existing_store_and_releases_guard(self):
        self.set_pin()
        before = (self.root / 'chat-locks.json').read_bytes()
        grants_before = dict(self.service.grants)
        active, exits = [False], []

        @contextmanager
        def guard():
            active[0] = True
            try:
                yield
            except OSError:
                exits.append('write-failure')
                raise
            finally:
                active[0] = False

        self.service.before_protect = lambda _catalog: guard()

        def fail_write(_path, _state):
            self.assertTrue(active[0])
            raise OSError('Synthetic persistence failure')

        with patch.object(self.service, '_write', side_effect=fail_write):
            self.error('io_error', 'set', account='43', chat='88',
                       pin='318642', confirmPin='318642')
        self.assertEqual(['write-failure'], exits)
        self.assertFalse(active[0])
        self.assertEqual(before, (self.root / 'chat-locks.json').read_bytes())
        self.assertEqual(grants_before, self.service.grants)
        self.assertFalse(self.call('status', account='43', chat='88')['protected'])

    def test_failed_guard_entry_refuses_pin_before_writing_or_granting(self):
        @contextmanager
        def unavailable():
            raise RuntimeError('Synthetic notification guard unavailable')
            yield

        self.service.before_protect = lambda _catalog: unavailable()
        with patch.object(self.service, '_write', wraps=self.service._write) as write:
            self.error('notification_unavailable', 'set', pin='681439', confirmPin='681439')
            write.assert_not_called()
        self.assertFalse((self.root / 'chat-locks.json').exists())
        self.assertEqual({}, self.service.grants)

    def test_refresh_cannot_read_old_catalogue_during_staged_confirmation(self):
        entered, release = threading.Event(), threading.Event()
        mutex_checked, reader_done = threading.Event(), threading.Event()
        failures, catalogues, acquired = [], [], []

        @contextmanager
        def guard():
            entered.set()
            if not release.wait(2):
                raise AssertionError('Synthetic confirmation barrier timed out')
            yield

        self.service.before_protect = lambda _catalog: guard()

        def set_pin():
            try:
                self.set_pin()
            except Exception as error:
                failures.append(error)

        def refresh():
            try:
                locked = self.service.mutex.acquire(blocking=False)
                acquired.append(locked)
                if locked:
                    self.service.mutex.release()
                mutex_checked.set()
                with self.service.mutex:
                    catalogues.append(self.service.catalog())
            except Exception as error:
                failures.append(error)
            finally:
                reader_done.set()

        setter = threading.Thread(target=set_pin)
        reader = threading.Thread(target=refresh)
        setter.start()
        try:
            self.assertTrue(entered.wait(1))
            reader.start()
            self.assertTrue(mutex_checked.wait(1))
            self.assertEqual([False], acquired)
            self.assertFalse(reader_done.is_set())
        finally:
            release.set()
            setter.join(2)
            if reader.ident is not None:
                reader.join(2)
        self.assertFalse(setter.is_alive())
        self.assertFalse(reader.is_alive())
        self.assertEqual([], failures)
        self.assertEqual(1, len(catalogues))
        self.assertEqual(['-77'], [row['chatId'] for row in catalogues[0]['protected']])

    def test_salted_protected_verifier_scope_expiry_and_unlock(self):
        created = self.set_pin()
        self.assertTrue(created["protected"])
        self.assertFalse(created["locked"])
        data = (self.root / "chat-locks.json").read_text()
        self.assertNotIn("681439", data)
        self.assertNotIn('"pin"', data)
        self.assertEqual(set(json.loads(data)["locks"]["42:-77"]), {"salt", "protected", "failures", "blockedUntil"})
        self.assertFalse(self.call("status", account="43")["protected"])
        self.now += 121
        self.assertTrue(self.call("status")["locked"])
        self.assertFalse(self.call("unlock", pin="681439")["locked"])
        other = self.fresh()
        self.assertTrue(other.execute("status", {"schema": 1, "accountId": "42", "chatId": "-77"})["locked"])

    def test_attempt_delay_survives_helper_restart(self):
        self.set_pin()
        self.call("lock")
        for _ in range(5):
            self.error("wrong_pin", "unlock", pin="000000")
        self.service = self.fresh()
        self.error("rate_limited", "unlock", pin="681439")
        self.now += 31
        self.assertFalse(self.call("unlock", pin="681439")["locked"])
        saved = json.loads((self.root / "chat-locks.json").read_text())["locks"]["42:-77"]
        self.assertEqual((saved["failures"], saved["blockedUntil"]), (0, 0))

    def test_remove_requires_pin_and_lock_all_preserves_records(self):
        self.set_pin()
        self.set_pin(account="43", chat="88")
        result = self.service.execute("lock-all", {"schema": 1})
        self.assertEqual(len(result["protected"]), 2)
        self.assertTrue(all(item["locked"] for item in result["protected"]))
        self.error("wrong_pin", "remove", pin="000000")
        removed = self.call("remove", pin="681439")
        self.assertFalse(removed["protected"])
        self.assertEqual(len(self.service.catalog()["protected"]), 1)

    def test_cannot_silently_replace_existing_pin(self):
        self.set_pin()
        self.error("already_protected", "set", pin="012345", confirmPin="012345")
        self.call("lock")
        self.error("wrong_pin", "unlock", pin="012345")
        self.assertFalse(self.call("unlock", pin="681439")["locked"])

    def test_malformed_store_fails_closed_without_echoing_input(self):
        self.root.mkdir()
        (self.root / "chat-locks.json").write_text('{"schema":1,"schema":1,"locks":{}}')
        with self.assertRaises(ChatLockError) as caught:
            self.service.catalog()
        self.assertEqual(caught.exception.code, "invalid_store")

    def test_bad_requests_reject_unknown_keys_ids_and_non_ascii_pins(self):
        for value in ("12", "1234567890123", "１２３４５６", 123456, True, "private-value"):
            self.error("invalid_pin", "set", pin=value, confirmPin=value)
        for account in ("0", "042", "9223372036854775808", 42, "PRIVATE"):
            self.error("invalid_request", "status", account=account)
        for chat in ("-0", "00", "01", "-9223372036854775809", 77):
            self.error("invalid_request", "status", chat=chat)
        self.error("invalid_request", "status", unexpected="PRIVATE")
        self.assertFalse(self.root.exists())

    def test_saved_messages_zero_is_a_separate_valid_chat(self):
        self.set_pin(chat="77")
        self.call("lock", chat="77")
        saved = self.call("status", chat="0")
        self.assertFalse(saved["protected"])
        self.assertFalse(saved["locked"])
        self.assertTrue(self.call("status", chat="77")["locked"])
        # Zero is an actual native chat ID. It is protected only if explicitly
        # configured, and has a distinct scope for every account.
        self.set_pin(chat="0")
        self.call("lock", chat="0")
        self.assertTrue(self.call("status", chat="0")["locked"])
        self.assertFalse(self.call("status", account="43", chat="0")["protected"])
        self.assertFalse(self.call("unlock", chat="0", pin="681439")["locked"])
        self.assertTrue(self.call("status", chat="77")["locked"])

    def test_owned_windows_protector_round_trip_and_corruption(self):
        if os.name != "nt":
            self.skipTest("Windows DPAPI unavailable")
        protector = WindowsProtector()
        source = (b"OWN-SYNTHETIC-VERIFIER-" * 2)[:32]
        self.assertEqual(len(source), 32)
        encrypted = protector.protect(source)
        self.assertNotEqual(encrypted, source)
        self.assertEqual(protector.unprotect(encrypted), source)
        with self.assertRaises(ChatLockError):
            protector.unprotect(b"corrupt-owned-test-data")


if __name__ == "__main__":
    unittest.main()
