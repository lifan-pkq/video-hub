import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import recorder


class DouyinRecordingTests(unittest.TestCase):
    @patch("app.services.recorder.subprocess.run")
    @patch("app.services.recorder._http_get")
    def test_resolves_douyin_through_live_recorder_bridge(self, http_get, run):
        expected = "http://pull-flv-q1.douyincdn.com/live.flv?wsSecret=working"
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps({"url": expected}), stderr="")
        http_get.return_value = '"http://pull-flv-q6.douyincdn.com/live.flv?k=forbidden"'

        result = recorder._resolve_douyin("https://v.douyin.com/example/", "best")

        self.assertEqual(result, expected)

    @patch("app.services.recorder.subprocess.run")
    @patch("app.services.recorder._http_get")
    def test_douyin_offline_error_skips_page_fallback(self, http_get, run):
        run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="",
            stderr="Traceback\nRuntimeError: 该直播间当前未开播\n")
        http_get.return_value = "<html></html>"

        with self.assertRaisesRegex(RuntimeError, "^该直播间当前未开播$"):
            recorder._resolve_douyin("https://live.douyin.com/example", "best")

        http_get.assert_not_called()

    def test_douyin_ffmpeg_command_uses_mobile_user_agent(self):
        command = recorder._build_ffmpeg_cmd(
            "http://pull-flv-q1.douyincdn.com/live.flv?wsSecret=working",
            "douyin", Path("/tmp/recording.mp4"))

        self.assertIn("Mozilla/5.0 (Linux; Android 11;", " ".join(command))


if __name__ == "__main__":
    unittest.main()
