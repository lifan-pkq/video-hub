import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService
from app.services.script_service import ScriptGenerator


class ScriptGeneratorDocumentaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_script_forwards_explicit_values_to_shared_service(self):
        expected_script = [
            {
                "timestamp": "00:00:00,000-00:00:03,000",
                "picture": "批次描述",
                "narration": "这里是解说词",
                "OST": 2,
            }
        ]
        callback = lambda _percent, _message: None

        with patch("app.services.script_service.DocumentaryFrameAnalysisService") as service_cls:
            service = service_cls.return_value
            service.generate_documentary_script = AsyncMock(return_value=expected_script)
            generator = ScriptGenerator()

            result = await generator.generate_script(
                video_path="demo.mp4",
                video_theme="荒野生存",
                custom_prompt="请聚焦生存动作",
                frame_interval_input=3,
                vision_batch_size=6,
                vision_llm_provider="openai",
                progress_callback=callback,
            )

        self.assertEqual(expected_script, result)
        self.assertTrue(result[0]["narration"])
        service.generate_documentary_script.assert_awaited_once()
        called_kwargs = service.generate_documentary_script.await_args.kwargs
        self.assertEqual("demo.mp4", called_kwargs["video_path"])
        self.assertEqual(3, called_kwargs["frame_interval_input"])
        self.assertEqual(6, called_kwargs["vision_batch_size"])
        self.assertEqual("openai", called_kwargs["vision_llm_provider"])
        self.assertEqual("荒野生存", called_kwargs["video_theme"])
        self.assertEqual("请聚焦生存动作", called_kwargs["custom_prompt"])
        self.assertIs(called_kwargs["progress_callback"], callback)

    async def test_generate_script_forwards_unset_values_as_none(self):
        expected_script = [
            {
                "timestamp": "00:00:00,000-00:00:03,000",
                "picture": "批次描述",
                "narration": "这里是解说词",
                "OST": 2,
            }
        ]
        with patch("app.services.script_service.DocumentaryFrameAnalysisService") as service_cls:
            service = service_cls.return_value
            service.generate_documentary_script = AsyncMock(return_value=expected_script)
            generator = ScriptGenerator()

            await generator.generate_script(video_path="demo.mp4")

        called_kwargs = service.generate_documentary_script.await_args.kwargs
        self.assertIsNone(called_kwargs["frame_interval_input"])
        self.assertIsNone(called_kwargs["vision_batch_size"])
        self.assertIsNone(called_kwargs["vision_llm_provider"])

    async def test_generate_script_warns_when_skip_seconds_or_threshold_are_non_default(self):
        expected_script = [
            {
                "timestamp": "00:00:00,000-00:00:03,000",
                "picture": "批次描述",
                "narration": "这里是解说词",
                "OST": 2,
            }
        ]
        with patch("app.services.script_service.DocumentaryFrameAnalysisService") as service_cls, patch(
            "app.services.script_service.logger.warning"
        ) as warning:
            service = service_cls.return_value
            service.generate_documentary_script = AsyncMock(return_value=expected_script)
            generator = ScriptGenerator()
            await generator.generate_script(
                video_path="demo.mp4",
                skip_seconds=2,
                threshold=20,
            )

        warning.assert_called_once()
        warning_message = warning.call_args.args[0]
        self.assertIn("skip_seconds", warning_message)
        self.assertIn("threshold", warning_message)
        self.assertIn("does not currently apply", warning_message)


class DocumentaryFrameAnalysisServiceScriptGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_generate_documentary_script_returns_final_narrated_items(self):
        service = DocumentaryFrameAnalysisService()
        analysis_payload = {
            "batches": [
                {
                    "batch_index": 0,
                    "time_range": "00:00:00,000-00:00:03,000",
                    "overall_activity_summary": "",
                    "fallback_summary": "回退摘要",
                    "frame_observations": [
                        {"timestamp": "00:00:00,000", "observation": "镜头里有一只猫"},
                    ],
                }
            ]
        }

        with TemporaryDirectory() as temp_dir:
            analysis_path = Path(temp_dir) / "frame_analysis_test.json"
            analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False), encoding="utf-8")

            with patch.object(
                DocumentaryFrameAnalysisService,
                "analyze_video",
                AsyncMock(return_value={"analysis_json_path": str(analysis_path)}),
            ), patch.dict(
                "app.services.documentary.frame_analysis_service.config.app",
                {
                    "text_llm_provider": "openai",
                    "text_openai_api_key": "test-key",
                    "text_openai_model_name": "test-model",
                    "text_openai_base_url": "https://example.com/v1",
                },
            ), patch(
                "app.services.documentary.frame_analysis_service.generate_narration",
                return_value='{"items":[{"timestamp":"00:00:00,000-00:00:03,000","picture":"镜头里有一只猫","narration":"一只猫警觉地望向镜头。"}]}',
            ):
                result = await service.generate_documentary_script(video_path="demo.mp4")

        self.assertEqual(1, len(result))
        self.assertEqual("00:00:00,000-00:00:03,000", result[0]["timestamp"])
        self.assertEqual("镜头里有一只猫", result[0]["picture"])
        self.assertEqual("一只猫警觉地望向镜头。", result[0]["narration"])
        self.assertEqual(2, result[0]["OST"])

    async def test_generate_documentary_script_raises_when_narration_json_is_malformed(self):
        service = DocumentaryFrameAnalysisService()
        analysis_payload = {
            "batches": [
                {
                    "batch_index": 0,
                    "time_range": "00:00:00,000-00:00:03,000",
                    "overall_activity_summary": "测试摘要",
                    "fallback_summary": "",
                    "frame_observations": [
                        {"timestamp": "00:00:00,000", "observation": "镜头里有一只猫"},
                    ],
                }
            ]
        }

        with TemporaryDirectory() as temp_dir:
            analysis_path = Path(temp_dir) / "frame_analysis_test.json"
            analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False), encoding="utf-8")

            with patch.object(
                DocumentaryFrameAnalysisService,
                "analyze_video",
                AsyncMock(return_value={"analysis_json_path": str(analysis_path)}),
            ), patch.dict(
                "app.services.documentary.frame_analysis_service.config.app",
                {
                    "text_llm_provider": "openai",
                    "text_openai_api_key": "test-key",
                    "text_openai_model_name": "test-model",
                    "text_openai_base_url": "https://example.com/v1",
                },
            ), patch(
                "app.services.documentary.frame_analysis_service.generate_narration",
                return_value="malformed narration payload",
            ):
                with self.assertRaises(Exception) as ctx:
                    await service.generate_documentary_script(video_path="demo.mp4")

        self.assertIn("解说文案格式错误", str(ctx.exception))
        self.assertIn("items", str(ctx.exception))

    def test_parse_narration_items_recovers_from_common_json_damage(self):
        service = DocumentaryFrameAnalysisService()
        damaged_payload = """
解释文字
```json
{{
  "items": [
    {{
      "timestamp": "00:00:00,000-00:00:03,000",
      "picture": "镜头里有一只猫",
      "narration": "一只猫警觉地望向镜头。",
    }},
  ],
}}
```
补充文字
""".strip()

        parsed_items = service._parse_narration_items(damaged_payload)

        self.assertEqual(1, len(parsed_items))
        self.assertEqual("00:00:00,000-00:00:03,000", parsed_items[0]["timestamp"])
        self.assertEqual("镜头里有一只猫", parsed_items[0]["picture"])
        self.assertEqual("一只猫警觉地望向镜头。", parsed_items[0]["narration"])

    def test_parse_narration_items_raises_for_unrecoverable_payload(self):
        service = DocumentaryFrameAnalysisService()

        with self.assertRaises(ValueError) as ctx:
            service._parse_narration_items("not-json-at-all ::: ???")

        self.assertIn("解说文案格式错误", str(ctx.exception))
        self.assertIn("items", str(ctx.exception))

    async def test_generate_documentary_script_includes_theme_and_custom_prompt_for_narration(self):
        service = DocumentaryFrameAnalysisService()
        analysis_payload = {
            "batches": [
                {
                    "batch_index": 0,
                    "time_range": "00:00:00,000-00:00:03,000",
                    "overall_activity_summary": "测试摘要",
                    "fallback_summary": "",
                    "frame_observations": [
                        {"timestamp": "00:00:00,000", "observation": "镜头里有一只猫"},
                    ],
                }
            ]
        }

        with TemporaryDirectory() as temp_dir:
            analysis_path = Path(temp_dir) / "frame_analysis_test.json"
            analysis_path.write_text(json.dumps(analysis_payload, ensure_ascii=False), encoding="utf-8")

            with patch.object(
                DocumentaryFrameAnalysisService,
                "analyze_video",
                AsyncMock(return_value={"analysis_json_path": str(analysis_path)}),
            ), patch.dict(
                "app.services.documentary.frame_analysis_service.config.app",
                {
                    "text_llm_provider": "openai",
                    "text_openai_api_key": "test-key",
                    "text_openai_model_name": "test-model",
                    "text_openai_base_url": "https://example.com/v1",
                },
            ), patch(
                "app.services.documentary.frame_analysis_service.generate_narration",
                return_value='{"items":[{"timestamp":"00:00:00,000-00:00:03,000","picture":"镜头里有一只猫","narration":"一只猫警觉地望向镜头。"}]}',
            ) as mocked_generate:
                await service.generate_documentary_script(
                    video_path="demo.mp4",
                    video_theme="野生动物纪录片",
                    custom_prompt="重点描述危险信号",
                )

        narration_input = mocked_generate.call_args.args[0]
        self.assertIn("## 创作上下文", narration_input)
        self.assertIn("视频主题：野生动物纪录片", narration_input)
        self.assertIn("补充创作要求：重点描述危险信号", narration_input)

    async def test_analyze_video_forwards_explicit_empty_base_url_without_config_fallback(self):
        service = DocumentaryFrameAnalysisService()

        with patch.dict(
            "app.services.documentary.frame_analysis_service.config.app",
            {
                "vision_llm_provider": "openai",
                "vision_openai_api_key": "config-key",
                "vision_openai_model_name": "config-model",
                "vision_openai_base_url": "https://config.example/v1",
            },
        ), patch(
            "app.services.documentary.frame_analysis_service.os.path.exists",
            return_value=True,
        ), patch.object(
            service,
            "_load_or_extract_keyframes",
            return_value=["/tmp/keyframe_000001_000000100.jpg"],
        ), patch.object(
            service,
            "_analyze_batches",
            AsyncMock(return_value=[]),
        ), patch.object(
            service,
            "_save_analysis_artifact",
            return_value="/tmp/frame_analysis_test.json",
        ), patch.object(
            service,
            "_build_video_clip_json",
            return_value=[],
        ), patch(
            "app.services.documentary.frame_analysis_service.create_vision_analyzer",
            return_value=object(),
        ) as mocked_create_analyzer:
            await service.analyze_video(
                video_path="/tmp/demo.mp4",
                vision_api_key="explicit-key",
                vision_model_name="explicit-model",
                vision_base_url="",
            )

        called_kwargs = mocked_create_analyzer.call_args.kwargs
        self.assertEqual("openai", called_kwargs["provider"])
        self.assertEqual("explicit-key", called_kwargs["api_key"])
        self.assertEqual("explicit-model", called_kwargs["model"])
        self.assertEqual("", called_kwargs["base_url"])


if __name__ == "__main__":
    unittest.main()
