import json
import unittest
from unittest.mock import patch

from engine import script


class ScriptReviewTests(unittest.TestCase):
    def test_failed_segment_is_rewritten_without_changing_timeline(self):
        items = [{
            "_id": 1,
            "segment_id": "opening",
            "timestamp": "00:00:00,000-00:00:03,000",
            "picture": "算命摊",
            "narration": "值得一提的是，不得不说这个人非常特别。",
            "story_role": "hook",
            "emotion": "平静",
            "OST": 2,
        }]
        critic = {"reviews": [{"_id": 1, "score": 35, "passed": False, "flags": ["cliche"], "reason": "空话"}]}
        rewritten = {"items": [{"_id": 1, "narration": "算命摊前，他停住了。", "estimated_duration": 2.4, "quality_flags": [], "score": 95}]}
        spec = {"clip_mode": "timeline", "voice_rate": 1.0}
        with patch("engine.script._text_settings", return_value=("model", "url", ["key"])), patch(
            "engine.script._chat_text", side_effect=[json.dumps(critic), json.dumps(rewritten)]
        ) as chat:
            result, report = script.critique_and_rewrite(items, "# 融合素材", spec, log=lambda _: None)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(result[0]["timestamp"], items[0]["timestamp"])
        self.assertEqual(result[0]["narration"], "算命摊前，他停住了。")
        self.assertTrue(report["passed"])

    def test_repetition_is_detected_locally(self):
        items = [
            {"_id": 1, "narration": "他慢慢走到摊位前。", "timestamp": "00:00:00,000-00:00:04,000"},
            {"_id": 2, "narration": "他慢慢走到摊位前。", "timestamp": "00:00:04,000-00:00:08,000"},
        ]
        reviews = script._local_reviews(items, {"clip_mode": "timeline", "voice_rate": 1})
        self.assertIn("repetition", reviews[1]["flags"])


class ScriptCritiqueTests(unittest.TestCase):
    PHRASES = [
        "他把手搭在门框上停了一会儿。",
        "街角的灯牌忽明忽暗闪了两下。",
        "她低头翻看那本旧相册里的信。",
        "雨点砸在铁皮棚上发出闷响。",
        "远处有人推着板车慢慢走过。",
        "镜头切到窗台上那盆枯掉的绿植。",
        "他把口袋里的车票重新折好放回。",
        "孩子在门口踮脚张望了很久。",
        "楼道感应灯亮起又暗了下去。",
        "桌上那杯茶早已凉透了。",
        "风把晾衣绳上的衬衫吹得鼓起来。",
        "他蹲下来系紧松开的鞋带。",
    ]

    @staticmethod
    def _clock(seconds):
        return f"00:{seconds // 60:02d}:{seconds % 60:02d},000"

    def _items(self, count):
        return [
            {
                "_id": index + 1,
                "segment_id": f"seg-{index + 1}",
                "timestamp": f"{self._clock(index * 5)}-{self._clock(index * 5 + 5)}",
                "picture": "画面摘要",
                "narration": f"{self.PHRASES[index % len(self.PHRASES)]}这是第 {index + 1} 段。",
                "story_role": "hook" if index == 0 else "body",
                "OST": 2,
            }
            for index in range(count)
        ]

    def test_critic_unavailable_is_inherited_by_final_report(self):
        items = self._items(2)
        spec = {"clip_mode": "timeline", "voice_rate": 1.0}
        with patch("engine.script._text_settings", return_value=("model", "url", ["key"])), patch(
            "engine.script._chat_text", side_effect=RuntimeError("文本模型不可用")
        ):
            result, report = script.critique_and_rewrite(items, "# 融合素材", spec, log=lambda _: None)
        self.assertTrue(report["critic_unavailable"])
        self.assertFalse(report["passed"])
        for item in result:
            self.assertIn("critic_unavailable", item["quality_flags"])
        for review in report["final_reviews"]:
            self.assertIn("critic_unavailable", review["flags"])
            self.assertFalse(review["passed"])

    def test_available_critic_can_still_pass(self):
        items = self._items(2)
        spec = {"clip_mode": "timeline", "voice_rate": 1.0}
        reviews = {"reviews": [{"_id": index + 1, "score": 95, "passed": True, "flags": [], "reason": ""} for index in range(2)]}
        with patch("engine.script._text_settings", return_value=("model", "url", ["key"])), patch(
            "engine.script._chat_text", return_value=json.dumps(reviews)
        ):
            _, report = script.critique_and_rewrite(items, "# 融合素材", spec, log=lambda _: None)
        self.assertFalse(report["critic_unavailable"])
        self.assertTrue(report["passed"])

    def _long_material(self):
        return "\n".join(
            f"- 00:{index // 60:02d}:{index % 60:02d},000-00:{index // 60:02d}:{index % 60 + 1:02d},000：画面细节 {index}"
            for index in range(4000)
        )

    def test_long_material_is_critiqued_and_rewritten_in_batches(self):
        items = self._items(25)
        spec = {"clip_mode": "timeline", "voice_rate": 1.0}
        critic = {"reviews": [{"_id": index + 1, "score": 40, "passed": False, "flags": ["empty_talk"], "reason": ""} for index in range(25)]}
        rewritten = {"items": [
            {"_id": index + 1, "narration": f"{self.PHRASES[index % len(self.PHRASES)]}重写 {index}。", "estimated_duration": 3.0, "quality_flags": []}
            for index in range(25)
        ]}

        def reply(_model, _base_url, _keys, messages, _log, **_kwargs):
            system = messages[0]["content"]
            return json.dumps(rewritten if system == script.SYSTEM_PROMPT else critic)

        with patch("engine.script._text_settings", return_value=("model", "url", ["key"])), patch(
            "engine.script._chat_text", side_effect=reply
        ) as chat:
            script.critique_and_rewrite(items, self._long_material(), spec, log=lambda _: None)
        calls = [(call.args[3][0]["content"], call.args[3][1]["content"]) for call in chat.call_args_list]
        critic_prompts = [prompt for system, prompt in calls if system != script.SYSTEM_PROMPT]
        rewrite_prompts = [prompt for system, prompt in calls if system == script.SYSTEM_PROMPT]
        self.assertEqual(len(critic_prompts), 3)
        self.assertEqual(len(rewrite_prompts), 3)
        for prompt in critic_prompts + rewrite_prompts:
            self.assertLessEqual(len(prompt), script.CRITIC_MATERIAL_BUDGET + 20000)
        self.assertEqual(len(set(critic_prompts)), len(critic_prompts))
        self.assertLessEqual(len(self._long_material()), 180000)


class ClicheScopeTests(unittest.TestCase):
    def _review(self, items):
        return script._local_reviews(items, {"clip_mode": "timeline", "voice_rate": 1})

    def test_opener_and_closer_greetings_are_not_cliches(self):
        items = [
            {"_id": 1, "narration": "大家好，今天带你看一段现场记录。", "timestamp": "00:00:00,000-00:00:05,000"},
            {"_id": 2, "narration": "他慢慢走到摊位前，停了两秒。", "timestamp": "00:00:05,000-00:00:10,000"},
            {"_id": 3, "narration": "看到这里就结束了，记得点赞。", "timestamp": "00:00:10,000-00:00:15,000"},
        ]
        flags = [review["flags"] for review in self._review(items)]
        self.assertNotIn("cliche", flags[0])
        self.assertNotIn("cliche", flags[2])

    def test_mid_body_cliches_are_still_flagged(self):
        items = [
            {"_id": 1, "narration": "他停在摊位前。", "timestamp": "00:00:00,000-00:00:05,000"},
            {"_id": 2, "narration": "他转过头说大家好，他确实等了三小时。", "timestamp": "00:00:05,000-00:00:10,000"},
            {"_id": 3, "narration": "他后来再没回来。记得点赞。", "timestamp": "00:00:10,000-00:00:15,000"},
        ]
        flags = [review["flags"] for review in self._review(items)]
        self.assertNotIn("cliche", flags[0])
        self.assertIn("cliche", flags[1])
        self.assertNotIn("cliche", flags[2])
        self.assertIn("值得一提的是", script.CLICHES)


if __name__ == "__main__":
    unittest.main()
