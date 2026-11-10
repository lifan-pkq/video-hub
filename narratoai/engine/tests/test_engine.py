import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import script, settings, spec  # noqa: E402
from engine.profiles import CONTENT_PROFILES, build_creative_context  # noqa: E402
from engine.store import TaskStore  # noqa: E402


def _video(root: Path) -> Path:
    path = root / "clip.mp4"
    path.write_bytes(b"fake-video")
    return path


class SpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = _video(self.root)

    def test_generic_defaults_are_not_film_specific(self):
        item = spec.normalize_spec({"video_path": str(self.video)})
        self.assertEqual(item["content_type"], "auto")
        self.assertEqual(item["mode"], "auto")
        self.assertEqual(item["aspect"], "9:16")
        self.assertEqual(item["tts_engine"], "edge_tts")

    def test_narration_defaults_use_compact_subtitles_and_natural_audio(self):
        item = spec.normalize_spec({"video_path": str(self.video)})
        self.assertEqual(item["voice_name"], "zh-CN-YunxiNeural")
        self.assertEqual(item["voice_rate"], 1.05)
        self.assertEqual(item["pause_seconds"], 0.4)
        self.assertEqual(item["original_volume"], 0.15)
        self.assertEqual(item["font_name"], "Noto Sans CJK SC")
        self.assertEqual(item["font_size"], 40)
        self.assertEqual(item["stroke_width"], 0.8)
        self.assertEqual(item["subtitle_max_chars"], 16)

    def test_aspect_follows_source_dimensions(self):
        portrait = spec.normalize_spec({"video_path": str(self.video), "video_width": 1080, "video_height": 1920})
        landscape = spec.normalize_spec({"video_path": str(self.video), "video_width": 1920, "video_height": 1080})
        square = spec.normalize_spec({"video_path": str(self.video), "video_width": 1080, "video_height": 1080})
        self.assertEqual(portrait["aspect"], "9:16")
        self.assertEqual(landscape["aspect"], "16:9")
        self.assertEqual(square["aspect"], "1:1")

    def test_script_text_is_allocated_across_timeline(self):
        item = spec.normalize_spec(
            {
                "video_path": str(self.video),
                "video_duration": 12,
                "script_text": "第一行解说\n第二行解说\n第三行解说",
            }
        )
        self.assertEqual(item["mode"], "script")
        self.assertEqual(len(item["script_items"]), 3)
        self.assertEqual(item["script_items"][0]["_id"], 1)
        self.assertEqual(item["script_items"][0]["timestamp"], "00:00:00,000-00:00:04,000")
        self.assertEqual(item["script_items"][-1]["timestamp"], "00:00:08,000-00:00:12,000")

    def test_script_items_keep_provided_timestamps(self):
        item = spec.normalize_spec(
            {
                "video_path": str(self.video),
                "mode": "script",
                "script_items": [
                    {"narration": "开场", "timestamp": "00:00:01,000-00:00:03,500", "OST": 2},
                ],
            }
        )
        self.assertEqual(item["script_items"][0]["timestamp"], "00:00:01,000-00:00:03,500")

    def test_transcript_mode_requires_text(self):
        with self.assertRaises(spec.SpecError):
            spec.normalize_spec({"video_path": str(self.video), "mode": "transcript"})

    def test_asr_mode_allows_empty_transcript(self):
        item = spec.normalize_spec({"video_path": str(self.video), "mode": "asr"})
        self.assertEqual(item["mode"], "asr")
        self.assertEqual(item["script_items"], [])

    def test_rejects_missing_video(self):
        with self.assertRaises(spec.SpecError):
            spec.normalize_spec({"video_path": str(self.root / "nope.mp4")})

    def test_rejects_bad_timestamp(self):
        with self.assertRaises(spec.SpecError):
            spec.normalize_timestamp("00:00:05-00:00:01")

    def test_subtitle_style_is_normalized(self):
        item = spec.normalize_spec(
            {
                "video_path": str(self.video),
                "subtitles": True,
                "font_name": "",
                "font_size": 72,
                "text_fore_color": "#ffcc00",
                "stroke_color": "bad",
                "stroke_width": 2.5,
                "subtitle_position": "custom",
                "custom_position": 62,
                "subtitle_mask_enabled": True,
                "subtitle_mask_y": 70,
                "subtitle_mask_height": 18,
                "subtitle_mask_blur": 22,
                "subtitle_mask_opacity": 90,
            }
        )
        self.assertEqual(item["font_name"], "")
        self.assertEqual(item["font_size"], 72)
        self.assertEqual(item["text_fore_color"], "#FFCC00")
        self.assertEqual(item["stroke_color"], "#000000")
        self.assertEqual(item["subtitle_y_percent"], 62)
        self.assertTrue(item["subtitle_mask_enabled"])
        self.assertEqual(item["subtitle_mask_y"], 70)


class ProfileTests(unittest.TestCase):
    def test_profiles_cover_generic_content(self):
        for key in ("auto", "knowledge", "documentary", "vlog", "product", "tutorial", "interview", "film", "custom"):
            self.assertIn(key, CONTENT_PROFILES)
        self.assertEqual(build_creative_context("auto")["theme"], "")

    def test_profile_prompt_contains_duration_and_topic(self):
        context = build_creative_context("knowledge", "咖啡", "讲给新手", 90)
        self.assertIn("咖啡", context["theme"])
        self.assertIn("90", context["prompt"])
        self.assertIn("新手", context["prompt"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = TaskStore(self.root)
        self.video = _video(self.root)
        self.spec = spec.normalize_spec({"video_path": str(self.video), "title": "测试"})

    def test_create_update_and_list(self):
        task = self.store.create(self.spec)
        self.assertEqual(task["status"], "queued")
        self.store.update_state(self.spec["task_id"], status="running", progress=42, message="生成中")
        current = self.store.public(self.spec["task_id"])
        self.assertEqual(current["status"], "running")
        self.assertEqual(current["progress"], 42)
        self.assertEqual(len(self.store.list()), 1)
        self.assertTrue((self.store.task_dir(self.spec["task_id"]) / "spec.json").is_file())

    def test_result_and_script_roundtrip(self):
        self.store.create(self.spec)
        self.store.write_script(self.spec["task_id"], [{"narration": "hi", "OST": 2, "timestamp": "00:00:00,000-00:00:01,000"}])
        self.store.write_result(self.spec["task_id"], {"output_path": "/tmp/x.mp4", "duration": 3})
        task = self.store.public(self.spec["task_id"])
        self.assertTrue(task["has_script"])
        self.assertEqual(task["result"]["duration"], 3)

    def test_cancel_terminal_is_idempotent(self):
        self.store.create(self.spec)
        self.store.update_state(self.spec["task_id"], status="success", progress=100)
        result = self.store.cancel(self.spec["task_id"])
        self.assertEqual(result["status"], "success")


class ScriptHelperTests(unittest.TestCase):
    def test_extract_json_handles_fenced_payload(self):
        fence = chr(96) * 3
        raw = fence + "json\n" + '{"items": [{"narration": "a"}]}' + "\n" + fence
        self.assertEqual(script._extract_json(raw)["items"][0]["narration"], "a")

    def test_normalize_items_repairs_single_timestamp(self):
        items = script._normalize_items(
            {"items": [{"timestamp": "00:00:02,500", "narration": "一句话", "picture": "画面"}]},
            30,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["OST"], 2)
        self.assertEqual(items[0]["timestamp"], "00:00:02,500-00:00:07,500")

    def test_transcript_markdown(self):
        text = "第一行\n第二行"
        markdown = script.build_markdown_from_transcript(text)
        self.assertIn("第一行", markdown)
        self.assertIn("第 2 段", markdown)


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_update_and_read_settings(self):
        updated = settings.update_settings(
            self.root,
            {
                "text": {"base_url": "https://api.example.com/v1", "model": "demo-model", "api_key": "sk-test-123456"},
                "vision": {"base_url": "https://api.example.com/v1", "model": "demo-vl", "api_key": "sk-vision-123456"},
                "tts": {"engine": "edge_tts", "voice_name": "zh-CN-XiaoxiaoNeural"},
            },
        )
        self.assertTrue(updated["text"]["configured"])
        self.assertTrue(updated["vision"]["configured"])
        self.assertNotIn("sk-test", json.dumps(updated))
        with patch("engine.settings.env_llm_overrides", return_value={
            "text_base_url": "", "text_api_key": "", "text_model": "",
            "vision_base_url": "", "vision_api_key": "", "vision_model": "",
        }):
            again = settings.settings_payload(self.root)
        self.assertEqual(again["text"]["model"], "demo-model")
        self.assertEqual(again["vision"]["model"], "demo-vl")

    def test_rejects_bad_url(self):
        with self.assertRaises(ValueError):
            settings.update_settings(self.root, {"text": {"base_url": "ftp://bad"}})


if __name__ == "__main__":
    unittest.main()
