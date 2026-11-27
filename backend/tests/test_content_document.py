import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import understanding
from app.services.chenze_pack import load_identity, load_rules, pack_dir
from app.services.content_document import chenze_notice, compose_document


def verified_account() -> dict:
    anchor = load_identity()
    return {
        "display_name": anchor["display_name"],
        "douyin_id": anchor["douyin_id"],
        "homepage_url": anchor["homepage_url"],
        "column": "游戏",
    }


def speech() -> list[dict]:
    return [
        {"start": 0.0, "end": 3.0, "text": "如果规则是这样"},
        {"start": 3.2, "end": 6.0, "text": "所以先把这句说完"},
        {"start": 8.0, "end": 11.0, "text": "后面换了一个话题"},
    ]


class RulesStayOffTests(unittest.TestCase):
    def test_twenty_eight_rules_stay_disabled(self):
        path = pack_dir() / "rules-draft.json"
        before = path.read_bytes()
        rules = load_rules()
        self.assertEqual(len(rules), 28)
        self.assertTrue(all(rule.get("enabled_for_automatic_editing") is False for rule in rules))
        video = {"id": 4, "title": "整场", "chenze_style": True, "chenze": verified_account()}
        notice = chenze_notice(video, speech())
        self.assertTrue(notice["style_informed"])
        self.assertEqual(notice["applied_rule_ids"], [])
        self.assertEqual(notice["enabled_rule_ids"], [])
        self.assertFalse(notice["validated_person_style"])
        self.assertFalse(notice["listening_conclusion"])
        self.assertFalse(notice["human_accepted_style"])
        markdown, _meta = compose_document(video, {
            "duration": 11,
            "tracks": {"speech": speech()},
            "structure": [{"start": 0, "end": 11, "role": "整段", "reason": "口播"}],
            "suggested_cuts": [{"start": 0, "end": 6, "kind": "keep", "reason": "连续口播"}],
            "people": [],
            "scenes": [],
            "sources": {"summary": "local", "visual": "skipped"},
            "chenze_notice": notice,
        })
        self.assertIn("风格参考", markdown)
        self.assertIn("不是听记结论", markdown)
        self.assertIn("不是已经人工接受的个人风格", markdown)
        self.assertEqual(path.read_bytes(), before)
        again = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(again["rules"]), 28)
        self.assertTrue(all(rule["enabled_for_automatic_editing"] is False for rule in again["rules"]))


class IdentityMismatchTests(unittest.TestCase):
    def test_missing_trailing_dot_does_not_apply_chenze_style(self):
        options = verified_account()
        options["douyin_id"] = options["douyin_id"].replace(".", "")
        video = {"id": 5, "title": "整场", "chenze_style": True, "chenze": options}
        notice = chenze_notice(video, speech())
        self.assertFalse(notice["style_informed"])
        self.assertFalse(notice["treated_as_self"])
        self.assertEqual(notice["applied_rule_ids"], [])
        self.assertEqual(notice["units"], [])
        markdown, meta = compose_document(video, {
            "duration": 11,
            "tracks": {"speech": speech()},
            "structure": [],
            "suggested_cuts": [],
            "people": [],
            "scenes": [],
            "sources": {"summary": "local", "visual": "skipped"},
            "chenze_notice": notice,
        })
        self.assertFalse(meta["chenze"]["style_informed"])
        self.assertIn("没有套用陈泽风格", markdown)
        self.assertNotIn("按知识包的事件单元整理", markdown)


class DocumentWithoutModelsTests(unittest.TestCase):
    @patch("app.services.understanding.update_video")
    @patch("app.services.understanding.get_video")
    @patch("app.services.understanding._request_json", side_effect=AssertionError("不该请求模型"))
    @patch("app.services.understanding.detect_beats", return_value=[])
    @patch("app.services.understanding.analyze_loudness", return_value={})
    @patch("app.services.understanding.detect_silence", return_value=[{"start": 6.0, "end": 7.6}])
    @patch("app.services.understanding.detect_scene_cuts", return_value=[6.5])
    @patch("app.services.ai_edit.transcribe")
    def test_document_is_written_from_local_transcript(
        self, transcribe, _cuts, _silence, _loudness, _beats, _request, get_video, update,
    ):
        transcribe.return_value = speech()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.mp4"
            path.write_bytes(b"video")
            get_video.return_value = {
                "id": 8,
                "title": "整场直播",
                "filename": path.name,
                "duration": 11,
                "chenze_style": False,
                "chenze": None,
            }
            with patch("app.services.understanding.VIDEOS_DIR", Path(temp)), \
                    patch("app.services.understanding.UNDERSTANDING_DIR", Path(temp) / "analysis"), \
                    patch("app.services.understanding.AI_MODEL", ""), \
                    patch("app.services.understanding.VISION_MODEL", ""), \
                    patch("app.services.understanding.VISION_API_KEY", ""):
                result = understanding.analyze_video(8)
            document = (Path(temp) / "analysis" / "8.md").read_text(encoding="utf-8")
        self.assertIn("如果规则是这样", document)
        self.assertIn("所以先把这句说完", document)
        self.assertIn("后面换了一个话题", document)
        self.assertIn("## 结构", document)
        self.assertIn("## 人物与场景", document)
        self.assertIn("人物：未识别", document)
        self.assertIn("场景：未识别", document)
        self.assertIn("## 建议切点", document)
        self.assertIn("难句", document)
        self.assertIn("没有解决", document)
        self.assertNotIn("已经听对", document)
        self.assertEqual(result["sources"]["summary"], "local")
        self.assertEqual(result["sources"]["visual"], "skipped")
        self.assertTrue(result["suggested_cuts"])
        self.assertEqual(update.call_args.kwargs["analysis_status"], "partial")
        self.assertIn("未配置文本模型", update.call_args.kwargs["analysis_error"])


if __name__ == "__main__":
    unittest.main()
