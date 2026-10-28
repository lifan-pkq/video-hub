import json
import unittest
from unittest.mock import Mock, patch

from app.routers import edit
from app.services.ai_edit import (
    AIResponseError,
    AIServiceError,
    analyze_transcript,
    chunk_segments,
    clean_intervals,
    repetition_candidates,
)
from app.services.tasks import Task


class RepetitionCandidateTests(unittest.TestCase):
    def test_normalized_exact_repeat_returns_only_later_occurrence(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Hello, 世界！"},
            {"start": 1.0, "end": 2.0, "text": "完全不同的内容"},
            {"start": 2.0, "end": 3.0, "text": "hello 世界"},
        ]

        self.assertEqual(repetition_candidates(segments), [segments[2]])

    def test_high_similarity_repeat_returns_later_occurrence(self):
        segments = [
            {"start": 4.0, "end": 6.0, "text": "今天我们一起学习数据库索引"},
            {"start": 8.0, "end": 10.0, "text": "今天我们一起学习数据库的索引"},
        ]

        self.assertEqual(repetition_candidates(segments), [segments[1]])

    def test_unique_sentences_do_not_become_candidates(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "第一段内容"},
            {"start": 1.0, "end": 2.0, "text": "完全不同的第二段内容"},
        ]

        self.assertEqual(repetition_candidates(segments), [])


class TranscriptChunkTests(unittest.TestCase):
    def test_char_budget_chunks_include_context_but_keep_core_bounds(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "甲" * 8},
            {"start": 2.0, "end": 4.0, "text": "乙" * 8},
            {"start": 4.0, "end": 6.0, "text": "丙" * 8},
        ]

        chunks = chunk_segments(segments, char_budget=25, context_chars=20)

        self.assertEqual([(chunk["start"], chunk["end"]) for chunk in chunks], [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)])
        self.assertEqual(
            [[segment["text"] for segment in chunk["segments"]] for chunk in chunks],
            [["甲" * 8, "乙" * 8], ["甲" * 8, "乙" * 8, "丙" * 8], ["乙" * 8, "丙" * 8]],
        )


class IntervalCleaningTests(unittest.TestCase):
    def test_drops_invalid_intervals_clips_and_merges_overlap(self):
        items = [
            {"start": float("nan"), "end": 2.0, "kind": "intro", "reason": "nan"},
            {"start": 1.0, "end": float("inf"), "kind": "ad", "reason": "infinity"},
            {"start": 5.0, "end": 4.0, "kind": "ad", "reason": "reversed"},
            {"start": 4.0, "end": 4.2, "kind": "ad", "reason": "short"},
            {"start": -1.0, "end": 2.0, "kind": "intro", "reason": "first"},
            {"start": 1.5, "end": 3.0, "kind": "ad", "reason": "overlap"},
            {"start": 9.8, "end": 12.0, "kind": "outro", "reason": "short after clipping"},
        ]

        self.assertEqual(clean_intervals(items, 10.0), [
            {"start": 0.0, "end": 3.0, "kind": "intro", "reason": "first", "source": "llm"},
        ])


def ai_response(content: str, status_code: int = 200):
    response = Mock()
    response.status_code = status_code
    response.text = content
    response.json.return_value = {"choices": [{"message": {"content": content}}]}
    return response


class TranscriptAnalysisTests(unittest.TestCase):
    def test_repeat_without_candidates_does_not_call_ai(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "第一段内容"},
            {"start": 1.0, "end": 2.0, "text": "完全不同的第二段内容"},
        ]
        with patch("app.services.ai_edit.AI_API_KEY", "test-key"), \
                patch("app.services.ai_edit.requests.post", return_value=ai_response("[]")) as post:
            result = analyze_transcript(segments, 2.0, kinds=["repeat"], log=lambda _: None)

        self.assertEqual(result, [])
        post.assert_not_called()

    def test_repeat_ai_receives_candidates_and_returns_repeat_source(self):
        segments = [
            {"start": 0.0, "end": 1.0, "text": "Hello, 世界！"},
            {"start": 1.0, "end": 2.0, "text": "完全不同的内容"},
            {"start": 2.0, "end": 3.0, "text": "hello 世界"},
        ]
        content = json.dumps([{"start": 2.0, "end": 3.0, "kind": "repeat", "reason": "重复句"}])
        with patch("app.services.ai_edit.AI_API_KEY", "test-key"), \
                patch("app.services.ai_edit.requests.post", return_value=ai_response(content)) as post:
            result = analyze_transcript(segments, 3.0, kinds=["repeat"], log=lambda _: None)

        self.assertEqual(result, [
            {"start": 2.0, "end": 3.0, "kind": "repeat", "reason": "重复句", "source": "llm"},
        ])
        prompt = post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertIn("hello 世界", prompt)
        self.assertNotIn("完全不同的内容", prompt)

    def test_each_chunk_clamps_out_of_range_ai_intervals_to_core_bounds(self):
        segments = [
            {"start": 0.0, "end": 2.0, "text": "甲" * 8},
            {"start": 2.0, "end": 4.0, "text": "乙" * 8},
            {"start": 4.0, "end": 6.0, "text": "丙" * 8},
        ]
        content = json.dumps([{"start": -10.0, "end": 99.0, "kind": "intro", "reason": "越界"}])
        with patch("app.services.ai_edit.AI_API_KEY", "test-key"), \
                patch("app.services.ai_edit.requests.post", side_effect=[ai_response(content) for _ in range(3)]) as post:
            result = analyze_transcript(
                segments, 6.0, kinds=["intro"], char_budget=25, context_chars=20, log=lambda _: None,
            )

        self.assertEqual(post.call_count, 3)
        self.assertEqual([(item["start"], item["end"]) for item in result], [(0.0, 2.0), (2.0, 4.0), (4.0, 6.0)])

    def test_http_failure_raises_ai_service_error(self):
        with patch("app.services.ai_edit.AI_API_KEY", "test-key"), \
                patch("app.services.ai_edit.requests.post", return_value=ai_response("upstream down", 500)):
            with self.assertRaises(AIServiceError) as caught:
                analyze_transcript(
                    [{"start": 0.0, "end": 1.0, "text": "内容"}],
                    1.0, kinds=["intro"], log=lambda _: None,
                )

        self.assertIn("500", str(caught.exception))
        self.assertIsInstance(caught.exception, RuntimeError)

    def test_malformed_ai_content_raises_response_parse_error(self):
        for content in ("not json", "[broken]", '{"start": 1}'):
            with self.subTest(content=content):
                with patch("app.services.ai_edit.AI_API_KEY", "test-key"), \
                        patch("app.services.ai_edit.requests.post", return_value=ai_response(content)):
                    with self.assertRaises(AIResponseError):
                        analyze_transcript(
                            [{"start": 0.0, "end": 1.0, "text": "内容"}],
                            1.0, kinds=["intro"], log=lambda _: None,
                        )

    def test_incomplete_openai_envelope_raises_response_parse_error(self):
        response = ai_response("[]")
        response.json.return_value = {"choices": []}
        with patch("app.services.ai_edit.AI_API_KEY", "test-key"), \
                patch("app.services.ai_edit.requests.post", return_value=response):
            with self.assertRaises(AIResponseError):
                analyze_transcript(
                    [{"start": 0.0, "end": 1.0, "text": "内容"}],
                    1.0, kinds=["intro"], log=lambda _: None,
                )


class AnalyzeTaskTests(unittest.TestCase):
    @staticmethod
    def video(video_id: int):
        return {"id": video_id, "title": f"视频 {video_id}", "duration": 4.0, "filename": f"video-{video_id}.mp4"}

    def test_legacy_request_shape_has_stable_metadata_and_legacy_keep(self):
        request = edit.AnalyzeRequest(video_ids=[7], custom_prompt="保留必要背景")
        suggestion = {"start": 1.0, "end": 2.0, "kind": "intro", "reason": "寒暄", "source": "llm"}
        ids = []
        with patch("app.routers.edit.transcribe", return_value=[{"start": 0.0, "end": 1.0, "text": "字幕"}]), \
                patch("app.routers.edit.analyze_transcript", return_value=[suggestion]) as analyze:
            for _ in range(2):
                task = Task("edit")
                edit._analyze(task, [(self.video(7), "video-7.mp4")], request.kinds, request.custom_prompt)
                analysis = task.result["analyses"][0]
                ids.append(analysis["waste"][0]["id"])

        self.assertEqual(request.kinds, ["intro", "outro", "ad"])
        self.assertEqual(analyze.call_args.kwargs["custom_prompt"], "保留必要背景")
        self.assertEqual(ids[0], ids[1])
        self.assertTrue(ids[0])
        self.assertEqual(analysis["waste"][0]["video_id"], 7)
        self.assertEqual(analysis["waste"][0]["source"], "llm")
        self.assertEqual(analysis["keep"], [{"start": 0.0, "end": 1.0}, {"start": 2.0, "end": 4.0}])
        self.assertEqual(task.status, "success")

    def test_silence_only_request_skips_transcription_and_ai(self):
        request = edit.AnalyzeRequest(video_ids=[1], kinds=["silence"])
        silence = {"start": 2.0, "end": 3.0, "kind": "silence", "reason": "静音", "source": "audio"}
        with patch("app.routers.edit.transcribe") as transcribe_mock, \
                patch("app.routers.edit.analyze_transcript", return_value=[]) as analyze_mock, \
                patch("app.routers.edit.detect_silence", return_value=[silence], create=True) as detect_mock:
            task = Task("edit")
            edit._analyze(task, [(self.video(1), "video-1.mp4")], request.kinds, request.custom_prompt)

        transcribe_mock.assert_not_called()
        analyze_mock.assert_not_called()
        detect_mock.assert_called_once()
        waste = task.result["analyses"][0]["waste"]
        self.assertEqual(waste[0]["kind"], "silence")
        self.assertEqual(waste[0]["source"], "audio")
        self.assertEqual(waste[0]["video_id"], 1)
        self.assertTrue(waste[0]["id"])

    def test_partial_results_include_typed_errors_and_successful_videos(self):
        items = [(self.video(video_id), f"video-{video_id}.mp4") for video_id in range(1, 5)]

        def transcribe(path, log):
            if path == "video-1.mp4":
                raise RuntimeError("whisper failed")
            return [{"start": 0.0, "end": 1.0, "text": path}]

        def analyze(segments, duration, kinds, custom_prompt, log):
            text = segments[0]["text"]
            if text == "video-2.mp4":
                raise AIServiceError("AI offline")
            if text == "video-3.mp4":
                raise AIResponseError("bad JSON")
            return [{"start": 1.0, "end": 2.0, "kind": "intro", "reason": "可剪", "source": "llm"}]

        with patch("app.routers.edit.transcribe", side_effect=transcribe), \
                patch("app.routers.edit.analyze_transcript", side_effect=analyze):
            task = Task("edit")
            edit._analyze(task, items, ["intro"], "")

        self.assertEqual(
            {error["video_id"]: error["type"] for error in task.result["errors"]},
            {1: "transcription", 2: "ai", 3: "parse"},
        )
        self.assertEqual(len(task.result["analyses"]), 4)
        self.assertEqual(task.result["analyses"][3]["waste"][0]["start"], 1.0)
        self.assertEqual(task.status, "partial")

    def test_transcription_error_does_not_discard_audio_results(self):
        silence = {"start": 2.0, "end": 3.0, "kind": "silence", "reason": "静音", "source": "audio"}
        with patch("app.routers.edit.transcribe", side_effect=RuntimeError("whisper failed")), \
                patch("app.routers.edit.analyze_transcript") as analyze_mock, \
                patch("app.routers.edit.detect_silence", return_value=[silence], create=True):
            task = Task("edit")
            edit._analyze(task, [(self.video(1), "video-1.mp4")], ["silence", "intro"], "")

        analyze_mock.assert_not_called()
        self.assertEqual(task.result["errors"][0]["type"], "transcription")
        self.assertEqual(task.result["analyses"][0]["waste"][0]["kind"], "silence")
        self.assertEqual(task.status, "partial")

    def test_empty_legacy_kind_list_keeps_original_default(self):
        request = edit.AnalyzeRequest(video_ids=[1], kinds=[])
        task = Task("edit")
        with patch("app.routers.edit.get_video", return_value=self.video(1)), \
                patch("app.routers.edit.Path.exists", return_value=True), \
                patch("app.routers.edit.task_manager.create", return_value=task), \
                patch("app.routers.edit.threading.Thread") as thread:
            result = edit.analyze_video(request)

        self.assertEqual(result, {"task_id": task.id})
        self.assertEqual(thread.call_args.kwargs["args"][2], ["intro", "outro", "ad"])


if __name__ == "__main__":
    unittest.main()
