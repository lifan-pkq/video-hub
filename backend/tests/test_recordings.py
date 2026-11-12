import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routers import recordings


class RecordingDeletionTests(unittest.TestCase):
    def test_delete_file_also_deletes_linked_video_cover(self):
        with tempfile.TemporaryDirectory() as root:
            covers_dir = Path(root) / "covers"
            covers_dir.mkdir()
            cover = covers_dir / "cover.jpg"
            cover.write_bytes(b"cover")
            rec = {"id": 3, "status": "completed", "video_id": 9}
            video = {"id": 9, "filename": "video.mp4", "cover": cover.name}
            with patch("app.routers.recordings.COVERS_DIR", covers_dir, create=True), \
                    patch("app.routers.recordings.get_recording", return_value=rec), \
                    patch("app.routers.recordings.recorder.recording_file_path", return_value=None), \
                    patch("app.db.get_video", return_value=video), \
                    patch("app.db.delete_video") as delete_video, \
                    patch("app.routers.recordings.delete_recording"):
                result = recordings.remove(3, delete_file=True)

            self.assertTrue(result["ok"])
            self.assertFalse(cover.exists())
            delete_video.assert_called_once_with(9)

    def test_active_recording_size_sums_segments(self):
        rec = {
            "id": 1, "status": "recording", "filename": "live_%03d.mp4",
            "segments_json": "[]", "started_at": None,
        }
        with patch("app.routers.recordings.recorder._file_size", return_value=300) as size:
            enriched = recordings._enrich(dict(rec))
        size.assert_called_once_with("live_%03d.mp4")
        self.assertEqual(enriched["size"], 300)
        self.assertTrue(enriched["starting"])

    def test_delete_file_after_stop_removes_merged_video(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            videos = root / "videos"
            recordings_dir = root / "recordings"
            videos.mkdir()
            recordings_dir.mkdir()
            merged = videos / "live.mp4"
            merged.write_bytes(b"merged")
            first = {"id": 3, "status": "recording", "filename": "live_%03d.mp4", "video_id": None}
            finished = {"id": 3, "status": "stopped", "filename": "live.mp4", "video_id": 9}
            video = {"id": 9, "filename": "live.mp4", "cover": None}
            with patch("app.services.recorder.VIDEOS_DIR", videos), \
                    patch("app.services.recorder.RECORDINGS_DIR", recordings_dir), \
                    patch("app.routers.recordings.get_recording", side_effect=[first, finished]), \
                    patch("app.routers.recordings.recorder.stop_recording") as stop, \
                    patch("app.db.get_video", return_value=video), \
                    patch("app.db.delete_video") as delete_video, \
                    patch("app.routers.recordings.delete_recording"):
                result = recordings.remove(3, delete_file=True)

            self.assertTrue(result["ok"])
            stop.assert_called_once_with(3)
            self.assertFalse(merged.exists())
            delete_video.assert_called_once_with(9)


if __name__ == "__main__":
    unittest.main()
