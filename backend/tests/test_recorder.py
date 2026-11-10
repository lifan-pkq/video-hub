import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import recorder


class DouyinRecordingTests(unittest.TestCase):
    @patch("app.services.recorder.Path.is_file", return_value=True)
    @patch("app.services.recorder.subprocess.run")
    @patch("app.services.recorder._http_get")
    def test_resolves_douyin_through_live_recorder_bridge(self, http_get, run, _is_file):
        expected = "http://pull-flv-q1.douyincdn.com/live.flv?wsSecret=working"
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"url": expected}), stderr="")
        http_get.return_value = '"http://pull-flv-q6.douyincdn.com/live.flv?k=forbidden"'

        result = recorder._resolve_douyin("https://v.douyin.com/example/", "best")

        self.assertEqual(result, expected)

    @patch("app.services.recorder.Path.is_file", return_value=True)
    @patch("app.services.recorder.subprocess.run")
    @patch("app.services.recorder._http_get")
    def test_douyin_offline_error_skips_page_fallback(self, http_get, run, _is_file):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="Traceback\nRuntimeError: 该直播间当前未开播\n")
        http_get.return_value = "<html></html>"

        with self.assertRaisesRegex(RuntimeError, "^该直播间当前未开播$"):
            recorder._resolve_douyin("https://live.douyin.com/example", "best")

        http_get.assert_not_called()

    def test_capture_without_fps_limit_stream_copies(self):
        command = recorder._build_ffmpeg_cmd(
            "https://cdn.example.test/live.flv", "direct", Path("/tmp/live_%03d.mp4"))

        joined = " ".join(command)
        self.assertIn("-c copy", joined)
        self.assertIn("-f segment", joined)
        for token in ("unsharp", "loudnorm", "libx264", "-vf"):
            self.assertNotIn(token, joined)

    def test_fps_limit_path_reencodes_with_fps_filter(self):
        command = recorder._build_ffmpeg_cmd(
            "https://cdn.example.test/live.flv", "direct", Path("/tmp/live_%03d.mp4"), fps=30)

        joined = " ".join(command)
        self.assertIn("-vf", joined)
        self.assertIn("fps=30", joined)
        self.assertIn("libx264", joined)
        self.assertNotIn("-c copy", joined)
        self.assertNotIn("unsharp", joined)
        self.assertNotIn("loudnorm", joined)

    def test_douyin_ffmpeg_command_uses_mobile_user_agent(self):
        command = recorder._build_ffmpeg_cmd(
            "http://pull-flv-q1.douyincdn.com/live.flv?wsSecret=working",
            "douyin", Path("/tmp/recording.mp4"))

        self.assertIn("Mozilla/5.0 (Linux; Android 11;", " ".join(command))


class MarkStartedTests(unittest.TestCase):
    def _run_mark_started(self, size: int):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            segment = root / "live_001.mp4"
            segment.write_bytes(b"x" * size)
            updates = []
            rec = {"id": 3, "status": "recording", "filename": "live_%03d.mp4", "started_at": None}
            with patch("app.services.recorder.RECORDINGS_DIR", root), \
                    patch("app.services.recorder.VIDEOS_DIR", root), \
                    patch("app.services.recorder.get_recording", return_value=rec), \
                    patch("app.services.recorder.update_recording",
                          side_effect=lambda *args, **kwargs: updates.append(kwargs)), \
                    patch("app.services.recorder.time.sleep"), \
                    patch("app.services.recorder.time.monotonic", side_effect=[0.0, 0.0, 1000.0]):
                recorder._mark_started(3, Mock(poll=lambda: None), segment)
        return updates

    def test_empty_segment_does_not_start_clock(self):
        self.assertEqual(self._run_mark_started(0), [])

    def test_non_empty_segment_starts_clock(self):
        updates = self._run_mark_started(4)
        self.assertEqual(len(updates), 1)
        self.assertIn("started_at", updates[0])


if __name__ == "__main__":
    unittest.main()
