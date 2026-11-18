"""提示里的时长上限必须约束口播密度回落，做不到就失败。"""
import unittest

from app.services.ai_edit import AIResponseError, AIServiceError
from app.services.douyin_plan import plan_douyin_shorts, requested_limits


def speech(duration: float, step: float = 4.0) -> list[dict]:
    items = []
    cursor = 0.0
    index = 0
    while cursor < duration - 1:
        items.append({
            "start": round(cursor, 2),
            "end": round(min(duration, cursor + step - 0.2), 2),
            "text": f"这是第{index}句可以单独听懂的口播内容",
        })
        cursor += step
        index += 1
    return items


def _lengths(plan: dict) -> list[float]:
    return [round(float(clip["end"]) - float(clip["start"]), 2) for clip in plan["clips"]]


class RequestedLimitTests(unittest.TestCase):
    def test_reads_the_acceptance_prompt(self):
        self.assertEqual(requested_limits("只生成1条，时长不超过15秒"), (15.0, 1))
        self.assertEqual(requested_limits("15秒以内"), (15.0, None))
        self.assertEqual(requested_limits(""), (None, None))

    def test_fallback_keeps_one_clip_within_15_seconds(self):
        def missing_key(_prompt, _log):
            raise AIServiceError("没有配置 AI API Key")

        plan = plan_douyin_shorts(
            speech(29.22),
            29.22,
            instruction="只生成1条，时长不超过15秒",
            request_ai=missing_key,
            log=lambda _message: None,
        )
        lengths = _lengths(plan)
        self.assertEqual(plan["source"], "heuristic")
        self.assertEqual(len(lengths), 1)
        self.assertLessEqual(lengths[0], 15.0)
        self.assertGreaterEqual(lengths[0], 5.0)
        self.assertTrue(any("口播密度" in note for note in plan["warnings"]))
        self.assertTrue(any("15" in note for note in plan["warnings"]))

    def test_model_clip_longer_than_the_cap_is_trimmed(self):
        def too_long(_prompt, _log):
            return [{
                "start": 0,
                "end": 28,
                "title": "整段",
                "hashtags": ["#口播"],
                "description": "整段口播",
                "score": 5,
            }]

        plan = plan_douyin_shorts(
            speech(29.22),
            29.22,
            instruction="时长不超过15秒",
            request_ai=too_long,
            log=lambda _message: None,
        )
        lengths = _lengths(plan)
        self.assertEqual(len(lengths), 1)
        self.assertLessEqual(lengths[0], 15.0)

    def test_default_fallback_can_still_use_most_of_a_short_source(self):
        def missing_key(_prompt, _log):
            raise AIServiceError("没有配置 AI API Key")

        plan = plan_douyin_shorts(
            speech(29.22),
            29.22,
            request_ai=missing_key,
            log=lambda _message: None,
        )
        self.assertGreater(_lengths(plan)[0], 15.0)

    def test_impossible_cap_fails_instead_of_succeeding(self):
        with self.assertRaises(AIResponseError) as caught:
            plan_douyin_shorts(
                speech(29.22),
                29.22,
                instruction="时长不超过3秒",
                request_ai=lambda *_args: [],
                log=lambda _message: None,
            )
        self.assertIn("没有生成成片", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
