import io
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import recorder
from app.services.chenze_pack import load_identity
from app.services.media import probe_video


def _box_order(path: Path) -> list[bytes]:
    data = path.read_bytes()
    offset = 0
    order = []
    while offset + 8 <= len(data) and len(order) < 12:
        size = int.from_bytes(data[offset:offset + 4], "big")
        order.append(data[offset + 4:offset + 8])
        if size < 8:
            break
        offset += size
    return order


class RecorderSingleVideoTests(unittest.TestCase):
    def test_ffmpeg_records_one_recoverable_file(self):
        command = recorder._build_ffmpeg_cmd("https://example.test/live.flv", "direct", Path("live.mp4"))
        joined = " ".join(command)
        self.assertNotIn("-f segment", joined)
        self.assertNotIn("-segment_time", joined)
        self.assertNotIn("%03d", joined)
        self.assertIn("-f mp4", joined)
        self.assertIn("frag_keyframe", joined)
        self.assertIn("empty_moov", joined)
        self.assertIn("-avioflags", joined)
        self.assertIn("direct", joined)
        self.assertIn("-flush_packets", joined)
        self.assertIn("-c copy", joined)

    def test_capture_files_ingest_as_one_video(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            videos = root / "videos"
            recordings.mkdir()
            videos.mkdir()
            paths = [recordings / "live-a.mp4", recordings / "live-b.mp4"]
            for path in paths:
                path.write_bytes(b"video")
            rec = {
                "id": 1,
                "title": "测试整场",
                "platform": "direct",
                "url": "https://example.test",
                "filename": "live.mp4",
                "chenze_style": 0,
                "chenze_json": None,
            }
            updates = []

            def assemble(sources, dest):
                self.assertEqual(sources, paths)
                dest.write_bytes(b"remuxed")
                return True

            with patch("app.services.recorder.RECORDINGS_DIR", recordings), \
                    patch("app.services.recorder.VIDEOS_DIR", videos), \
                    patch("app.services.recorder.COVERS_DIR", root), \
                    patch("app.services.recorder._recording_paths", return_value=paths), \
                    patch("app.services.recorder._assemble_single_mp4", side_effect=assemble), \
                    patch("app.services.recorder._probe_with_retry", return_value={"duration": 1.2, "width": 320, "height": 180}), \
                    patch("app.services.recorder._media_usable", return_value=True), \
                    patch("app.services.recorder.make_cover", return_value=True), \
                    patch("app.services.recorder.add_video", side_effect=lambda **fields: {"id": 9, **fields}) as add_video, \
                    patch("app.services.recorder.update_recording", side_effect=lambda *args, **kwargs: updates.append(kwargs)), \
                    patch("app.services.quality.update_quality", return_value={"score": 88}), \
                    patch("app.services.understanding.start_analysis", side_effect=RuntimeError("模型没配")):
                recorder._finalized.clear()
                self.assertTrue(recorder._finish_success(1, rec))
            self.assertEqual(add_video.call_count, 1)
            saved = add_video.call_args.kwargs
            self.assertEqual(saved["title"], "测试整场")
            self.assertNotIn("第", saved["title"])
            self.assertEqual(saved["duration"], 1.2)
            segments = json.loads(updates[0]["segments_json"])
            self.assertEqual(len(segments), 1)
            self.assertEqual(segments[0]["video_id"], 9)
            self.assertEqual(updates[0]["video_id"], 9)
            self.assertEqual(updates[0]["duration"], 1.2)

    def test_chenze_option_is_stored_and_does_not_change_capture(self):
        commands = []

        def popen(command, **_kwargs):
            commands.append(list(command))
            return Mock()

        anchor = load_identity()
        options = {
            "display_name": anchor["display_name"],
            "douyin_id": anchor["douyin_id"],
            "homepage_url": anchor["homepage_url"],
            "column": "游戏",
        }
        stored = []
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            tmp = root / "tmp"
            recordings.mkdir()
            tmp.mkdir()
            with patch("app.services.recorder.RECORDINGS_DIR", recordings), \
                    patch("app.services.recorder.TMP_DIR", tmp), \
                    patch("app.services.recorder.resolve_stream_url", return_value="https://cdn.example.test/live.flv"), \
                    patch("app.services.recorder.time.strftime", return_value="20261127_030000"), \
                    patch("app.services.recorder.add_recording", side_effect=lambda *args, **kwargs: stored.append((args, kwargs)) or len(stored)), \
                    patch("app.services.recorder.subprocess.Popen", side_effect=popen), \
                    patch("app.services.recorder.threading.Thread") as thread, \
                    patch("app.services.recorder.get_recording", side_effect=lambda rec_id: {"id": rec_id}):
                thread.return_value.start.return_value = None
                recorder.start_recording("https://example.test/live", "同一直播", chenze_style=False)
                recorder.start_recording("https://example.test/live", "同一直播", chenze_style=True, chenze_options=options)
                for call in thread.call_args_list:
                    resource = call.kwargs["args"][2]
                    if hasattr(resource, "close"):
                        resource.close()
        self.assertEqual(commands[0][:-1], commands[1][:-1])
        self.assertNotIn("-f segment", " ".join(commands[0]))
        self.assertNotIn("chenze", " ".join(commands[1]).lower())
        self.assertEqual(stored[0][1]["chenze_style"], 0)
        self.assertIsNone(stored[0][1]["chenze_json"])
        self.assertEqual(stored[1][1]["chenze_style"], 1)
        self.assertEqual(json.loads(stored[1][1]["chenze_json"])["douyin_id"], anchor["douyin_id"])
        self.assertNotEqual(commands[0][-1], commands[1][-1])

    def test_monitor_keeps_partial_capture_when_ffmpeg_exits_nonzero(self):
        proc = Mock(returncode=1)
        proc.wait.return_value = 1
        states = [
            {"id": 4, "status": "recording", "filename": "live.mp4"},
            {"id": 4, "status": "completed", "filename": "live.mp4", "video_id": 9},
        ]
        with patch("app.services.recorder.get_recording", side_effect=lambda _rec_id: states.pop(0)), \
                patch("app.services.recorder._file_size", return_value=100), \
                patch("app.services.recorder._finish_success", return_value=True) as finish, \
                patch("app.services.recorder.update_recording") as update:
            recorder._monitor(4, proc, io.BytesIO())
        finish.assert_called_once()
        self.assertTrue(any(call.kwargs.get("error") and "已写出" in call.kwargs["error"] for call in update.call_args_list))


class CrashRecoveryTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("需要 ffmpeg")

    def test_killed_capture_stays_readable_and_remuxes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "color=c=0x335577:s=320x180:d=12",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", "25", "-c:a", "aac",
                    str(source),
                ],
                check=True, capture_output=True, timeout=60,
            )
            capture = root / "capture.mp4"
            command = recorder._build_ffmpeg_cmd(str(source), "direct", capture)
            insert_at = command.index("-i")
            command[insert_at:insert_at] = ["-re"]
            proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(3)
            self.assertIsNone(proc.poll(), "样片在杀掉之前就结束了，没法验证中断恢复")
            proc.kill()
            if proc.stdin:
                proc.stdin.close()
            proc.wait(timeout=10)
            self.assertNotEqual(proc.returncode, 0)
            killed = probe_video(capture)
            self.assertTrue(killed.get("duration") or killed.get("width") or killed.get("codec"), killed)
            self.assertGreater(float(killed.get("duration") or 0), 0.4)
            self.assertLess(float(killed.get("duration") or 99), 11)
            final = root / "final.mp4"
            self.assertTrue(recorder._remux_to_faststart(capture, final))
            remuxed = probe_video(final)
            self.assertGreater(float(remuxed.get("duration") or 0), 0.4)
            order = _box_order(final)
            self.assertIn(b"moov", order)
            self.assertIn(b"mdat", order)
            self.assertLess(order.index(b"moov"), order.index(b"mdat"))


if __name__ == "__main__":
    unittest.main()
