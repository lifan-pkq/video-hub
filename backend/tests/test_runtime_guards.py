"""重启收口和磁盘空间检查。"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.jobs import create, fail_interrupted_jobs, get, update
from app.services.ai_edit import AIServiceError
from app.services.downloader import explain_download_error
from app.services.exporter import ExportError, create_export, export_file
from app.services.maintenance import sweep_temp
from app.services.profiles import ProfileError, reference_path
from app.services.transcription import _get_model


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


class PathAndTempTests(unittest.TestCase):
    def test_export_and_profile_names_reject_traversal(self):
        with self.assertRaises(ExportError):
            export_file("abc123", "../secret.txt")
        with self.assertRaises(ExportError):
            export_file("../abc", "cover.jpg")
        with self.assertRaises(ExportError):
            export_file("a" * 40, "cover.jpg")
        with self.assertRaises(ProfileError):
            reference_path("../etc/passwd")
        with self.assertRaises(ProfileError):
            reference_path("ref_not_a_profile.png")

    def test_sweep_temp_removes_only_old_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "old.bin"
            fresh = root / "fresh.bin"
            old.write_bytes(b"old")
            fresh.write_bytes(b"new")
            stale = time.time() - 48 * 3600
            os.utime(old, (stale, stale))
            with patch("app.services.maintenance.TMP_DIR", root):
                result = sweep_temp(24)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertEqual(result["files"], 1)
            self.assertGreaterEqual(result["bytes"], 3)

    def test_download_timeout_and_missing_model_say_what_to_do(self):
        problem = explain_download_error("ERROR: Read timed out")
        self.assertEqual(problem["code"], "download_timeout")
        self.assertIn("上传", problem["action"])
        with patch("app.services.transcription._model", None), patch.dict("sys.modules", {"faster_whisper": None}):
            with self.assertRaises(AIServiceError) as caught:
                _get_model()
        self.assertIn("VIDEO_HUB_ASR_URL", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
