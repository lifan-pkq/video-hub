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

    def test_stop_keeps_concat_failure_recorded_by_finish(self):
        proc = Mock()
        proc.poll.return_value = 0
        rec = {"id": 4, "status": "recording", "filename": "live_%03d.mp4"}
        updates = []

        def finish(_rec_id, _rec):
            updates.append({"status": "failed", "error": "录制收尾失败: 分段合并失败"})
            raise RuntimeError("分段合并失败")

        with patch("app.services.recorder.get_recording", return_value=rec), \
                patch("app.services.recorder._file_size", return_value=50), \
                patch("app.services.recorder._finish_success", side_effect=finish), \
                patch("app.services.recorder.update_recording",
                      side_effect=lambda *args, **kwargs: updates.append(kwargs)):
            recorder._procs[4] = proc
            try:
                self.assertTrue(recorder.stop_recording(4))
            finally:
                recorder._procs.pop(4, None)

        self.assertEqual(updates[0]["status"], "stopped")
        self.assertEqual(updates[1]["status"], "failed")
        self.assertEqual([item.get("status") for item in updates], ["stopped", "failed"])

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
                    resource = call.kwargs["args"][2]
                    if hasattr(resource, "close"):
                        resource.close()

        self.assertEqual(len(records), 2)
        self.assertNotEqual(records[0], records[1])


if __name__ == "__main__":
    unittest.main()
