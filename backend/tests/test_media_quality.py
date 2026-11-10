import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services import media_quality


class MediaQualityTests(unittest.TestCase):
    @patch("app.services.media_quality._vision_score")
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_threshold_is_inclusive(self, _valid, vision):
        vision.return_value = {"score": 70, "reasons": []}
        result = media_quality.score_image(Path("image.png"), "提示词", [], 70)
        self.assertTrue(result.passed)
        self.assertEqual(result.score, 70)

    @patch("app.services.media_quality._vision_score")
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_low_score_contains_reasons(self, _valid, vision):
        vision.return_value = {"score": 69, "reasons": ["人物服装不一致"]}
        result = media_quality.score_image(Path("image.png"), "提示词", [], 70)
        self.assertFalse(result.passed)
        self.assertEqual(result.reasons, ["人物服装不一致"])

    @patch("app.services.media_quality._vision_score")
    @patch("app.services.media_quality._valid_image", return_value=False)
    def test_invalid_file_is_zero(self, _valid, vision):
        result = media_quality.score_image(Path("bad.png"), "提示词", [], 70)
        self.assertEqual(result.score, 0)
        self.assertFalse(result.passed)
        self.assertEqual(result.checker, "technical")
        vision.assert_not_called()

    @patch("app.services.media_quality._vision_score", side_effect=media_quality.QualityUnavailable("视觉模型不可用"))
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_unavailable_scorer_does_not_pass(self, _valid, _vision):
        with self.assertRaises(media_quality.QualityUnavailable):
            media_quality.score_image(Path("image.png"), "提示词", [], 70)

    @patch("app.services.media_quality._vision_score", side_effect=RuntimeError("请求失败"))
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_scorer_error_is_normalized(self, _valid, _vision):
        with self.assertRaisesRegex(media_quality.QualityUnavailable, "请求失败"):
            media_quality.score_image(Path("image.png"), "提示词", [], 70)

    @patch("app.services.media_quality._vision_score", return_value={"score": 80})
    @patch("app.services.media_quality._valid_image", return_value=True)
    def test_scorer_must_return_reasons(self, _valid, _vision):
        with self.assertRaises(media_quality.QualityUnavailable):
            media_quality.score_image(Path("image.png"), "提示词", [], 70)

    def test_result_dict_copies_reasons(self):
        result = media_quality.QualityResult(80, True, ["清晰"], "vision")
        value = result.as_dict()
        value["reasons"].append("修改")
        self.assertEqual(result.reasons, ["清晰"])
        self.assertEqual(value["checker"], "vision")

    @patch("app.services.media_quality.VISION_MODEL", "")
    def test_vision_score_requires_configured_model(self):
        with self.assertRaisesRegex(media_quality.QualityUnavailable, "未配置视觉模型"):
            media_quality._vision_score([], "提示词", [], kind="image")

    @patch("app.services.media_quality.VISION_MODEL", "vision-test")
    @patch("app.services.understanding._request_json", return_value={"score": 88, "reasons": []})
    def test_vision_score_sends_prompt_references_frames_and_dimensions(self, request):
        with tempfile.TemporaryDirectory() as temp:
            frame = Path(temp) / "frame.jpg"
            frame.write_bytes(b"jpeg")
            result = media_quality._vision_score(
                [frame], "原始提示词", ["data:image/png;base64,cmVm"], kind="image"
            )
        self.assertEqual(result, {"score": 88, "reasons": []})
        self.assertTrue(request.call_args.kwargs["vision"])
        content = request.call_args.args[0][0]["content"]
        self.assertIn("原始提示词", content[0]["text"])
        self.assertIn("提示词符合度", content[0]["text"])
        self.assertIn("构图", content[0]["text"])
        self.assertIn("清晰度", content[0]["text"])
        self.assertIn("畸形", content[0]["text"])
        self.assertEqual(content[1]["image_url"]["url"], "data:image/png;base64,cmVm")
        self.assertTrue(content[2]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    @patch("app.services.media_quality._vision_score", return_value={"score": 70, "reasons": []})
    @patch("app.services.media_quality._middle_frames", return_value=[
        Path(f"middle-{index}.jpg") for index in range(1, 6)
    ])
    @patch("app.services.media_quality._frame_at", return_value=True)
    @patch("app.services.media_quality._probe_video")
    def test_video_scores_eight_frames_with_reference(self, probe, frame, middle, vision):
        probe.return_value = {"duration": 10.0, "width": 1280, "height": 720}
        result = media_quality.score_video(Path("clip.mp4"), "提示词", "reference.png", 70)
        self.assertTrue(result.passed)
        self.assertEqual(frame.call_count, 3)
        timestamps = [call.args[1] for call in frame.call_args_list]
        self.assertEqual(timestamps, [0.0, 5.0, 9.95])
        self.assertEqual(middle.call_args.args[:2], (Path("clip.mp4"), 5.0))
        self.assertEqual(len(vision.call_args.args[0]), 8)
        self.assertEqual(vision.call_args.args[2], ["reference.png"])
        self.assertEqual(vision.call_args.kwargs["kind"], "video")

    @patch("app.services.media_quality.subprocess.run")
    def test_middle_frames_use_one_sequential_decode_for_vfr(self, run):
        with tempfile.TemporaryDirectory() as temp:
            def decode(command, **_kwargs):
                for index in range(1, 6):
                    Path(command[-1].replace("%d", str(index))).write_bytes(b"frame")
                return Mock(returncode=0)

            run.side_effect = decode
            frames = media_quality._middle_frames(Path("vfr.mp4"), 4.25, Path(temp))
        self.assertEqual(len(frames), 5)
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertEqual(command.count("-ss"), 1)
        self.assertEqual(command[command.index("-ss") + 1], "4.250")
        self.assertEqual(command[command.index("-frames:v") + 1], "5")

    @patch("app.services.media_quality._vision_score", return_value={"score": 70, "reasons": []})
    @patch("app.services.media_quality._middle_frames", return_value=[Path("middle-1.jpg")])
    @patch("app.services.media_quality._frame_at", return_value=True)
    @patch("app.services.media_quality._probe_video", return_value={
        "duration": 0.08, "width": 320, "height": 240,
    })
    def test_valid_short_video_uses_available_middle_frames(self, _probe, _frame, _middle, vision):
        result = media_quality.score_video(Path("short.mp4"), "短视频", "", 70)
        self.assertTrue(result.passed)
        self.assertEqual(result.checker, "vision")
        self.assertEqual(len(vision.call_args.args[0]), 4)

    @patch("app.services.media_quality._vision_score")
    @patch("app.services.media_quality._probe_video", return_value=None)
    def test_invalid_video_is_zero_without_scoring(self, _probe, vision):
        result = media_quality.score_video(Path("bad.mp4"), "提示词", "", 70)
        self.assertEqual(result.score, 0)
        self.assertFalse(result.passed)
        self.assertEqual(result.checker, "technical")
        vision.assert_not_called()

    @patch("app.services.media_quality._vision_score")
    @patch("app.services.media_quality._frame_at", return_value=False)
    @patch("app.services.media_quality._probe_video", return_value={
        "duration": 10.0, "width": 1280, "height": 720, "avg_frame_rate": "25/1",
    })
    def test_undecodable_video_is_zero(self, _probe, _frame, vision):
        result = media_quality.score_video(Path("bad.mp4"), "提示词", "", 70)
        self.assertEqual(result.score, 0)
        self.assertFalse(result.passed)
        vision.assert_not_called()

    @patch("app.services.media_quality._vision_score", side_effect=RuntimeError("服务异常"))
    @patch("app.services.media_quality._middle_frames", return_value=[
        Path(f"middle-{index}.jpg") for index in range(1, 6)
    ])
    @patch("app.services.media_quality._frame_at", return_value=True)
    @patch("app.services.media_quality._probe_video", return_value={
        "duration": 10.0, "width": 1280, "height": 720,
    })
    def test_video_scorer_error_does_not_pass(self, _probe, _frame, _middle, _vision):
        with self.assertRaisesRegex(media_quality.QualityUnavailable, "服务异常"):
            media_quality.score_video(Path("clip.mp4"), "提示词", "", 70)


if __name__ == "__main__":
    unittest.main()
