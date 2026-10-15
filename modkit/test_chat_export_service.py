"""Synthetic coordinator regressions; no MAX process, profile or network access."""
from __future__ import annotations

from contextlib import contextmanager
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

import chat_export_service as service
import chat_export_storage as storage


CHAT = {"id": "-9007199254740993", "title": "Искусственный чат", "timezoneOffsetMinutes": 180}
COMPLETE = {"historyComplete": True, "stopReason": "end_of_history", "pagesFetched": 1}
PNG = b"\x89PNG\r\n\x1a\nsynthetic-owned-media"


def attachment(identifier="a1", **changes):
    result = {"id": identifier, "name": "Искусственный файл.png", "mime": "image/png",
              "type": "image", "status": "pending", "reason": None}
    result.update(changes)
    return result


def message(identifier="9007199254740993", **changes):
    result = {"id": identifier, "author": {"id": "100", "name": "Искусственный автор"},
              "timestampMs": 1700000000000, "text": "Искусственный пример", "outgoing": False,
              "reply": None, "attachments": []}
    result.update(changes)
    return result


def native_path(value):
    parsed = urlsplit(value)
    if parsed.scheme != "file" or parsed.netloc:
        raise AssertionError("Coordinator returned a non-local synthetic media path")
    text = unquote(parsed.path)
    if os.name == "nt" and text.startswith("/"):
        text = text[1:]
    return Path(text)


class ValidationTests(unittest.TestCase):
    def invalid(self, callback, *args):
        with self.assertRaises(service.ChatExportError) as raised:
            callback(*args)
        self.assertEqual(raised.exception.code, "invalid_request")
        self.assertEqual(str(raised.exception), "invalid_request")

    def test_unknown_action_types_have_finite_error(self):
        coordinator = service.ChatExportService()
        self.addCleanup(coordinator.close)
        for value in ([], {}, None, 0, True, "unknown"):
            with self.subTest(action=value):
                self.invalid(coordinator.execute, value, {"schema": 1})

    def test_unknown_attachment_types_have_finite_error(self):
        for field in ("type", "status", "reason"):
            for bad in ([], {}, 1, True):
                row = message(attachments=[attachment(**{field: bad})])
                with self.subTest(field=field, bad=bad):
                    self.invalid(service.validate_message, row)

    def test_unknown_completion_reason_types_have_finite_error(self):
        for bad in ([], {}, None, 1, True):
            with self.subTest(reason=bad):
                self.invalid(service.validate_completion, {**COMPLETE, "stopReason": bad})

    def test_completion_never_claims_success_without_end_of_history(self):
        for changes in ({"stopReason": "timeout"}, {"historyComplete": False},
                        {"pagesFetched": True}, {"pagesFetched": 20001}, {"extra": "synthetic"}):
            with self.subTest(changes=changes):
                self.invalid(service.validate_completion, {**COMPLETE, **changes})

    def test_identifiers_keep_precision_and_reject_extra_secret_fields(self):
        self.assertEqual(service.validate_message(message())["id"], "9007199254740993")
        self.invalid(service.validate_message, message(id=9007199254740993))
        self.invalid(service.validate_message, {**message(), "authorization": "synthetic-only"})
        self.invalid(service.validate_message, message(text="\ud800"))

    def test_pending_and_unavailable_media_contract(self):
        unavailable = attachment(type="unsupported", status="unavailable", reason="unsupported")
        self.assertEqual(service.validate_message(message(attachments=[unavailable]))["attachments"], [unavailable])
        for item in (attachment(type="unsupported"), attachment(reason="timeout"),
                     attachment(status="unavailable"), attachment(mime="text/html; secret=x")):
            with self.subTest(item=item):
                self.invalid(service.validate_message, message(attachments=[item]))


@unittest.skipUnless(os.name == "nt", "Windows pinned handles and atomic publish")
class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        parent = Path("D:/ValMAX/analysis/chat-export-service-synthetic")
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="case-", dir=parent)
        self.root = Path(self.temporary.name)
        self.sentinel = self.root / "unrelated.txt"
        self.sentinel.write_text("synthetic sentinel", encoding="utf8")
        self.coordinator = service.ChatExportService()
        self.threads = []

    def tearDown(self):
        for thread in self.threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive(), "Synthetic test worker did not stop")
        self.coordinator.close()
        self.temporary.cleanup()

    def start(self, **changes):
        request = {"schema": 1, "accountId": "9223372036854775807", "chat": copy.deepcopy(CHAT),
                   "directory": self.root.as_uri()}
        request.update(changes)
        response = self.coordinator.execute("start", request)
        self.credentials = {"schema": 1, "jobId": response["jobId"], "jobToken": response["jobToken"]}
        self.job = self.coordinator.jobs[response["jobId"]]
        return response

    def execute(self, action, **changes):
        return self.coordinator.execute(action, {**self.credentials, **changes})

    def page(self, rows, sequence=0):
        return self.execute("page", sequence=sequence, messages=rows)

    def ack(self, item, status="ready", reason=None):
        return self.execute("media", messageId=item["messageId"], attachmentId=item["attachmentId"],
                            status=status, reason=reason)

    def chunk(self, item, offset, data):
        return self.execute("media-chunk", messageId=item["messageId"], attachmentId=item["attachmentId"],
                            offset=offset, data=base64.b64encode(data).decode("ascii"))

    def wait(self):
        worker = self.job.worker
        if worker is not None:
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive(), "Synthetic render did not stop")
        return self.job.snapshot()

    def assert_error(self, codes, callback, *args, **kwargs):
        if isinstance(codes, str): codes = (codes,)
        with self.assertRaises(service.ChatExportError) as raised:
            callback(*args, **kwargs)
        self.assertIn(raised.exception.code, codes)
        self.assertEqual(str(raised.exception), raised.exception.code)
        return raised.exception.code

    def assert_private_cleanup(self):
        self.assertEqual(list(self.root.glob(".valmax-index-*")), [])
        self.assertEqual(list(self.root.glob(".valmax-export-*")), [])
        self.assertEqual(self.sentinel.read_text("utf8"), "synthetic sentinel")

    def finish(self, **changes):
        completion = {**COMPLETE, **changes}
        if completion["historyComplete"]:
            # Synthetic native callback provenance, separate from UI booleans.
            common = {"kind": "chat-export-history", "jobId": self.job.id}
            self.coordinator.observe_history(999, {**common, "event": "bound"})
            self.coordinator.observe_history(999, {**common, "event": "page", "direction": "center", "count": 1,
                "pendingDone": True, "moreBackward": False, "moreForward": False})
        self.execute("finish", completion=completion)
        return self.wait()

    def test_start_token_binding_and_single_live_job(self):
        response = self.start()
        self.assertEqual(len(response["jobToken"]), 64)
        self.assertEqual(response["state"], "exporting")
        self.assertEqual(response["limits"], service.LIMITS)
        request = {**self.credentials, "jobToken": "0" * 64}
        self.assert_error("not_found", self.coordinator.execute, "status", request)
        self.assert_error("busy", self.start)
        self.assertEqual(len(self.coordinator.jobs), 1)

    def test_selected_directory_must_be_explicit_existing_local_folder(self):
        for value in ("https://invalid.example/export", "file://server/share", self.root.as_uri() + "?x=1",
                      (self.root / "absent").as_uri(), self.sentinel.as_uri()):
            with self.subTest(value=value):
                self.assert_error("unsafe_path", self.start, directory=value)
        self.assertEqual(list(self.root.iterdir()), [self.sentinel])

    def test_account_id_is_canonical_not_number_or_overflow(self):
        for account in (0, "0", "01", "-1", "9223372036854775808", "1.0", "1 "):
            with self.subTest(account=account):
                self.assert_error("invalid_request", self.start, accountId=account)
        self.assertEqual(list(self.root.iterdir()), [self.sentinel])

    def test_same_page_retry_is_idempotent_and_stable_media_path(self):
        self.start()
        rows = [message(attachments=[attachment()])]
        first = self.page(rows)
        second = self.page(copy.deepcopy(rows))
        self.assertEqual(first, second)
        self.assertEqual(self.job.snapshot()["counts"]["messages"], 1)
        self.assertEqual(self.job.snapshot()["counts"]["attachments"], 1)
        self.assertEqual(self.job.sequence, 1)
        self.assertTrue(native_path(first["media"][0]["nativePathFileURL"]).is_relative_to(self.job.workspace.staging))
        self.assertEqual(list((self.job.workspace.staging / "media").iterdir()), [])

    def test_changed_or_out_of_order_retry_is_rejected_without_rows(self):
        self.start()
        self.page([message("1")])
        self.assert_error("sequence_conflict", self.page, [message("2")], 0)
        self.assert_error("sequence_conflict", self.page, [message("3")], 2)
        self.assertEqual(self.job.snapshot()["counts"]["messages"], 1)
        self.assertEqual(self.job.sequence, 1)

    def test_overlapping_pages_deduplicate_in_sqlite_and_render_chronologically(self):
        self.start()
        self.page([message("3", timestampMs=3000), message("2", timestampMs=2000)])
        self.page([message("2", timestampMs=2000), message("1", timestampMs=1000)], 1)
        self.page([message("1", timestampMs=1000)], 2)
        self.assertEqual(self.job.snapshot()["counts"]["messages"], 3)
        self.assertEqual(self.job.db.execute("SELECT count(*) FROM messages").fetchone()[0], 3)
        result = self.finish(pagesFetched=3)
        self.assertEqual(result["state"], "complete")
        content = Path(result["htmlPath"]).read_text("utf8")
        positions = [content.index('id="m' + hashlib.sha256(value.encode()).hexdigest()) for value in ("1", "2", "3")]
        self.assertEqual(positions, sorted(positions))
        self.assert_private_cleanup()

    def test_duplicate_id_within_first_page_is_finite_and_atomic(self):
        self.start()
        self.assert_error("invalid_request", self.page, [message("1"), message("1")])
        self.assertEqual(self.job.db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(self.job.sequence, 0)
        self.assertEqual(self.job.snapshot()["state"], "exporting")

    def test_declared_page_byte_limit_is_enforced_before_sqlite_mutation(self):
        self.start()
        rows = [message(str(index), text="я" * 65536) for index in range(17)]
        self.assertGreater(len(json.dumps(rows, ensure_ascii=False).encode("utf8")), service.LIMITS["maxPageBytes"])
        self.assert_error(("limit_reached", "invalid_request"), self.page, rows)
        self.assertEqual(self.job.sequence, 0)
        self.assertEqual(self.job.db.execute("SELECT count(*) FROM messages").fetchone()[0], 0)
        self.assertEqual(self.job.snapshot()["counts"]["messages"], 0)

    def test_ready_media_ack_hash_and_retry_are_exactly_once(self):
        self.start()
        item = self.page([message(attachments=[attachment(name="../CON.exe")])])["media"][0]
        path = native_path(item["nativePathFileURL"])
        self.assertNotIn("CON", path.name)
        path.write_bytes(PNG)
        first, retry = self.ack(item), self.ack(item)
        self.assertEqual(first, retry)
        self.assertEqual(first["counts"]["ready"], 1)
        self.assertEqual(first["counts"]["mediaBytes"], len(PNG))
        self.assert_error("invalid_state", self.ack, item, "error", "download_failed")
        result = self.finish()
        self.assertEqual(result["state"], "complete")
        published = self.job.destination / item["source"]
        self.assertEqual(published.read_bytes(), PNG)
        manifest = json.loads((self.job.destination / "assets.jsonl").read_text("utf8"))
        self.assertEqual(manifest["sha256"], hashlib.sha256(PNG).hexdigest())
        self.assert_private_cleanup()

    def test_pending_media_cannot_be_silently_finished(self):
        self.start()
        self.page([message(attachments=[attachment()])])
        self.assert_error("invalid_state", self.execute, "finish", completion=COMPLETE)
        self.assertEqual(self.job.snapshot()["state"], "exporting")
        self.assertIsNone(self.job.worker)

    def test_failed_media_ack_keeps_partial_export_and_discards_partial_file(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        path = native_path(item["nativePathFileURL"])
        path.write_bytes(b"synthetic incomplete download")
        first, retry = self.ack(item, "error", "download_failed"), self.ack(item, "error", "download_failed")
        self.assertEqual(first, retry)
        self.assertEqual(first["counts"]["unavailable"], 1)
        result = self.finish()
        self.assertEqual(result["state"], "partial")
        self.assertFalse((self.job.destination / item["source"]).exists())
        omitted = json.loads((self.job.destination / "omissions.jsonl").read_text("utf8"))
        self.assertEqual(omitted["reason"], "download_failed")
        self.assert_private_cleanup()

    def test_unsupported_media_is_honest_partial_result_without_native_path(self):
        self.start()
        result = self.page([message(attachments=[attachment(type="unsupported", status="unavailable", reason="unsupported")])])
        self.assertEqual(result["media"], [])
        snapshot = self.finish()
        self.assertEqual(snapshot["state"], "partial")
        self.assertEqual(snapshot["counts"]["unavailable"], 1)
        self.assert_private_cleanup()

    def test_unknown_media_ack_reason_is_finite_and_keeps_job_pending(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        for bad in ([], {}, None, 1):
            with self.subTest(reason=bad):
                self.assert_error("invalid_request", self.ack, item, "error", bad)
        self.assertEqual(self.job.snapshot()["counts"]["unavailable"], 0)
        self.assertEqual(self.job.snapshot()["state"], "exporting")

    def test_audio_chunks_are_bounded_sequential_and_retry_safe(self):
        self.start()
        item = self.page([message(attachments=[attachment(type="voice", mime="audio/ogg")])])["media"][0]
        first_data = b"OggS" + b"x" * (48 * 1024 - 4)
        first = self.chunk(item, 0, first_data)
        self.assertEqual(first["offset"], 48 * 1024)
        self.assertEqual(self.chunk(item, 0, first_data), first)
        final = self.chunk(item, first["offset"], b"synthetic tail")
        self.assertEqual(final["offset"], len(first_data) + len(b"synthetic tail"))
        local = native_path(item["nativePathFileURL"])
        self.assertEqual(local.read_bytes(), first_data + b"synthetic tail")
        self.assertEqual(self.job.snapshot()["counts"]["ready"], 0)
        self.ack(item)
        result = self.finish()
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["counts"]["mediaBytes"], final["offset"])
        self.assertIn("<audio ", Path(result["htmlPath"]).read_text("utf8"))
        self.assert_private_cleanup()

    def test_chunk_conflicting_retry_or_gap_never_changes_file(self):
        self.start()
        item = self.page([message(attachments=[attachment(type="audio", mime="audio/mpeg")])])["media"][0]
        data = b"ID3synthetic"
        self.chunk(item, 0, data)
        path = native_path(item["nativePathFileURL"])
        for offset, changed in ((0, b"different"), (len(data) + 1, b"gap"), (1, b"overlap")):
            with self.subTest(offset=offset):
                self.assert_error("sequence_conflict", self.chunk, item, offset, changed)
                self.assertEqual(path.read_bytes(), data)
        self.chunk(item, len(data), b"tail")
        self.assert_error("sequence_conflict", self.chunk, item, 0, data)
        self.assertEqual(path.read_bytes(), data + b"tail")

    def test_malformed_chunk_base64_and_offset_have_finite_errors(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        for data in ("", "!!!!", "Y Q==", "синтетика", [], {}, b"YQ==", "a" * 65540):
            with self.subTest(data_type=type(data).__name__, length=len(data)):
                self.assert_error("invalid_request", self.execute, "media-chunk", messageId=item["messageId"],
                                  attachmentId=item["attachmentId"], offset=0, data=data)
        for offset in (-1, True, 0.0, [], None):
            with self.subTest(offset=offset):
                self.assert_error("invalid_request", self.chunk, item, offset, b"synthetic")
        self.assertFalse(native_path(item["nativePathFileURL"]).exists())

    def test_chunk_size_limit_is_checked_before_file_creation(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        self.assert_error(("invalid_request", "limit_reached"), self.chunk, item, 0,
                          b"x" * (service.LIMITS["maxChunkBytes"] + 1))
        self.assertFalse(native_path(item["nativePathFileURL"]).exists())
        self.assertEqual(self.job.snapshot()["state"], "exporting")

    def test_total_chunk_cap_rejects_overflow_without_appending(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        with patch.dict(service.LIMITS, maxAudioBytes=8):
            self.chunk(item, 0, b"123456")
            self.assert_error("limit_reached", self.chunk, item, 6, b"789")
            self.assertEqual(native_path(item["nativePathFileURL"]).read_bytes(), b"123456")
            self.chunk(item, 6, b"78")
            self.assert_error("invalid_request", self.chunk, item, 8, b"9")
        self.assertEqual(native_path(item["nativePathFileURL"]).read_bytes(), b"12345678")

    def test_chunk_must_target_pending_reserved_asset(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        wrong = {**item, "attachmentId": "missing-synthetic"}
        self.assert_error("invalid_state", self.chunk, wrong, 0, b"test")
        self.chunk(item, 0, PNG)
        self.ack(item)
        self.assert_error("invalid_state", self.chunk, item, len(PNG), b"extra")
        self.assertEqual(native_path(item["nativePathFileURL"]).read_bytes(), PNG)

    def test_chunk_cannot_reuse_other_job_token_or_cancelled_reservation(self):
        self.start()
        old_credentials = dict(self.credentials)
        old_item = self.page([message(attachments=[attachment()])])["media"][0]
        self.execute("cancel")
        self.assert_error("invalid_state", self.chunk, old_item, 0, b"old")
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        request = {**self.credentials, "jobToken": old_credentials["jobToken"],
                   "messageId": item["messageId"], "attachmentId": item["attachmentId"],
                   "offset": 0, "data": "dGVzdA=="}
        self.assert_error("not_found", self.coordinator.execute, "media-chunk", request)
        self.assertFalse(native_path(item["nativePathFileURL"]).exists())
        self.assertFalse(native_path(old_item["nativePathFileURL"]).exists())

    def test_chunk_write_failure_cleans_owned_stage_with_finite_code(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        with patch.object(self.job.workspace, "write_asset_chunk", side_effect=OSError(28, "synthetic disk full")):
            self.assert_error("insufficient_space", self.chunk, item, 0, b"synthetic")
        result = self.job.snapshot()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "insufficient_space")
        self.assert_private_cleanup()

    def test_cancellation_interrupts_chunk_writer_and_removes_partial_data(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        self.chunk(item, 0, b"partial")
        entered, saw_cancel = threading.Event(), threading.Event()
        errors = []
        def writer(*args, **kwargs):
            entered.set()
            if self.job.cancel_event.wait(1): saw_cancel.set()
            raise storage.ChatExportStorageError("cancelled")
        def append():
            try: self.chunk(item, len(b"partial"), b"continuation")
            except service.ChatExportError as error: errors.append(error.code)
        with patch.object(self.job.workspace, "write_asset_chunk", side_effect=writer):
            incoming = threading.Thread(target=append, daemon=True)
            self.threads.append(incoming)
            incoming.start()
            self.assertTrue(entered.wait(1))
            self.execute("cancel")
            incoming.join(timeout=3)
            self.assertTrue(saw_cancel.is_set())
        self.assertEqual(errors, ["cancelled"])
        self.assertEqual(self.job.snapshot()["state"], "cancelled")
        self.assert_private_cleanup()

    def test_asset_disappearing_after_ack_never_reports_complete(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        path = native_path(item["nativePathFileURL"])
        path.write_bytes(PNG)
        self.ack(item)
        path.unlink()
        result = self.finish()
        self.assertEqual(result["state"], "partial")
        self.assertEqual(result["counts"]["unavailable"], 1)
        self.assertEqual(result["counts"]["ready"], 0)
        self.assertEqual(result["counts"]["mediaBytes"], 0)
        self.assert_private_cleanup()

    def test_cancellation_before_render_cleans_only_owned_entries(self):
        self.start()
        self.page([message()])
        first = self.execute("cancel")
        self.assertEqual(first["state"], "cancelled")
        self.assertEqual(self.execute("cancel"), first)
        self.assertFalse(self.job.destination.exists())
        self.assert_private_cleanup()

    def test_cancel_during_async_render_prevents_publish_and_cleans_after_worker(self):
        entered, release = threading.Event(), threading.Event()
        def renderer(workspace, chat, messages, completion):
            list(messages)
            with workspace.open_text("index.html") as stream:
                stream.write("synthetic render in progress")
                entered.set()
                if not release.wait(3): raise RuntimeError("synthetic timeout")
        self.coordinator.renderer = renderer
        self.start()
        self.page([message()])
        self.execute("finish", completion=COMPLETE)
        try:
            self.assertTrue(entered.wait(2))
            self.execute("cancel")
            self.assertTrue(self.job.cancel_event.is_set())
            self.assertFalse(self.job.destination.exists())
        finally:
            release.set()
        result = self.wait()
        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(self.job.destination.exists())
        self.assert_private_cleanup()

    def test_cancel_can_interrupt_asset_verification_without_global_lock_deadlock(self):
        self.start()
        item = self.page([message(attachments=[attachment()])])["media"][0]
        entered, saw_cancel, release = threading.Event(), threading.Event(), threading.Event()
        errors = []
        def verifier(*args, **kwargs):
            entered.set()
            if self.job.cancel_event.wait(1):
                saw_cancel.set()
                raise storage.ChatExportStorageError("cancelled")
            release.wait(2)
            return {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
        def acknowledge():
            try: self.ack(item)
            except service.ChatExportError as error: errors.append(error.code)
        def cancel():
            try: self.execute("cancel")
            except service.ChatExportError as error: errors.append(error.code)
        with patch.object(self.job.workspace, "verify_asset", side_effect=verifier):
            incoming = threading.Thread(target=acknowledge, daemon=True)
            cancelling = threading.Thread(target=cancel, daemon=True)
            self.threads.extend((incoming, cancelling))
            incoming.start()
            try:
                self.assertTrue(entered.wait(1))
                cancelling.start()
                self.assertTrue(saw_cancel.wait(0.7), "Cancel was blocked behind verification locks")
            finally:
                release.set()
                incoming.join(timeout=4)
                if cancelling.ident is not None: cancelling.join(timeout=4)
        self.assertEqual(self.job.snapshot()["state"], "cancelled")
        self.assert_private_cleanup()

    def test_worker_start_failure_has_finite_code_and_no_stuck_rendering(self):
        self.start()
        self.page([message()])
        with patch.object(service.threading.Thread, "start", side_effect=RuntimeError("synthetic thread failure")):
            self.assert_error("io_error", self.execute, "finish", completion=COMPLETE)
        self.assertEqual(self.job.snapshot()["state"], "failed")
        self.assertFalse(self.coordinator.pending())
        self.coordinator.close()
        self.assert_private_cleanup()

    def test_renderer_failure_cleans_partial_output_and_never_echoes_data(self):
        def renderer(workspace, chat, messages, completion):
            list(messages)
            with workspace.open_text("index.html") as stream: stream.write("synthetic partial")
            raise RuntimeError("synthetic diagnostic must not escape")
        self.coordinator.renderer = renderer
        self.start()
        self.page([message()])
        result = self.finish()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "io_error")
        self.assertNotIn("synthetic diagnostic", json.dumps(result))
        self.assertNotIn("htmlPath", result)
        self.assertFalse(self.job.destination.exists())
        self.assert_private_cleanup()

    def test_competing_output_directory_is_preserved(self):
        self.start()
        self.page([message()])
        self.job.destination.mkdir()
        keep = self.job.destination / "keep.txt"
        keep.write_text("synthetic competing destination", encoding="utf8")
        result = self.finish()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(keep.read_text("utf8"), "synthetic competing destination")
        self.assert_private_cleanup()

    def test_idle_job_reaped_and_owned_files_removed(self):
        self.start()
        self.job.updated = time.monotonic() - 901
        self.assertFalse(self.coordinator.pending())
        self.assertEqual(self.job.snapshot()["state"], "cancelled")
        self.assert_private_cleanup()

    def test_sqlite_cleanup_keeps_selected_parent_pinned_until_last_io(self):
        original_hold = service._hold_directory
        original_check = service._check_path
        active = []
        checks = []
        @contextmanager
        def held(path):
            with original_hold(path):
                active.append(Path(path))
                try: yield
                finally: active.remove(Path(path))
        def checked(path, *, directory):
            path = Path(path)
            if hasattr(self, "job") and (path == self.job.index_path or path.parent == self.job.index_path):
                checks.append(path)
                self.assertIn(self.root, active, "Selected parent was released before SQLite index cleanup")
            return original_check(path, directory=directory)
        with patch.object(service, "_hold_directory", side_effect=held), \
             patch.object(storage, "_hold_directory", side_effect=held), \
             patch.object(service, "_check_path", side_effect=checked):
            self.start()
            self.execute("cancel")
            self.assertTrue(checks, "Synthetic cleanup observer saw no index checks")
            self.assertEqual(active, [])
        self.assert_private_cleanup()


if __name__ == "__main__":
    unittest.main()
