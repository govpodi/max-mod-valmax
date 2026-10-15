"""Synthetic policy/ACK tests; no MAX, real account IDs or PIN storage."""
from concurrent.futures import Future
import threading
import unittest
from unittest.mock import patch

import notification_policy as target


def catalog(*rows):
    return {'schema': 1, 'ready': True,
            'protected': [{'accountId': owner, 'chatId': chat, 'locked': locked}
                          for owner, chat, locked in rows]}


def snapshot(policy, **changes):
    return {'ready': True, 'applied': True, 'revision': policy['revision'],
            'error': None, 'masked': 0, 'scopeUnknown': 0, **changes}


class PolicyTests(unittest.TestCase):
    def test_large_owner_signed_chat_and_saved_messages_keep_exact_scope(self):
        value = target.from_catalog(catalog(('9007199254740993', '-9007199254740995', True),
                                            ('42', '0', False)))
        self.assertEqual([{'ownerId': '42', 'chatId': '0'},
                          {'ownerId': '9007199254740993', 'chatId': '-9007199254740995'}], value['protected'])
        self.assertEqual(5000, value['leaseMs'])

    def test_unlock_grants_do_not_change_preview_policy_or_revision(self):
        locked = target.from_catalog(catalog(('42', '7', True)))
        granted = target.from_catalog(catalog(('42', '7', False)))
        self.assertEqual(locked, granted)
        self.assertEqual({'schema', 'revision', 'ready', 'protected', 'leaseMs'}, set(locked))
        self.assertNotIn('pin', repr(locked).lower())

    def test_unavailable_is_distinct_from_known_empty_protection_list(self):
        empty = target.from_catalog(catalog())
        unavailable = target.unavailable_policy()
        self.assertTrue(empty['ready'])
        self.assertFalse(unavailable['ready'])
        self.assertNotEqual(empty['revision'], unavailable['revision'])

    def test_order_is_stable_but_account_and_chat_changes_update_revision(self):
        first = target.from_catalog(catalog(('42', '7', True), ('43', '0', False)))
        second = target.from_catalog(catalog(('43', '0', True), ('42', '7', False)))
        self.assertEqual(first, second)
        self.assertNotEqual(first['revision'], target.from_catalog(catalog(('43', '7', True)))['revision'])

    def test_malformed_duplicate_noncanonical_and_oversized_catalogues_rejected(self):
        values = [None, {}, {'schema': True, 'ready': True, 'protected': []},
                  {'schema': 1, 'ready': False, 'protected': []},
                  catalog(('42', '7', True), ('42', '7', False)),
                  catalog(('0', '7', True)), catalog(('42', '-0', True)),
                  catalog(('42', '01', True)), catalog(('42', 7, True)),
                  catalog(('42', '9223372036854775808', True)),
                  catalog(*[(str(n + 1), '7', True) for n in range(257)])]
        for value in values:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(target.NotificationPolicyError):
                target.from_catalog(value)

    def test_native_snapshot_is_bounded_and_value_free(self):
        policy = target.from_catalog(catalog())
        good = snapshot(policy, masked=5, scopeUnknown=2)
        self.assertEqual(good, target.checked_snapshot(good))
        for changes in ({'masked': True}, {'masked': 1000001}, {'error': []},
                        {'revision': 'not-a-revision'}, {'ready': False},
                        {'error': 'hook_failed'}, {'payload': 'synthetic content'}):
            with self.subTest(changes=changes), self.assertRaises(target.NotificationPolicyError):
                target.checked_snapshot({**good, **changes})


class FakeScript:
    def __init__(self):
        self.handlers = []
        self.posts = []
        self.reply = True
        self.mutate = lambda value: value

    def on(self, event, handler):
        self.handlers.append(handler)

    def off(self, event, handler):
        if handler in self.handlers:
            self.handlers.remove(handler)

    def emit(self, payload, data=None):
        for handler in list(self.handlers):
            handler({'type': 'send', 'payload': payload}, data)

    def post(self, message):
        self.posts.append(message)
        if self.reply:
            self.emit({'kind': target.ACK, 'requestId': message['requestId'],
                       'snapshot': self.mutate(snapshot(message['policy']))})


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self.script = FakeScript()
        self.channel = target.PolicyChannel(self.script)
        self.policy = target.from_catalog(catalog(('42', '7', True)))

    def tearDown(self):
        self.channel.close()

    def test_matching_ack_required_before_publish_completes(self):
        value = self.channel.publish(self.policy)
        self.assertEqual(self.policy['revision'], value['revision'])
        self.assertEqual(target.REQUEST, self.script.posts[0]['type'])
        self.assertIsNone(self.channel.pending)

    def test_envelope_generation_increases_across_refresh_and_channel_recreation(self):
        self.channel.publish(self.policy)
        self.channel.publish(self.policy)
        self.channel.close()
        replacement_script = FakeScript()
        replacement = target.PolicyChannel(replacement_script)
        try:
            replacement.publish(self.policy)
        finally:
            replacement.close()
        messages = self.script.posts + replacement_script.posts
        generations = [message['generation'] for message in messages]
        self.assertTrue(all(type(value) is int and 0 < value < 2**53 for value in generations))
        self.assertTrue(all(first < second for first, second in zip(generations, generations[1:])))
        self.assertEqual(3, len({message['requestId'] for message in messages}))
        # An envelope epoch must not change the stable policy revision or add
        # transport fields to the native protection catalogue itself.
        self.assertTrue(all(message['policy'] == self.policy for message in messages))
        self.assertNotIn('generation', self.policy)

    def test_wrong_revision_and_unapplied_ack_refuse_protection(self):
        for change in ({'revision': 'f' * 64}, {'applied': False},
                       {'ready': False, 'applied': False, 'error': 'waiting_for_modules'}):
            script = FakeScript()
            script.mutate = lambda value, change=change: {**value, **change}
            channel = target.PolicyChannel(script)
            try:
                with self.assertRaises(target.NotificationPolicyError):
                    channel.publish(self.policy)
                self.assertTrue(channel.stale)
            finally:
                channel.close()

    def test_missing_ack_times_out_and_channel_cannot_be_reused(self):
        self.script.reply = False
        with self.assertRaises(target.NotificationPolicyError):
            self.channel.publish(self.policy, timeout=.02)
        self.assertTrue(self.channel.stale)
        with self.assertRaises(target.NotificationPolicyError):
            self.channel.publish(self.policy)
        self.assertIsNone(self.channel.pending)

    def test_binary_or_unrelated_messages_cannot_satisfy_ack(self):
        self.script.reply = False
        result = []
        worker = threading.Thread(target=lambda: result.append(self.channel.publish(self.policy, timeout=1)))
        worker.start()
        while self.channel.pending is None:
            threading.Event().wait(.001)
        identifier = self.channel.pending[0]
        payload = {'kind': target.ACK, 'requestId': identifier, 'snapshot': snapshot(self.policy)}
        self.script.emit(payload, b'synthetic binary')
        self.script.emit({**payload, 'kind': 'privacy-snapshot'})
        self.script.emit({**payload, 'requestId': '0' * 32})
        self.assertFalse(result)
        self.script.emit(payload)
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual(1, len(result))

    def test_close_cancels_waiter_without_waiting_for_post_thread(self):
        self.script.reply = False
        failures = []
        def wait():
            try:
                self.channel.publish(self.policy, timeout=1)
            except target.NotificationPolicyError:
                failures.append(True)
        worker = threading.Thread(target=wait)
        worker.start()
        while self.channel.pending is None:
            threading.Event().wait(.001)
        self.channel.close()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        self.assertEqual([True], failures)

    def test_ack_followed_by_close_cannot_confirm_a_waiting_publish(self):
        ack_received, resume = threading.Event(), threading.Event()
        failures, results, futures = [], [], []

        class BarrierFuture(Future):
            def __init__(self):
                super().__init__()
                futures.append(self)

            def result(self, timeout=None):
                value = super().result(timeout)
                ack_received.set()
                if not resume.wait(2):
                    raise AssertionError('Synthetic waiter barrier timed out')
                return value

        def publish():
            try:
                results.append(self.channel.publish(self.policy, timeout=1))
            except Exception as error:
                failures.append(error)

        with patch.object(target, 'Future', BarrierFuture):
            worker = threading.Thread(target=publish)
            worker.start()
            try:
                self.assertTrue(ack_received.wait(1))
                self.channel.close()
            finally:
                resume.set()
                worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual([], results)
        self.assertEqual(1, len(failures))
        self.assertIsInstance(failures[0], target.NotificationPolicyError)
        self.assertTrue(self.channel.closed)
        self.assertTrue(self.channel.stale)
        self.assertIsNone(self.channel.pending)
        self.assertEqual(1, len(futures))
        self.assertTrue(futures[0].done())

    def test_close_is_idempotent_and_removes_only_its_own_handler(self):
        off_finished = threading.Event()
        other_handler = lambda _message, _data=None: None
        self.script.on('message', other_handler)
        original_off = self.script.off

        def off(event, handler):
            try:
                original_off(event, handler)
            finally:
                off_finished.set()

        real_thread = threading.Thread
        with patch.object(self.script, 'off', side_effect=off) as remove:
            with patch.object(target.threading, 'Thread', wraps=real_thread) as spawn:
                self.channel.close()
                self.channel.close()
                self.assertTrue(off_finished.wait(1))
                self.channel.close()
                self.assertEqual(1, spawn.call_count)
            remove.assert_called_once_with('message', self.channel.handler)
        self.assertEqual([other_handler], self.script.handlers)

    def test_late_ack_from_a_blocked_post_cannot_revive_timed_out_channel(self):
        entered, release, delivered = threading.Event(), threading.Event(), threading.Event()
        original_post = self.script.post

        def post(message):
            entered.set()
            try:
                if not release.wait(2):
                    raise AssertionError('Synthetic post barrier timed out')
                original_post(message)
            finally:
                delivered.set()

        with patch.object(self.script, 'post', side_effect=post):
            try:
                with self.assertRaises(target.NotificationPolicyError):
                    self.channel.publish(self.policy, timeout=.05)
                self.assertTrue(entered.wait(1))
                self.assertTrue(self.channel.stale)
                self.assertIsNone(self.channel.pending)
            finally:
                release.set()
                self.assertTrue(delivered.wait(1))
        with self.assertRaises(target.NotificationPolicyError):
            self.channel.publish(self.policy)
        self.assertEqual(1, len(self.script.posts))
        self.assertIsNone(self.channel.pending)


class RuntimeTests(unittest.TestCase):
    def test_late_ack_cannot_reinsert_removed_or_unsynced_client(self):
        runtime = target.NotificationRuntime(clock=lambda: 1.0)
        policy = target.from_catalog(catalog(('42', '7', True)))
        value = snapshot(policy)
        runtime.update(100, value)
        self.assertEqual({}, runtime.clients)
        for discard in (lambda: runtime.remove(100), lambda: runtime.sync([])):
            runtime.register(100)
            runtime.update(100, value)
            self.assertTrue(runtime.status(policy)['applied'])
            discard()
            runtime.update(100, value)
            self.assertEqual({}, runtime.clients)
            self.assertFalse(runtime.status(policy)['ready'])
            self.assertFalse(runtime.status(policy)['applied'])

    def test_register_requires_a_real_pid_and_discards_previous_confirmation(self):
        runtime = target.NotificationRuntime(clock=lambda: 1.0)
        policy = target.from_catalog(catalog())
        for pid in (0, -1, 2**32, True, '100', None):
            with self.subTest(pid=pid), self.assertRaises(target.NotificationPolicyError):
                runtime.register(pid)
        self.assertEqual({}, runtime.clients)
        runtime.register(100)
        runtime.update(100, snapshot(policy))
        self.assertTrue(runtime.status(policy)['applied'])
        runtime.register(100)
        self.assertIsNone(runtime.clients[100])
        self.assertFalse(runtime.status(policy)['applied'])

    def test_every_live_client_must_ack_latest_policy_with_a_fresh_lease(self):
        now = [1.0]
        runtime = target.NotificationRuntime(clock=lambda: now[0])
        policy = target.from_catalog(catalog(('42', '7', True)))
        runtime.sync([100, 200])
        runtime.update(100, snapshot(policy, masked=3))
        self.assertFalse(runtime.status(policy)['applied'])
        runtime.update(200, snapshot(policy, masked=4))
        self.assertTrue(runtime.status(policy)['applied'])
        self.assertEqual(7, runtime.status(policy)['masked'])
        changed = target.from_catalog(catalog(('42', '8', True)))
        self.assertFalse(runtime.status(changed)['applied'])
        now[0] += 5.0
        self.assertFalse(runtime.status(policy)['ready'])


if __name__ == '__main__':
    unittest.main()
