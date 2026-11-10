import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import task, voice
from app.services.generate_video import _build_ffmpeg_merge_command
from engine.finishing import DIMENSIONS, crop_filter
from engine import runner
from engine.subtitle_design import build_events, write_ass

FFMPEG = shutil.which("ffmpeg")


def _segment_color(video_path: Path, moment: float, width: int, x: int, y: int) -> tuple[int, int, int]:
    """抽帧读取指定像素颜色，用来证明裁切中心真的落在那一段的主体上。"""
    from PIL import Image

    frame = video_path.with_name(f"frame-{moment:.1f}.png")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{moment:.3f}", "-i", str(video_path),
         "-frames:v", "1", str(frame)],
        check=True,
    )
    return Image.open(frame).convert("RGB").getpixel((x, y))


class RenderFinishingTests(unittest.TestCase):
    def test_emotion_changes_actual_rate_and_pitch(self):
        self.assertEqual(voice.emotion_settings("激动", 1.0, 1.0), (1.1, 1.06))
        self.assertEqual(voice.emotion_settings("哽咽", 1.0, 1.0), (0.88, 0.93))

    @patch("app.services.voice.create_subtitle", return_value=("subtitle.srt", 2.0))
    @patch("app.services.voice.tts")
    def test_tts_multiple_uses_segment_voice_and_emotion(self, tts, _subtitle):
        tts.return_value = SimpleNamespace(offset=[(0, 10000000)], subs=["你好"])
        with tempfile.TemporaryDirectory() as temp, patch("app.services.voice.utils.task_dir", return_value=temp):
            result = voice.tts_multiple("task", [{
                "_id": 1, "timestamp": "00:00:00,000-00:00:02,000", "narration": "你好", "OST": 2,
                "voice_name": "zh-CN-YunjianNeural", "emotion": "激动"
            }], "zh-CN-XiaoxiaoNeural", 1.0, 1.0, "edge_tts")
        self.assertEqual(tts.call_args.kwargs["voice_name"], "zh-CN-YunjianNeural")
        self.assertEqual(tts.call_args.kwargs["voice_rate"], 1.1)
        self.assertEqual(result[0]["words"][0]["text"], "你好")

    def test_subtitle_keeps_original_text_when_pronunciation_differs(self):
        events = build_events(
            [{"_id": 1, "narration": "重庆", "tts_text": "崇庆", "editedTimeRange": "00:00:00-00:00:02"}],
            [{"_id": 1, "text": "崇庆", "duration": 2, "words": [{"start": 0, "end": 2, "text": "崇庆"}]}],
        )
        self.assertEqual(events[0]["text"], "重庆")
        self.assertFalse(events[0]["karaoke"])

    def test_subject_crop_uses_time_expression_and_safe_fallback(self):
        graph, status = crop_filter(1920, 1080, "9:16", [{"timestamp": 2, "subject_box": {"x": 600, "y": 100, "width": 100, "height": 200}}])
        self.assertIn("if(lt(t,2.000)", graph)
        self.assertEqual(status, "subject_track")
        _graph, fallback = crop_filter(1920, 1080, "16:9", [])
        self.assertEqual(fallback, "safe_center")

    @patch("app.services.generate_video._has_existing_file", side_effect=lambda path: path in {"voice.mp3", "bgm.mp3"})
    @patch("app.services.generate_video._probe_video", return_value={"duration": 2, "width": 1080, "height": 1920, "has_audio": True})
    def test_audio_mix_ducks_bgm_and_normalizes(self, _probe, _exists):
        command, _temp_files, _duration = _build_ffmpeg_merge_command(
            "video.mp4", "voice.mp3", "out.mp4", None, "bgm.mp3",
            {"original_audio_volume": 0.2, "subtitle_enabled": False},
        )
        joined = "\n".join(command)
        self.assertIn("sidechaincompress", joined)
        self.assertIn("afftdn,deesser,loudnorm=I=-16", joined)


@unittest.skipUnless(FFMPEG, "缺少 ffmpeg，无法实渲染验证裁切表达式")
class CropTrackSegmentsTests(unittest.TestCase):
    """用 ffmpeg 实渲染验证多点裁切：三段各自取到对应主体，而不是只用最后一个点。"""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.source = cls.root / "moving.mp4"
        strip = ",".join(
            f"drawbox=x={x}:y=460:w=120:h=160:color={color}:t=fill"
            for x, color in ((420, "red"), (900, "green"), (1380, "blue"))
        )
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=black:s=1920x1080:d=6:r=10", "-vf", strip,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.source)],
            check=True,
        )
        cls.track = [
            {"timestamp": 0.0, "subject_box": {"x": 200, "y": 420, "width": 100, "height": 200}},
            {"timestamp": 2.0, "subject_box": {"x": 450, "y": 420, "width": 100, "height": 200}},
            {"timestamp": 4.0, "subject_box": {"x": 700, "y": 420, "width": 100, "height": 200}},
        ]
        graph, cls.status = crop_filter(1920, 1080, "9:16", cls.track)
        cls.cropped = cls.root / "cropped.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(cls.source), "-vf", graph,
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cls.cropped)],
            check=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def _center_color(self, moment: float) -> tuple[int, int, int]:
        width, height = 606, 1080
        return _segment_color(self.cropped, moment, width, width // 2, height // 2)

    def test_first_segment_keeps_first_subject(self):
        red, green, blue = self._center_color(1.0)
        self.assertGreater(red, 150)
        self.assertLess(green, 80)
        self.assertLess(blue, 80)

    def test_second_segment_follows_second_subject(self):
        red, green, blue = self._center_color(3.0)
        self.assertGreater(green, 90)
        self.assertLess(red, 80)
        self.assertLess(blue, 80)

    def test_third_segment_follows_third_subject(self):
        red, green, blue = self._center_color(5.0)
        self.assertGreater(blue, 150)
        self.assertLess(red, 80)
        self.assertLess(green, 80)

    def test_expression_nests_every_segment(self):
        graph, _status = crop_filter(1920, 1080, "9:16", self.track)
        x_expression = graph.split(":x='")[1].split("':y=")[0]
        self.assertEqual(x_expression.count("if(lt(t,"), 3)
        self.assertLess(x_expression.index("2.000"), x_expression.index("4.000"))


@unittest.skipUnless(FFMPEG, "缺少 ffmpeg，无法实渲染验证裁切表达式")
class CropCenterFallbackTests(unittest.TestCase):
    def test_before_first_sample_crop_stays_centered(self):
        track = [{"timestamp": 2.0, "subject_box": {"x": 900, "y": 460, "width": 100, "height": 200}}]
        graph, _status = crop_filter(1920, 1080, "9:16", track)
        self.assertIn("if(lt(t,2.000),iw/2", graph)


@unittest.skipUnless(FFMPEG, "缺少 ffmpeg，无法实渲染验证主体跟拍")
class WalkingSubjectTests(unittest.TestCase):
    """验收场景：主体从左走到右，逐秒检查主体是否始终落在裁切框内。"""

    def test_walking_subject_stays_inside_crop_every_second(self):
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "walk.mp4"
            steps = [f"drawbox=x={100 + index * 160}:y=460:w=120:h=160:color=white:t=fill:enable='between(t,{index},{index + 1})'" for index in range(10)]
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=1920x1080:d=10:r=10", "-vf", ",".join(steps),
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(source)],
                check=True,
            )
            track = [
                {
                    "timestamp": float(index),
                    "subject_box": {
                        "x": (100 + index * 160) / 1920 * 1000,
                        "y": 420 / 1080 * 1000,
                        "width": 120 / 1920 * 1000,
                        "height": 160 / 1080 * 1000,
                    },
                }
                for index in range(10)
            ]
            graph, status = crop_filter(1920, 1080, "9:16", track)
            self.assertEqual(status, "subject_track")
            cropped = root / "cropped.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-vf", graph,
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", str(cropped)],
                check=True,
            )
            width, height = 606, 1080
            centers = []
            for second in range(10):
                frame = root / f"walk-{second}.png"
                subprocess.run(
                    ["ffmpeg", "-y", "-v", "error", "-ss", f"{second + 0.5}", "-i", str(cropped),
                     "-frames:v", "1", str(frame)],
                    check=True,
                )
                image = Image.open(frame).convert("L")
                pixels = image.load()
                columns = [
                    x for x in range(width)
                    if any(pixels[x, y] > 140 for y in range(0, height, 20))
                ]
                self.assertTrue(columns, f"第 {second + 0.5}s 主体不在裁切框内")
                centers.append(sum(columns) / len(columns))
            # 主体在源片里走了约 1600px：靠左边缘的点被裁切边界夹住，其余都应落在画面中心附近
            self.assertTrue(all(abs(center - width / 2) < 40 for center in centers[1:]), centers)
            self.assertAlmostEqual(centers[-1], width / 2, delta=40)


class DesignedSubtitleTests(unittest.TestCase):
    def _ass(self, script, tts_results, **kwargs) -> str:
        with tempfile.TemporaryDirectory() as temp:
            path = write_ass(Path(temp) / "designed.ass", script, tts_results, **kwargs)
            return path.read_text(encoding="utf-8-sig")

    def test_play_res_follows_landscape_output_size(self):
        content = self._ass(
            [{"_id": 1, "narration": "横屏字幕", "timestamp": "00:00:00,000-00:00:02,000"}],
            [{"_id": 1, "text": "横屏字幕", "duration": 2}],
            play_res_x=1920,
            play_res_y=1080,
        )
        self.assertIn("PlayResX: 1920", content)
        self.assertIn("PlayResY: 1080", content)

    def test_play_res_follows_portrait_output_size(self):
        content = self._ass(
            [{"_id": 1, "narration": "竖屏字幕", "timestamp": "00:00:00,000-00:00:02,000"}],
            [{"_id": 1, "text": "竖屏字幕", "duration": 2}],
            play_res_x=DIMENSIONS["9:16"][0],
            play_res_y=DIMENSIONS["9:16"][1],
        )
        self.assertIn("PlayResX: 1080", content)
        self.assertIn("PlayResY: 1920", content)

    def test_subject_in_lower_half_moves_subtitle_up(self):
        content = self._ass(
            [{"_id": 1, "narration": "主体在下半部", "timestamp": "00:00:00,000-00:00:02,000"}],
            [{"_id": 1, "text": "主体在下半部", "duration": 2}],
            subject_track=[{"timestamp": 0.0, "subject_box": {"x": 300, "y": 700, "width": 200, "height": 200}}],
        )
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.00,Top,", content)

    def test_subject_in_upper_half_keeps_subtitle_at_bottom(self):
        content = self._ass(
            [{"_id": 1, "narration": "主体在上半部", "timestamp": "00:00:00,000-00:00:02,000"}],
            [{"_id": 1, "text": "主体在上半部", "duration": 2}],
            subject_track=[{"timestamp": 0.0, "subject_box": {"x": 300, "y": 80, "width": 200, "height": 200}}],
        )
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.00,Bottom,", content)

    def test_without_subject_track_subtitle_stays_at_bottom(self):
        content = self._ass(
            [{"_id": 1, "narration": "没有轨迹", "timestamp": "00:00:00,000-00:00:02,000"}],
            [{"_id": 1, "text": "没有轨迹", "duration": 2}],
        )
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:02.00,Bottom,", content)
        self.assertNotIn(",Top,,", content)

    def test_alignment_follows_nearest_track_moment(self):
        script = [
            {"_id": 1, "narration": "前段", "timestamp": "00:00:00,000-00:00:02,000"},
            {"_id": 2, "narration": "后段", "timestamp": "00:00:08,000-00:00:10,000"},
        ]
        track = [
            {"timestamp": 0.0, "subject_box": {"x": 300, "y": 100, "width": 100, "height": 100}},
            {"timestamp": 9.0, "subject_box": {"x": 300, "y": 800, "width": 100, "height": 100}},
        ]
        events = build_events(
            script,
            [{"_id": 1, "text": "前段", "duration": 2}, {"_id": 2, "text": "后段", "duration": 2}],
            subject_track=track,
        )
        self.assertEqual([event["alignment"] for event in events], [2, 8])

    def test_without_word_timings_keeps_whole_sentence_and_no_highlight(self):
        content = self._ass(
            [{"_id": 1, "narration": "整句字幕", "timestamp": "00:00:00,000-00:00:02,000"}],
            [{"_id": 1, "text": "整句字幕", "duration": 2}],
        )
        self.assertIn("整句字幕", content)
        self.assertNotIn(r"{\k", content)


class RunnerSubtitleWiringTests(unittest.TestCase):
    def _task(self, temp: str, aspect: str = "9:16") -> tuple[Path, dict, list]:
        task_dir = Path(temp)
        (task_dir / "analysis").mkdir(parents=True, exist_ok=True)
        items = [
            {"_id": 1, "narration": "你好世界", "timestamp": "00:00:00,000-00:00:05,000", "clip_duration": 5.0},
            {"_id": 2, "narration": "第二段解说", "timestamp": "00:01:00,000-00:01:05,000", "clip_duration": 5.0},
        ]
        understanding = {
            "tracks": {
                "speech": [
                    {"text": "你好世界", "words": [
                        {"start": 0.0, "end": 0.5, "text": "你好"},
                        {"start": 0.5, "end": 1.0, "text": "世界"},
                    ]},
                    {"text": "第二段解说", "words": [
                        {"start": 60.0, "end": 60.5, "text": "第二段"},
                        {"start": 60.5, "end": 61.0, "text": "解说"},
                    ]},
                ],
                "visual": [{"timestamp": 0.0, "subject_box": {"x": 400, "y": 760, "width": 200, "height": 200}}],
            },
        }
        (task_dir / "analysis" / "understanding.json").write_text(
            json.dumps(understanding, ensure_ascii=False), encoding="utf-8"
        )
        spec = {"task_id": "narrate-wired", "aspect": aspect, "clip_mode": "timeline", "subtitles": True}
        return task_dir, spec, items

    def _write_tts_words(self, task_dir: Path, items: list[dict]) -> None:
        (task_dir / "analysis" / "tts_words.json").write_text(
            json.dumps({"schema_version": 1, "items": items}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_designed_subtitles_rebases_words_and_uses_output_play_res(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp, aspect="16:9")
            path = runner._designed_subtitles(task_dir, spec, items)
            self.assertIsNotNone(path)
            content = path.read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 1920", content)
        self.assertIn("PlayResY: 1080", content)
        self.assertIn("Top,", content)
        self.assertIn("0:00:05.00", content)

    def test_no_word_timings_falls_back_to_srt_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            (task_dir / "analysis" / "understanding.json").write_text(
                json.dumps({"tracks": {"speech": [], "visual": []}}), encoding="utf-8"
            )
            self.assertIsNone(runner._designed_subtitles(task_dir, spec, items))

    def test_missing_understanding_file_falls_back_to_srt_flow(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            (task_dir / "analysis" / "understanding.json").unlink()
            self.assertIsNone(runner._designed_subtitles(task_dir, spec, items))

    def test_word_timings_read_saved_tts_words_as_segment_relative(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            (task_dir / "analysis" / "understanding.json").unlink()
            self._write_tts_words(task_dir, [
                {"_id": 1, "text": "你好世界", "words": [
                    {"start": 0.0, "end": 0.5, "text": "你好"},
                    {"start": 0.5, "end": 1.0, "text": "世界"},
                ]},
                {"_id": 2, "text": "第二段解说", "words": [
                    {"start": 0.0, "end": 0.5, "text": "第二段"},
                    {"start": 0.5, "end": 1.0, "text": "解说"},
                ]},
            ])
            timings = runner._word_timings(task_dir, items, {})
        self.assertEqual(sorted(timings), [1, 2])
        self.assertEqual([word["start"] for word in timings[2]], [0.0, 0.5])
        self.assertEqual([word["text"] for word in timings[2]], ["第二段", "解说"])

    def test_word_timings_prefer_tts_words_over_understanding(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            self._write_tts_words(task_dir, [
                {"_id": 2, "text": "第二段解说", "words": [
                    {"start": 0.0, "end": 0.5, "text": "第二段"},
                    {"start": 0.5, "end": 1.0, "text": "解说"},
                ]},
            ])
            understanding = json.loads(
                (task_dir / "analysis" / "understanding.json").read_text(encoding="utf-8")
            )
            timings = runner._word_timings(task_dir, items, understanding)
        # understanding.json 记的是原片绝对时间 60.0s，落盘文件记的是段内 0.0s：落盘优先且不再扣 source_start。
        self.assertEqual([word["start"] for word in timings[2]], [0.0, 0.5])
        self.assertNotIn(1, timings)

    def test_both_sources_missing_returns_empty_timings_and_srt_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            (task_dir / "analysis" / "understanding.json").unlink()
            timed = runner._clip_timerange(items, spec)
            self.assertEqual(runner._word_timings(task_dir, timed, {}), {})
            self.assertIsNone(runner._designed_subtitles(task_dir, spec, items))

    def test_corrupt_tts_words_falls_back_to_understanding(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            (task_dir / "analysis" / "tts_words.json").write_text("{not json", encoding="utf-8")
            understanding = json.loads(
                (task_dir / "analysis" / "understanding.json").read_text(encoding="utf-8")
            )
            timings = runner._word_timings(task_dir, items, understanding)
        self.assertEqual([word["start"] for word in timings[2]], [0.0, 0.5])

    def test_saved_tts_words_produce_karaoke_ass_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            self._write_tts_words(task_dir, [
                {"_id": 1, "text": "你好世界", "words": [
                    {"start": 0.0, "end": 0.5, "text": "你好"},
                    {"start": 0.5, "end": 1.0, "text": "世界"},
                ]},
                {"_id": 2, "text": "第二段解说", "words": [
                    {"start": 0.0, "end": 0.5, "text": "第二段"},
                    {"start": 0.5, "end": 1.0, "text": "解说"},
                ]},
            ])
            path = runner._designed_subtitles(task_dir, spec, items)
            self.assertIsNotNone(path)
            self.assertEqual(path.name, "designed.ass")
            self.assertTrue(path.is_file())
            content = path.read_text(encoding="utf-8-sig")
        self.assertIn(r"{\k50}你好", content)
        self.assertIn(r"{\k50}世界", content)
        self.assertIn(r"{\k50}解说", content)
        self.assertIn("Dialogue: 0,0:00:00.00,0:00:00.50,", content)
        self.assertIn("Dialogue: 0,0:00:05.00,0:00:05.50,", content)

    def test_clip_mode_narration_does_not_guess_composition_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir, spec, items = self._task(temp)
            spec["clip_mode"] = "narration"
            self.assertIsNone(runner._designed_subtitles(task_dir, spec, items))

    def test_burn_failure_keeps_original_subtitles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "clean.mp4"
            source.write_bytes(b"video")
            target = root / "burned.mp4"
            with patch("engine.runner.subprocess.run", side_effect=OSError("缺少 libass")):
                ok = runner._burn_ass(source, target, root / "designed.ass", log=lambda _m: None)
        self.assertFalse(ok)
        self.assertFalse(target.exists())

    def test_burn_uses_subtitles_filter_and_keeps_audio(self):
        seen = {}

        def fake_run(command, **kwargs):
            seen["command"] = command
            Path(command[-1]).write_bytes(b"video")
            return SimpleNamespace(returncode=0, stderr="")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "clean.mp4"
            source.write_bytes(b"video")
            with patch("engine.runner.subprocess.run", side_effect=fake_run):
                ok = runner._burn_ass(source, root / "burned.mp4", root / "designed.ass", log=lambda _m: None)
        self.assertTrue(ok)
        command = seen["command"]
        self.assertIn("subtitles='", " ".join(command))
        self.assertEqual(command[command.index("-c:a") + 1], "copy")

    def test_run_calls_write_ass_path(self):
        lines = Path(runner.__file__).read_text(encoding="utf-8").splitlines()
        called = next(index for index, line in enumerate(lines, 1)
                      if "ass_path = _designed_subtitles(task_dir, spec, items)" in line)
        burned = next(index for index, line in enumerate(lines, 1)
                      if "_burn_ass(clean, burned, ass_path, log=log)" in line)
        quality = next(index for index, line in enumerate(lines, 1)
                       if "evaluate_quality(task_dir, spec, items, output_path)" in line)
        self.assertLess(called, burned)
        self.assertLess(burned, quality)


class TtsWordsPersistenceTests(unittest.TestCase):
    def test_saved_words_round_trip_to_task_analysis_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("app.services.task.utils.task_dir", return_value=temp):
                task._save_tts_words("narrate-words", [
                    {"_id": 1, "text": "你好世界", "words": [
                        {"start": 0.0, "end": 0.5, "text": "你好"},
                        {"start": 0.5, "end": 1.0, "text": "世界"},
                    ]},
                    {"_id": 2, "text": "没有词级时间戳", "words": []},
                    {"_id": 3, "text": "字段缺失"},
                ])
            payload = json.loads(
                (Path(temp) / "analysis" / "tts_words.json").read_text(encoding="utf-8")
            )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual([item["_id"] for item in payload["items"]], [1])
        self.assertEqual(payload["items"][0]["text"], "你好世界")
        self.assertEqual(
            payload["items"][0]["words"],
            [{"start": 0.0, "end": 0.5, "text": "你好"}, {"start": 0.5, "end": 1.0, "text": "世界"}],
        )

    def test_no_words_writes_nothing_and_write_failure_is_swallowed(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("app.services.task.utils.task_dir", return_value=temp):
                task._save_tts_words("narrate-words", [{"_id": 1, "text": "空", "words": []}])
                self.assertFalse((Path(temp) / "analysis" / "tts_words.json").exists())
                with patch("app.services.task.utils.task_dir", side_effect=OSError("只读磁盘")):
                    task._save_tts_words("narrate-words", [
                        {"_id": 1, "text": "你好", "words": [{"start": 0.0, "end": 0.5, "text": "你好"}]},
                    ])

    def test_saved_words_feed_designed_subtitles_end_to_end(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch("app.services.task.utils.task_dir", return_value=temp):
                task._save_tts_words("narrate-words", [
                    {"_id": 1, "text": "你好世界", "words": [
                        {"start": 0.0, "end": 0.5, "text": "你好"},
                        {"start": 0.5, "end": 1.0, "text": "世界"},
                    ]},
                ])
            task_dir = Path(temp)
            spec = {"task_id": "narrate-words", "aspect": "9:16", "clip_mode": "timeline", "subtitles": True}
            items = [{"_id": 1, "narration": "你好世界", "timestamp": "00:00:00,000-00:00:05,000", "clip_duration": 5.0}]
            path = runner._designed_subtitles(task_dir, spec, items)
            self.assertIsNotNone(path)
            content = path.read_text(encoding="utf-8-sig")
        self.assertIn(r"{\k50}你好", content)

    def test_start_subclip_unified_saves_words_after_tts(self):
        lines = Path(task.__file__).read_text(encoding="utf-8").splitlines()
        start = next(index for index, line in enumerate(lines, 1)
                     if line.startswith("def start_subclip_unified("))
        within = lambda marker: next(index for index, line in enumerate(lines[start:], start + 1)
                                     if marker in line)
        tts = within("tts_results = voice.tts_multiple(")
        saved = within("_save_tts_words(task_id, tts_results)")
        clipped = within("video_clip_result = clip_video.clip_video_unified(")
        self.assertLess(tts, saved)
        self.assertLess(saved, clipped)


if __name__ == "__main__":
    unittest.main()
