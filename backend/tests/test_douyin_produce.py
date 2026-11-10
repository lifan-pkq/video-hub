"""抖音一键成片：选段、文案、竖屏裁切和任务入口。"""

import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from app.services.ai_edit import AIResponseError, AIServiceError
from app.services.douyin_plan import (
    ass_text,
    ass_timestamp,
    build_ass,
    build_captions,
    build_subject_track,
    boxes_from_understanding,
    clean_douyin_clips,
    clip_budget,
    crop_origin,
    douyin_prompt,
    fallback_highlights,
    normalize_copy,
    plan_douyin_shorts,
    portrait_crop_size,
    sample_times,
    skin_subject_box,
    vertical_filter,
)


def speech(duration: float, step: float = 4.0, text: str = "这是一句可以单独听懂的口播") -> list[dict]:
    segments = []
    cursor = 0.0
    while cursor < duration - 1:
        segments.append({
            "start": round(cursor, 2),
            "end": round(min(duration, cursor + step - 0.2), 2),
            "text": f"{text}{int(cursor)}",
        })
        cursor += step
    return segments


class DouyinBudgetTests(unittest.TestCase):
    def test_budget_grows_with_duration_and_caps_at_ten(self):
        self.assertEqual(clip_budget(15), 1)
        self.assertEqual(clip_budget(30), 1)
        self.assertEqual(clip_budget(140), 2)
        self.assertEqual(clip_budget(600, cap=3), 3)
        self.assertEqual(clip_budget(900), 10)
        self.assertEqual(clip_budget(7200), 10)

    def test_settings_clamp_output_size(self):
        from app.config import douyin_settings

        with patch.dict(os.environ, {
            "VIDEO_HUB_DOUYIN_WIDTH": "4000",
            "VIDEO_HUB_DOUYIN_HEIGHT": "10",
            "VIDEO_HUB_DOUYIN_MAX_CLIPS": "0",
            "VIDEO_HUB_DOUYIN_MIN_SECONDS": "nope",
            "VIDEO_HUB_DOUYIN_FFMPEG_THREADS": "8",
        }):
            settings = douyin_settings()
        self.assertEqual(settings["width"], 1080)
        self.assertEqual(settings["height"], 480)
        self.assertEqual(settings["max_clips"], 1)
        self.assertEqual(settings["min_seconds"], 12)
        self.assertEqual(settings["threads"], 2)


class DouyinPlanTests(unittest.TestCase):
    def test_clean_trims_long_range_and_packages_copy(self):
        segments = speech(120, step=2)
        cleaned = clean_douyin_clips(
            [{
                "start": 10,
                "end": 100,
                "title": "太长了 #直播",
                "hashtags": ["话题", "#话题", ""],
                "description": "说明",
                "score": 9,
                "hook": "先抛冲突",
            }],
            120,
            segments,
            max_clips=3,
            min_seconds=12,
            max_seconds=60,
        )
        self.assertEqual(len(cleaned), 1)
        length = cleaned[0]["end"] - cleaned[0]["start"]
        self.assertGreaterEqual(length, 12)
        self.assertLessEqual(length, 60.75)
        self.assertEqual(cleaned[0]["title"], "太长了")
        self.assertEqual(cleaned[0]["score"], 5)
        self.assertTrue(cleaned[0]["hashtags"][0].startswith("#"))
        self.assertEqual(len({tag.lower() for tag in cleaned[0]["hashtags"]}), len(cleaned[0]["hashtags"]))
        self.assertTrue(cleaned[0]["captions"])
        self.assertGreaterEqual(cleaned[0]["captions"][0]["start"], 0)

    def test_overlap_keeps_higher_score(self):
        segments = speech(80, step=2)
        cleaned = clean_douyin_clips(
            [
                {"start": 0, "end": 20, "title": "甲", "score": 2, "hashtags": ["#甲"], "description": "甲"},
                {"start": 8, "end": 30, "title": "乙", "score": 5, "hashtags": ["#乙"], "description": "乙"},
                {"start": 40, "end": 58, "title": "丙", "score": 4, "hashtags": ["#丙"], "description": "丙"},
            ],
            80,
            segments,
            max_clips=10,
            min_seconds=12,
            max_seconds=60,
        )
        titles = [item["title"] for item in cleaned]
        self.assertIn("乙", titles)
        self.assertNotIn("甲", titles)
        self.assertIn("丙", titles)
        self.assertLess(cleaned[0]["start"], cleaned[1]["start"])

    def test_captions_are_relative_and_wrapped(self):
        segments = [{"start": 10, "end": 16, "text": "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"}]
        cues = build_captions(segments, 9, 20)
        self.assertGreaterEqual(cues[0]["start"], 0.9)
        self.assertLess(cues[0]["start"], 2)
        self.assertGreater(len(cues), 1)
        self.assertIn("\n", cues[0]["text"])
        self.assertNotIn("{", ass_text("{你好}"))
        self.assertEqual(ass_timestamp(65.2), "0:01:05.20")

    def test_copy_fills_title_and_topics_from_speech(self):
        copy = normalize_copy({}, "今天把这件事说清楚")
        self.assertTrue(copy["title"])
        self.assertGreaterEqual(len(copy["hashtags"]), 2)
        self.assertTrue(copy["description"])

    def test_short_source_rejected_and_service_error_is_not_hidden(self):
        with self.assertRaises(AIResponseError):
            plan_douyin_shorts([{"start": 0, "end": 2, "text": "太短"}], 4, request_ai=lambda *_: [])

        def explode(prompt, log):
            raise AIServiceError("AI 接口错误 401：bad")

        with self.assertRaises(AIServiceError):
            plan_douyin_shorts(speech(40), 40, request_ai=explode)

    def test_model_plan_is_used_and_empty_answer_falls_back(self):
        segments = speech(40)
        seen = {}

        def ask(prompt, log):
            seen["prompt"] = prompt
            return [{
                "start": 2,
                "end": 24,
                "title": "钩子",
                "hashtags": ["#测试"],
                "description": "简介",
                "score": 5,
            }]

        planned = plan_douyin_shorts(segments, 40, instruction="只要金句", request_ai=ask)
        self.assertEqual(planned["source"], "llm")
        self.assertEqual(planned["clips"][0]["title"], "钩子")
        self.assertIn("9:16", seen["prompt"])
        self.assertIn("只要金句", seen["prompt"])

        fallback = plan_douyin_shorts(speech(180), 180, request_ai=lambda prompt, log: [])
        self.assertEqual(fallback["source"], "heuristic")
        self.assertGreaterEqual(len(fallback["clips"]), 1)
        self.assertLessEqual(len(fallback["clips"]), fallback["budget"])
        for left, right in zip(fallback["clips"], fallback["clips"][1:]):
            self.assertLessEqual(left["end"], right["start"] + 0.35)

    def test_fallback_covers_a_long_talk(self):
        clips = fallback_highlights(speech(600), 600, clip_budget(600))
        self.assertGreaterEqual(len(clips), 3)
        self.assertLessEqual(len(clips), 10)
        for clip in clips:
            self.assertGreaterEqual(clip["duration"], 12)
            self.assertLessEqual(clip["duration"], 60.75)
            self.assertTrue(clip["captions"])


class DouyinFramingTests(unittest.TestCase):
    def test_subject_box_pulls_crop_off_center(self):
        crop_w, crop_h = portrait_crop_size(1920, 1080)
        self.assertLess(crop_w / crop_h, 0.58)
        center_x, _ = crop_origin(1920, 1080, crop_w, crop_h, None)
        left_x, _ = crop_origin(1920, 1080, crop_w, crop_h, {"x": 40, "y": 200, "width": 80, "height": 220})
        right_x, _ = crop_origin(1920, 1080, crop_w, crop_h, {"x": 800, "y": 180, "width": 150, "height": 280})
        self.assertLess(left_x, center_x)
        self.assertGreater(right_x, center_x)
        graph, mode = vertical_filter(
            1920, 1080,
            [
                {"timestamp": 0, "subject_box": {"x": 40, "y": 200, "width": 80, "height": 220}},
                {"timestamp": 8, "subject_box": {"x": 800, "y": 180, "width": 150, "height": 280}},
            ],
            1080, 1920,
        )
        self.assertEqual(mode, "subject")
        self.assertIn("scale=1080:1920", graph)
        self.assertIn(str(left_x), graph)
        self.assertIn(str(right_x), graph)
        podcast, podcast_mode = vertical_filter(
            1920, 1080,
            [{"timestamp": 0, "subject_box": {"x": 40, "y": 200, "width": 80, "height": 220}}],
            720, 1280, "podcast",
        )
        self.assertEqual(podcast_mode, "podcast")
        self.assertIn("boxblur", podcast)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", podcast)
        self.assertNotIn("if(lt(t,", podcast)

    def test_skin_blob_on_the_left_is_not_center(self):
        width, height = 40, 30
        frame = bytearray([20, 40, 180] * width * height)
        for y in range(4, 16):
            for x in range(2, 11):
                index = (y * width + x) * 3
                frame[index:index + 3] = bytes((220, 160, 130))
        box = skin_subject_box(bytes(frame), width, height)
        self.assertIsNotNone(box)
        self.assertLess(box["x"] + box["width"] / 2, 400)

    def test_speaker_turns_follow_each_person(self):
        segments = [
            {"start": 0, "end": 10, "text": "我先说", "speaker": "host"},
            {"start": 10, "end": 20, "text": "我反驳", "speaker": "guest"},
        ]
        boxes = [
            {"time": 5, "box": {"x": 100, "y": 100, "width": 80, "height": 80}},
            {"time": 15, "box": {"x": 700, "y": 120, "width": 80, "height": 80}},
        ]
        self.assertEqual(len(sample_times(0, 20, segments)), 2)
        track = build_subject_track(0, 20, segments, boxes, [])
        self.assertEqual([item["subject_box"]["x"] for item in track], [100, 700])
        self.assertLess(track[0]["timestamp"], track[1]["timestamp"])
        self.assertEqual(track[0]["timestamp"], 5)

    def test_understanding_boxes_are_optional(self):
        self.assertEqual(boxes_from_understanding(None), [])
        boxes = boxes_from_understanding({
            "tracks": {"visual": [
                {"timestamp": 1.5, "subject_box": {"x": 10, "y": 10, "width": 10, "height": 10}},
                {"timestamp": "bad", "subject_box": {"x": 1, "y": 1, "width": 1, "height": 1}},
                {"timestamp": 3, "description": "没有框"},
            ]},
        })
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0]["time"], 1.5)

    def test_ass_uses_output_frame(self):
        script = build_ass([{"start": 0.2, "end": 1.4, "text": "你好\n世界"}], 1080, 1920, "WenQuanYi Micro Hei")
        self.assertIn("PlayResX: 1080", script)
        self.assertIn("PlayResY: 1920", script)
        self.assertIn("你好\\N世界", script)


class DouyinRenderTests(unittest.TestCase):
    def test_render_writes_vertical_video_cover_and_pack(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg required")
        from app.services.douyin_render import build_clip_zip, build_pack_zip, render_short, write_copy_files, write_cover

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error",
                    "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(source),
                ],
                check=True,
                timeout=60,
            )
            folder = root / "01"
            clip = {
                "title": "测试短视频",
                "hashtags": ["#测试"],
                "description": "一条说明",
                "hook": "开头",
                "start": 0.4,
                "end": 3.2,
            }
            write_copy_files(folder, clip)
            rendered = render_short(
                source,
                folder / "video.mp4",
                start=0.4,
                end=3.2,
                src_w=640,
                src_h=360,
                has_audio=True,
                track=[{"timestamp": 0.2, "subject_box": {"x": 80, "y": 120, "width": 120, "height": 200}}],
                captions=[{"start": 0.2, "end": 1.5, "text": "你好"}],
                out_w=270,
                out_h=480,
                threads=1,
            )
            self.assertEqual(rendered["framing"], "subject")
            self.assertTrue(rendered["captions_burned"])
            self.assertTrue(write_cover(folder / "video.mp4", folder / "cover.jpg"))
            archive = build_pack_zip(root, [{
                "index": 1,
                **clip,
                "framing": rendered["framing"],
                "captions_burned": True,
            }])
            with zipfile.ZipFile(archive) as packed:
                names = set(packed.namelist())
            self.assertIn("01/video.mp4", names)
            self.assertIn("01/cover.jpg", names)
            self.assertIn("01/copy.txt", names)
            self.assertIn("manifest.json", names)
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height", "-of", "csv=p=0",
                    str(folder / "video.mp4"),
                ],
                check=True, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(probe.stdout.strip(), "270,480")
            self.assertTrue(build_clip_zip(folder))
            with zipfile.ZipFile(folder / "kit.zip") as packed:
                kit_names = set(packed.namelist())
            self.assertIn("video.mp4", kit_names)
            self.assertIn("cover.jpg", kit_names)
            self.assertIn("copy.txt", kit_names)
            podcast_folder = root / "podcast"
            podcast = render_short(
                source,
                podcast_folder / "video.mp4",
                start=0.2,
                end=2.4,
                src_w=640,
                src_h=360,
                has_audio=True,
                track=[],
                captions=[],
                out_w=270,
                out_h=480,
                threads=1,
                layout="podcast",
            )
            self.assertEqual(podcast["framing"], "podcast")
            podcast_probe = subprocess.run(
                [
                    "ffprobe", "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height", "-of", "csv=p=0",
                    str(podcast_folder / "video.mp4"),
                ],
                check=True, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(podcast_probe.stdout.strip(), "270,480")


class DouyinJobPackagingTests(unittest.TestCase):
    def test_produce_writes_previewable_shorts_and_a_pack(self):
        from app.jobs import create, get
        from app.services.douyin_flow import produce_douyin

        previous = os.environ.get("VIDEO_HUB_DATA_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VIDEO_HUB_DATA_DIR"] = tmp
            try:
                job = create("edit.douyin", "抖音成片", {"video_id": 7, "instruction": "金句"})
                plan = {
                    "source": "llm",
                    "budget": 2,
                    "warnings": [],
                    "clips": [
                        {
                            "title": "甲", "hashtags": ["#甲"], "description": "说明甲", "hook": "先问一句",
                            "reason": "有冲突", "start": 1, "end": 20, "duration": 19, "score": 5,
                            "captions": [{"start": 0.2, "end": 2, "text": "甲"}],
                        },
                        {
                            "title": "乙", "hashtags": ["#乙"], "description": "说明乙", "hook": "",
                            "reason": "", "start": 30, "end": 48, "duration": 18, "score": 4,
                            "captions": [],
                        },
                    ],
                }

                seen = {}

                def fake_render(source, dest, **kwargs):
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(b"fake-mp4")
                    seen["layout"] = kwargs.get("layout")
                    return {"framing": "subject" if kwargs.get("track") else "center", "captions_burned": bool(kwargs.get("captions")), "width": 1080, "height": 1920}

                def fake_cover(video, dest, **kwargs):
                    dest.write_bytes(b"fake-jpg")
                    return True

                with patch("app.services.douyin_flow.fetch", return_value={"id": 7, "title": "原片"}), \
                        patch("app.services.douyin_flow.resolve_media", return_value=Path(tmp) / "source.mp4"), \
                        patch("app.services.douyin_flow.probe_source", return_value={"duration": 80, "width": 1920, "height": 1080, "has_audio": True}), \
                        patch("app.services.douyin_flow.transcribe", return_value=[{"start": 0, "end": 2, "text": "你好"}]), \
                        patch("app.services.douyin_flow.plan_douyin_shorts", return_value=plan), \
                        patch("app.services.douyin_flow.load_subject_boxes", return_value=[]), \
                        patch("app.services.douyin_flow.sample_skin_boxes", return_value=[{"time": 4, "box": {"x": 100, "y": 100, "width": 80, "height": 80}}]), \
                        patch("app.services.douyin_flow.render_short", side_effect=fake_render), \
                        patch("app.services.douyin_flow.write_cover", side_effect=fake_cover):
                    produce_douyin(job["id"], 7, "金句", 0)
                saved = get(job["id"])
            finally:
                if previous is None:
                    os.environ.pop("VIDEO_HUB_DATA_DIR", None)
                else:
                    os.environ["VIDEO_HUB_DATA_DIR"] = previous
        self.assertEqual(saved["status"], "success")
        shorts = saved["result"]["shorts"]
        self.assertEqual([item["title"] for item in shorts], ["甲", "乙"])
        self.assertEqual(shorts[0]["framing"], "subject")
        self.assertEqual(shorts[0]["layout"], "interview")
        self.assertEqual(seen["layout"], "interview")
        self.assertTrue(shorts[0]["captions_burned"])
        self.assertTrue(shorts[0]["file_url"].endswith("/shorts/1/file"))
        self.assertTrue(shorts[0]["cover_url"].endswith("/shorts/1/cover"))
        self.assertTrue(shorts[0]["kit_url"].endswith("/shorts/1/pack"))
        self.assertEqual(saved["result"]["layout"], "interview")
        self.assertTrue(saved["result"]["pack_url"].endswith("/pack"))
        self.assertEqual(saved["input"]["video_id"], 7)


class DouyinRouteTests(unittest.TestCase):
    def setUp(self):
        self._inline = os.environ.get("VIDEO_HUB_JOBS_INLINE")
        self._data = os.environ.get("VIDEO_HUB_DATA_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VIDEO_HUB_JOBS_INLINE"] = "1"
        os.environ["VIDEO_HUB_DATA_DIR"] = self.tmp.name
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        if self._inline is None:
            os.environ.pop("VIDEO_HUB_JOBS_INLINE", None)
        else:
            os.environ["VIDEO_HUB_JOBS_INLINE"] = self._inline
        if self._data is None:
            os.environ.pop("VIDEO_HUB_DATA_DIR", None)
        else:
            os.environ["VIDEO_HUB_DATA_DIR"] = self._data
        self.tmp.cleanup()

    def test_missing_video_fails_before_a_job(self):
        response = self.client.post("/api/edit/douyin", json={"video_id": 999999999})
        self.assertEqual(response.status_code, 404)
        self.assertIn("不存在", response.json()["detail"]["message"])

    def test_produce_job_is_tracked_as_edit_douyin(self):
        from app.jobs import update

        def run(job_id, video_id, instruction, max_clips, layout="interview"):
            update(
                job_id,
                status="success",
                progress=100,
                stage="done",
                label="抖音成片已生成",
                result={"video_id": video_id, "layout": layout, "shorts": [{"index": 1, "title": "一条"}], "pack_url": ""},
            )

        with patch("app.routers.edit.fetch", return_value={"id": 1, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=run):
            response = self.client.post("/api/edit/douyin", json={
                "video_id": 1,
                "instruction": "留金句",
                "max_clips": 4,
            })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["kind"], "edit.douyin")
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["input"]["video_id"], 1)
        self.assertEqual(body["input"]["max_clips"], 4)
        self.assertEqual(body["input"]["layout"], "interview")
        self.assertEqual(body["result"]["layout"], "interview")
        self.assertEqual(body["result"]["shorts"][0]["title"], "一条")

        with patch("app.routers.edit.fetch", return_value={"id": 1, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=run):
            podcast = self.client.post("/api/edit/douyin", json={"video_id": 1, "layout": "podcast"})
        self.assertEqual(podcast.status_code, 200, podcast.text)
        self.assertEqual(podcast.json()["input"]["layout"], "podcast")
        self.assertEqual(podcast.json()["result"]["layout"], "podcast")


if __name__ == "__main__":
    unittest.main()
