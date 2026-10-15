"""Native pagination provenance is independent of the frontend's end flags."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from chat_export_service import ChatExportService


@unittest.skipUnless(os.name == "nt", "Windows export workspace")
class HistoryProofTests(unittest.TestCase):
    def setUp(self):
        parent = Path("D:/ValMAX/analysis/chat-export-service-synthetic")
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.service = ChatExportService()
        response = self.service.execute("start", {"schema": 1, "accountId": "123", "directory": Path(self.temp.name).as_uri(),
            "chat": {"id": "456", "title": "Синтетическая история", "timezoneOffsetMinutes": 0}})
        self.body = {"schema": 1, "jobId": response["jobId"], "jobToken": response["jobToken"]}
        self.common = {"kind": "chat-export-history", "jobId": response["jobId"]}
        self.job = self.service.jobs[response["jobId"]]

    def tearDown(self):
        self.service.close(); self.temp.cleanup()

    def bound(self, pid=999):
        self.service.observe_history(pid, {**self.common, "event": "bound"})

    def page(self, pid=999, **changes):
        event = {**self.common, "event": "page", "direction": "center", "count": 1, "pendingDone": True,
                 "moreBackward": False, "moreForward": False, **changes}
        self.service.observe_history(pid, event)

    def history(self): return self.service.execute("history", self.body)["history"]

    def test_page_without_marked_view_binding_cannot_claim_completeness(self):
        self.page()
        self.assertEqual(self.history(), {"ready": False, "backward": False, "forward": False})

    def test_empty_terminal_response_may_be_failure_and_never_proves_end(self):
        self.bound(); self.page(count=0)
        self.assertEqual(self.history(), {"ready": True, "backward": False, "forward": False})

    def test_directional_positive_terminal_pages_are_required(self):
        self.bound()
        self.page(direction="backward", moreForward=True)
        self.assertEqual(self.history(), {"ready": True, "backward": True, "forward": False})
        self.page(direction="forward", pendingDone=False)
        self.assertFalse(self.history()["forward"])
        self.page(direction="forward")
        self.assertTrue(all(self.history().values()))
        self.page(direction="backward", moreBackward=True)
        self.assertFalse(self.history()["backward"])

    def test_wrong_pid_unknown_job_extra_fields_and_wrong_types_are_ignored(self):
        self.bound()
        self.page(pid=1000)
        self.page(jobId="f" * 32)
        self.page(messageText="SYNTHETIC_ONLY")
        self.page(count=True)
        self.page(moreBackward=0)
        self.assertEqual(self.history(), {"ready": True, "backward": False, "forward": False})

    def test_frontend_success_without_native_proof_is_downgraded(self):
        self.service.execute("finish", {**self.body, "completion": {"historyComplete": True, "stopReason": "end_of_history", "pagesFetched": 1}})
        self.job.worker.join(timeout=5)
        self.assertFalse(self.job.worker.is_alive())
        result = self.job.snapshot()
        self.assertEqual(result["state"], "partial")
        manifest = json.loads((Path(result["htmlPath"]).parent / "manifest.json").read_text("utf8"))
        self.assertIs(manifest["historyComplete"], False)
        self.assertEqual(manifest["stopReason"], "unsupported_history")


if __name__ == "__main__": unittest.main()
