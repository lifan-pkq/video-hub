import unittest
from unittest.mock import patch

from app.services import drama_planner


class DramaPlannerTests(unittest.TestCase):
    @patch("app.services.drama_planner._request_json")
    def test_ai_controls_count_and_duration(self, request):
        request.return_value = {
            "shots": [
                {"scene": "客厅", "action": "角色抬头", "seconds": 4, "characters": ["c1"]},
                {"scene": "门口", "action": "角色停下", "seconds": 8, "characters": ["c1"]},
                {"scene": "雨夜", "action": "角色回头", "seconds": 12, "characters": []},
            ]
        }

        shots = drama_planner.plan_episode(
            {"index": 1, "title": "第一集", "summary": "测试", "source_excerpt": "正文"}, []
        )

        self.assertEqual(len(shots), 3)
        self.assertEqual([shot["seconds"] for shot in shots], [4, 8, 12])
        self.assertEqual([shot["id"] for shot in shots], ["e1s1", "e1s2", "e1s3"])
        self.assertEqual([shot["index"] for shot in shots], [1, 2, 3])
        self.assertEqual(shots[0]["status"], "pending")
        self.assertEqual(shots[0]["revision"], 1)
        self.assertEqual(shots[0]["image"]["status"], "pending")
        self.assertEqual(shots[0]["video"]["status"], "pending")
        self.assertIn("客厅", shots[0]["prompt"])
        self.assertIn("角色抬头", shots[0]["prompt"])
        self.assertIn("固定", shots[0]["prompt"])
        messages = request.call_args.args[0]
        prompt = messages[-1]["content"]
        self.assertIn("自主决定", prompt)
        self.assertIn("4–12", prompt)

    def test_duration_outside_provider_range_is_rejected(self):
        for seconds in (3, 13):
            with self.subTest(seconds=seconds), self.assertRaises(drama_planner.PlannerError):
                drama_planner.validate_shot({"scene": "客厅", "action": "抬头", "seconds": seconds})

    @patch("app.services.drama_planner._request_json")
    def test_empty_or_malformed_shot_plans_fail_explicitly(self, request):
        invalid_plans = [
            {},
            {"shots": []},
            {"shots": None},
            {"shots": [{}]},
            {"shots": [{"scene": "客厅", "action": "抬头"}]},
        ]
        episode = {"index": 1, "summary": "测试", "source_excerpt": "正文"}
        for plan in invalid_plans:
            request.return_value = plan
            with self.subTest(plan=plan), self.assertRaises(drama_planner.PlannerError):
                drama_planner.plan_episode(episode, [])

    @patch("app.services.drama_planner._request_json")
    def test_plan_novel_requests_characters_and_episode_outline(self, request):
        result = {
            "title": "测试短剧",
            "logline": "冲突",
            "characters": [{"name": "角色", "age": 30}],
            "episodes": [{"index": 1, "title": "第一集", "summary": "剧情"}],
        }
        request.return_value = result

        self.assertEqual(drama_planner.plan_novel("小说正文", 1), result)
        prompt = request.call_args.args[0][-1]["content"]
        self.assertIn("1 集", prompt)
        self.assertIn("小说正文", prompt)

    @patch("app.services.drama_planner._request_json")
    def test_plan_novel_rejects_empty_characters_or_episodes(self, request):
        for result in (
            {"characters": [], "episodes": [{"index": 1}]},
            {"characters": [{"name": "角色"}], "episodes": []},
        ):
            request.return_value = result
            with self.subTest(result=result), self.assertRaises(drama_planner.PlannerError):
                drama_planner.plan_novel("小说正文", 1)

    @patch("app.services.drama_planner._request_json")
    def test_plan_novel_requires_exact_complete_ordered_episodes(self, request):
        characters = [{"name": "角色", "age": 30}]
        valid = {"index": 1, "title": "第一集", "summary": "完整剧情"}
        invalid_episode_sets = [
            (2, [valid]),
            (1, [{**valid, "summary": "   "}]),
            (1, [{**valid, "index": 0}]),
            (1, [{**valid, "index": 2}]),
            (1, [valid, {**valid, "index": 2, "title": "第二集"}]),
            (2, [valid, valid]),
        ]
        for count, episodes in invalid_episode_sets:
            request.return_value = {"characters": characters, "episodes": episodes}
            with self.subTest(count=count, episodes=episodes), self.assertRaises(drama_planner.PlannerError):
                drama_planner.plan_novel("小说正文", count)


if __name__ == "__main__":
    unittest.main()
