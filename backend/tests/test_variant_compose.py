import unittest
from unittest.mock import patch

from app.services import narration


class VariantComposeTests(unittest.TestCase):
    @patch("app.services.narration._public", side_effect=lambda row, with_source=True: row)
    @patch("app.services.narration.create_narration_task", side_effect=lambda **fields: fields)
    @patch("app.services.narration._request", return_value={"task": {"status": "queued", "progress": 0}})
    @patch("app.services.narration.get_narration_task")
    def test_compose_namespaces_segment_ids_and_preserves_metadata(self, get_task, request, create, _public):
        get_task.return_value = {
            "id": "base", "video_id": 1, "title": "测试", "request": {
                "video_path": "source.mp4", "video_duration": 12, "variant_group_id": "group"
            }
        }
        plans = {
            "a": {"items": [{"_id": 1, "segment_id": "segment-1", "narration": "A 开头", "timestamp": "00:00:00,000-00:00:04,000", "story_role": "hook"}]},
            "b": {"items": [{"_id": 1, "segment_id": "segment-1", "narration": "B 结尾", "timestamp": "00:00:08,000-00:00:12,000", "story_role": "ending"}]},
        }
        with patch("app.services.narration.task_plan", side_effect=lambda task_id: plans[task_id]):
            narration.compose_variants("base", [
                {"source_task_id": "a", "segment_id": "segment-1"},
                {"source_task_id": "b", "segment_id": "segment-1"},
            ])
        spec = request.call_args.kwargs["payload"]
        self.assertEqual(spec["mode"], "script")
        self.assertEqual(len(spec["script_items"]), 2)
        self.assertNotEqual(spec["script_items"][0]["segment_id"], spec["script_items"][1]["segment_id"])
        self.assertEqual(spec["script_items"][0]["story_role"], "hook")


if __name__ == "__main__":
    unittest.main()
