"""A real open synthetic file must survive cancel until its writer finishes."""
import os
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlsplit, unquote

from chat_export_service import ChatExportService, ChatExportError


@unittest.skipUnless(os.name == "nt", "Windows file sharing and directory guards")
class NativeTransferCancelTests(unittest.TestCase):
    def setUp(self):
        parent = Path("D:/ValMAX/analysis/chat-export-service-synthetic")
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.directory = Path(self.temp.name)
        self.activity = (2, True)
        self.service = ChatExportService(activity=lambda: self.activity)
        self.started = []

    def tearDown(self):
        self.activity = (0, True)
        self.service.close()
        self.temp.cleanup()

    def start_transfer(self):
        start = self.service.execute("start", {"schema": 1, "accountId": "123", "directory": self.directory.as_uri(),
            "chat": {"id": "456", "title": "Искусственный чат", "timezoneOffsetMinutes": 0}})
        job = {"schema": 1, "jobId": start["jobId"], "jobToken": start["jobToken"]}
        row = {"id": "789", "author": {"id": "123", "name": "Тест"}, "timestampMs": 1,
               "text": "Синтетический пример", "outgoing": True, "reply": None,
               "attachments": [{"id": "a", "name": "test.png", "mime": "image/png", "type": "image", "status": "pending", "reason": None}]}
        response = self.service.execute("page", {**job, "sequence": 0, "messages": [row]})
        media = {**job, "messageId": "789", "attachmentId": "a"}
        self.service.execute("media-start", media)
        parsed = urlsplit(response["media"][0]["nativePathFileURL"])
        path = Path(unquote(parsed.path)[1:])
        self.started.append((job, media, path))
        return job, media, path

    def test_cancel_does_not_remove_a_live_native_writer_directory(self):
        job, media, path = self.start_transfer()
        with path.open("wb") as writer:
            writer.write(b"synthetic partial"); writer.flush()
            state = self.service.execute("cancel", job)
            self.assertEqual(state["state"], "cancelled")
            self.assertIs(state["cleanupPending"], True)
            self.assertTrue(path.exists())
            self.assertTrue(self.service.pending())
            writer.write(b" completed"); writer.flush()
        state = self.service.execute("media", {**media, "status": "cancelled", "reason": "cancelled"})
        self.assertEqual(state["state"], "cancelled")
        self.assertNotIn("cleanupPending", state)
        self.assertFalse(path.parent.parent.exists())
        self.assertFalse(self.service.pending())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_only_confirmed_process_exit_can_replace_a_lost_callback(self):
        job, _, path = self.start_transfer()
        path.write_bytes(b"synthetic")
        self.service.execute("cancel", job)
        self.activity = (0, False)
        self.assertTrue(self.service.pending()); self.assertTrue(path.exists())
        self.activity = (1, True)
        self.assertTrue(self.service.pending()); self.assertTrue(path.exists())
        self.activity = (0, True)
        self.assertFalse(self.service.pending()); self.assertFalse(path.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_ready_callback_after_cancel_cleans_without_publishing(self):
        job, media, path = self.start_transfer()
        self.service.execute("cancel", job)
        path.write_bytes(b"synthetic completed native file")
        state = self.service.execute("media", {**media, "status": "ready", "reason": None})
        self.assertEqual(state["state"], "cancelled")
        self.assertNotIn("htmlPath", state)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_foreign_callback_cannot_release_active_transfer(self):
        job, media, path = self.start_transfer()
        self.service.execute("cancel", job)
        with self.assertRaises(ChatExportError):
            self.service.execute("media", {**media, "attachmentId": "other", "status": "cancelled", "reason": "cancelled"})
        self.assertTrue(self.service.pending()); self.assertTrue(path.parent.exists())


if __name__ == "__main__": unittest.main()
