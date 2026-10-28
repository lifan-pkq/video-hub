import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import recorder


class RecorderSafetyTests(unittest.TestCase):
    def test_monitor_marks_recording_failed_when_finalization_raises(self):
        proc = Mock(returncode=0)
        proc.wait.return_value = 0
        rec = {"id": 7, "status": "recording", "filename": "live.mp4"}
        with patch("app.services.recorder.get_recording", return_value=rec), \
                patch("app.services.recorder._file_size", return_value=100), \
                patch("app.services.recorder._finish_success", side_effect=RuntimeError("remux failed")), \
                patch("app.services.recorder.update_recording") as update:
            try:
                recorder._monitor(7, proc, io.BytesIO())
            except Exception as exc:
                self.fail(f"_monitor 未处理收尾异常: {exc}")

        update.assert_called_once()
        self.assertEqual(update.call_args.kwargs["status"], "failed")
        self.assertIn("remux failed", update.call_args.kwargs["error"])

    def test_start_generates_unique_filename_for_same_title_and_timestamp(self):
        with tempfile.TemporaryDirectory() as root:
            recordings_dir = Path(root) / "recordings"
            tmp_dir = Path(root) / "tmp"
            recordings_dir.mkdir()
            tmp_dir.mkdir()
            records = []
            with patch("app.services.recorder.RECORDINGS_DIR", recordings_dir), \
                    patch("app.services.recorder.TMP_DIR", tmp_dir), \
                    patch("app.services.recorder.resolve_stream_url", return_value="https://cdn.example.test/live.flv"), \
                    patch("app.services.recorder.time.strftime", return_value="20261025_120000"), \
                    patch("app.services.recorder.add_recording", side_effect=(1, 2)) as add_recording, \
                    patch("app.services.recorder.subprocess.Popen", return_value=Mock()), \
                    patch("app.services.recorder.threading.Thread") as thread, \
                    patch("app.services.recorder.get_recording", side_effect=lambda rec_id: {"id": rec_id}):
                thread.return_value.start.return_value = None
                recorder.start_recording("https://example.test/live", "同一直播")
                recorder.start_recording("https://example.test/live", "同一直播")
                records.extend(call.args[3] for call in add_recording.call_args_list)
                for call in thread.call_args_list:
                    call.kwargs["args"][2].close()

        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0], records[1])


if __name__ == "__main__":
    unittest.main()
