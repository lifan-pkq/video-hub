"""文字与轻效果：默认关闭、字幕校验、真实烧录和原声。"""

import os
import shutil
import subprocess
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from app.services.text_enhance import (
    TextEnhanceError,
    assert_font_covers,
    build_document,
    font_covers,
    overlapped_duration,
    prepare_cues,
    wrap_lines,
)
from engine.errors import EngineError

KNOWN = "如果没有备注，就不能这样判断；这是自动换行的较长中文示例句，不代表任何真人发言。"
FORBIDDEN = set("，。、；：！？,.;:!?」』）】》")


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True, timeout=60)


def _rgb_frame(path: Path, second: float) -> tuple[int, int, bytes]:
    proc = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{second:.3f}", "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        check=True, capture_output=True, timeout=30,
    )
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
        ],
        check=True, capture_output=True, text=True, timeout=30,
    )
    width, height = (int(part) for part in probe.stdout.strip().split(","))
    return width, height, proc.stdout


def _count_yellow(frame: bytes, width: int, height: int, y0: int, y1: int) -> int:
    count = 0
    for y in range(y0, y1):
        row = y * width * 3
        for x in range(width):
            index = row + x * 3
            red, green, blue = frame[index], frame[index + 1], frame[index + 2]
            if red > 180 and green > 160 and blue < 80:
                count += 1
    return count


def _count_white(frame: bytes, width: int, height: int, y0: int, y1: int) -> int:
    count = 0
    for y in range(max(0, y0), min(height, y1)):
        row = y * width * 3
        for x in range(width):
            index = row + x * 3
            red, green, blue = frame[index], frame[index + 1], frame[index + 2]
            if red > 220 and green > 220 and blue > 220:
                count += 1
    return count


class TextRulesTests(unittest.TestCase):
    def test_wrap_keeps_characters_and_avoids_line_start_punctuation(self):
        for width in range(8, 18):
            lines = wrap_lines(KNOWN, width)
            self.assertEqual("".join(lines), KNOWN)
            for line in lines:
                self.assertNotIn(line[0], FORBIDDEN)

    def test_overlap_duration_shrinks_and_fade_contract_does_not(self):
        self.assertEqual(overlapped_duration(2, 2, "0.4"), Decimal("3.6"))
        with self.assertRaises(TextEnhanceError):
            overlapped_duration(2, 2, 2)

    def test_bad_cues_are_rejected(self):
        for cue in (
            {"start": "nan", "end": 1, "text": "甲"},
            {"start": -1, "end": 1, "text": "甲"},
            {"start": 2, "end": 1, "text": "甲"},
            {"start": 0, "end": 9, "text": "甲"},
        ):
            with self.subTest(cue=cue):
                with self.assertRaises(TextEnhanceError) as caught:
                    prepare_cues([cue], 4)
                self.assertEqual(caught.exception.code, "INVALID_INTERVAL")
        with self.assertRaises(TextEnhanceError) as overlap:
            prepare_cues(
                [{"start": 0, "end": 2, "text": "不是这样"}, {"start": 1, "end": 3, "text": "所以停下"}],
                4,
            )
        self.assertEqual(overlap.exception.code, "OVERLAPPING_CUES")
        with self.assertRaises(TextEnhanceError):
            prepare_cues([{"start": 0, "end": 1, "text": "！！！"}], 4)

    def test_ass_escapes_overrides_and_keeps_quote_separate_from_title(self):
        script = build_document(
            [{"start": 0.2, "end": 1.2, "text": "他说{不是}这样"}],
            "旁注：不是原话",
            width=720,
            height=1280,
            font_name="WenQuanYi Micro Hei",
            style="yellow",
            emphasis="pop",
        )
        self.assertIn("PlayResX: 720", script)
        self.assertIn("PlayResY: 1280", script)
        self.assertIn("WrapStyle: 2", script)
        self.assertIn("Style: Caption", script)
        self.assertIn("Style: Title", script)
        self.assertIn("｛不是｝", script)
        self.assertNotIn("{不是}", script)
        self.assertIn(r"\fscx80", script)
        self.assertLess(script.index("Title,,"), script.index("Caption,,"))

    def test_font_preflight_rejects_missing_file_and_missing_glyphs(self):
        with self.assertRaises(TextEnhanceError) as missing:
            assert_font_covers(Path("/tmp/does-not-exist-font.ttf"), "字幕")
        self.assertEqual(missing.exception.code, "FONT_FILE_NOT_FOUND")
        font = Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")
        if not font.is_file():
            self.skipTest("wqy font missing")
        self.assertEqual(font_covers(font, "口播字幕"), [])
        self.assertIn("\ue000", font_covers(font, "甲\ue000"))


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required")
class TextRenderTests(unittest.TestCase):
    def _source(self, root: Path) -> Path:
        source = root / "source.mp4"
        _ffmpeg(
            "-f", "lavfi", "-i", "color=c=0x202020:s=640x360:r=25:d=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(source),
        )
        return source

    def test_enabled_burns_chinese_title_and_caption_disabled_does_not(self):
        from app.services.douyin_render import render_short

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            plain = root / "plain.mp4"
            styled = root / "styled.mp4"
            common = dict(
                start=0.2, end=2.4, src_w=640, src_h=360, has_audio=True, track=[],
                out_w=360, out_h=640, threads=1, layout="podcast",
            )
            off = render_short(source, plain, captions=[], **common)
            self.assertNotIn("text_effects", off)
            on = render_short(
                source, styled,
                captions=[{"start": 0.3, "end": 1.6, "text": "不是原话，要核对。"}],
                enhance={
                    "enabled": True, "captions": True, "title_text": "旁注说明",
                    "style": "yellow", "fade": True, "emphasis": "fade",
                },
                **common,
            )
            self.assertIn("captions", on["text_effects"])
            self.assertIn("title", on["text_effects"])
            self.assertIn("fade", on["text_effects"])
            self.assertTrue(on["captions_burned"])
            self.assertEqual(on["quote_cues"][0]["role"], "quote")
            self.assertIn("不是", on["quote_cues"][0]["text"])
            width, height, frame = _rgb_frame(styled, 0.8)
            self.assertEqual((width, height), (360, 640))
            self.assertGreater(_count_yellow(frame, width, height, int(height * 0.55), height), 30)
            self.assertGreater(_count_white(frame, width, height, 0, int(height * 0.28)), 20)
            _, _, plain_frame = _rgb_frame(plain, 0.8)
            self.assertLess(_count_white(plain_frame, width, height, 0, int(height * 0.28)), 5)
            styled_probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(styled)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            plain_probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(plain)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            styled_duration = float(styled_probe.stdout.strip())
            plain_duration = float(plain_probe.stdout.strip())
            self.assertAlmostEqual(styled_duration, plain_duration, delta=0.15)
            self.assertAlmostEqual(styled_duration, 2.2, delta=0.25)
            audio = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(styled)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            self.assertIn("audio", audio.stdout)
            ass = (styled.parent / "captions.ass").read_text(encoding="utf-8")
            self.assertNotIn("{不是}", ass)
            frame_path = Path("/tmp/text-enhance-frame.png")
            _ffmpeg("-ss", "0.8", "-i", str(styled), "-frames:v", "1", str(frame_path))
            self.assertGreater(frame_path.stat().st_size, 1000)

    def test_injection_and_overlap_do_not_reach_ffmpeg_graph(self):
        from app.services.douyin_render import render_short

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self._source(root)
            dest = root / "safe.mp4"
            render_short(
                source, dest,
                start=0.1, end=1.5, src_w=640, src_h=360, has_audio=True, track=[],
                captions=[{"start": 0.2, "end": 1.0, "text": "';[vout]fade=t=in"}],
                out_w=360, out_h=640, threads=1, layout="podcast",
                enhance={
                    "enabled": True, "captions": True, "title_text": "{ignore}\\Npos",
                    "style": "outline", "fade": False, "emphasis": "none",
                },
            )
            ass = (dest.parent / "captions.ass").read_text(encoding="utf-8")
            self.assertNotIn("{ignore}", ass)
            self.assertIn("｛ignore｝", ass)
            with self.assertRaises(EngineError) as caught:
                render_short(
                    source, root / "bad.mp4",
                    start=0.1, end=1.5, src_w=640, src_h=360, has_audio=True, track=[],
                    captions=[
                        {"start": 0.2, "end": 1.0, "text": "前一句"},
                        {"start": 0.4, "end": 1.2, "text": "叠在一起"},
                    ],
                    out_w=360, out_h=640, threads=1,
                    enhance={"enabled": True, "captions": True, "title_text": "", "style": "outline", "fade": False, "emphasis": "none"},
                )
            self.assertEqual(caught.exception.code, "OVERLAPPING_CUES")

    def test_overlap_transition_duration_on_real_media(self):
        """产品导出不做重叠转场。这里只核对实验里的时长：2+2-0.4=3.6。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            left = root / "left.mp4"
            right = root / "right.mp4"
            out = root / "joined.mp4"
            _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(left))
            _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=320x180:r=25:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(right))
            _ffmpeg(
                "-i", str(left), "-i", str(right),
                "-filter_complex", "xfade=transition=fade:duration=0.4:offset=1.6",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
            )
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(out)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            self.assertAlmostEqual(float(probe.stdout.strip()), 3.6, delta=0.08)


class TextEnhanceRouteTests(unittest.TestCase):
    def setUp(self):
        self._inline = os.environ.get("VIDEO_HUB_JOBS_INLINE")
        self._data = os.environ.get("VIDEO_HUB_DATA_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VIDEO_HUB_JOBS_INLINE"] = "1"
        os.environ["VIDEO_HUB_DATA_DIR"] = self.tmp.name
        from fastapi.testclient import TestClient
        from app.main import app
        self.client = TestClient(app)

    def tearDown(self):
        if self._inline is None:
            os.environ.pop("VIDEO_HUB_JOBS_INLINE", None)
        else:
            os.environ["VIDEO_HUB_JOBS_INLINE"] = self._inline
        if self._data is None:
            os.environ.pop("VIDEO_HUB_DATA_DIR", None)
        else:
            os.environ["VIDEO_HUB_DATA_DIR"] = self._data
        self.tmp.cleanup()

    def test_default_off_and_retry_keeps_the_choice(self):
        from app.jobs import update
        from unittest.mock import patch

        seen = []

        def run(job_id, video_id, instruction, max_clips, layout="interview", **extra):
            seen.append(extra.get("text_enhance"))
            update(job_id, status="failed", stage="failed", label="失败", error={"message": "失败"})

        with patch("app.routers.edit.fetch", return_value={"id": 1, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=run):
            off = self.client.post("/api/edit/douyin", json={"video_id": 1})
            on = self.client.post("/api/edit/douyin", json={
                "video_id": 1,
                "text_enhance": {
                    "enabled": True,
                    "captions": True,
                    "title_text": "旁注",
                    "style": "yellow",
                    "fade": True,
                    "emphasis": "pop",
                },
            })
        self.assertEqual(off.status_code, 200, off.text)
        self.assertFalse(off.json()["input"]["text_enhance"]["enabled"])
        self.assertFalse(seen[0]["enabled"])
        self.assertEqual(on.status_code, 200, on.text)
        self.assertTrue(on.json()["input"]["text_enhance"]["enabled"])
        self.assertEqual(on.json()["input"]["text_enhance"]["title_text"], "旁注")
        self.assertTrue(seen[1]["enabled"])
        failed = on.json()
        with patch("app.routers.edit.fetch", return_value={"id": 1, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=run):
            retried = self.client.post(f"/api/edit/douyin/{failed['id']}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertTrue(retried.json()["input"]["text_enhance"]["fade"])
        self.assertEqual(retried.json()["input"]["text_enhance"]["style"], "yellow")


if __name__ == "__main__":
    unittest.main()
