import json
import struct
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path
from unittest.mock import patch

from engine import export_task

ROOT = Path(__file__).resolve().parent.parent.parent


def write_png(path: Path, width: int, height: int) -> None:
    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    raw = b"".join(b"\x00" + bytes([40, 60, 90]) * width for _ in range(height))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def script_fixture() -> list[dict]:
    return [
        {"narration": "开场他停在镜子前", "timestamp": "00:00:00,000-00:00:03,000", "story_role": "hook"},
        {"narration": "中段冲突爆发", "timestamp": "00:00:10,000-00:00:13,000", "story_role": "climax"},
        {"narration": "结尾他选择离开", "timestamp": "00:00:20,000-00:00:23,000", "story_role": "ending"},
    ]


class ExportTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"video")
        self.task = self.root / "task"
        self.task.mkdir()

    def _patch_render(self):
        covers = patch(
            "engine.export_task._cover_with_title",
            side_effect=lambda frame, target, title, root, index: target.write_bytes(b"cover"),
        )
        frames = patch(
            "engine.export_task._frame",
            side_effect=lambda source, timestamp, target: target.write_bytes(b"frame"),
        )
        reframes = patch(
            "engine.export_task.reframe",
            side_effect=lambda source, target, aspect, track: target.write_bytes(b"video") or {"aspect": aspect},
        )
        return covers, frames, reframes

    def _task_with_script(self) -> Path:
        self.task.joinpath("script.json").write_text(
            json.dumps(script_fixture(), ensure_ascii=False), encoding="utf-8"
        )
        return self.task

    def test_bundle_contains_all_real_files_and_manifest(self):
        covers, frames, reframes = self._patch_render()
        with covers, frames, reframes as reframe:
            result = export_task.export_bundle(self.source, self.root / "export", "测试", self._task_with_script())
        archive = self.root / "export" / "video-package.zip"
        self.assertTrue(result["complete"])
        self.assertTrue(archive.is_file())
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
        self.assertIn("portrait.mp4", names)
        self.assertIn("subtitles.srt", names)
        self.assertIn("cover-3.jpg", names)
        self.assertGreaterEqual(reframe.call_count, 2)

    # ---- 标题（问题 1）----

    @patch("engine.export_task._model_titles")
    def test_model_titles_are_three_real_rewrites(self, model):
        model.return_value = {
            "suspense": "镜子里的人为什么突然停住脚步",
            "information": "三分钟看懂这段关系的崩塌全过程",
            "emotion": "他停在镜子前那一下，我也跟着停了",
        }
        titles = export_task._titles("测试", script_fixture(), {})
        self.assertEqual([item["kind"] for item in titles], ["suspense", "information", "emotion"])
        self.assertTrue(all(item["generated"] for item in titles))
        self.assertEqual(len({item["title"] for item in titles}), 3)

    @patch("engine.export_task._model_titles", side_effect=RuntimeError("未配置文本模型 Key"))
    def test_title_falls_back_honestly_when_model_unavailable(self, _model):
        titles = export_task._titles("测试", script_fixture(), {})
        self.assertTrue(all(item["generated"] is False for item in titles))
        self.assertEqual(titles[0]["title"], "开场他停在镜子前，接下来呢？")
        self.assertIn("这一刻，", titles[2]["title"])

    @patch("engine.export_task._model_titles")
    def test_identical_model_titles_are_marked_not_generated(self, model):
        model.return_value = {
            "suspense": "同一句标题",
            "information": "同一句标题",
            "emotion": "同一句标题",
        }
        titles = export_task._titles("测试", script_fixture(), {})
        self.assertTrue(all(item["generated"] is False for item in titles))

    # ---- 封面（问题 2）----

    def test_cover_timestamps_use_hook_climax_ending_not_zero(self):
        timestamps = export_task._cover_timestamps(script_fixture())
        self.assertEqual(timestamps, [1.5, 11.5, 21.5])
        self.assertNotIn(0.0, timestamps)
        self.assertEqual(export_task._cover_timestamps([]), [1.0, 4.0, 7.0])

    def test_cover_is_16_9_and_title_fits_box(self):
        frame = self.root / "wide.jpg"
        write_png(frame, 640, 360)
        target = self.root / "cover.jpg"
        report = export_task._cover_with_title(
            frame, target, "镜子里的人为什么突然停住脚步，那一刻到底发生了什么变化", ROOT, 0
        )
        self.assertEqual(tuple(report["image_size"]), export_task.COVER_SIZE)
        self.assertGreaterEqual(report["lines"], 2)
        self.assertLessEqual(report["lines"], export_task.TITLE_MAX_LINES)
        self.assertLessEqual(report["max_line_width"], report["box_width"])
        box_height = export_task.TITLE_BOX[3] - export_task.TITLE_BOX[1]
        self.assertLessEqual(
            report["lines"] * (report["font_size"] + export_task.TITLE_LINE_SPACING), box_height
        )

    def test_square_frame_is_cropped_not_stretched(self):
        frame = self.root / "square.png"
        write_png(frame, 400, 400)
        report = export_task._cover_with_title(frame, self.root / "cover-square.jpg", "方形封面", ROOT, 1)
        self.assertEqual(tuple(report["image_size"]), export_task.COVER_SIZE)

    # ---- 横屏（问题 4）----

    def test_landscape_source_is_copied_not_reencoded(self):
        covers, frames, reframes = self._patch_render()
        with patch("engine.export_task._is_landscape", return_value=True), covers, frames, reframes as reframe:
            result = export_task.export_bundle(self.source, self.root / "export", "测试")
        self.assertEqual((self.root / "export" / "landscape.mp4").read_bytes(), b"video")
        self.assertFalse(result["landscape_reencoded"])
        self.assertEqual(sorted(call.args[2] for call in reframe.call_args_list), ["1:1", "9:16"])

    def test_is_landscape_probes_real_dimensions(self):
        cases = [("1920x1080", 1920, 1080, True), ("720p", 1280, 720, True), ("portrait", 1080, 1920, False)]
        for label, width, height, expected in cases:
            with self.subTest(label):
                probe = '{"streams": [{"width": %d, "height": %d}]}' % (width, height)
                with patch("engine.export_task.subprocess.run") as run:
                    run.return_value = type("R", (), {"stdout": probe, "returncode": 0})()
                    self.assertEqual(export_task._is_landscape(self.source), expected)

    # ---- 无字幕版（问题 3）----

    def test_manifest_does_not_claim_without_subtitles_without_clean(self):
        covers, frames, reframes = self._patch_render()
        with covers, frames, reframes:
            result = export_task.export_bundle(self.source, self.root / "export", "测试", self.task)
        self.assertEqual(result["without_subtitles_source"], "unavailable")
        self.assertNotIn("without_subtitles", result["files"])
        with zipfile.ZipFile(self.root / "export" / "video-package.zip") as bundle:
            self.assertNotIn("without-subtitles.mp4", bundle.namelist())

    def test_clean_master_is_shipped_as_without_subtitles(self):
        (self.task / "clean.mp4").write_bytes(b"clean-video")
        covers, frames, reframes = self._patch_render()
        with covers, frames, reframes:
            result = export_task.export_bundle(self.source, self.root / "export", "测试", self.task)
        self.assertEqual(result["without_subtitles_source"], "clean.mp4")
        self.assertEqual((self.root / "export" / "without-subtitles.mp4").read_bytes(), b"clean-video")

    # ---- SRT（问题 5）----

    def test_srt_prefers_real_speech_timeline(self):
        understanding = {"tracks": {"speech": [
            {"start": 0.4, "end": 2.6, "text": "开场他停在镜子前"},
            {"start": 10.2, "end": 12.9, "text": "中段冲突爆发"},
        ]}}
        target = self.root / "subtitles.srt"
        report = export_task._srt(script_fixture(), understanding, target)
        self.assertEqual(report, {"srt_timing": "tts", "srt_cues": 2, "srt_source": "understanding_speech"})
        text = target.read_text(encoding="utf-8")
        self.assertIn("00:00:00,400 --> 00:00:02,600", text)
        self.assertIn("00:00:10,200 --> 00:00:12,900", text)

    def test_merged_subtitles_win_over_real_speech_timeline(self):
        self.task.joinpath("script_subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,975\n这手法太离谱了\n\n"
            "2\n00:00:01,975 --> 00:00:03,950\n你看他这手指\n",
            encoding="utf-8",
        )
        understanding = {"tracks": {"speech": [{"start": 9.0, "end": 11.0, "text": "不该用的时间"}]}}
        target = self.root / "merged.srt"
        report = export_task._srt(script_fixture(), understanding, target, self.task)
        self.assertEqual(report, {"srt_timing": "tts", "srt_cues": 2, "srt_source": "merged_subtitles"})
        text = target.read_text(encoding="utf-8")
        self.assertIn("00:00:00,000 --> 00:00:01,975", text)
        self.assertIn("你看他这手指", text)
        self.assertNotIn("不该用的时间", text)

    def test_srt_without_real_timeline_is_marked_planned(self):
        target = self.root / "subtitles.srt"
        report = export_task._srt(script_fixture(), {}, target)
        self.assertEqual(report["srt_timing"], "planned")
        self.assertEqual(report["srt_source"], "planned_script")
        self.assertIn("00:00:00,000 --> 00:00:03,000", target.read_text(encoding="utf-8"))

    def test_empty_script_has_no_srt_timeline(self):
        report = export_task._srt([], {}, self.root / "empty.srt")
        self.assertEqual(report, {"srt_timing": "unavailable", "srt_cues": 0, "srt_source": "none"})

    def test_manifest_reports_incomplete_when_script_empty(self):
        covers, frames, reframes = self._patch_render()
        with covers, frames, reframes:
            result = export_task.export_bundle(self.source, self.root / "export", "测试", self.task)
        self.assertFalse(result["complete"])
        self.assertEqual(result["srt_timing"], "unavailable")
        self.assertNotIn("srt", result["files"])
        self.assertIn("incomplete_reason", result)
        with zipfile.ZipFile(self.root / "export" / "video-package.zip") as bundle:
            self.assertNotIn("subtitles.srt", bundle.namelist())

    # ---- subject-track 契约 ----

    def test_subject_track_prefers_cli_path_over_task_dir(self):
        self.task.joinpath("analysis").mkdir()
        self.task.joinpath("analysis", "understanding.json").write_text(
            json.dumps({"tracks": {"visual": [{"timestamp": 0.0, "subject_box": {"x": 100, "y": 100, "width": 100, "height": 100}}]}}),
            encoding="utf-8",
        )
        payload = self.root / "subject.json"
        payload.write_text(
            json.dumps({"tracks": {"visual": [{"timestamp": 1.5, "subject_box": {"x": 600, "y": 300, "width": 200, "height": 200}}]}}),
            encoding="utf-8",
        )
        track = export_task.load_subject_track(payload, self.task)
        self.assertEqual(track[0]["subject_box"]["x"], 600)
        self.assertEqual(len(track), 1)

    def test_subject_track_falls_back_to_task_dir(self):
        self.task.joinpath("analysis").mkdir()
        self.task.joinpath("analysis", "understanding.json").write_text(
            json.dumps({"tracks": {"visual": [{"timestamp": 2.0, "subject_box": {"x": 400, "y": 500, "width": 100, "height": 100}}]}}),
            encoding="utf-8",
        )
        track = export_task.load_subject_track(None, self.task)
        self.assertEqual(track[0]["timestamp"], 2.0)
        self.assertEqual(export_task.load_subject_track(self.root / "missing.json", self.task), track)
        self.assertEqual(export_task.load_subject_track(None, None), [])

    def test_subject_track_reaches_reframe(self):
        payload = self.root / "subject.json"
        payload.write_text(
            json.dumps({"tracks": {"visual": [{"timestamp": 1.5, "subject_box": {"x": 600, "y": 300, "width": 200, "height": 200}}]}}),
            encoding="utf-8",
        )
        seen = []

        def fake_reframe(source, target, aspect, track):
            seen.append((aspect, track))
            target.write_bytes(b"video")
            return {"aspect": aspect}

        covers, frames, _ = self._patch_render()
        with patch("engine.export_task.reframe", side_effect=fake_reframe), covers, frames:
            export_task.export_bundle(
                self.source, self.root / "export", "测试", None, subject_track_path=payload
            )
        self.assertTrue(seen)
        for _aspect, track in seen:
            self.assertEqual(track[0]["subject_box"]["x"], 600)


if __name__ == "__main__":
    unittest.main()
