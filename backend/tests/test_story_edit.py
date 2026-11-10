import unittest

from app.services.ai_edit import conserve_duration
from app.services.video_analysis import snap_plan_to_cuts


class StoryEditTests(unittest.TestCase):
    def test_duration_conservation_preserves_hook_and_records_changes(self):
        clips = [
            {"start": 20, "end": 25, "story_role": "hook", "importance": 5, "reason": "钩子"},
            {"start": 0, "end": 10, "story_role": "setup", "importance": 1, "reason": "铺垫"},
        ]
        result, adjustments = conserve_duration(clips, 30, 8)
        self.assertEqual(result[0]["story_role"], "hook")
        self.assertEqual(result[0]["end"], 25)
        self.assertTrue(adjustments)
        self.assertLessEqual(sum(item["end"] - item["start"] for item in result), 8.8)

    def test_snap_preserves_story_order_and_prefers_sentence_end(self):
        clips = [{"start": 9.7, "end": 14.8, "story_role": "hook", "reason": ""}]
        result = snap_plan_to_cuts(clips, [10.5, 15.5], 30, sentence_ends=[10.0, 15.0])
        self.assertEqual(result[0]["start"], 10.0)
        self.assertEqual(result[0]["end"], 15.0)
        self.assertEqual(result[0]["story_role"], "hook")


if __name__ == "__main__":
    unittest.main()
