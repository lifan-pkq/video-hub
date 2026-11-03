import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routers import edit
from app.services.tasks import TaskManager


class EditTaskInputTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.videos_dir = Path(self.temp_dir.name)
        self.videos = {
            1: {"id": 1, "title": "第一条素材", "filename": "one.mp4", "duration": 12.0},
            2: {"id": 2, "title": "第二条素材", "filename": "two.mp4", "duration": 20.0},
        }
        for video in self.videos.values():
            (self.videos_dir / video["filename"]).touch()
        self.manager = TaskManager()
        for target, value in (
            ("app.routers.edit.task_manager", self.manager),
            ("app.routers.edit.VIDEOS_DIR", self.videos_dir),
        ):
            item = patch(target, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch("app.routers.edit.get_video", side_effect=self.videos.get)
        item.start()
        self.addCleanup(item.stop)
        item = patch("app.routers.edit.threading.Thread")
        item.start()
        self.addCleanup(item.stop)

    def task_input(self, response):
        task = self.manager.get(response["task_id"])
        self.assertIsNotNone(task)
        return task.to_dict(with_logs=False).get("input")

    def test_analyze_task_keeps_source_videos_and_options_in_serialized_input(self):
        response = edit.analyze_video(edit.AnalyzeRequest(
            video_ids=[1, 2],
            kinds=["ad", "silence", "invalid"],
            custom_prompt="删掉重复表达",
        ))

        self.assertEqual(self.task_input(response), {
            "mode": "analyze",
            "video_ids": [1, 2],
            "kinds": ["ad", "silence"],
            "custom_prompt": "删掉重复表达",
        })

    def test_plan_task_keeps_video_target_and_prompt_in_serialized_input(self):
        response = edit.plan_video(edit.PlanRequest(
            video_id=1,
            target_duration=45,
            custom_prompt="保留产品介绍",
        ))

        self.assertEqual(self.task_input(response), {
            "mode": "plan",
            "video_ids": [1],
            "target_duration": 45.0,
            "custom_prompt": "保留产品介绍",
        })

    def test_concat_task_keeps_timeline_and_export_settings_in_serialized_input(self):
        response = edit.concat_videos(edit.ConcatRequest(
            title="合成成片",
            clips=[
                edit.Clip(video_id=1, start=1.25, end=5.5),
                edit.Clip(video_id=2, start=2.0, end=8.0),
            ],
            split_parts=2,
        ))

        self.assertEqual(self.task_input(response), {
            "mode": "concat",
            "title": "合成成片",
            "clips": [
                {"video_id": 1, "start": 1.25, "end": 5.5},
                {"video_id": 2, "start": 2.0, "end": 8.0},
            ],
            "split_parts": 2,
        })


if __name__ == "__main__":
    unittest.main()
