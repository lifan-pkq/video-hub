import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import recorder


ROOT = Path(__file__).resolve().parents[2]


class RecorderSegmentTests(unittest.TestCase):
    def test_ffmpeg_records_ten_minute_segments(self):
        command = recorder._build_ffmpeg_cmd("https://example.test/live.flv", "direct", Path("part_%03d.mp4"))
        joined = " ".join(command)
        self.assertIn("-f segment", joined)
        self.assertIn("-segment_time 600", joined)
        self.assertIn("-segment_format mp4", joined)
        self.assertIn("-c copy", joined)

    def test_concat_command_stream_copies_without_reencode(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = []
            for index in (1, 2):
                path = root / f"part_{index}.mp4"
                path.write_bytes(b"part")
                paths.append(path)
            dst = root / "merged.mp4"

            def fake_run(command, **_kwargs):
                Path(command[-1]).write_bytes(b"merged")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            with patch("app.services.recorder.shutil.which", return_value="/usr/bin/ffmpeg"), \
                    patch("app.services.recorder.subprocess.run", side_effect=fake_run) as run:
                recorder._concat_segments(paths, dst)

            command = run.call_args.args[0]
            joined = " ".join(command)
            self.assertIn("-f concat", joined)
            self.assertIn("-c copy", joined)
            self.assertIn("-movflags +faststart", joined)
            for token in ("libx264", "-vf", "loudnorm", "unsharp"):
                self.assertNotIn(token, joined)
            self.assertFalse((root / ".merged.ffconcat.txt").exists())

    def test_segments_are_concatenated_into_one_library_video(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            videos = root / "videos"
            recordings.mkdir()
            videos.mkdir()
            paths = [recordings / f"live_{index:03d}.mp4" for index in (1, 2)]
            for path in paths:
                path.write_bytes(b"video")
            rec = {
                "id": 1, "title": "测试", "platform": "direct",
                "url": "https://example.test", "filename": "live_%03d.mp4",
            }
            updates = []

            def fake_probe(path, *_args, **_kwargs):
                if Path(path).name == "live.mp4":
                    return {"duration": 1200, "width": 1080, "height": 1920}
                return {"duration": 600, "width": 1080, "height": 1920}

            def fake_concat(sources, dst):
                self.assertEqual([item.name for item in sources], ["live_001.mp4", "live_002.mp4"])
                dst.write_bytes(b"merged")

            with patch("app.services.recorder.RECORDINGS_DIR", recordings), \
                    patch("app.services.recorder.VIDEOS_DIR", videos), \
                    patch("app.services.recorder.COVERS_DIR", root), \
                    patch("app.services.recorder._recording_paths", return_value=paths), \
                    patch("app.services.recorder._concat_segments", side_effect=fake_concat) as concat, \
                    patch("app.services.recorder._probe_with_retry", side_effect=fake_probe), \
                    patch("app.services.recorder.make_cover", return_value=True), \
                    patch("app.services.recorder.add_video", side_effect=lambda **fields: {"id": 11, **fields}) as add_video, \
                    patch("app.services.recorder.update_recording", side_effect=lambda *args, **kwargs: updates.append(kwargs)), \
                    patch("app.services.quality.update_quality", return_value={"score": 88}) as quality, \
                    patch("app.services.understanding.start_analysis") as analysis:
                recorder._finalized.clear()
                self.assertTrue(recorder._finish_success(1, rec))

            self.assertEqual(concat.call_count, 1)
            self.assertEqual(add_video.call_count, 1)
            self.assertEqual(add_video.call_args.kwargs["title"], "测试")
            self.assertNotIn("第", add_video.call_args.kwargs["title"])
            self.assertEqual(add_video.call_args.kwargs["filename"], "live.mp4")
            quality.assert_called_once_with(11, videos / "live.mp4")
            analysis.assert_called_once_with(11)
            self.assertEqual(updates[0]["video_id"], 11)
            self.assertEqual(updates[0]["duration"], 1200)
            self.assertEqual(updates[0]["filename"], "live.mp4")
            segments = json.loads(updates[0]["segments_json"])
            self.assertEqual([item["index"] for item in segments], [1, 2])
            self.assertTrue(all("video_id" not in item for item in segments))
            self.assertTrue((videos / "live.mp4").is_file())
            for path in paths:
                self.assertFalse(path.exists())

    def test_single_segment_is_ingested_without_concat(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            videos = root / "videos"
            recordings.mkdir()
            videos.mkdir()
            source = recordings / "live_000.mp4"
            source.write_bytes(b"video")
            rec = {
                "id": 2, "title": "短直播", "platform": "direct",
                "url": "https://example.test", "filename": "live_%03d.mp4",
            }
            updates = []

            def fake_remux(src, dst):
                dst.write_bytes(src.read_bytes())
                return True

            with patch("app.services.recorder.RECORDINGS_DIR", recordings), \
                    patch("app.services.recorder.VIDEOS_DIR", videos), \
                    patch("app.services.recorder.COVERS_DIR", root), \
                    patch("app.services.recorder._recording_paths", return_value=[source]), \
                    patch("app.services.recorder._concat_segments", side_effect=AssertionError("单段不应拼接")) as concat, \
                    patch("app.services.recorder._remux_to_faststart", side_effect=fake_remux), \
                    patch("app.services.recorder._probe_with_retry", return_value={"duration": 90, "width": 720, "height": 1280}), \
                    patch("app.services.recorder.make_cover", return_value=True), \
                    patch("app.services.recorder.add_video", side_effect=lambda **fields: {"id": 4, **fields}) as add_video, \
                    patch("app.services.recorder.update_recording", side_effect=lambda *args, **kwargs: updates.append(kwargs)), \
                    patch("app.services.quality.update_quality", return_value={"score": 70}) as quality, \
                    patch("app.services.understanding.start_analysis") as analysis:
                recorder._finalized.clear()
                self.assertTrue(recorder._finish_success(2, rec))

        concat.assert_not_called()
        self.assertEqual(add_video.call_count, 1)
        self.assertEqual(add_video.call_args.kwargs["title"], "短直播")
        quality.assert_called_once()
        analysis.assert_called_once_with(4)
        self.assertEqual(updates[0]["duration"], 90)
        self.assertEqual(updates[0]["filename"], "live_000.mp4")

    def test_short_concat_does_not_ingest_or_delete_segments(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            videos = root / "videos"
            recordings.mkdir()
            videos.mkdir()
            paths = [recordings / f"live_{index:03d}.mp4" for index in (1, 2)]
            for path in paths:
                path.write_bytes(b"video")
            rec = {
                "id": 3, "title": "测试", "platform": "direct",
                "url": "https://example.test", "filename": "live_%03d.mp4",
            }
            updates = []

            def fake_probe(path, *_args, **_kwargs):
                if Path(path).name == "live.mp4":
                    return {"duration": 10, "width": 1080, "height": 1920}
                return {"duration": 600, "width": 1080, "height": 1920}

            with patch("app.services.recorder.RECORDINGS_DIR", recordings), \
                    patch("app.services.recorder.VIDEOS_DIR", videos), \
                    patch("app.services.recorder.COVERS_DIR", root), \
                    patch("app.services.recorder._recording_paths", return_value=paths), \
                    patch("app.services.recorder._concat_segments", side_effect=lambda sources, dst: dst.write_bytes(b"short")), \
                    patch("app.services.recorder._probe_with_retry", side_effect=fake_probe), \
                    patch("app.services.recorder.add_video") as add_video, \
                    patch("app.services.recorder.update_recording", side_effect=lambda *args, **kwargs: updates.append(kwargs)), \
                    patch("app.services.understanding.start_analysis") as analysis:
                recorder._finalized.clear()
                with self.assertRaisesRegex(RuntimeError, "时长异常"):
                    recorder._finish_success(3, rec)

            add_video.assert_not_called()
            analysis.assert_not_called()
            self.assertEqual(updates[-1]["status"], "failed")
            self.assertFalse((videos / "live.mp4").exists())
            for path in paths:
                self.assertTrue(path.exists())

    def test_concat_copy_joins_segment_muxer_files(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg required")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error",
                 "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
                 "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
                 "-t", "6", "-c:v", "libx264", "-g", "25", "-c:a", "aac", "-shortest", str(source)],
                check=True, capture_output=True, timeout=30,
            )
            pattern = root / "part_%03d.mp4"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-c", "copy",
                 "-f", "segment", "-segment_time", "2", "-segment_format", "mp4",
                 "-segment_format_options", "movflags=+faststart", str(pattern)],
                check=True, capture_output=True, timeout=30,
            )
            parts = sorted(root.glob("part_*.mp4"))
            self.assertGreaterEqual(len(parts), 2)
            merged = root / "merged.mp4"
            recorder._concat_segments(parts, merged)
            source_duration = float(recorder.probe_video(source).get("duration") or 0)
            merged_duration = float(recorder.probe_video(merged).get("duration") or 0)

        self.assertGreater(source_duration, 5)
        self.assertAlmostEqual(merged_duration, source_duration, delta=0.5)

    def test_record_page_promises_one_library_item(self):
        page = (ROOT / "frontend/src/pages/RecordPage.tsx").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for phrase in (
            "停止后每段都会入库",
            "停止后每个已写出的分段都会入库",
            "停止后会自动分段入库",
            "停止后自动分段入库",
            "每 10 分钟一段，停止后自动入库",
            "不删除已经入库的视频分段",
        ):
            self.assertNotIn(phrase, page)
        self.assertIn("合并成一条", page)
        self.assertIn("已由 ${segments.length} 段合并为一条入库", page)
        self.assertNotIn("独立入库", readme)
        self.assertIn("合并成一条入库", readme)


if __name__ == "__main__":
    unittest.main()
