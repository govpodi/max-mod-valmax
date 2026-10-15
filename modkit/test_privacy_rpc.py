"""Pure fake-script checks: no MAX execution, Frida attach or real RPC."""
from __future__ import annotations

import threading
import time
import unittest

from privacy_rpc import PROTOCOL, SnapshotChannel, SnapshotTimeout, SnapshotUnavailable


class FakeScript:
    def __init__(self, mode="reply"):
        self.handlers = []
        self.requests = []
        self.mode = mode
        self.posted = threading.Event()
        self.release = threading.Event()

    def on(self, name, handler):
        assert name == "message"
        self.handlers.append(handler)

    def off(self, name, handler):
        if self.mode == "blocked-off":
            self.release.wait(2)
        self.handlers.remove(handler)

    def post(self, message):
        self.requests.append(message)
        self.posted.set()
        if self.mode == "raise":
            raise RuntimeError("PRIVATE_NATIVE_ERROR_VALUE_MUST_NOT_ESCAPE")
        if self.mode == "blocked-post":
            self.release.wait(2)
        if self.mode in ("reply", "blocked-post", "blocked-off"):
            self.reply(message)

    def reply(self, request, *, snapshot=None, kind=PROTOCOL, identifier=None, data=None):
        value = {"stats": {"tracerHooks": 1}, "tracerCounts": {}, "connections": {}, "failures": 0} if snapshot is None else snapshot
        message = {"type": "send", "payload": {"kind": kind,
                   "requestId": request["requestId"] if identifier is None else identifier, "snapshot": value}}
        for handler in tuple(self.handlers):
            handler(message, data)


class SnapshotTests(unittest.TestCase):
    def test_successful_reply_and_repeated_calls_leave_no_pending(self):
        script = FakeScript()
        with SnapshotChannel(script) as channel:
            self.assertEqual(channel.snapshot(timeout=.3)["stats"]["tracerHooks"], 1)
            self.assertEqual(channel.snapshot(timeout=.3)["failures"], 0)
            self.assertEqual(channel.pending_count, 0)
            self.assertFalse(channel.stale)
        self.assertEqual(script.handlers, [])
        self.assertNotEqual(script.requests[0]["requestId"], script.requests[1]["requestId"])

    def test_never_reply_has_real_deadline_and_cleans_pending(self):
        script = FakeScript("silent")
        channel = SnapshotChannel(script)
        started = time.monotonic()
        with self.assertRaises(SnapshotTimeout):
            channel.snapshot(timeout=.04)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(channel.pending_count, 0)
        self.assertTrue(channel.stale)
        with self.assertRaises(SnapshotUnavailable):
            channel.snapshot(timeout=.04)
        self.assertTrue(channel.close())
        self.assertEqual(script.handlers, [])

    def test_late_reply_after_timeout_and_after_close_is_ignored(self):
        script = FakeScript("silent")
        channel = SnapshotChannel(script)
        handler = script.handlers[0]
        with self.assertRaises(SnapshotTimeout):
            channel.snapshot(timeout=.02)
        script.reply(script.requests[0])
        channel.close()
        handler({"type": "send", "payload": {"kind": PROTOCOL,
                "requestId": script.requests[0]["requestId"], "snapshot": {"late": True}}}, None)
        self.assertEqual(channel.pending_count, 0)
        self.assertTrue(channel.stale)

    def test_unrelated_wrong_identifier_and_binary_replies_are_ignored(self):
        script = FakeScript("silent")
        channel = SnapshotChannel(script)
        def replies():
            script.posted.wait(.3)
            request = script.requests[0]
            script.reply(request, kind="packet")
            script.reply(request, identifier="wrong")
            script.reply(request, data=b"private bytes are not accepted")
            script.reply(request, snapshot={"safe": True})
        thread = threading.Thread(target=replies)
        thread.start()
        self.assertEqual(channel.snapshot(timeout=.3), {"safe": True})
        thread.join(timeout=.3)
        channel.close()

    def test_post_failure_does_not_expose_native_error_value(self):
        script = FakeScript("raise")
        channel = SnapshotChannel(script)
        with self.assertRaises(SnapshotUnavailable) as context:
            channel.snapshot(timeout=.3)
        self.assertNotIn("PRIVATE", str(context.exception))
        self.assertEqual(channel.pending_count, 0)
        self.assertTrue(channel.stale)
        channel.close()

    def test_blocked_post_does_not_block_deadline_or_late_cleanup(self):
        script = FakeScript("blocked-post")
        channel = SnapshotChannel(script)
        started = time.monotonic()
        with self.assertRaises(SnapshotTimeout):
            channel.snapshot(timeout=.03)
        self.assertLess(time.monotonic() - started, .5)
        self.assertEqual(channel.pending_count, 0)
        channel.close()
        script.release.set()

    def test_close_wakes_pending_snapshot(self):
        script = FakeScript("silent")
        channel = SnapshotChannel(script)
        failures = []
        def call():
            try:
                channel.snapshot(timeout=2)
            except SnapshotUnavailable:
                failures.append(True)
        thread = threading.Thread(target=call)
        thread.start()
        self.assertTrue(script.posted.wait(.3))
        channel.close()
        thread.join(timeout=.3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [True])
        self.assertEqual(channel.pending_count, 0)

    def test_slow_listener_removal_has_bounded_close(self):
        script = FakeScript("blocked-off")
        channel = SnapshotChannel(script)
        started = time.monotonic()
        self.assertFalse(channel.close(timeout=.02))
        self.assertLess(time.monotonic() - started, .5)
        self.assertTrue(channel.stale)
        self.assertFalse(channel.close(timeout=.01))
        script.release.set()
        self.assertTrue(channel.close(timeout=.3))

    def test_malformed_matching_reply_marks_channel_stale(self):
        script = FakeScript("silent")
        channel = SnapshotChannel(script)
        def reply():
            script.posted.wait(.3)
            script.reply(script.requests[0], snapshot=[])
        thread = threading.Thread(target=reply)
        thread.start()
        with self.assertRaises(SnapshotUnavailable):
            channel.snapshot(timeout=.3)
        thread.join(timeout=.3)
        self.assertTrue(channel.stale)
        self.assertEqual(channel.pending_count, 0)
        channel.close()

    def test_concurrent_snapshot_is_rejected_without_corrupting_first(self):
        script = FakeScript("silent")
        channel = SnapshotChannel(script)
        replies = []
        thread = threading.Thread(target=lambda: replies.append(channel.snapshot(timeout=.5)))
        thread.start()
        self.assertTrue(script.posted.wait(.3))
        with self.assertRaises(SnapshotUnavailable):
            channel.snapshot(timeout=.1)
        script.reply(script.requests[0], snapshot={"first": True})
        thread.join(timeout=.3)
        self.assertEqual(replies, [{"first": True}])
        self.assertFalse(channel.stale)
        channel.close()


if __name__ == "__main__":
    unittest.main()
