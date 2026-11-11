"""带人声的短片：真实转写、无 Key 时的口播选段、竖屏渲染、封面 ZIP。

默认不进 unittest discover 的日常集合：设置 VIDEO_HUB_RUN_E2E=1 才跑。
需要 ffmpeg、espeak-ng，以及可下载的 faster-whisper tiny。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path


def _speech(text: str, wav: Path) -> None:
    subprocess.run(
        ["espeak-ng", "-v", "en", "-s", "130", "-w", str(wav), text],
        check=True, capture_output=True, timeout=60,
    )


def _clip(wav: Path, mp4: Path) -> float:
    with wave.open(str(wav), "rb") as handle:
        seconds = handle.getnframes() / float(handle.getframerate() or 1)
    length = max(8.0, seconds + 0.4)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c=0x224466:s=640x360:d={length:.2f}",
            "-i", str(wav),
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(mp4),
        ],
        check=True, capture_output=True, timeout=120,
    )
    return length


@unittest.skipUnless(os.environ.get("VIDEO_HUB_RUN_E2E") == "1", "设置 VIDEO_HUB_RUN_E2E=1 才跑真实转写")
class SpokenClipPipelineTests(unittest.TestCase):
    def test_transcribe_plan_render_and_zip(self):
        if not shutil.which("ffmpeg") or not shutil.which("espeak-ng") or not shutil.which("ffprobe"):
            self.skipTest("需要 ffmpeg、ffprobe 和 espeak-ng")
        previous_model = os.environ.get("VIDEO_HUB_WHISPER_MODEL")
        os.environ["VIDEO_HUB_WHISPER_MODEL"] = "tiny"
        self.addCleanup(lambda: os.environ.__setitem__("VIDEO_HUB_WHISPER_MODEL", previous_model) if previous_model is not None else os.environ.pop("VIDEO_HUB_WHISPER_MODEL", None))
        os.environ.setdefault("VIDEO_HUB_DOUYIN_WIDTH", "360")
        os.environ.setdefault("VIDEO_HUB_DOUYIN_HEIGHT", "640")
        os.environ.setdefault("VIDEO_HUB_DOUYIN_MIN_SECONDS", "6")

        from app.services.ai_edit import transcribe
        from app.services.douyin_plan import plan_douyin_shorts
        from app.services.douyin_render import probe_source, render_short, write_cover
        from app.services.export_bundle import export_bundle
        from app.services.media import probe_video

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            wav = root / "speech.wav"
            source = root / "talk.mp4"
            _speech(
                "This is a short talk about making a vertical video. "
                "The first idea is to keep the sentence clear. "
                "The second idea is to stop when the point is made.",
                wav,
            )
            _clip(wav, source)
            meta = probe_video(source)
            self.assertGreater(float(meta.get("duration") or 0), 5)

            segments = transcribe(source, log=lambda message: None)
            self.assertGreaterEqual(len(segments), 1, segments)
            self.assertTrue(any(str(item.get("text") or "").strip() for item in segments))

            from app.services.ai_edit import AIServiceError

            def no_key(prompt, log):
                raise AIServiceError("没有配置 AI API Key")

            plan = plan_douyin_shorts(segments, float(meta["duration"]), request_ai=no_key)
            self.assertEqual(plan["source"], "heuristic")
            clip = plan["clips"][0]
            self.assertGreater(float(clip["end"]) - float(clip["start"]), 3)

            probed = probe_source(source)
            out = root / "short.mp4"
            rendered = render_short(
                source,
                out,
                start=float(clip["start"]),
                end=float(clip["end"]),
                src_w=int(probed["width"]),
                src_h=int(probed["height"]),
                has_audio=True,
                track=[],
                captions=list(clip.get("captions") or []),
                out_w=360,
                out_h=640,
                threads=1,
                layout="podcast",
            )
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 1000)
            self.assertIn(rendered["framing"], {"center", "subject", "podcast", "fill"})
            spoken = probe_video(out)
            self.assertGreater(float(spoken.get("duration") or 0), 1)
            self.assertTrue(write_cover(out, root / "cover.jpg"))

            manifest = export_bundle(out, root / "pack", str(clip.get("title") or "测试成片"))
            archive = root / "pack" / manifest["archive"]
            self.assertTrue(archive.is_file())
            self.assertGreater(archive.stat().st_size, 1000)
            self.assertTrue((root / "pack" / "portrait.mp4").is_file())
            self.assertTrue((root / "pack" / "cover-1.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
