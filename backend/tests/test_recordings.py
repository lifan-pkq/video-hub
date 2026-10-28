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


if __name__ == "__main__":
    unittest.main()
