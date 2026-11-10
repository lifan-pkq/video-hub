import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import server, storyboard
from engine.storyboard import build_storyboard


_REAL_RUN = subprocess.run


def _fake_ffmpeg(commands):
    def side_effect(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"jpeg")
        return subprocess.CompletedProcess(command, 0, b"", b"")
    return side_effect


def _silent_ffmpeg(commands, returncode=0):
    """ffmpeg 不写任何文件（超出片长或源片被替换时的真实行为）。"""
    def side_effect(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, returncode, b"", b"")
    return side_effect


class StoryboardTests(unittest.TestCase):
    @patch("engine.storyboard.subprocess.run")
    def test_storyboard_uses_source_midpoint_and_morph_gate(self, run):
        run.side_effect = _fake_ffmpeg([])
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            items = [{
                "_id": 1, "segment_id": "mirror", "timestamp": "00:00:04,000-00:00:10,000",
                "narration": "镜子里的人慢慢变老", "transition": "morph"
            }]
            result = build_storyboard(task, items, "source.mp4")
        command = run.call_args.args[0]
        self.assertIn("7.000", command)
        self.assertEqual(result["items"][0]["frame_timestamp"], 7.0)
        self.assertTrue(result["items"][0]["morph_preview_required"])
        self.assertEqual(result["items"][0]["morph_preview_status"], "pending")

    @patch("engine.storyboard.subprocess.run")
    def test_long_segment_frame_is_not_taken_after_start(self, run):
        run.side_effect = _fake_ffmpeg([])
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            items = [{"segment_id": "long", "timestamp": "00:00:00,000-00:00:40,000"}]
            build_storyboard(task, items, "source.mp4")
        command = run.call_args.args[0]
        self.assertIn("20.000", command)
        self.assertNotIn("2.000", command)

    @patch("engine.storyboard.subprocess.run")
    def test_changed_timestamp_and_new_segment_are_extracted_again(self, run):
        commands = []
        run.side_effect = _fake_ffmpeg(commands)
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            first = [
                {"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000"},
                {"segment_id": "b", "timestamp": "00:00:10,000-00:00:14,000"},
            ]
            build_storyboard(task, first, "source.mp4")
            self.assertEqual([Path(c[-1]).name for c in commands], ["a.jpg", "b.jpg"])
            commands.clear()
            second = [
                {"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000"},
                {"segment_id": "b", "timestamp": "00:00:20,000-00:00:24,000"},
                {"segment_id": "c", "timestamp": "00:00:30,000-00:00:34,000"},
            ]
            result = build_storyboard(task, second, "source.mp4")
        self.assertEqual([Path(c[-1]).name for c in commands], ["b.jpg", "c.jpg"])
        self.assertIn("22.000", commands[0])
        self.assertIn("32.000", commands[1])
        self.assertEqual(result["items"][0]["frame_timestamp"], 2.0)
        self.assertEqual(result["items"][1]["frame_timestamp"], 22.0)
        self.assertEqual(result["items"][2]["frame_timestamp"], 32.0)
        self.assertEqual([item["frame"] for item in result["items"]], ["a.jpg", "b.jpg", "c.jpg"])

    @patch("engine.storyboard.subprocess.run")
    def test_unchanged_timestamps_are_not_extracted_again(self, run):
        commands = []
        run.side_effect = _fake_ffmpeg(commands)
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            items = [
                {"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000"},
                {"segment_id": "b", "timestamp": "00:00:10,000-00:00:14,000"},
            ]
            build_storyboard(task, items, "source.mp4")
            self.assertEqual(len(commands), 2)
            commands.clear()
            result = build_storyboard(task, items, "source.mp4")
        self.assertEqual(commands, [])
        self.assertEqual([item["frame"] for item in result["items"]], ["a.jpg", "b.jpg"])
        self.assertEqual([item["frame_timestamp"] for item in result["items"]], [2.0, 12.0])

    @patch("engine.storyboard.subprocess.run")
    def test_missing_frame_is_extracted_again(self, run):
        commands = []
        run.side_effect = _fake_ffmpeg(commands)
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            items = [{"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000"}]
            result = build_storyboard(task, items, "source.mp4")
            (task / "storyboard" / result["items"][0]["frame"]).unlink()
            commands.clear()
            again = build_storyboard(task, items, "source.mp4")
        self.assertEqual([Path(c[-1]).name for c in commands], ["a.jpg"])
        self.assertEqual(again["items"][0]["frame"], "a.jpg")

    @patch("engine.storyboard.subprocess.run")
    def test_silent_failure_does_not_claim_new_timestamp_and_retries_next_build(self, run):
        for returncode in (0, 234):
            with self.subTest(returncode=returncode):
                commands = []
                run.side_effect = _fake_ffmpeg(commands)
                with tempfile.TemporaryDirectory() as temp:
                    task = Path(temp)
                    first = [{"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000"}]
                    build_storyboard(task, first, "source.mp4")
                    self.assertEqual([Path(c[-1]).name for c in commands], ["a.jpg"])
                    commands.clear()
                    run.side_effect = _silent_ffmpeg(commands, returncode)
                    second = [{"segment_id": "a", "timestamp": "00:00:20,000-00:00:24,000"}]
                    failed = build_storyboard(task, second, "source.mp4")
                    self.assertEqual(len(commands), 1)
                    self.assertNotEqual(failed["items"][0]["frame_timestamp"], 22.0)
                    commands.clear()
                    run.side_effect = _silent_ffmpeg(commands, returncode)
                    build_storyboard(task, second, "source.mp4")
                    self.assertEqual(len(commands), 1)

    @patch("engine.storyboard.subprocess.run")
    def test_silent_failure_keeps_retrying_until_frame_is_written(self, run):
        commands = []
        run.side_effect = _fake_ffmpeg(commands)
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            items = [{"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000"}]
            build_storyboard(task, items, "source.mp4")
            commands.clear()
            run.side_effect = _silent_ffmpeg(commands)
            changed = [{"segment_id": "a", "timestamp": "00:00:20,000-00:00:24,000"}]
            build_storyboard(task, changed, "source.mp4")
            commands.clear()
            run.side_effect = _fake_ffmpeg(commands)
            recovered = build_storyboard(task, changed, "source.mp4")
        self.assertEqual(len(commands), 1)
        self.assertEqual(recovered["items"][0]["frame_timestamp"], 22.0)
        self.assertEqual(recovered["items"][0]["frame"], "a.jpg")

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 不可用")
    def test_real_ffmpeg_beyond_end_of_source_does_not_claim_timestamp_and_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            video = task / "source.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2:r=5",
                 "-pix_fmt", "yuv420p", str(video)],
                check=True,
                capture_output=True,
            )
            built = build_storyboard(task, [{"segment_id": "a", "timestamp": "00:00:00,000-00:00:02,000"}], str(video))
            target = task / "storyboard" / built["items"][0]["frame"]
            self.assertTrue(target.is_file())
            old_bytes = target.read_bytes()
            beyond = [{"segment_id": "a", "timestamp": "00:00:20,000-00:00:24,000"}]
            calls = []

            def counting_run(command, **kwargs):
                calls.append(command)
                return _REAL_RUN(command, **kwargs)

            with patch("engine.storyboard.subprocess.run", side_effect=counting_run):
                failed = build_storyboard(task, beyond, str(video))
                self.assertEqual(len(calls), 1)
                self.assertNotEqual(failed["items"][0]["frame_timestamp"], 22.0)
                calls.clear()
                again = build_storyboard(task, beyond, str(video))
                self.assertEqual(len(calls), 1)
        self.assertNotEqual(again["items"][0]["frame_timestamp"], 22.0)

    @patch("engine.storyboard.subprocess.run")
    def test_same_sanitized_id_does_not_overwrite_frame(self, run):
        run.side_effect = _fake_ffmpeg([])
        items = [
            {"segment_id": "任务A:段1", "timestamp": "00:00:00,000-00:00:04,000"},
            {"segment_id": "任务A_段1", "timestamp": "00:00:10,000-00:00:20,000"},
        ]
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            result = build_storyboard(task, items, "source.mp4")
            names = [item["frame"] for item in result["items"]]
            self.assertNotEqual(names[0], names[1])
            self.assertTrue((task / "storyboard" / names[0]).is_file())
            self.assertTrue((task / "storyboard" / names[1]).is_file())
            again = build_storyboard(task, items, "source.mp4")
        self.assertEqual([item["frame"] for item in again["items"]], names)

    def test_unique_id_suffixes_are_stable(self):
        used = set()
        ids = [storyboard._unique_id("a:b", index, used) for index in range(3)]
        self.assertEqual(ids, ["a_b", "a_b-2", "a_b-3"])

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg 不可用")
    def test_real_ffmpeg_frame_is_reused_when_timestamp_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            task = Path(temp)
            video = task / "source.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=6:r=5",
                 "-pix_fmt", "yuv420p", str(video)],
                check=True,
                capture_output=True,
            )
            items = [{"segment_id": "real", "timestamp": "00:00:00,000-00:00:04,000"}]
            result = build_storyboard(task, items, str(video))
            target = task / "storyboard" / result["items"][0]["frame"]
            self.assertTrue(target.is_file())
            self.assertGreater(target.stat().st_size, 0)
            self.assertEqual(result["items"][0]["frame_timestamp"], 2.0)
            before = target.stat().st_mtime_ns
            again = build_storyboard(task, items, str(video))
            self.assertEqual(again["items"][0]["frame_timestamp"], 2.0)
            self.assertEqual(target.stat().st_mtime_ns, before)
            stored = json.loads((task / "analysis" / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["items"][0]["frame_timestamp"], 2.0)


class UpdateScriptRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = server.TaskStore(self.root)
        self.task_id = "review-task"
        video = self.root / "source.mp4"
        video.write_bytes(b"fake-video")
        self.store.create({"task_id": self.task_id, "video_path": str(video), "video_duration": 60})
        self.store.update_state(self.task_id, status="awaiting_review")

    def _handler(self, items):
        handler = server.Handler.__new__(server.Handler)
        handler.headers = {}
        handler._body = lambda: {"items": items}
        handler._send = lambda status, payload: None
        handler._error = lambda status, message, detail="": self.fail(f"{status} {message} {detail}")
        return handler

    @patch("engine.storyboard.subprocess.run")
    def test_review_save_refreshes_only_changed_and_new_segments(self, run):
        commands = []
        run.side_effect = _fake_ffmpeg(commands)
        self.store.write_script(
            self.task_id,
            [{"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000", "narration": "一", "OST": 2}],
        )
        with patch.object(server, "STORE", self.store):
            self._handler([
                {"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000", "narration": "一", "OST": 2},
                {"segment_id": "b", "timestamp": "00:00:10,000-00:00:14,000", "narration": "二", "OST": 2},
            ])._update_script(self.task_id)
            self.assertEqual([Path(c[-1]).name for c in commands], ["a.jpg", "b.jpg"])
            commands.clear()
            self._handler([
                {"segment_id": "a", "timestamp": "00:00:00,000-00:00:04,000", "narration": "一", "OST": 2},
                {"segment_id": "b", "timestamp": "00:00:20,000-00:00:24,000", "narration": "二", "OST": 2},
                {"segment_id": "c", "timestamp": "00:00:30,000-00:00:34,000", "narration": "三", "OST": 2},
            ])._update_script(self.task_id)
        self.assertEqual([Path(c[-1]).name for c in commands], ["b.jpg", "c.jpg"])
        stored = json.loads((self.store.task_dir(self.task_id) / "analysis" / "storyboard.json").read_text(encoding="utf-8"))
        self.assertEqual([item["frame"] for item in stored["items"]], ["a.jpg", "b.jpg", "c.jpg"])
        self.assertEqual([item["frame_timestamp"] for item in stored["items"]], [2.0, 22.0, 32.0])


if __name__ == "__main__":
    unittest.main()
