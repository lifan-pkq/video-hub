import unittest
import os
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.services.documentary.frame_analysis_models import DocumentaryAnalysisConfig
from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService
from app.utils import utils


class DocumentaryFrameAnalysisServiceTests(unittest.TestCase):
    def test_build_analysis_prompt_formats_real_frame_count(self):
        service = DocumentaryFrameAnalysisService()

        prompt = service._build_analysis_prompt(frame_count=3)

        self.assertIn("我提供了 3 张视频帧", prompt)
        self.assertNotIn("%s", prompt)
        self.assertIn("frame_observations", prompt)
        self.assertIn("overall_activity_summary", prompt)

    def test_parse_failed_batch_keeps_raw_response_and_time_range(self):
        service = DocumentaryFrameAnalysisService()

        batch = service._build_failed_batch_result(
            batch_index=2,
            raw_response="not-json",
            error_message="JSON decode failed",
            frame_paths=["/tmp/keyframe_000000_000000000.jpg"],
            time_range="00:00:00,000-00:00:03,000",
        )

        self.assertEqual("failed", batch.status)
        self.assertEqual("not-json", batch.raw_response)
        self.assertEqual("00:00:00,000-00:00:03,000", batch.time_range)
        self.assertTrue(batch.fallback_summary)

    def test_parse_failed_batch_uses_non_empty_fallback_when_raw_response_is_empty(self):
        service = DocumentaryFrameAnalysisService()

        batch = service._build_failed_batch_result(
            batch_index=3,
            raw_response="",
            error_message="Empty model response",
            frame_paths=["/tmp/keyframe_000001_000001000.jpg"],
            time_range="00:00:03,000-00:00:06,000",
        )

        self.assertEqual("failed", batch.status)
        self.assertEqual("", batch.raw_response)
        self.assertTrue(batch.fallback_summary)

    def test_failed_batch_result_uses_prompt_contract_field_names(self):
        service = DocumentaryFrameAnalysisService()

        batch = service._build_failed_batch_result(
            batch_index=4,
            raw_response="not-json",
            error_message="JSON decode failed",
            frame_paths=["/tmp/keyframe_000002_000002000.jpg"],
            time_range="00:00:06,000-00:00:09,000",
        )

        self.assertEqual([], batch.frame_observations)
        self.assertEqual("", batch.overall_activity_summary)
        self.assertFalse(hasattr(batch, "observations"))
        self.assertFalse(hasattr(batch, "summary"))

    def test_parse_batch_returns_failed_result_when_json_is_invalid(self):
        service = DocumentaryFrameAnalysisService()

        batch = service._parse_batch_response(
            batch_index=0,
            raw_response="plain text",
            frame_paths=["/tmp/keyframe_000000_000000000.jpg"],
            time_range="00:00:00,000-00:00:03,000",
        )

        self.assertEqual("failed", batch.status)
        self.assertEqual("plain text", batch.raw_response)
        self.assertEqual(["/tmp/keyframe_000000_000000000.jpg"], batch.frame_paths)
        self.assertEqual([], batch.frame_observations)
        self.assertEqual("", batch.overall_activity_summary)

    def test_parse_batch_returns_failed_result_for_empty_json_object(self):
        service = DocumentaryFrameAnalysisService()

        batch = service._parse_batch_response(
            batch_index=0,
            raw_response="{}",
            frame_paths=["/tmp/keyframe_000000_000000000.jpg"],
            time_range="00:00:00,000-00:00:03,000",
        )

        self.assertEqual("failed", batch.status)
        self.assertEqual("{}", batch.raw_response)
        self.assertIn("frame_observations", batch.error_message)

    def test_parse_batch_returns_failed_result_when_observations_are_too_short(self):
        service = DocumentaryFrameAnalysisService()
        raw_response = """
{
  "frame_observations": [
    {"observation": "第一帧画面"}
  ],
  "overall_activity_summary": "只有一条帧观察"
}
""".strip()

        batch = service._parse_batch_response(
            batch_index=1,
            raw_response=raw_response,
            frame_paths=[
                "/tmp/keyframe_000000_000000000.jpg",
                "/tmp/keyframe_000075_000003000.jpg",
            ],
            time_range="00:00:00,000-00:00:06,000",
        )

        self.assertEqual("failed", batch.status)
        self.assertEqual(raw_response, batch.raw_response)
        self.assertIn("frame_observations", batch.error_message)

    def test_parse_batch_parses_code_fenced_json_into_structured_result(self):
        service = DocumentaryFrameAnalysisService()
        raw_response = """```json
{
  "frame_observations": [
    {"observation": "第一帧画面"},
    {"observation": "第二帧画面"}
  ],
  "overall_activity_summary": "人物从房间走到街道"
}
```"""

        batch = service._parse_batch_response(
            batch_index=1,
            raw_response=raw_response,
            frame_paths=[
                "/tmp/keyframe_000000_000000000.jpg",
                "/tmp/keyframe_000075_000003000.jpg",
            ],
            time_range="00:00:00,000-00:00:06,000",
        )

        self.assertEqual("success", batch.status)
        self.assertEqual(
            [
                {
                    "frame_path": "/tmp/keyframe_000000_000000000.jpg",
                    "timestamp": "",
                    "observation": "第一帧画面",
                },
                {
                    "frame_path": "/tmp/keyframe_000075_000003000.jpg",
                    "timestamp": "",
                    "observation": "第二帧画面",
                },
            ],
            batch.frame_observations,
        )
        self.assertEqual("人物从房间走到街道", batch.overall_activity_summary)
        self.assertEqual("", batch.fallback_summary)

    def test_parse_batch_preserves_frames_when_summary_is_missing(self):
        service = DocumentaryFrameAnalysisService()
        raw_response = """
{
  "frame_observations": [
    {"observation": "第一帧画面"},
    {"observation": "第二帧画面"}
  ]
}
""".strip()

        batch = service._parse_batch_response(
            batch_index=2,
            raw_response=raw_response,
            frame_paths=[
                "/tmp/keyframe_000000_000000000.jpg",
                "/tmp/keyframe_000075_000003000.jpg",
            ],
            time_range="00:00:00,000-00:00:06,000",
        )

        self.assertEqual("success", batch.status)
        self.assertEqual(2, len(batch.frame_observations))
        self.assertEqual("", batch.overall_activity_summary)

    def test_cache_key_changes_when_interval_changes(self):
        service = DocumentaryFrameAnalysisService()

        with patch("app.services.documentary.frame_analysis_service.os.path.getmtime", return_value=100.0):
            key_a = service._build_cache_key("video.mp4", 3.0, "prompt-v1", "model-a", 10, 2)
            key_b = service._build_cache_key("video.mp4", 5.0, "prompt-v1", "model-a", 10, 2)

        self.assertNotEqual(key_a, key_b)

    def test_cache_key_changes_when_model_changes(self):
        service = DocumentaryFrameAnalysisService()

        with patch("app.services.documentary.frame_analysis_service.os.path.getmtime", return_value=100.0):
            key_a = service._build_cache_key("video.mp4", 3.0, "prompt-v1", "model-a", 10, 2)
            key_b = service._build_cache_key("video.mp4", 3.0, "prompt-v1", "model-b", 10, 2)

        self.assertNotEqual(key_a, key_b)

    def test_cache_key_starts_with_legacy_video_hash_prefix(self):
        service = DocumentaryFrameAnalysisService()

        with patch("app.services.documentary.frame_analysis_service.os.path.getmtime", return_value=123.0):
            key = service._build_cache_key("video.mp4", 3.0, "prompt-v1", "model-a", 10, 2)

        expected_prefix = utils.md5("video.mp4" + "123.0")
        self.assertTrue(key.startswith(expected_prefix))

    def test_clear_keyframes_cache_respects_scope_and_prefix_match(self):
        with TemporaryDirectory() as temp_root:
            service = DocumentaryFrameAnalysisService()
            analysis_dir = os.path.join(temp_root, "analysis")
            os.makedirs(analysis_dir, exist_ok=True)

            with patch("app.services.documentary.frame_analysis_service.os.path.getmtime", return_value=123.0):
                target_key_a = service._build_cache_key("video.mp4", 3.0, "prompt-v1", "model-a", 10, 2)
                target_key_b = service._build_cache_key("video.mp4", 5.0, "prompt-v1", "model-a", 10, 2)
                keep_key = service._build_cache_key("other.mp4", 3.0, "prompt-v1", "model-a", 10, 2)

            target_dir_a = os.path.join(analysis_dir, target_key_a)
            target_dir_b = os.path.join(analysis_dir, target_key_b)
            keep_dir = os.path.join(analysis_dir, keep_key)
            os.makedirs(target_dir_a, exist_ok=True)
            os.makedirs(target_dir_b, exist_ok=True)
            os.makedirs(keep_dir, exist_ok=True)

            with patch("app.utils.utils.temp_dir", return_value=temp_root), patch(
                "app.utils.utils.os.path.getmtime", return_value=123.0
            ):
                utils.clear_keyframes_cache(video_path="video.mp4", cache_scope="analysis")

            self.assertFalse(os.path.exists(target_dir_a))
            self.assertFalse(os.path.exists(target_dir_b))
            self.assertTrue(os.path.exists(keep_dir))


class DocumentaryAnalysisConfigTests(unittest.TestCase):
    def test_config_rejects_non_positive_frame_interval(self):
        with self.assertRaises(ValueError):
            DocumentaryAnalysisConfig(
                video_path="/tmp/demo.mp4",
                frame_interval_seconds=0,
                vision_batch_size=5,
                vision_llm_provider="openai",
                vision_model_name="gpt-4o-mini",
            )

    def test_config_rejects_non_positive_batch_size(self):
        with self.assertRaises(ValueError):
            DocumentaryAnalysisConfig(
                video_path="/tmp/demo.mp4",
                frame_interval_seconds=5,
                vision_batch_size=0,
                vision_llm_provider="openai",
                vision_model_name="gpt-4o-mini",
            )

    def test_config_rejects_non_positive_max_concurrency(self):
        with self.assertRaises(ValueError):
            DocumentaryAnalysisConfig(
                video_path="/tmp/demo.mp4",
                frame_interval_seconds=5,
                vision_batch_size=5,
                vision_llm_provider="openai",
                vision_model_name="gpt-4o-mini",
                max_concurrency=0,
            )
