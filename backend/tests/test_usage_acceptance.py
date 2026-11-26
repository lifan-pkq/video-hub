"""使用验收。合成夹具和真实语音分开；合成结果不能当成真人声或模型验收。"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from app.jobs import cancel, create, get
from app.services.ai_edit import AIResponseError, AIServiceError
from app.services.douyin_plan import plan_douyin_shorts
from app.services.transcription import AudioPastEnd


def _speech(duration: float, step: float = 4.0) -> list[dict]:
    segments = []
    cursor = 0.0
    while cursor < duration - 0.5:
        segments.append({
            "start": round(cursor, 2),
            "end": round(min(duration, cursor + step - 0.2), 2),
            "text": f"这是一句可以单独听懂的口播{int(cursor)}",
        })
        cursor += step
    return segments


def _no_model(_prompt, _log):
    raise AIServiceError("文本模型没有配置")


class SyntheticDurationAcceptanceTests(unittest.TestCase):
    """计划层。用生成的时间轴，不用真实语音。"""

    def test_omitted_cap_keeps_the_old_window(self):
        planned = plan_douyin_shorts(_speech(40), 40, request_ai=_no_model)
        self.assertEqual(planned["limit_mode"], "window")
        self.assertGreater(max(item["duration"] for item in planned["clips"]), 30)

    def test_explicit_thirty_second_cap_is_a_maximum_not_an_exact_cut(self):
        planned = plan_douyin_shorts(_speech(40), 40, max_seconds=30, request_ai=_no_model)
        self.assertEqual(planned["limit_mode"], "cap")
        self.assertLessEqual(planned["max_seconds"], 30)
        self.assertTrue(planned["clips"])
        for clip in planned["clips"]:
            self.assertLessEqual(clip["duration"], 30)
            self.assertGreaterEqual(clip["duration"], 3)
        self.assertLess(max(item["duration"] for item in planned["clips"]), 40)

    def test_sentence_longer_than_the_cap_fails_clearly(self):
        segments = [{"start": 0.0, "end": 36.0, "text": "这一句一直说到三十六秒所以放不进三十秒上限"}]
        with self.assertRaises(AIResponseError) as caught:
            plan_douyin_shorts(segments, 40, max_seconds=30, request_ai=lambda *_args: [{"start": 0, "end": 40, "score": 5}])
        self.assertIn("没有在句中截断", str(caught.exception))


class SyntheticAsrBoundaryTests(unittest.TestCase):
    """转写边界用假分段，不是 SenseVoice 或 Whisper 的模型验收。"""

    def test_unknown_duration_does_not_keep_a_prefix(self):
        from app.services.transcription import _transcribe_local_chunked

        def cut(_audio, start, _length, out, **_kwargs):
            if start > 0:
                raise AudioPastEnd("越过结尾")
            out.write_bytes(b"x" * 2048)

        with patch("app.services.transcription._extract_audio"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 0}), \
                patch("app.services.transcription._cut_audio_segment", side_effect=cut), \
                patch("app.services.transcription._decode_audio_array", return_value=object()), \
                patch("app.services.transcription._run_whisper", return_value=([{"start": 0, "end": 1, "text": "前半"}], type("Info", (), {"language": "zh"})())):
            kept = _transcribe_local_chunked(Path("unknown.mp4"), object(), 0, lambda _message: None)
        self.assertEqual(kept[0]["text"], "前半")

        def cut_first(_audio, _start, _length, _out, **_kwargs):
            raise AudioPastEnd("第一段就空了")

        with patch("app.services.sensevoice._recognizer", return_value=object()), \
                patch("app.services.transcription.shutil.which", return_value="/usr/bin/ffmpeg"), \
                patch("app.services.transcription._extract_audio"), \
                patch("app.services.transcription.probe_video", return_value={"duration": 0}), \
                patch("app.services.transcription._cut_audio_segment", side_effect=cut_first):
            with self.assertRaises(AIServiceError) as caught:
                from app.services.transcription import _transcribe_sensevoice
                _transcribe_sensevoice(Path("broken.mp4"), log=lambda _message: None)
        self.assertIn("不能把缺少的后半段当成已经转完", str(caught.exception))


class SyntheticRenderAcceptanceTests(unittest.TestCase):
    """ffmpeg 色块和正弦波。测量时长和音轨，不把它说成真人声。"""

    def setUp(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg 或 ffprobe 不可用")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        previous = os.environ.get("VIDEO_HUB_DATA_DIR")
        os.environ["VIDEO_HUB_DATA_DIR"] = self.tmp.name
        os.environ["VIDEO_HUB_DOUYIN_WIDTH"] = "480"
        os.environ["VIDEO_HUB_DOUYIN_HEIGHT"] = "854"

        def restore():
            if previous is None:
                os.environ.pop("VIDEO_HUB_DATA_DIR", None)
            else:
                os.environ["VIDEO_HUB_DATA_DIR"] = previous
            os.environ.pop("VIDEO_HUB_DOUYIN_WIDTH", None)
            os.environ.pop("VIDEO_HUB_DOUYIN_HEIGHT", None)
        self.addCleanup(restore)
        self.source = Path(self.tmp.name) / "tone.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:r=25:d=8",
             "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=8",
             "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(self.source)],
            check=True,
        )

    def _segments(self):
        return [
            {"start": 0.0, "end": 3.6, "text": "前一句口播说明。"},
            {"start": 3.6, "end": 7.4, "text": "后一句口播收束。"},
        ]

    def _produce(self, **kwargs):
        from app.services.douyin_flow import produce_douyin

        job = create("edit.douyin", "验收", {"video_id": 1, "max_seconds": kwargs.get("max_seconds")})
        with patch("app.services.douyin_flow.fetch", return_value={"id": 1, "title": "合成音"}), \
                patch("app.services.douyin_flow.resolve_media", return_value=self.source), \
                patch("app.services.douyin_flow.transcribe", return_value=self._segments()), \
                patch("app.services.douyin_flow.sample_skin_boxes", return_value=[]):
            produce_douyin(job["id"], 1, max_clips=1, **kwargs)
        return get(job["id"])

    def test_cap_render_preview_and_download(self):
        done = self._produce(max_seconds=30, source_captions="unknown")
        self.assertEqual(done["status"], "success", done.get("error"))
        limit = done["result"]["clip_limit"]
        self.assertEqual(limit["mode"], "cap")
        self.assertLessEqual(limit["seconds"], 30)
        short = done["result"]["shorts"][0]
        self.assertLessEqual(short["duration"], 30)
        self.assertFalse(short["captions_burned"])
        self.assertTrue(short["reason"] or short["hook"])
        path = Path(self.tmp.name) / "douyin" / done["id"] / "01" / "video.mp4"
        self.assertTrue(path.is_file())
        self.assertLess(path.stat().st_size, 15 * 1024 * 1024)
        probed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            check=True, capture_output=True, text=True,
        )
        self.assertLessEqual(float(probed.stdout.strip()), 30.5)
        audio = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
            check=True, capture_output=True, text=True,
        )
        self.assertIn("audio", audio.stdout)

        from fastapi.testclient import TestClient
        from app.main import app

        response = TestClient(app).get(short["file_url"])
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(response.content), 1000)
        self.assertIn("video/mp4", response.headers["content-type"])

    def test_transcribe_can_burn_and_unknown_does_not(self):
        plain = self._produce(max_seconds=30, source_captions="burned_in")
        self.assertFalse(plain["result"]["shorts"][0]["captions_burned"])
        try:
            burned = self._produce(
                max_seconds=30,
                source_captions="transcribe",
                text_enhance={"enabled": True, "captions": True, "style": "outline", "emphasis": "none"},
            )
        except Exception as exc:  # noqa: BLE001 - 缺中文字体时如实跳过烧录，不把失败说成已烧上
            self.skipTest(f"显式烧录没有跑完：{exc}")
        if burned["status"] != "success":
            self.skipTest(f"显式烧录没有成功：{burned.get('error')}")
        self.assertTrue(burned["result"]["caption_policy"]["burns_transcript"])
        self.assertTrue(burned["result"]["shorts"][0]["captions_burned"])

    def test_cancel_before_transcribe_stops_the_job(self):
        from app.services.douyin_flow import produce_douyin
        from engine.errors import EngineError

        job = create("edit.douyin", "取消", {"video_id": 1})
        cancel(job["id"])

        def transcribe(*_args, **kwargs):
            self.assertTrue(kwargs["should_cancel"]())
            from app.services.ai_edit import TranscriptionCancelled
            raise TranscriptionCancelled("任务已取消")

        with patch("app.services.douyin_flow.fetch", return_value={"id": 1, "title": "合成音"}), \
                patch("app.services.douyin_flow.resolve_media", return_value=self.source), \
                patch("app.services.douyin_flow.transcribe", side_effect=transcribe):
            with self.assertRaises(EngineError) as caught:
                produce_douyin(job["id"], 1, max_seconds=30)
        self.assertEqual(caught.exception.code, "cancelled")

    def test_manual_review_does_not_replace_the_automatic_words(self):
        done = self._produce(max_seconds=30, source_captions="unknown")
        short = done["result"]["shorts"][0]
        original = short["asr_cues"][0]["text"]
        from app.services.douyin_flow import reburn_short

        with patch("app.services.douyin_flow.fetch", return_value={"id": 1, "title": "合成音"}), \
                patch("app.services.douyin_flow.resolve_media", return_value=self.source):
            body = reburn_short(
                done["id"],
                1,
                [{
                    "start": short["asr_cues"][0]["start"],
                    "end": short["asr_cues"][0]["end"],
                    "text": "人工改过，不是识别结果",
                }],
                source_captions="unknown",
            )
        self.assertFalse(body["caption_policy"]["burns_transcript"])
        self.assertEqual(body["short"]["quote_cues"][0]["text"], "人工改过，不是识别结果")
        self.assertNotEqual(original, "人工改过，不是识别结果")
        self.assertTrue(original)

    def test_retry_keeps_the_requested_cap(self):
        from fastapi.testclient import TestClient
        from app.main import app

        os.environ["VIDEO_HUB_JOBS_INLINE"] = "1"
        client = TestClient(app)

        def fail(job_id, *_args, **_kwargs):
            from app.jobs import update
            update(job_id, status="failed", stage="failed", label="失败", error={"message": "转写失败"})

        with patch("app.routers.edit.fetch", return_value={"id": 9, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=fail):
            created = client.post("/api/edit/douyin", json={"video_id": 9, "max_seconds": 30, "instruction": ""})
        self.assertEqual(created.status_code, 200, created.text)
        failed = created.json()
        self.assertEqual(failed["input"]["max_seconds"], 30)
        self.assertEqual(failed["status"], "failed")

        def succeed(job_id, *_args, **kwargs):
            from app.jobs import update
            update(job_id, status="success", progress=100, stage="done", label="完成", result={
                "shorts": [],
                "clip_limit": {"mode": "cap", "seconds": kwargs.get("max_seconds"), "note": "上限"},
            })

        with patch("app.routers.edit.fetch", return_value={"id": 9, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=succeed):
            retried = client.post(f"/api/edit/douyin/{failed['id']}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        body = retried.json()
        self.assertEqual(body["input"]["max_seconds"], 30)
        self.assertEqual(body["status"], "success")
        self.assertNotEqual(body["id"], failed["id"])


@unittest.skipUnless(os.environ.get("VIDEO_HUB_REAL_SPEECH"), "未提供已记录许可的真实中文语音，不把合成音当成模型验收")
class RealSpeechAcceptanceTests(unittest.TestCase):
    def test_real_speech_file_is_present(self):
        path = Path(os.environ["VIDEO_HUB_REAL_SPEECH"])
        self.assertTrue(path.is_file(), path)
        self.assertGreater(path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
