import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.ai_edit import (
    AIResponseError,
    AIServiceError,
    analyze_transcript,
    chunk_segments,
    clean_intervals,
    repetition_candidates,
)
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


class LocalChunkedTranscriptionTests(unittest.TestCase):
    def test_long_audio_is_transcribed_in_chunks(self):
        from app.services.ai_edit import _transcribe_local

        class Info:
            language = "zh"
            duration = 300

        model = Mock()
        model.transcribe.return_value = ([], Info())
        with patch("app.services.ai_edit._get_model", return_value=model), \
                patch("app.services.ai_edit.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.ai_edit.probe_video", return_value={"duration": 650}), \
                patch("app.services.ai_edit._extract_audio") as extract, \
                patch("app.services.ai_edit._cut_audio_segment") as cut, \
                patch("app.services.ai_edit._decode_audio_array", return_value=object()) as decode:
            result = _transcribe_local(Path("long.mp4"), log=lambda _: None)

        self.assertEqual(result, [])
        extract.assert_called_once()
        self.assertEqual(cut.call_count, 3)
        self.assertEqual(decode.call_count, 3)
        self.assertEqual(model.transcribe.call_count, 3)

    def test_short_audio_stays_one_pass(self):
        from app.services.ai_edit import _transcribe_local

        class Info:
            language = "zh"
            duration = 8

        model = Mock()
        model.transcribe.return_value = ([], Info())
        with patch("app.services.ai_edit._get_model", return_value=model), \
                patch("app.services.ai_edit.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.ai_edit.probe_video", return_value={"duration": 8}), \
                patch("app.services.ai_edit._extract_audio") as extract, \
                patch("app.services.ai_edit._cut_audio_segment") as cut, \
                patch("app.services.ai_edit._decode_audio_array", return_value=object()):
            _transcribe_local(Path("short.mp4"), log=lambda _: None)

        extract.assert_not_called()
        cut.assert_not_called()
        model.transcribe.assert_called_once()


class TranscriptionCancelTests(unittest.TestCase):
    def test_cancel_before_start_does_not_touch_the_file(self):
        from app.services.ai_edit import TranscriptionCancelled, transcribe

        with self.assertRaises(TranscriptionCancelled):
            transcribe(Path("missing.mp4"), should_cancel=lambda: True)


if __name__ == "__main__":
    unittest.main()
