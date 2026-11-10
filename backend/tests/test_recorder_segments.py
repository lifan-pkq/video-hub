import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import recorder


class RecorderSegmentTests(unittest.TestCase):
    def test_ffmpeg_records_ten_minute_segments(self):
        command = recorder._build_ffmpeg_cmd("https://example.test/live.flv", "direct", Path("part_%03d.mp4"))
        joined = " ".join(command)
        self.assertIn("-f segment", joined)
        self.assertIn("-segment_time 600", joined)
        self.assertIn("-segment_format mp4", joined)
        self.assertIn("-c copy", joined)

    def test_each_segment_is_ingested_and_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = root / "recordings"
            videos = root / "videos"
            recordings.mkdir()
            videos.mkdir()
            paths = [recordings / f"live_{index:03d}.mp4" for index in (1, 2)]
            for path in paths:
                path.write_bytes(b"video")
            rec = {"id": 1, "title": "测试", "platform": "direct", "url": "https://example.test", "filename": "live_%03d.mp4"}
            updates = []
            with patch("app.services.recorder.RECORDINGS_DIR", recordings), patch("app.services.recorder.VIDEOS_DIR", videos), patch("app.services.recorder.COVERS_DIR", root), \
                    patch("app.services.recorder._recording_paths", return_value=paths), \
                    patch("app.services.recorder._remux_to_faststart", side_effect=lambda src, dst: dst.write_bytes(b"video") or True), \
                    patch("app.services.recorder._probe_with_retry", return_value={"duration": 600, "width": 1080, "height": 1920}), \
                    patch("app.services.recorder.make_cover", return_value=True), \
                    patch("app.services.recorder.add_video", side_effect=lambda **fields: {"id": fields["filename"], **fields}) as add_video, \
                    patch("app.services.recorder.update_recording", side_effect=lambda *args, **kwargs: updates.append(kwargs)), \
                    patch("app.services.quality.update_quality", return_value={"score": 88}), \
                    patch("app.services.understanding.start_analysis"):
                recorder._finalized.clear()
                self.assertTrue(recorder._finish_success(1, rec))
            self.assertEqual(add_video.call_count, 2)
            segments = json.loads(updates[0]["segments_json"])
            self.assertEqual(len(segments), 2)
            self.assertEqual(updates[0]["duration"], 1200)


if __name__ == "__main__":
    unittest.main()
