"""目标时长、独立原声解说，以及下载/转写的失败边界。"""
import os
import shutil
import sqlite3
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from app import db
from app.services.ai_edit import AIResponseError, AIServiceError
from app.services.douyin_plan import plan_douyin_shorts, requested_limits
from app.services.speech_script import create_draft, draft_script, render_original, save_review


def _speech(duration: float, step: float = 4.0) -> list[dict]:
    segments = []
    cursor = 0.0
    while cursor < duration - 1:
        segments.append({
            "start": round(cursor, 2),
            "end": round(min(duration, cursor + step - 0.2), 2),
            "text": f"这是一句可以单独听懂的口播{int(cursor)}",
        })
        cursor += step
    return segments


def _no_model(prompt, log):
    raise AIServiceError("文本模型没有配置")


class DurationConstraintTests(unittest.TestCase):
    def test_instruction_and_argument_are_read(self):
        self.assertEqual(requested_limits("只生成1条，时长不超过15秒"), (15.0, 1))
        self.assertEqual(requested_limits("12秒以内"), (12.0, None))
        self.assertEqual(requested_limits(""), (None, None))

    def test_target_keeps_sentence_end_inside_the_cap(self):
        def ask(_prompt, _log):
            return [{"start": 0, "end": 29, "title": "整段", "score": 5, "description": "整段"}]

        planned = plan_douyin_shorts(
            _speech(29), 29, instruction="不超过15秒", max_clips=1, request_ai=ask,
        )
        self.assertTrue(planned["clips"])
        for clip in planned["clips"]:
            self.assertLessEqual(clip["duration"], 15.0)
            self.assertGreaterEqual(clip["duration"], 3.0)
            ends = [item["end"] for item in _speech(29)]
            self.assertIn(clip["end"], ends)

    def test_unsplittable_sentence_fails_instead_of_keeping_the_source(self):
        segments = [{"start": 0.0, "end": 22.0, "text": "这一句一直说到二十二秒所以放不进十五秒"}]
        with self.assertRaises(AIResponseError) as caught:
            plan_douyin_shorts(
                segments, 29, max_seconds=15, request_ai=lambda *_args: [{"start": 0, "end": 29, "score": 5}],
            )
        self.assertIn("无法在", str(caught.exception))
        self.assertIn("没有在句中截断", str(caught.exception))

    def test_source_shorter_than_the_cap_can_stay_whole(self):
        planned = plan_douyin_shorts(_speech(12), 12, max_seconds=15, request_ai=_no_model)
        self.assertTrue(planned["clips"])
        self.assertLessEqual(max(item["duration"] for item in planned["clips"]), 12.05)

    def test_without_a_cap_a_short_source_can_exceed_fifteen_seconds(self):
        planned = plan_douyin_shorts(_speech(29), 29, request_ai=_no_model)
        self.assertGreater(max(item["duration"] for item in planned["clips"]), 15)

    def test_out_of_range_cap_is_rejected(self):
        with self.assertRaises(AIResponseError):
            plan_douyin_shorts(_speech(40), 40, instruction="不超过3秒", request_ai=_no_model)


class NarrationScriptTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init_schema(self.conn)
        self.patcher = patch("app.db._get_conn", return_value=self.conn)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.conn.close)
        self.video = Path(self.id().replace(".", "_") + ".mp4")

    def tearDown(self):
        self.video.unlink(missing_ok=True)

    def _segments(self):
        return [
            {"start": 0.0, "end": 6.0, "text": "今天讲一件小事，先把结论说完。"},
            {"start": 6.0, "end": 12.0, "text": "后面还有一句补充。"},
            {"start": 12.0, "end": 28.0, "text": "这段已经超出目标，不应该整段留下。"},
        ]

    def test_target_stops_on_a_sentence_and_marks_forced_splits(self):
        script = draft_script(self._segments(), 15)
        self.assertEqual(script["audio"], "original")
        self.assertEqual(script["pause_seconds"], 0.45)
        self.assertEqual(script["stopped"], "limit")
        self.assertLessEqual(script["covered_seconds"], 15.05)
        self.assertLess(script["covered_seconds"], 28)
        self.assertTrue(all(beat["speed"] == 1.0 for beat in script["beats"]))

        with self.assertRaises(AIResponseError) as caught:
            draft_script([{"start": 0.0, "end": 20.0, "text": "只有一句很长。"}], 8)
        self.assertIn("没有截断", str(caught.exception))

    def test_render_requires_review_then_writes_original_audio(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg 不可用")
        import tempfile
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        previous = os.environ.get("VIDEO_HUB_DATA_DIR")
        os.environ["VIDEO_HUB_DATA_DIR"] = work.name
        def restore():
            if previous is None:
                os.environ.pop("VIDEO_HUB_DATA_DIR", None)
            else:
                os.environ["VIDEO_HUB_DATA_DIR"] = previous
        self.addCleanup(restore)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=4",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
             "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.video)],
            check=True,
        )
        with patch("app.services.speech_script.fetch", return_value={"id": 7, "title": "原片"}):
            row = create_draft(7, 30, segments=self._segments())
        with self.assertRaises(AIResponseError):
            render_original(row["id"])
        reviewed = save_review(row["id"], [
            {"text": "第一句", "start": 0.2, "end": 1.4},
            {"text": "第二句", "start": 1.6, "end": 3.0},
        ])
        self.assertTrue(reviewed["result"]["reviewed"])
        self.assertEqual(reviewed["result"]["beats"][0]["pause_after"], 0.45)
        with patch("app.services.speech_script.fetch", return_value={"id": 7, "title": "原片"}), \
                patch("app.services.speech_script.resolve_media", return_value=self.video):
            done = render_original(row["id"])
        output = Path(done["result"]["output"])
        self.assertTrue(output.is_file())
        probed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(output)],
            check=True, capture_output=True, text=True,
        )
        duration = float(probed.stdout.strip())
        self.assertGreater(duration, 2.4)
        self.assertLess(duration, 4.5)

        from fastapi.testclient import TestClient
        from app.main import app

        response = TestClient(app).get(f"/api/narrate/tasks/{row['id']}/file")
        self.assertEqual(response.status_code, 200)
        self.assertIn("video/mp4", response.headers["content-type"])
        self.assertGreater(len(response.content), 1000)

    def test_missing_video_and_empty_script_fail_clearly(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        missing = client.post("/api/narrate/tasks", json={"video_id": 999999, "target_seconds": 30})
        self.assertEqual(missing.status_code, 404)
        rejected = client.post("/api/narrate/tasks", json={"video_id": 1, "target_seconds": 2})
        self.assertEqual(rejected.status_code, 422)
        with patch("app.services.speech_script.fetch", return_value={"id": 3, "title": "原片"}):
            row = create_draft(3, 20, segments=[{"start": 0, "end": 6, "text": "一句完整的话。"}])
        empty = client.post(f"/api/narrate/tasks/{row['id']}/script", json={"beats": [{"text": "  ", "start": 0, "end": 1}]})
        self.assertEqual(empty.status_code, 422)


class TranscriptionBoundaryTests(unittest.TestCase):
    def test_past_end_after_the_first_chunk_stops_the_walk(self):
        from app.services.transcription import AudioPastEnd, _transcribe_local_chunked

        def cut(_audio, start, _length, out, **_kwargs):
            if start > 0:
                raise AudioPastEnd("越过结尾")
            out.write_bytes(b"x" * 2048)

        info = Mock(language="zh")
        with patch("app.services.transcription._extract_audio"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 0}), \
                patch("app.services.transcription._cut_audio_segment", side_effect=cut), \
                patch("app.services.transcription._decode_audio_array", return_value=object()), \
                patch("app.services.transcription._run_whisper", return_value=([{"start": 0, "end": 1, "text": "一句"}], info)):
            result = _transcribe_local_chunked(Path("unknown.mp4"), Mock(), 0, lambda _message: None)
        self.assertEqual(result, [{"start": 0, "end": 1, "text": "一句"}])

    def test_past_end_on_the_first_chunk_is_not_a_transcript(self):
        from app.services.transcription import AudioPastEnd, _transcribe_sensevoice

        def cut(_audio, _start, _length, _out, **_kwargs):
            raise AudioPastEnd("第一段就空了")

        with patch("app.services.sensevoice._recognizer", return_value=object()), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription._extract_audio"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 0}), \
                patch("app.services.transcription._cut_audio_segment", side_effect=cut):
            with self.assertRaises(AIServiceError) as caught:
                _transcribe_sensevoice(Path("broken.mp4"), log=lambda _message: None)
        self.assertIn("不能把缺少的后半段当成已经转完", str(caught.exception))

    def test_unknown_walk_without_an_ending_is_rejected(self):
        from app.services.transcription import _transcribe_local_chunked

        def cut(_audio, _start, _length, out, **_kwargs):
            out.write_bytes(b"x" * 2048)

        info = Mock(language="zh")
        with patch("app.services.transcription._extract_audio"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 0}), \
                patch("app.services.transcription._cut_audio_segment", side_effect=cut), \
                patch("app.services.transcription._decode_audio_array", return_value=object()), \
                patch("app.services.transcription._run_whisper", return_value=([], info)):
            with self.assertRaises(AIServiceError) as caught:
                _transcribe_local_chunked(Path("endless.mp4"), Mock(), 0, lambda _message: None)
        self.assertIn("不能把前半段当成完整转写", str(caught.exception))

    def test_decode_timeout_still_fails(self):
        from app.services.transcription import _decode_audio_array

        with patch("app.services.transcription.subprocess.run", side_effect=subprocess.TimeoutExpired("ffmpeg", 180)):
            with self.assertRaises(AIServiceError) as caught:
                _decode_audio_array(Path("slow.mp4"))
        self.assertIn("超时", str(caught.exception))


class DouyinDownloadBoundaryTests(unittest.TestCase):
    def test_millisecond_duration_and_short_values(self):
        from app.services.douyin_fallback import douyin_duration_seconds

        self.assertEqual(douyin_duration_seconds(29000), 29)
        self.assertEqual(douyin_duration_seconds(8500), 8.5)
        self.assertEqual(douyin_duration_seconds(12), 12)
        self.assertIsNone(douyin_duration_seconds(0))

    def _download(self, **kwargs):
        from app.services.douyin_fallback import download_douyin

        class Response:
            def __init__(self, chunks, headers=None):
                self.headers = headers or {}
                self._chunks = chunks

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield from self._chunks

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class Session:
            def __init__(self, response):
                self.response = response

            def get(self, *_args, **_kwargs):
                return self.response

        path = Path(self.id().replace(".", "_") + ".mp4")
        self.addCleanup(path.unlink, missing_ok=True)
        with patch("app.services.douyin_fallback.parse_douyin", return_value={"_play_url": "https://example.invalid/a.mp4", "id": "1"}), \
                patch("app.services.douyin_fallback._session", return_value=Session(kwargs.pop("response"))), \
                patch("app.services.douyin_fallback.shutil.disk_usage", return_value=kwargs.pop("disk")):
            return download_douyin("https://v.douyin.com/x", path, **kwargs), path

    def test_cancel_size_timeout_and_disk(self):
        from app.services.douyin_fallback import FallbackCancelled, download_douyin

        with self.assertRaises(FallbackCancelled):
            download_douyin("https://v.douyin.com/x", Path("unused.mp4"), should_cancel=lambda: True, max_bytes=1000, deadline_s=5)

        class Usage:
            free = 1024

        with self.assertRaises(RuntimeError) as disk_error:
            self._download(response=self._response([b"abcdef"]), disk=Usage(), max_bytes=1000, deadline_s=5)
        self.assertIn("512MB", str(disk_error.exception))

        class Enough:
            free = 1024 * 1024 * 1024

        with self.assertRaises(RuntimeError) as size_error:
            self._download(response=self._response([b"a" * 50]), disk=Enough(), max_bytes=10, deadline_s=30)
        self.assertIn("大小上限", str(size_error.exception))

        clock = {"now": 0.0}

        def monotonic():
            clock["now"] += 10
            return clock["now"]

        with patch("app.services.douyin_fallback.time.monotonic", side_effect=monotonic):
            with self.assertRaises(TimeoutError):
                self._download(response=self._response([b"a" * 20, b"b" * 20]), disk=Enough(), max_bytes=1000, deadline_s=5)

    @staticmethod
    def _response(chunks, headers=None):
        class Response:
            def __init__(self):
                self.headers = headers or {}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield from chunks

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Response()


if __name__ == "__main__":
    unittest.main()
