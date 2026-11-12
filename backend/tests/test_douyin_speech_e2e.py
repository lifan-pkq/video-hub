"""中文口播短片：真实转写、字幕、无 Key 选段、竖屏和带汉字的封面。

语音来自本机 edge-tts（zh-CN-XiaoxiaoNeural）预先合成的 tests/fixtures/zh_talk.mp3。
espeak-ng 的中文声线 Whisper 听不懂，所以不用它当这条测试的音源。
设置 VIDEO_HUB_RUN_E2E=1 才跑。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "zh_talk.mp3"
SCRIPT = "今天我们讲一件小事。开头先把结论说清楚。然后补上原因。最后停在这里。"


def _clip(audio: Path, mp4: Path) -> None:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(audio)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    length = max(8.0, float(probe.stdout.strip()) + 0.3)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c=0x224466:s=640x360:d={length:.2f}",
            "-i", str(audio),
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(mp4),
        ],
        check=True, capture_output=True, timeout=120,
    )


@unittest.skipUnless(os.environ.get("VIDEO_HUB_RUN_E2E") == "1", "设置 VIDEO_HUB_RUN_E2E=1 才跑中文转写")
class ChineseSpeechPipelineTests(unittest.TestCase):
    def test_chinese_transcript_subtitles_and_cover(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("需要 ffmpeg 和 ffprobe")
        self.assertTrue(FIXTURE.is_file(), FIXTURE)
        previous = os.environ.get("VIDEO_HUB_WHISPER_MODEL")
        os.environ["VIDEO_HUB_WHISPER_MODEL"] = "tiny"
        self.addCleanup(
            lambda: os.environ.__setitem__("VIDEO_HUB_WHISPER_MODEL", previous)
            if previous is not None else os.environ.pop("VIDEO_HUB_WHISPER_MODEL", None)
        )

        from app.services.ai_edit import AIServiceError, transcribe
        from app.services.douyin_plan import plan_douyin_shorts
        from app.services.douyin_render import probe_source, render_short
        from app.services.export_bundle import export_bundle
        from app.services.media import probe_video

        def no_key(prompt, log):
            raise AIServiceError("没有配置 AI API Key")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "talk.mp4"
            _clip(FIXTURE, source)
            meta = probe_video(source)
            segments = transcribe(source, log=lambda message: None)
            heard = "".join(str(item.get("text") or "") for item in segments)
            for piece in ("今天", "结论", "原因", "最后"):
                self.assertIn(piece, heard, heard)

            plan = plan_douyin_shorts(segments, float(meta["duration"]), request_ai=no_key)
            self.assertEqual(plan["source"], "heuristic")
            clip = plan["clips"][0]
            self.assertLess(float(clip["start"]), 2.5)
            self.assertGreater(float(clip["end"]), 6)
            captions = " ".join(str(item.get("text") or "") for item in clip.get("captions") or [])
            self.assertIn("结论", captions)

            probed = probe_source(source)
            out = root / "short.mp4"
            rendered = render_short(
                source, out,
                start=float(clip["start"]), end=float(clip["end"]),
                src_w=int(probed["width"]), src_h=int(probed["height"]),
                has_audio=True, track=[], captions=list(clip.get("captions") or []),
                out_w=360, out_h=640, threads=1, layout="podcast",
            )
            self.assertEqual(rendered["framing"], "podcast")
            self.assertGreater(out.stat().st_size, 1000)
            ass = (root / "captions.ass").read_text(encoding="utf-8")
            self.assertIn("结论", ass)

            title = "今天的结论"
            manifest = export_bundle(out, root / "pack", title)
            self.assertTrue((root / "pack" / "landscape.mp4").is_file())
            self.assertTrue((root / "pack" / "portrait.mp4").is_file())
            self.assertTrue((root / "pack" / "square.mp4").is_file())
            self.assertIn("结论", manifest["selected_title"])
            from PIL import Image
            cover = Image.open(root / "pack" / manifest["selected_cover"]).convert("RGB")
            band = cover.crop((60, 40, 900, 220))
            colors = band.getcolors(maxcolors=200000) or []
            self.assertGreater(len(colors), 8, "封面标题区应该画上汉字，而不是一整块纯色")


if __name__ == "__main__":
    unittest.main()
