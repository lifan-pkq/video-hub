"""重启收口和磁盘空间检查。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs import create, fail_interrupted_jobs, get, update
from app.services.exporter import ExportError, create_export


class InterruptedJobTests(unittest.TestCase):
    def test_running_job_is_failed_after_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            previous = os.environ.get("VIDEO_HUB_DATA_DIR")
            os.environ["VIDEO_HUB_DATA_DIR"] = temp
            self.addCleanup(lambda: os.environ.__setitem__("VIDEO_HUB_DATA_DIR", previous) if previous else os.environ.pop("VIDEO_HUB_DATA_DIR", None))
            job = create("edit.douyin", "成片", {"video_id": 1})
            update(job["id"], status="running", stage="asr", label="转写")
            self.assertEqual(fail_interrupted_jobs(), 1)
            saved = get(job["id"])
            self.assertEqual(saved["status"], "failed")
            self.assertEqual(saved["error"]["code"], "interrupted")
            self.assertEqual(fail_interrupted_jobs(), 0)


class DiskSpaceTests(unittest.TestCase):
    def test_export_refuses_when_disk_is_low(self):
        with patch("app.services.exporter.get_video", return_value={"id": 1, "filename": "a.mp4", "title": "t", "source_url": ""}), \
                patch("app.services.exporter.VIDEOS_DIR", Path(".")), \
                patch("pathlib.Path.is_file", return_value=True), \
                patch("app.services.media.free_mb", return_value=20):
            with self.assertRaises(ExportError) as caught:
                create_export(1)
        self.assertIn("512", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
