"""Export headroom regression: synthetic files/credentials, no MAX or WinCred."""
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import mdata_archive
import mdata_files
import mdata_login
import mdata_service
import test_mdata_service as fixtures


class ExportCapacityTests(unittest.TestCase):
    setUp = fixtures.MDataServiceTests.setUp
    profile = fixtures.MDataServiceTests.profile
    service = fixtures.MDataServiceTests.service
    job = fixtures.MDataServiceTests.job
    file_values = staticmethod(fixtures.MDataServiceTests.file_values)
    decode = staticmethod(fixtures.MDataServiceTests.decode)

    def assert_early_refusal(self, profile_free, output_free):
        root, memory = self.profile("early-capacity", ["42"])
        output = self.base / "early.mdata"
        activity = Mock(return_value=(1, True))
        service = self.service(root, memory, activity=activity)
        before_files, before_keys = self.file_values(root), dict(memory.data)
        calls = []

        def capacity(directory):
            directory = Path(directory)
            calls.append(directory)
            self.assertIn(directory, (root, output.parent))
            return SimpleNamespace(free=profile_free if directory == root else output_free)

        with patch("mdata_service.shutil.disk_usage", side_effect=capacity), \
                patch("mdata_service.threading.Thread", side_effect=AssertionError("Worker created")) as worker, \
                patch("mdata_login.capture", side_effect=AssertionError("Auth snapshot read")) as capture, \
                patch("mdata_files.source", side_effect=AssertionError("Profile content opened")) as source, \
                patch("mdata_files.atomic_output", side_effect=AssertionError("Output opened")) as publish:
            with self.assertRaises(mdata_service.MDataError) as caught:
                service.execute("export", output.as_uri(), fixtures.PASSWORD, "restore")
        self.assertEqual(caught.exception.code, "insufficient_space")
        self.assertIsNone(service._worker)
        self.assertFalse(service.pending())
        self.assertEqual(service.snapshot()["state"], "idle")
        worker.assert_not_called()
        capture.assert_not_called()
        source.assert_not_called()
        publish.assert_not_called()
        activity.assert_not_called()
        self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))
        self.assertEqual((self.file_values(root), memory.data), (before_files, before_keys))
        self.assertFalse(output.exists())
        return calls, root, output

    def test_low_profile_volume_rejects_before_ack_even_if_output_volume_has_space(self):
        calls, root, _ = self.assert_early_refusal(mdata_service.EXPORT_SPACE_RESERVE - 1, 10 * 1024**3)
        self.assertEqual(calls, [root])

    def test_low_output_volume_rejects_before_ack_even_if_profile_volume_has_space(self):
        calls, root, output = self.assert_early_refusal(10 * 1024**3, mdata_service.EXPORT_SPACE_RESERVE)
        self.assertEqual(calls, [root, output.parent])

    def test_busy_job_rejects_before_profile_listing_or_capacity_reads(self):
        root, memory = self.profile("busy-capacity", ["42"])
        service = self.service(root, memory)
        service._job_lock.acquire()
        try:
            with patch("mdata_login.listing", side_effect=AssertionError("Auth metadata listed")) as listing, \
                    patch("mdata_service.shutil.disk_usage", side_effect=AssertionError("Capacity read")) as capacity:
                with self.assertRaises(mdata_service.MDataError) as caught:
                    service.execute("export", (self.base / "busy.mdata").as_uri(), fixtures.PASSWORD, "restore")
            self.assertEqual(caught.exception.code, "busy")
            listing.assert_not_called()
            capacity.assert_not_called()
            self.assertTrue(service._job_lock.locked())
            self.assertEqual((memory.reads, memory.writes, memory.inventories), ([], [], 0))
        finally:
            service._job_lock.release()

    def test_exact_preflight_boundary_accepts_and_real_container_round_trips(self):
        root, memory = self.profile("boundary-capacity", ["42"])
        output = self.base / "boundary.mdata"
        total = sum(item["size"] for item in mdata_login.listing(root))
        bound = mdata_service._archive_space_bound(total)

        def capacity(directory):
            return SimpleNamespace(free=mdata_service.EXPORT_SPACE_RESERVE
                                   + (0 if Path(directory) == root else bound))

        before = self.file_values(root), dict(memory.data)
        with patch("mdata_service.shutil.disk_usage", side_effect=capacity):
            result = self.job(self.service(root, memory), "export", output)
        self.assertEqual(result["result"], "exported")
        manifest, content = self.decode(output)
        self.assertEqual(len(manifest["files"]), len(content))
        self.assertEqual(content, {"auth_42": before[0]["auth_42"]})
        self.assertEqual(memory.data, before[1])
        self.assertLessEqual(output.stat().st_size, bound)

    def test_capacity_is_rechecked_after_live_capture_before_output_is_opened(self):
        root, memory = self.profile("late-capacity", ["42"])
        output = self.base / "late.mdata"
        service = self.service(root, memory)
        captured = False
        original_capture = mdata_login.capture

        def capture(*args, **kwargs):
            nonlocal captured
            value = original_capture(*args, **kwargs)
            captured = True
            return value

        def capacity(_directory):
            return SimpleNamespace(free=0 if captured else 10 * 1024**3)

        with patch("mdata_login.capture", side_effect=capture), \
                patch.object(service, "_wait_closed", side_effect=AssertionError("Export waited for native exit")) as wait, \
                patch("mdata_service.shutil.disk_usage", side_effect=capacity), \
                patch("mdata_files.atomic_output", side_effect=AssertionError("Output opened")) as publish:
            self.job(service, "export", output, error="insufficient_space")
        publish.assert_not_called()
        wait.assert_not_called()
        self.assertTrue(captured)
        self.assertFalse(output.exists())
        self.assertFalse(service.restart_requested())
        self.assertFalse(memory.writes)

    def midstream_refusal(self, failing_volume):
        root, memory = self.profile("stream-capacity-" + failing_volume, ["42"])
        (root / "cache" / "large.bin").write_bytes(b"synthetic" * (3 * mdata_archive.CHUNK_BYTES // 9))
        output = self.base / ("stream-" + failing_volume + ".mdata")
        before = self.file_values(root), dict(memory.data)
        written = 0
        original_write = mdata_service._ExportOutput.write

        def capacity(directory):
            volume = "profile" if Path(directory) == root else "output"
            low = written >= mdata_archive.CHUNK_BYTES and volume == failing_volume
            return SimpleNamespace(free=0 if low else 10 * 1024**3)

        def tracked_write(wrapper, data):
            nonlocal written
            count = original_write(wrapper, data)
            written += count
            return count

        service = self.service(root, memory)
        with patch("mdata_service.shutil.disk_usage", side_effect=capacity), \
                patch.object(mdata_service._ExportOutput, "write", tracked_write):
            with self.assertRaises(mdata_archive.MDataArchiveError) as caught:
                service._write_archive(output, fixtures.PASSWORD, login_only=False)
            self.assertEqual(caught.exception.code, "insufficient_space")
        self.assertGreaterEqual(written, mdata_archive.CHUNK_BYTES)
        self.assertLess(written, 2 * mdata_archive.CHUNK_BYTES)
        self.assertFalse(output.exists())
        self.assertFalse(list(output.parent.glob(".mdata-*.tmp")))
        self.assertEqual((self.file_values(root), memory.data), before)
        self.assertFalse(memory.writes)

    def test_output_volume_loss_midstream_discards_partial_archive(self):
        self.midstream_refusal("output")

    def test_profile_volume_loss_midstream_discards_partial_archive_on_other_volume(self):
        self.midstream_refusal("profile")

    def test_small_frame_writes_share_capacity_budget(self):
        output = io.BytesIO()
        stream = mdata_service._ExportOutput(output, self.base / "profile", self.base / "output")
        with patch("mdata_service.shutil.disk_usage", return_value=SimpleNamespace(free=10 * 1024**3)) as capacity:
            for _ in range(600):
                stream.write(b"x" * 21)
            self.assertEqual(capacity.call_count, 2)
            stream.write(b"x" * mdata_archive.CHUNK_BYTES)
            self.assertEqual(capacity.call_count, 4)
        self.assertEqual(len(output.getvalue()), 600 * 21 + mdata_archive.CHUNK_BYTES)


if __name__ == "__main__":
    unittest.main()
