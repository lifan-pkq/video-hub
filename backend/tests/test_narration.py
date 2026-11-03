import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import narration


class NarrationSpecTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.video_path = Path(self.temp.name) / "clip.mp4"
        self.video_path.write_bytes(b"video")
        self.video = {
            "id": 7,
            "title": "一条视频",
            "path": str(self.video_path),
            "width": 1080,
            "height": 1920,
            "duration": 30,
        }

    def test_generic_visual_spec(self):
        spec = narration._engine_spec(self.video, {"content_type": "vlog", "topic": "露营"}, "narrate-1")
        self.assertEqual(spec["mode"], "auto")
        self.assertEqual(spec["aspect"], "9:16")
        self.assertEqual(spec["content_type"], "vlog")
        self.assertEqual(spec["video_path"], str(self.video_path))

    def test_landscape_source_keeps_landscape(self):
        self.video["width"], self.video["height"] = 1920, 1080
        spec = narration._engine_spec(self.video, {}, "narrate-1")
        self.assertEqual(spec["aspect"], "16:9")

    def test_script_text_switches_mode(self):
        spec = narration._engine_spec(self.video, {"script_text": "第一段\n第二段"}, "narrate-1")
        self.assertEqual(spec["mode"], "script")

    def test_transcript_switches_mode(self):
        spec = narration._engine_spec(self.video, {"transcript_text": "原口播"}, "narrate-1")
        self.assertEqual(spec["mode"], "transcript")

    def test_subtitle_style_is_forwarded(self):
        spec = narration._engine_spec(
            self.video,
            {
                "font_name": "",
                "font_size": 72,
                "text_fore_color": "#FFCC00",
                "stroke_color": "#112233",
                "stroke_width": 2.5,
                "subtitle_position": "custom",
                "custom_position": 62,
                "subtitle_y_percent": 62,
                "subtitle_mask_enabled": True,
                "subtitle_mask_y": 70,
                "subtitle_mask_height": 18,
                "subtitle_mask_blur": 22,
                "subtitle_mask_opacity": 90,
            },
            "narrate-1",
        )
        self.assertEqual(spec["font_name"], "")
        self.assertEqual(spec["font_size"], 72)
        self.assertEqual(spec["text_fore_color"], "#FFCC00")
        self.assertEqual(spec["subtitle_y_percent"], 62)
        self.assertTrue(spec["subtitle_mask_enabled"])
        self.assertEqual(spec["subtitle_mask_blur"], 22)

    def test_asr_mode_needs_no_text(self):
        spec = narration._engine_spec(self.video, {"mode": "asr"}, "narrate-1")
        self.assertEqual(spec["mode"], "asr")
        self.assertEqual(spec["transcript_text"], "")

    def test_script_mode_without_text_is_rejected(self):
        with self.assertRaises(narration.NarrationError):
            narration._engine_spec(self.video, {"mode": "script"}, "narrate-1")

    def test_public_hides_engine_paths(self):
        row = {
            "id": "narrate-1",
            "video_id": 7,
            "title": "测试",
            "status": "success",
            "progress": 100,
            "request": {"content_type": "auto", "script_text": "secret"},
            "result": {"output_path": "/engine/storage/combined.mp4", "duration": 12, "width": 1080, "height": 1920},
        }
        public = narration._public(row)
        self.assertEqual(public["result"]["duration"], 12)
        self.assertNotIn("output_path", public["result"])
        self.assertNotIn("script_text", public["request"])


class NarrationFlowTests(unittest.TestCase):
    def test_create_task_posts_engine_spec_and_persists(self):
        video = {"id": 3, "title": "素材", "path": "/tmp/x.mp4", "width": 1080, "height": 1920, "duration": 20}
        engine_task = {"task_id": "narrate-x", "status": "queued", "progress": 0, "message": "已排队"}
        with patch.object(narration, "_video_row", return_value=video), \
             patch.object(narration, "_request", return_value={"task": engine_task}) as request, \
             patch.object(narration, "create_narration_task", side_effect=lambda **fields: {**fields, "output_video_id": None}) as created:
            task = narration.create_task(3, {"content_type": "knowledge", "topic": "咖啡"})
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["video_id"], 3)
        self.assertEqual(request.call_args.args[0], "POST")
        self.assertEqual(request.call_args.args[1], "/tasks")
        self.assertEqual(created.call_args.kwargs["engine_task_id"], task["id"])

    def test_result_path_prefers_shared_tasks_dir(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "tasks"
            task_dir = root / "narrate-1"
            task_dir.mkdir(parents=True)
            output = task_dir / "combined.mp4"
            output.write_bytes(b"x")
            with patch.object(narration, "NARRATO_TASKS_DIR", root):
                self.assertEqual(narration._result_path({"engine_task_id": "narrate-1"}), output)

    def test_import_result_links_video(self):
        row = {"id": "narrate-1", "title": "测试", "output_video_id": None, "engine_task_id": "narrate-1"}
        updated = {"id": "narrate-1", "title": "测试", "output_video_id": 42, "engine_task_id": "narrate-1"}
        with patch.object(narration, "get_narration_task", side_effect=[row, updated]), \
             patch.object(narration, "_result_path", return_value=Path("/tmp/combined.mp4")), \
             patch.object(narration.narrato_import, "import_task", return_value={"id": 42}), \
             patch.object(narration, "update_narration_task") as update:
            task = narration.import_result("narrate-1")
        self.assertEqual(task["output_video_id"], 42)
        self.assertEqual(update.call_args.args[0], "narrate-1")


if __name__ == "__main__":
    unittest.main()
