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
        from app.services.transcription import _transcribe_local

        class Info:
            language = "zh"
            duration = 300

        model = Mock()
        model.transcribe.return_value = ([], Info())
        with patch("app.services.transcription._get_model", return_value=model), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 650}), \
                patch("app.services.transcription._extract_audio") as extract, \
                patch("app.services.transcription._cut_audio_segment") as cut, \
                patch("app.services.transcription._decode_audio_array", return_value=object()) as decode:
            result = _transcribe_local(Path("long.mp4"), log=lambda _: None)

        self.assertEqual(result, [])
        extract.assert_called_once()
        self.assertEqual(cut.call_count, 3)
        self.assertEqual(decode.call_count, 3)
        self.assertEqual(model.transcribe.call_count, 3)

    def test_short_audio_stays_one_pass(self):
        from app.services.transcription import _transcribe_local

        class Info:
            language = "zh"
            duration = 8

        model = Mock()
        model.transcribe.return_value = ([], Info())
        with patch("app.services.transcription._get_model", return_value=model), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 8}), \
                patch("app.services.transcription._extract_audio") as extract, \
                patch("app.services.transcription._cut_audio_segment") as cut, \
                patch("app.services.transcription._decode_audio_array", return_value=object()):
            _transcribe_local(Path("short.mp4"), log=lambda _: None)

        extract.assert_not_called()
        cut.assert_not_called()
        model.transcribe.assert_called_once()

    def test_missing_duration_extracts_audio_instead_of_decoding_the_file(self):
        import numpy as np

        from app.services.transcription import _transcribe_local

        class Info:
            language = "zh"
            duration = 20

        model = Mock()
        model.transcribe.return_value = ([], Info())
        arrays = [
            np.zeros(16000 * 300, dtype=np.float32),
            np.zeros(16000 * 20, dtype=np.float32),
        ]

        def decode(path, **kwargs):
            self.assertNotEqual(Path(path), Path("mystery.mp4"))
            return arrays.pop(0)

        with patch("app.services.transcription._get_model", return_value=model), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription.probe_video", return_value={}), \
                patch("app.services.transcription._extract_audio") as extract, \
                patch("app.services.transcription._cut_audio_segment") as cut, \
                patch("app.services.transcription._decode_audio_array", side_effect=decode):
            _transcribe_local(Path("mystery.mp4"), log=lambda _: None)

        extract.assert_called_once()
        self.assertEqual(cut.call_count, 2)
        self.assertEqual(model.transcribe.call_count, 2)
        self.assertEqual(arrays, [])

    def test_missing_duration_does_not_swallow_the_first_cut_error(self):
        from app.services.transcription import _transcribe_local

        model = Mock()
        with patch("app.services.transcription._get_model", return_value=model), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription.probe_video", return_value={}), \
                patch("app.services.transcription._extract_audio"), \
                patch("app.services.transcription._cut_audio_segment", side_effect=AIServiceError("切分失败")), \
                patch("app.services.transcription._decode_audio_array") as decode:
            with self.assertRaises(AIServiceError):
                _transcribe_local(Path("mystery.mp4"), log=lambda _: None)
        decode.assert_not_called()
        model.transcribe.assert_not_called()

    def test_underestimated_duration_retries_in_chunks_when_pcm_is_capped(self):
        from app.services.transcription import AudioDecodeLimit, _transcribe_local

        class Info:
            language = "zh"
            duration = 8

        model = Mock()
        model.transcribe.return_value = ([], Info())
        with patch("app.services.transcription._get_model", return_value=model), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 8}), \
                patch("app.services.transcription._decode_audio_array", side_effect=AudioDecodeLimit("cap")), \
                patch("app.services.transcription._transcribe_local_chunked", return_value=[{"start": 0, "end": 1, "text": "分段"}]) as chunked:
            result = _transcribe_local(Path("short-lie.mp4"), log=lambda _: None)
        self.assertEqual(result[0]["text"], "分段")
        chunked.assert_called_once()

    def _unknown(self, cut, decode, *, chunks=None):
        import numpy as np

        from app.services.transcription import _transcribe_local

        class Info:
            language = "zh"
            duration = 300

        model = Mock()
        model.transcribe.return_value = ([], Info())
        if chunks is None:
            chunks = [np.zeros(16000 * 300, dtype=np.float32)]

        def decode_side(path, **kwargs):
            return chunks.pop(0)

        patches = [
            patch("app.services.transcription._get_model", return_value=model),
            patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("app.services.transcription.probe_video", return_value={}),
            patch("app.services.transcription._extract_audio"),
            patch("app.services.transcription._cut_audio_segment", side_effect=cut),
            patch("app.services.transcription._decode_audio_array", side_effect=decode or decode_side),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return _transcribe_local(Path("mystery.mp4"), log=lambda _: None), model

    def test_timeout_after_first_segment_is_not_treated_as_eof(self):
        with self.assertRaises(AIServiceError) as caught:
            self._unknown([300.0, AIServiceError("切分音频失败（300s 段）超时（600s）")], None)
        self.assertIn("超时", str(caught.exception))
        self.assertNotIn("末尾", str(caught.exception))

    def test_cancel_after_first_segment_is_not_treated_as_eof(self):
        from app.services.ai_edit import TranscriptionCancelled

        with self.assertRaises(TranscriptionCancelled):
            self._unknown([300.0, TranscriptionCancelled("任务已取消")], None)

    def test_decode_failure_after_first_segment_is_not_treated_as_eof(self):
        import numpy as np

        chunks = [np.zeros(16000 * 300, dtype=np.float32)]

        def decode(path, **kwargs):
            if chunks:
                return chunks.pop(0)
            raise AIServiceError("ffmpeg 解码音频失败：invalid data")

        with self.assertRaises(AIServiceError) as caught:
            self._unknown([300.0, 300.0], decode)
        self.assertIn("解码音频失败", str(caught.exception))

    def test_past_end_after_a_full_segment_keeps_only_audio_already_read(self):
        from app.services.transcription import AudioPastEnd

        result, model = self._unknown([300.0, AudioPastEnd("越过结尾")], None)
        self.assertEqual(result, [])
        self.assertEqual(model.transcribe.call_count, 1)

    def test_short_decode_of_a_full_segment_does_not_count_as_eof(self):
        import numpy as np

        chunks = [
            np.zeros(16000 * 300, dtype=np.float32),
            np.zeros(16000 * 20, dtype=np.float32),
        ]
        with self.assertRaises(AIServiceError) as caught:
            self._unknown([300.0, 300.0], lambda path, **kwargs: chunks.pop(0))
        self.assertIn("解码只有", str(caught.exception))

    def test_walking_forever_fails_instead_of_returning_a_prefix(self):
        import numpy as np

        full = np.zeros(16000 * 300, dtype=np.float32)
        with patch("app.services.transcription._MAX_UNKNOWN_CHUNKS", 2):
            with self.assertRaises(AIServiceError) as caught:
                self._unknown([300.0, 300.0], lambda path, **kwargs: full.copy())
        self.assertIn("仍未到结尾", str(caught.exception))
        self.assertIn("2 段", str(caught.exception))

    def test_known_duration_past_end_fails_instead_of_returning_a_prefix(self):
        import numpy as np

        from app.services.transcription import AudioPastEnd, _transcribe_local

        class Info:
            language = "zh"
            duration = 300

        model = Mock()
        model.transcribe.return_value = ([], Info())
        with patch("app.services.transcription._get_model", return_value=model), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 650}), \
                patch("app.services.transcription._extract_audio"), \
                patch("app.services.transcription._cut_audio_segment", side_effect=[300.0, AudioPastEnd("越过")]), \
                patch("app.services.transcription._decode_audio_array", return_value=np.zeros(16000 * 300, dtype=np.float32)):
            with self.assertRaises(AIServiceError) as caught:
                _transcribe_local(Path("long.mp4"), log=lambda _: None)
        self.assertIn("没有可转写", str(caught.exception))
        self.assertEqual(model.transcribe.call_count, 1)


class DecodeLimitTests(unittest.TestCase):
    def test_decode_stops_past_the_pcm_cap_and_skips_video(self):
        import os
        import shutil
        import subprocess
        import tempfile

        from app.services.ai_edit import AIServiceError
        from app.services.transcription import _decode_audio_array

        if not shutil.which("ffmpeg"):
            self.skipTest("需要 ffmpeg")
        commands = []
        real_popen = subprocess.Popen

        def spy(cmd, **kwargs):
            commands.append(cmd)
            return real_popen(cmd, **kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            wav = os.path.join(tmp, "tone.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-ar", "16000", "-ac", "1", wav],
                check=True, timeout=30,
            )
            with patch("app.services.transcription.subprocess.Popen", side_effect=spy), \
                    patch("app.services.transcription._PCM_BYTE_CAP", 1000):
                with self.assertRaises(AIServiceError) as caught:
                    _decode_audio_array(Path(wav), timeout=30)
        self.assertIn("内存上限", str(caught.exception))
        self.assertTrue(commands)
        self.assertIn("-vn", commands[0])

    def test_decode_timeout_kills_a_blocked_ffmpeg(self):
        import os
        import shutil
        import tempfile
        import time

        from app.services.ai_edit import AIServiceError
        from app.services.transcription import _decode_audio_array

        if not shutil.which("ffmpeg"):
            self.skipTest("需要 ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = os.path.join(tmp, "block.fifo")
            os.mkfifo(fifo)
            started = time.monotonic()
            with self.assertRaises(AIServiceError) as caught:
                _decode_audio_array(Path(fifo), timeout=1)
        self.assertIn("超时", str(caught.exception))
        self.assertLess(time.monotonic() - started, 8)

    def test_cut_distinguishes_eof_timeout_and_corrupt_input(self):
        import os
        import shutil
        import subprocess
        import tempfile

        from app.services.ai_edit import TranscriptionCancelled
        from app.services.transcription import AudioPastEnd, _cut_audio_segment
        from engine.errors import EngineError

        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("需要 ffmpeg 和 ffprobe")
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "tone.mp3")
            subprocess.run(
                ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-ar", "16000", "-ac", "1", "-b:a", "64k", source],
                check=True, timeout=30,
            )
            tail = _cut_audio_segment(Path(source), 1.2, 300, Path(tmp) / "tail.mp3")
            self.assertGreater(tail, 0.2)
            self.assertLess(tail, 1.5)
            with self.assertRaises(AudioPastEnd):
                _cut_audio_segment(Path(source), 30, 300, Path(tmp) / "past.mp3")
            junk = Path(tmp) / "junk.mp3"
            junk.write_bytes(b"not-an-mp3")
            with self.assertRaises(AIServiceError) as caught:
                _cut_audio_segment(junk, 0, 1, Path(tmp) / "bad.mp3")
            self.assertNotIsInstance(caught.exception, AudioPastEnd)

        with patch("engine.media.ffmpeg", side_effect=EngineError("命令超时（600s）", code="timeout")):
            with self.assertRaises(AIServiceError) as timed:
                _cut_audio_segment(Path("audio.mp3"), 300, 300, Path("seg.mp3"))
        self.assertIn("超时", str(timed.exception))
        self.assertNotIsInstance(timed.exception, AudioPastEnd)

        with patch("engine.media.ffmpeg", side_effect=EngineError("任务已取消", code="cancelled")):
            with self.assertRaises(TranscriptionCancelled):
                _cut_audio_segment(Path("audio.mp3"), 300, 300, Path("seg.mp3"))


class TranscriptionCancelTests(unittest.TestCase):
    def test_cancel_before_start_does_not_touch_the_file(self):
        from app.services.ai_edit import TranscriptionCancelled, transcribe

        with self.assertRaises(TranscriptionCancelled):
            transcribe(Path("missing.mp4"), should_cancel=lambda: True)


if __name__ == "__main__":
    unittest.main()
