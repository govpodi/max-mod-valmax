"""Only synthetic user-authored templates in temporary directories."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

import quick_reply_store as module
from quick_reply_store import QuickReplyStore, QuickReplyStoreError


def reply(number=1, **changes):
    result = {"id": f"{number:032x}", "command": "ответ" + str(number),
              "title": "Тестовая заготовка", "text": "Это искусственный пример.\nВторая строка."}
    result.update(changes)
    return result


def _process_writer(path, revision, number, barrier, queue):
    try:
        barrier.wait(timeout=8)
        QuickReplyStore(path).replace(revision, [reply(number)])
        queue.put("saved")
    except QuickReplyStoreError as error:
        queue.put(error.code)
    except Exception:
        queue.put("test_process_failed")


class QuickReplyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="max-quick-replies-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "settings" / "quick-replies.json"
        self.store = QuickReplyStore(self.path)

    def error(self, code, function, *args):
        with self.assertRaises(QuickReplyStoreError) as raised:
            function(*args)
        self.assertEqual(code, raised.exception.code)
        self.assertEqual(code, str(raised.exception))

    def write_raw(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(data)

    def save(self, values=None):
        return self.store.replace(self.store.snapshot()["revision"],
                                  [reply()] if values is None else values)

    def test_constructor_does_not_access_filesystem(self):
        with mock.patch.object(module, "_parents", side_effect=AssertionError("filesystem")):
            QuickReplyStore(self.path)
        self.assertFalse(self.path.parent.exists())

    def test_absent_read_creates_nothing_and_has_stable_revision(self):
        first = self.store.snapshot()
        self.assertEqual({"schema", "revision", "replies"}, set(first))
        canonical = b'{"replies":[],"schema":1}'
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), first["revision"])
        self.assertEqual([], first["replies"])
        self.assertEqual(first, self.store.snapshot())
        self.assertEqual([], list(self.root.iterdir()))

    def test_absent_file_read_with_existing_parent_creates_nothing(self):
        self.path.parent.mkdir()
        self.assertEqual([], self.store.snapshot()["replies"])
        self.assertEqual([], list(self.path.parent.iterdir()))

    def test_round_trip_utf8_multiline_and_revision(self):
        result = self.save()
        self.assertEqual(result, self.store.snapshot())
        raw = self.path.read_bytes()
        self.assertIn("Тестовая".encode(), raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["revision"])
        self.assertEqual({"schema": 1, "replies": [reply()]}, json.loads(raw))
        self.assertEqual([], list(self.path.parent.glob("*.tmp")))

    def test_revision_is_canonical_and_independent_of_json_whitespace(self):
        original = self.save()
        self.path.write_text(json.dumps({"schema": 1, "replies": [reply()]},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(original, self.store.snapshot())

    def test_caller_mutation_does_not_change_stored_templates(self):
        values = [reply()]
        result = self.save(values)
        values[0]["text"] = "Изменено после сохранения"
        result["replies"].clear()
        self.assertEqual([reply()], self.store.snapshot()["replies"])

    def test_stale_revision_preserves_exact_file(self):
        initial = self.store.snapshot()["revision"]
        self.save()
        before = self.path.read_bytes()
        self.error("revision_conflict", self.store.replace, initial, [reply(2)])
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_revisions_do_not_create_parent(self):
        for revision in [None, True, 1, "", "a" * 63, "G" * 64, "A" * 64]:
            with self.subTest(revision=revision):
                self.error("invalid_revision", self.store.replace, revision, [])
        self.assertFalse(self.path.parent.exists())

    def test_clear_all_requires_current_revision(self):
        saved = self.save()
        cleared = self.store.replace(saved["revision"], [])
        self.assertEqual([], cleared["replies"])
        self.assertEqual(cleared, self.store.snapshot())

    def test_invalid_rows_are_rejected_before_any_write(self):
        cases = [None, {}, (), [None], [dict(reply(), secret="forbidden")],
                 [{key: value for key, value in reply().items() if key != "text"}],
                 [reply(id="g" * 32)], [reply(id="A" * 32)], [reply(id="0" * 31)],
                 [reply(title=81 * "а")], [reply(title=None)], [reply(text="")],
                 [reply(text="я" * 5001)], [reply(text=12)], [reply(text="\x00")],
                 [reply(text="\ud800")], [reply(title="\udfff")],
                 [reply(command="/ответ")], [reply(command="Ответ")],
                 [reply(command="answer-now")], [reply(command="1answer")],
                 [reply(command="a" * 33)], [reply(command="x\n")],
                 [reply(1), reply(2, id=reply(1)["id"])],
                 [reply(1), reply(2, command=reply(1)["command"])]]
        for values in cases:
            with self.subTest(case=repr(values)[:90]):
                self.error("invalid_replies", self.store.replace,
                           self.store.snapshot()["revision"], values)
        self.assertFalse(self.path.parent.exists())

    def test_exact_field_limits_and_cyrillic_commands(self):
        values = [reply(command="ё" + "я" * 31, title="т" * 80, text="а" * 5000)]
        self.assertEqual(values, self.save(values)["replies"])

    def test_legacy_read_keeps_exact_file_and_original_revision(self):
        data = module._canonical([reply()], "invalid_store")
        self.write_raw(data)
        result = self.store.snapshot()
        self.assertEqual([reply()], result["replies"])
        self.assertEqual(hashlib.sha256(data).hexdigest(), result["revision"])
        self.assertEqual(data, self.path.read_bytes())

    def test_extended_reply_roundtrip_keeps_legacy_rows_and_deep_copies(self):
        attachment = {"url": "file:///D:/ValMAX/test%20folder/пример.txt", "kind": "file"}
        extended = reply(2, accountId="9223372036854775807", text="", attachments=[attachment],
                         parts=[{"text": "Следующий текст"},
                                {"text": "", "attachments": [{"url": "file:///D:/photos/photo.jpg", "kind": "media"}]}])
        values = [reply(), extended]
        result = self.save(values)
        self.assertEqual(values, self.store.snapshot()["replies"])
        self.assertEqual(1, result["schema"])
        values[1]["attachments"][0]["url"] = "file:///D:/changed.txt"
        result["replies"][1]["parts"][0]["text"] = "Changed"
        saved = self.store.snapshot()["replies"][1]
        self.assertEqual(attachment["url"], "file:///D:/changed.txt")
        self.assertEqual(saved["attachments"][0]["url"], "file:///D:/ValMAX/test%20folder/пример.txt")
        self.assertEqual(saved["parts"][0]["text"], "Следующий текст")

    def test_same_command_is_unique_per_account_and_common_scope(self):
        values = [reply(1, command="away"), reply(2, command="away", accountId="101"),
                  reply(3, command="away", accountId="202")]
        saved = self.save(values)
        self.assertEqual(values, saved["replies"])
        for duplicate in (reply(4, command="away", accountId=""),
                          reply(4, command="away", accountId="101")):
            self.error("invalid_replies", self.store.replace, saved["revision"], values + [duplicate])
        self.assertEqual(values, self.store.snapshot()["replies"])

    def test_account_ids_reject_noncanonical_foreign_types_and_overflow(self):
        for account in (None, 101, True, "0", "01", "-1", "+1", "1.0", " 1", "1\n",
                        "9223372036854775808", "1" * 20):
            with self.subTest(account=account):
                self.error("invalid_replies", self.store.replace, self.store.snapshot()["revision"],
                           [reply(accountId=account)])
        self.assertFalse(self.path.exists())

    def test_file_urls_are_validated_without_target_filesystem_access(self):
        values = [reply(attachments=[{"url": "file:///D:/does-not-exist/report%20final.pdf", "kind": "file"}])]
        # Validation occurs before the store opens its own path. No URL target
        # is dereferenced, including missing files and removable drives.
        with mock.patch.object(module.Path, "stat", side_effect=AssertionError("unexpected file stat")):
            clean = module._validated_replies(values, "invalid_replies")
        self.assertEqual(values, clean)
        self.assertEqual(values, self.save(values)["replies"])

    def test_remote_ambiguous_and_device_attachment_urls_are_rejected(self):
        urls = ("https://example.org/a", "file://server/share/a", "file://localhost/D:/a.txt",
                "file:////server/share/a", "file:///tmp/a", "D:/a.txt", "file:///D:/",
                "file:///D:/../a", "file:///D:/%2e%2e/a", "file:///D:/a\\b", "file:///D:/a%5cb",
                "file:///D:/a.txt:stream", "file:///D:/CON.txt", "file:///D:/a/./b",
                "file:///D:/a?query", "file:///D:/a#fragment", "file:///D:/a%00.txt",
                "file:///D:/a%0A.txt", "file:///D:/%ff.txt", "file:///D:/a%.txt",
                "file:///D:/trailing.", "file:///D:/a%20", "file:///D://a.txt")
        for url in urls:
            with self.subTest(url=url):
                self.error("invalid_replies", self.store.replace, self.store.snapshot()["revision"],
                           [reply(attachments=[{"url": url, "kind": "file"}])])
        self.assertFalse(self.path.exists())

    def test_attachment_limits_types_and_duplicate_decoded_paths(self):
        files = [{"url": f"file:///D:/example/{i}.txt", "kind": "file"} for i in range(10)]
        saved = self.save([reply(text="", attachments=files)])
        self.assertEqual(files, saved["replies"][0]["attachments"])
        bad = ([], files + [{"url": "file:///D:/extra.txt", "kind": "file"}],
               [dict(files[0], kind="url")], [dict(files[0], content="not allowed")],
               [{"url": "file:///D:/a%20b.txt", "kind": "file"}, {"url": "file:///d:/A b.txt", "kind": "media"}],
               [{"url": "file:///D:/" + "a" * 4096, "kind": "file"}])
        for attachments in bad:
            with self.subTest(attachments=str(attachments)[:80]):
                self.error("invalid_replies", self.store.replace, saved["revision"],
                           [reply(attachments=attachments)])

    def test_message_parts_are_bounded_and_each_requires_content(self):
        saved = self.save([reply(parts=[{"text": "x" * 5000} for _ in range(9)])])
        for parts in ([], [{"text": "x"}] * 10, [{"text": ""}], [{"text": "x" * 5001}],
                      [{"text": "ok", "autoSend": True}], [None], "text"):
            with self.subTest(parts=str(parts)[:50]):
                self.error("invalid_replies", self.store.replace, saved["revision"], [reply(parts=parts)])

    def test_extended_change_still_requires_matching_cas_revision(self):
        saved = self.save([reply()])
        extended = reply(accountId="101", parts=[{"text": "Далее"}])
        changed = self.store.replace(saved["revision"], [extended])
        before = self.path.read_bytes()
        self.error("revision_conflict", self.store.replace, saved["revision"], [reply()])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(changed, self.store.snapshot())

    def test_one_hundred_templates_allowed_but_not_one_hundred_one(self):
        saved = self.save([reply(i, text="Тест") for i in range(100)])
        self.assertEqual(100, len(saved["replies"]))
        before = self.path.read_bytes()
        self.error("invalid_replies", self.store.replace, saved["revision"],
                   [reply(i, text="Тест") for i in range(101)])
        self.assertEqual(before, self.path.read_bytes())

    def test_total_size_limit_counts_utf8_bytes(self):
        values = [reply(i, text="я" * 5000) for i in range(30)]
        self.error("invalid_replies", self.store.replace,
                   self.store.snapshot()["revision"], values)
        self.assertFalse(self.path.parent.exists())

    def test_malformed_existing_file_is_never_overwritten(self):
        valid = json.dumps({"schema": 1, "replies": [reply()]}).encode()
        cases = [b"", b"{", b"\xff", b'{"schema":NaN,"replies":[]}',
                 b'{"schema":1,"schema":1,"replies":[]}',
                 b'{"schema":true,"replies":[]}', b'{"schema":2,"replies":[]}',
                 b'{"schema":1,"replies":[],"other":1}', b'[]',
                 valid.replace(b'"text":', b'"text":"duplicate", "text":'),
                 b'{"schema":1,"replies":[]} trailing',
                 b" " * (module.MAX_STORE_BYTES + 1)]
        for raw in cases:
            with self.subTest(length=len(raw)):
                self.write_raw(raw)
                self.error("invalid_store", self.store.snapshot)
                self.error("invalid_store", self.store.replace, "0" * 64, [])
                self.assertEqual(raw, self.path.read_bytes())

    def test_recursion_and_huge_integer_fail_with_fixed_code(self):
        for raw in [b"[" * 3000 + b"]" * 3000,
                    b'{"schema":' + b"9" * 5000 + b',"replies":[]}']:
            self.write_raw(raw)
            self.error("invalid_store", self.store.snapshot)

    def test_regular_directory_cannot_be_template_file(self):
        self.path.mkdir(parents=True)
        self.error("unsafe_path", self.store.snapshot)
        self.error("unsafe_path", self.store.replace, "0" * 64, [])

    def test_hard_link_is_rejected_without_modifying_either_name(self):
        target = self.root / "synthetic-target.json"
        target.write_bytes(b"synthetic private test file")
        self.path.parent.mkdir()
        os.link(target, self.path)
        self.error("unsafe_path", self.store.snapshot)
        self.error("unsafe_path", self.store.replace, "0" * 64, [])
        self.assertEqual(b"synthetic private test file", target.read_bytes())

    def test_symlink_parent_is_rejected(self):
        target = self.root / "synthetic-target"
        target.mkdir()
        try:
            self.path.parent.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is unavailable for this test process")
        self.error("unsafe_path", self.store.snapshot)
        self.error("unsafe_path", self.store.replace, "0" * 64, [])
        self.assertEqual([], list(target.iterdir()))

    @unittest.skipUnless(os.name == "nt", "Windows junction guard")
    def test_windows_junction_parent_is_rejected(self):
        target = self.root / "synthetic-target"
        target.mkdir()
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(self.path.parent), str(target)],
                                capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            self.skipTest("Creating a test junction is unavailable")
        try:
            self.error("unsafe_path", self.store.snapshot)
            self.error("unsafe_path", self.store.replace, "0" * 64, [])
            self.assertEqual([], list(target.iterdir()))
        finally:
            self.assertTrue(self.path.parent.is_relative_to(self.root))
            os.rmdir(self.path.parent)  # Remove only our junction, never its target.

    @unittest.skipUnless(os.name == "nt", "Windows directory handle sharing")
    def test_pinned_parent_allows_atomic_child_replace_but_refuses_parent_rename(self):
        self.path.parent.mkdir()
        destination = self.root / "renamed-settings"
        with module._parents(self.path, create=False) as parent:
            with self.assertRaises(PermissionError):
                self.path.parent.rename(destination)
            temporary = parent / "synthetic.tmp"
            temporary.write_bytes(b"synthetic")
            os.replace(temporary, parent / "synthetic.json")
            self.assertEqual(b"synthetic", (parent / "synthetic.json").read_bytes())
        self.assertFalse(destination.exists())

    def test_unsafe_lexical_paths_are_rejected(self):
        values = ["relative.json", self.root / ".." / "escape.json",
                  str(self.root) + os.sep + "." + os.sep + "x.json",
                  str(self.root / "x.json") + " ", str(self.root / "x.json") + "\x00"]
        if os.name == "nt":
            values += [str(self.root / "x.json:stream"), str(self.root / "NUL"),
                       "\\\\localhost\\share\\quick-replies.json"]
        for value in values:
            with self.subTest(value=value):
                self.error("unsafe_path", QuickReplyStore, value)

    def test_replace_failure_preserves_original_and_cleans_temp(self):
        saved = self.save()
        before = self.path.read_bytes()
        with mock.patch.object(module.os, "replace", side_effect=PermissionError("sensitive path")):
            self.error("io_error", self.store.replace, saved["revision"], [reply(2)])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob("*.tmp")))

    def test_fsync_failure_preserves_original_and_cleans_temp(self):
        saved = self.save()
        before = self.path.read_bytes()
        with mock.patch.object(module.os, "fsync", side_effect=OSError("sensitive payload")):
            self.error("io_error", self.store.replace, saved["revision"], [reply(2)])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob("*.tmp")))

    def test_exclusive_temp_creation_never_clobbers_existing_file(self):
        saved = self.save()
        before = self.path.read_bytes()
        temporary = self.path.with_name("." + self.path.name + "." + "a" * 32 + ".tmp")
        temporary.write_bytes(b"existing synthetic temporary file")
        with mock.patch.object(module.uuid, "uuid4", return_value=mock.Mock(hex="a" * 32)):
            self.error("io_error", self.store.replace, saved["revision"], [reply(2)])
        self.assertEqual(b"existing synthetic temporary file", temporary.read_bytes())
        self.assertEqual(before, self.path.read_bytes())

    def test_file_modified_during_write_is_revalidated_before_replace(self):
        saved = self.save()
        def corrupt(_fd):
            self.path.write_bytes(b"malformed synthetic file")
        with mock.patch.object(module.os, "fsync", side_effect=corrupt):
            self.error("invalid_store", self.store.replace, saved["revision"], [reply(2)])
        self.assertEqual(b"malformed synthetic file", self.path.read_bytes())
        self.assertEqual([], list(self.path.parent.glob("*.tmp")))

    def test_concurrent_instances_allow_only_one_matching_revision_write(self):
        revision = self.save([])["revision"]
        barrier, outcomes = threading.Barrier(2), []
        def writer(number):
            barrier.wait(timeout=5)
            try:
                QuickReplyStore(self.path).replace(revision, [reply(number)])
                outcomes.append("saved")
            except QuickReplyStoreError as error:
                outcomes.append(error.code)
        threads = [threading.Thread(target=writer, args=(i,)) for i in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=8)
            self.assertFalse(thread.is_alive())
        self.assertCountEqual(["saved", "revision_conflict"], outcomes)
        self.assertEqual(1, len(self.store.snapshot()["replies"]))

    def test_cross_process_writers_allow_only_one_matching_revision_write(self):
        revision = self.save([])["revision"]
        ctx = multiprocessing.get_context("spawn")
        barrier, queue = ctx.Barrier(2), ctx.Queue()
        processes = [ctx.Process(target=_process_writer,
                                 args=(str(self.path), revision, i, barrier, queue)) for i in (1, 2)]
        try:
            for process in processes:
                process.start()
            outcomes = [queue.get(timeout=15), queue.get(timeout=15)]
            self.assertCountEqual(["saved", "revision_conflict"], outcomes)
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(0, process.exitcode)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            queue.close()

    def test_error_codes_are_fixed_and_do_not_accept_arbitrary_messages(self):
        with self.assertRaises(ValueError):
            QuickReplyStoreError("arbitrary template content")
        self.assertEqual("invalid_store", str(QuickReplyStoreError("invalid_store")))


if __name__ == "__main__":
    unittest.main()
