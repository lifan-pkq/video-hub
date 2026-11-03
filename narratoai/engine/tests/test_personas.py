import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import script, spec


NEUTRAL_PAYLOAD = {
    "items": [
        {
            "_id": 1,
            "timestamp": "00:00:00,000-00:00:05,000",
            "picture": "选手在弯道加速",
            "narration": "选手在弯道突然加速，把对手甩在身后。",
            "OST": 2,
        },
        {
            "_id": 2,
            "timestamp": "00:00:05,000-00:00:10,000",
            "picture": "选手冲过终点",
            "narration": "最后他第一个冲过终点，赢下这场比赛。",
            "OST": 2,
        },
    ]
}


class PersonaSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.video = Path(self.temp.name) / "source.mp4"
        self.video.write_bytes(b"video")

    def test_default_persona_is_generic_and_natural(self):
        item = spec.normalize_spec({"video_path": str(self.video)})

        self.assertEqual(item.get("persona"), "auto")
        self.assertEqual(item.get("persona_strength"), "natural")

    def test_persona_and_strength_are_normalized(self):
        item = spec.normalize_spec({
            "video_path": str(self.video),
            "persona": "CHENBO",
            "persona_strength": "strong",
        })

        self.assertEqual(item.get("persona"), "chenbo")
        self.assertEqual(item.get("persona_strength"), "strong")

    def test_unknown_persona_and_strength_fall_back_safely(self):
        item = spec.normalize_spec({
            "video_path": str(self.video),
            "persona": "someone-else",
            "persona_strength": "extreme",
        })

        self.assertEqual(item.get("persona"), "auto")
        self.assertEqual(item.get("persona_strength"), "natural")


class PersonaGenerationTests(unittest.TestCase):
    def setUp(self):
        self.spec = {
            "content_type": "auto",
            "topic": "赛车比赛",
            "instruction": "",
            "target_duration": 10,
            "video_duration": 10,
            "language": "中文",
            "coverage": "highlights",
            "analysis_mode": "fast",
            "persona": "chenze",
            "persona_strength": "signature",
        }

    def test_generic_persona_keeps_single_model_call(self):
        self.spec["persona"] = "auto"
        with patch("engine.script._text_settings", return_value=("model", "url", ["key"])), \
                patch("engine.script._chat_text", return_value=json.dumps(NEUTRAL_PAYLOAD)) as chat:
            items = script.generate_items("# 比赛素材", self.spec, log=lambda _: None)

        self.assertEqual(chat.call_count, 1)
        self.assertEqual(items[0]["narration"], NEUTRAL_PAYLOAD["items"][0]["narration"])

    def test_persona_rewrite_preserves_timestamps_pictures_and_ost(self):
        styled = {
            "items": [
                {"_id": 1, "narration": "老铁们，这个弯道一加速，对手连尾灯都看不见了。"},
                {"_id": 2, "narration": "冲线了，第一名就是他的，没得说。"},
            ]
        }
        with patch("engine.script._text_settings", return_value=("model", "url", ["key"])), \
                patch("engine.script._chat_text", side_effect=[json.dumps(NEUTRAL_PAYLOAD), json.dumps(styled)]) as chat:
            items = script.generate_items("# 比赛素材", self.spec, log=lambda _: None)

        self.assertEqual(chat.call_count, 2)
        second_prompt = chat.call_args_list[1].args[3][1]["content"]
        self.assertIn("陈泽", second_prompt)
        self.assertIn(NEUTRAL_PAYLOAD["items"][0]["narration"], second_prompt)
        for index, item in enumerate(items):
            original = NEUTRAL_PAYLOAD["items"][index]
            self.assertEqual(item["timestamp"], original["timestamp"])
            self.assertEqual(item["picture"], original["picture"])
            self.assertEqual(item["OST"], original["OST"])
        self.assertEqual(items[0]["narration"], styled["items"][0]["narration"])

    def test_persona_rewrite_rejects_missing_segments(self):
        incomplete = {"items": [{"_id": 1, "narration": "只有第一段"}]}
        with patch("engine.script._text_settings", return_value=("model", "url", ["key"])), \
                patch("engine.script._chat_text", side_effect=[json.dumps(NEUTRAL_PAYLOAD), json.dumps(incomplete)]), \
                self.assertRaises(script.ScriptGenerationError):
            script.generate_items("# 比赛素材", self.spec, log=lambda _: None)

    def test_catchphrase_density_is_limited_without_changing_timeline(self):
        items = [
            {"_id": index + 1, "timestamp": f"00:00:0{index},000-00:00:0{index + 1},000", "narration": f"第{index + 1}段，天花板级别。", "picture": "画面", "OST": 2}
            for index in range(4)
        ]

        self.assertTrue(hasattr(script, "_enforce_persona_limits"))
        limited = script._enforce_persona_limits(items, "chenbo", "natural")

        self.assertLessEqual(sum("天花板" in item["narration"] for item in limited), 1)
        self.assertEqual([item["timestamp"] for item in limited], [item["timestamp"] for item in items])


if __name__ == "__main__":
    unittest.main()
