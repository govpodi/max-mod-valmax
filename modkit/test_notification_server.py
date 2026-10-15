"""Synthetic notification host/HTTP integration; never starts or attaches MAX.

Only the test's temporary PIN store and local ephemeral HTTP socket are used.
Fake scripts exercise the real ACK channel; eternal guards reject all posts.
"""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from chat_lock_service import ChatLockError, ChatLockService
import notification_native as native
import notification_policy as policy
import privacy_server as host


class FakeProtector:
    def protect(self, value):
        return b'OWN-NOTIFICATION-TEST:' + value

    def unprotect(self, value):
        prefix = b'OWN-NOTIFICATION-TEST:'
        if not value.startswith(prefix):
            raise ChatLockError('protector_unavailable')
        return value[len(prefix):]


class Script:
    def __init__(self, *, eternal=False):
        self.eternal = eternal
        self.handlers, self.posts = [], []
        self.fail, self.mutate = False, lambda value: value

    def on(self, event, handler):
        if event != 'message':
            raise AssertionError('Unexpected synthetic script event')
        self.handlers.append(handler)

    def off(self, event, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)

    def post(self, message):
        if self.eternal:
            raise AssertionError('Eternal guards must never receive policy posts')
        if self.fail:
            raise RuntimeError('Synthetic updater unavailable')
        self.posts.append(message)
        value = self.mutate({'ready': True, 'applied': True,
                             'revision': message['policy']['revision'], 'error': None,
                             'masked': 0, 'scopeUnknown': 0})
        for handler in list(self.handlers):
            handler({'type': 'send', 'payload': {'kind': policy.ACK,
                    'requestId': message['requestId'], 'snapshot': value}}, None)


BRIDGE = {'kind': 'valmax-notification-bridge-ready', 'schema': 1,
          'pointer': '0x1000', 'size': 65536, 'magic': '56414c4e42523031'}


class BootstrapScript(Script):
    def __init__(self, *, eternal=False):
        super().__init__(eternal=eternal)
        self.on_load = lambda: None
        self.load_error, self.eternalized = False, False
        self.off_finished = threading.Event()

    def load(self):
        if self.load_error:
            raise RuntimeError('Synthetic script load failure')
        self.on_load()
        if self.eternal:
            for handler in list(self.handlers):
                handler({'type': 'send', 'payload': dict(BRIDGE)}, None)

    def eternalize(self):
        self.eternalized = True

    def off(self, event, handler):
        super().off(event, handler)
        self.off_finished.set()


class BootstrapSession:
    def __init__(self):
        self.guard = BootstrapScript(eternal=True)
        self.updater = BootstrapScript()
        self.created = []

    def create_script(self, source):
        self.created.append(source)
        if source == 'OWN-GUARD-SOURCE':
            return self.guard
        if source == 'OWN-UPDATER-SOURCE':
            return self.updater
        raise AssertionError('Unexpected synthetic source')


def staged_catalog(account='42', chat='-77'):
    return {'schema': 1, 'ready': True, 'protected': [
        {'accountId': account, 'chatId': chat, 'locked': True}]}


class NotificationHostTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='notification-host-',
                                               dir='D:/ValMAX/analysis/test-temp')
        self.addCleanup(temporary.cleanup)
        self.profile = Path(temporary.name) / 'profile'
        self.monitor = host.Monitor.__new__(host.Monitor)
        self.monitor.notification_sessions = {}
        self.monitor.notification_runtime = policy.NotificationRuntime()
        self.monitor.notification_policy = policy.unavailable_policy()
        self.monitor.notification_lock = threading.RLock()
        self.monitor.root = Path(temporary.name) / 'program'
        self.monitor.notification_image_path = Path(temporary.name) / 'owned-generic.png'
        self.monitor.chat_locks = ChatLockService(
            self.profile, protector=FakeProtector(),
            before_protect=self.monitor.prepare_protected_notifications)
        self.channels = []
        self.addCleanup(self.close_channels)

    def close_channels(self):
        for channel in self.channels:
            channel.close()
        for _guard, _updater, channel in self.monitor.notification_sessions.values():
            channel.close()

    def add_client(self, pid=100):
        guard, updater = Script(eternal=True), Script()
        channel = policy.PolicyChannel(updater)
        self.channels.append(channel)
        self.monitor.notification_sessions[pid] = guard, updater, channel
        self.monitor.notification_runtime.register(pid)
        return guard, updater, channel

    def set_pin(self, account='42', chat='-77'):
        return self.monitor.chat_locks.execute('set', {'schema': 1, 'accountId': account,
            'chatId': chat, 'pin': '681439', 'confirmPin': '681439'})

    def test_pin_requires_an_attached_notification_guard_before_persistence(self):
        with self.assertRaises(ChatLockError) as error:
            self.set_pin()
        self.assertEqual('notification_unavailable', error.exception.code)
        self.assertFalse((self.profile / 'chat-locks.json').exists())
        self.assertEqual({}, self.monitor.chat_locks.grants)

    def test_preparation_confirms_exact_staged_scope_and_holds_guard_through_write(self):
        guard, updater, _channel = self.add_client()
        service = self.monitor.chat_locks
        original_write = service._write
        observed = []

        def write(path, state):
            observed.append((service.mutex._is_owned(),
                             self.monitor.notification_lock._is_owned()))
            self.assertTrue(self.monitor.notification_status()['applied'])
            self.assertNotIn('42:-77', service.grants)
            return original_write(path, state)

        with patch.object(service, '_write', side_effect=write):
            self.assertTrue(self.set_pin()['protected'])
        self.assertEqual([(True, True)], observed)
        self.assertEqual([{'ownerId': '42', 'chatId': '-77'}], updater.posts[0]['policy']['protected'])
        self.assertEqual([], guard.posts)
        self.assertFalse(self.monitor.notification_lock._is_owned())

    def test_every_client_must_ack_or_pin_and_grant_are_refused(self):
        _first_guard, first, _channel = self.add_client(100)
        _second_guard, second, _channel = self.add_client(200)
        second.mutate = lambda value: {**value, 'applied': False}
        with self.assertRaises(ChatLockError) as error:
            self.set_pin()
        self.assertEqual('notification_unavailable', error.exception.code)
        self.assertEqual(1, len(first.posts))
        self.assertEqual(1, len(second.posts))
        self.assertFalse((self.profile / 'chat-locks.json').exists())
        self.assertEqual({}, self.monitor.chat_locks.grants)
        self.assertIsNone(self.monitor.notification_runtime.clients[200])
        self.assertFalse(self.monitor.notification_status()['applied'])

    def test_stale_or_closed_channel_recreated_on_live_updater_not_eternal_guard(self):
        for old_state in ('stale', 'closed'):
            with self.subTest(old_state=old_state):
                guard, updater, original = self.add_client()
                self.monitor._refresh_notifications()
                if old_state == 'closed':
                    original.close()
                else:
                    original.stale = True
                self.monitor._refresh_notifications()
                current_guard, current_updater, replacement = self.monitor.notification_sessions[100]
                self.assertIs(guard, current_guard)
                self.assertIs(updater, current_updater)
                self.assertIs(updater, replacement.script)
                self.assertIsNot(original, replacement)
                self.assertTrue(original.closed)
                self.assertEqual([], guard.posts)
                self.assertEqual(2, len(updater.posts))
                self.assertLess(updater.posts[0]['generation'], updater.posts[1]['generation'])
                self.assertTrue(self.monitor.notification_status()['applied'])
                replacement.close()

    def test_wrong_revision_invalidates_then_recreates_updater_channel(self):
        guard, updater, old = self.add_client()
        updater.mutate = lambda value: {**value, 'revision': 'f' * 64}
        with self.assertRaises(policy.NotificationPolicyError):
            self.monitor._refresh_notifications()
        self.assertTrue(old.stale)
        self.assertIsNone(self.monitor.notification_runtime.clients[100])
        self.assertFalse(self.monitor.notification_status()['applied'])
        updater.mutate = lambda value: value
        self.monitor._refresh_notifications()
        replacement = self.monitor.notification_sessions[100][2]
        self.assertIsNot(old, replacement)
        self.assertTrue(old.closed)
        self.assertIs(updater, replacement.script)
        self.assertTrue(self.monitor.notification_status()['applied'])
        self.assertLess(updater.posts[0]['generation'], updater.posts[1]['generation'])
        self.assertEqual([], guard.posts)

    def test_changed_session_identity_after_ack_cannot_confirm_protection(self):
        guard, updater, channel = self.add_client()
        replacement_guard, replacement_updater = Script(eternal=True), Script()
        replacement = (replacement_guard, replacement_updater, policy.PolicyChannel(replacement_updater))
        self.channels.append(replacement[2])

        def changed(_value):
            self.monitor.notification_sessions[100] = replacement
            return {'ready': True, 'applied': True,
                    'revision': self.monitor.notification_policy['revision'], 'error': None,
                    'masked': 0, 'scopeUnknown': 0}

        updater.mutate = changed
        with self.assertRaises(ChatLockError) as error:
            self.set_pin()
        self.assertEqual('notification_unavailable', error.exception.code)
        self.assertIs(replacement, self.monitor.notification_sessions[100])
        self.assertIsNone(self.monitor.notification_runtime.clients[100])
        self.assertFalse((self.profile / 'chat-locks.json').exists())
        self.assertEqual([], guard.posts)

    def test_forget_waits_until_confirmation_context_finishes(self):
        _guard, _updater, channel = self.add_client()
        attempted, finished = threading.Event(), threading.Event()
        acquired, failures = [], []

        def forget():
            try:
                available = self.monitor.notification_lock.acquire(blocking=False)
                acquired.append(available)
                if available:
                    self.monitor.notification_lock.release()
                attempted.set()
                self.monitor._forget_notifications(100)
            except Exception as error:
                failures.append(error)
            finally:
                finished.set()

        worker = threading.Thread(target=forget)
        try:
            with self.monitor.chat_locks.mutex:
                with self.monitor.prepare_protected_notifications(staged_catalog()):
                    worker.start()
                    self.assertTrue(attempted.wait(1))
                    self.assertEqual([False], acquired)
                    self.assertFalse(finished.is_set())
                    self.assertTrue(self.monitor.notification_status()['applied'])
        finally:
            if worker.ident is not None:
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], failures)
        self.assertTrue(channel.closed)
        self.assertNotIn(100, self.monitor.notification_sessions)
        self.assertNotIn(100, self.monitor.notification_runtime.clients)
        self.assertFalse(self.monitor.notification_status()['applied'])

    def test_write_failure_after_host_ack_is_io_error_and_refresh_removes_staged_scope(self):
        _guard, updater, _channel = self.add_client()
        service = self.monitor.chat_locks

        def fail(_path, _value):
            self.assertTrue(self.monitor.notification_lock._is_owned())
            self.assertTrue(self.monitor.notification_status()['applied'])
            raise OSError('Synthetic write failure')

        with patch.object(service, '_write', side_effect=fail):
            with self.assertRaises(ChatLockError) as error:
                self.set_pin()
        self.assertEqual('io_error', error.exception.code)
        self.assertEqual({}, service.grants)
        self.assertFalse((self.profile / 'chat-locks.json').exists())
        self.assertFalse(self.monitor.notification_lock._is_owned())
        self.assertEqual(1, len(updater.posts[0]['policy']['protected']))
        self.monitor._refresh_notifications()
        self.assertEqual([], updater.posts[-1]['policy']['protected'])
        self.assertTrue(self.monitor.notification_status()['applied'])

    def test_bootstrap_holds_store_mutex_until_registered_then_new_pin_reaches_both_clients(self):
        _old_guard, old_updater, _old_channel = self.add_client(100)
        session = BootstrapSession()
        guard_entered, resume_guard = threading.Event(), threading.Event()
        writer_checked, writer_done = threading.Event(), threading.Event()
        acquired, failures = [], []

        def paused_load():
            self.assertTrue(self.monitor.chat_locks.mutex._is_owned())
            guard_entered.set()
            if not resume_guard.wait(2):
                raise AssertionError('Synthetic bootstrap barrier timed out')

        session.guard.on_load = paused_load

        def attach():
            try:
                self.monitor._attach_notifications(200, session)
            except Exception as error:
                failures.append(error)

        def writer():
            try:
                available = self.monitor.chat_locks.mutex.acquire(blocking=False)
                acquired.append(available)
                if available:
                    self.monitor.chat_locks.mutex.release()
                writer_checked.set()
                self.set_pin()
            except Exception as error:
                failures.append(error)
            finally:
                writer_done.set()

        attaching, writing = threading.Thread(target=attach), threading.Thread(target=writer)
        with patch.object(native, 'source_for', return_value='OWN-GUARD-SOURCE'), \
                patch.object(native, 'updater_source_for', return_value='OWN-UPDATER-SOURCE', create=True), \
                patch.object(host, 'bounded_rpc', side_effect=lambda call: call()):
            attaching.start()
            try:
                self.assertTrue(guard_entered.wait(1))
                writing.start()
                self.assertTrue(writer_checked.wait(1))
                self.assertEqual([False], acquired)
                self.assertFalse(writer_done.is_set())
                self.assertFalse((self.profile / 'chat-locks.json').exists())
            finally:
                resume_guard.set()
                attaching.join(2)
                if writing.ident is not None:
                    writing.join(2)
        self.assertFalse(attaching.is_alive())
        self.assertFalse(writing.is_alive())
        self.assertEqual([], failures)
        self.assertTrue(session.guard.eternalized)
        self.assertEqual([], session.guard.posts)
        self.assertEqual([], session.guard.handlers)
        self.assertEqual(['OWN-GUARD-SOURCE', 'OWN-UPDATER-SOURCE'], session.created)
        self.assertEqual([], session.updater.posts[0]['policy']['protected'])
        latest = policy.from_catalog(self.monitor.chat_locks.catalog())
        self.assertEqual(latest, self.monitor.notification_policy)
        self.assertEqual(latest, session.updater.posts[-1]['policy'])
        self.assertEqual(latest, old_updater.posts[-1]['policy'])
        self.assertTrue(self.monitor.notification_status()['applied'])

    def test_invalid_bootstrap_metadata_stops_before_eternalize_or_registration(self):
        for factory_error in (ValueError('Synthetic invalid bridge'),
                              OSError('Synthetic updater resource unavailable')):
            with self.subTest(error_type=type(factory_error).__name__):
                session = BootstrapSession()
                with patch.object(native, 'source_for', return_value='OWN-GUARD-SOURCE'), \
                        patch.object(native, 'updater_source_for', side_effect=factory_error, create=True), \
                        patch.object(host, 'bounded_rpc', side_effect=lambda call: call()):
                    with self.assertRaises(policy.NotificationPolicyError):
                        self.monitor._attach_notifications(100, session)
                self.assertFalse(session.guard.eternalized)
                self.assertEqual(['OWN-GUARD-SOURCE'], session.created)
                self.assertEqual([], session.guard.handlers)
                self.assertEqual({}, self.monitor.notification_sessions)
                self.assertEqual({}, self.monitor.notification_runtime.clients)
                self.assertFalse(self.monitor.chat_locks.mutex._is_owned())

    def test_updater_failure_keeps_eternal_guard_and_cleans_owned_channel(self):
        for failure in ('load', 'post'):
            with self.subTest(failure=failure):
                session = BootstrapSession()
                session.updater.load_error = failure == 'load'
                session.updater.fail = failure == 'post'
                expected = RuntimeError if failure == 'load' else policy.NotificationPolicyError
                with patch.object(native, 'source_for', return_value='OWN-GUARD-SOURCE'), \
                        patch.object(native, 'updater_source_for', return_value='OWN-UPDATER-SOURCE', create=True), \
                        patch.object(host, 'bounded_rpc', side_effect=lambda call: call()):
                    with self.assertRaises(expected):
                        self.monitor._attach_notifications(100, session)
                self.assertTrue(session.guard.eternalized)
                self.assertTrue(session.updater.off_finished.wait(1))
                self.assertEqual([], session.guard.posts)
                self.assertEqual([], session.guard.handlers)
                self.assertEqual([], session.updater.handlers)
                self.assertEqual({}, self.monitor.notification_sessions)
                self.assertEqual({}, self.monitor.notification_runtime.clients)
                self.assertFalse(self.monitor.chat_locks.mutex._is_owned())


class NotificationHTTPTests(unittest.TestCase):
    def setUp(self):
        self.monitor = host.Monitor.__new__(host.Monitor)
        self.monitor.notification_runtime = policy.NotificationRuntime()
        self.monitor.notification_lock = threading.RLock()
        self.monitor.notification_policy = policy.from_catalog(staged_catalog('9007199254740993', '-9007199254740995'))
        self.monitor.notification_runtime.register(100)
        self.monitor.notification_runtime.update(100, {'ready': True, 'applied': True,
            'revision': self.monitor.notification_policy['revision'], 'error': None,
            'masked': 7, 'scopeUnknown': 2})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), host.handler_factory(
            lambda: {'schema': 1}, notification_privacy=self.monitor.notification_status))
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, *, method='GET', extra=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request(method, '/notification-privacy', headers={
                'Host': f'127.0.0.1:{host.PORT}', **(extra or {})})
            response = connection.getresponse()
            return response.status, dict(response.headers), response.read()
        finally:
            connection.close()

    def test_status_exposes_only_readiness_and_bounded_counters(self):
        status, headers, raw = self.request()
        self.assertEqual(200, status)
        value = json.loads(raw)
        self.assertEqual({'schema': 1, 'ready': True, 'applied': True,
                          'masked': 7, 'scopeUnknown': 2}, value)
        self.assertEqual('no-store', headers['Cache-Control'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        for private in (b'9007199254740993', b'9007199254740995',
                        self.monitor.notification_policy['revision'].encode(), b'protected', b'pin'):
            self.assertNotIn(private, raw)
        self.monitor.notification_runtime.remove(100)
        value = json.loads(self.request()[2])
        self.assertFalse(value['ready'])
        self.assertFalse(value['applied'])

    def test_status_is_read_only_and_rejects_nonlocal_host_or_web_origin(self):
        self.assertEqual(405, self.request(method='POST')[0])
        self.assertEqual(405, self.request(method='OPTIONS')[0])
        self.assertEqual(404, self.request(extra={'Host': 'example.invalid'})[0])
        self.assertEqual(404, self.request(extra={'Origin': 'https://example.invalid'})[0])


if __name__ == '__main__':
    unittest.main()
