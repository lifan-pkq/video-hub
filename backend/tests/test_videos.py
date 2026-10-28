import asyncio
import io
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.routers import videos


class VideoUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.videos_dir = root / "videos"
        self.covers_dir = root / "covers"
        self.tmp_dir = root / "tmp"
        for directory in (self.videos_dir, self.covers_dir, self.tmp_dir):
            directory.mkdir()
        for name, value in (
            ("VIDEOS_DIR", self.videos_dir),
            ("COVERS_DIR", self.covers_dir),
            ("TMP_DIR", self.tmp_dir),
        ):
            item = patch(f"app.routers.videos.{name}", value, create=True)
            item.start()
            self.addCleanup(item.stop)
        item = patch("app.routers.videos.ensure_dirs")
        item.start()
        self.addCleanup(item.stop)

        self.probe = patch(
            "app.routers.videos.probe_video",
            return_value={"duration": 2.0, "width": 640, "height": 360, "codec": "h264"},
        ).start()
        self.addCleanup(self.probe.stop)
        self.ensure_h264 = patch(
            "app.routers.videos.ensure_h264", return_value=False, create=True).start()
        self.addCleanup(self.ensure_h264.stop)
        self.make_cover = patch("app.routers.videos.make_cover", return_value=False).start()
        self.addCleanup(self.make_cover.stop)
        self.add_video = patch(
            "app.routers.videos.add_video",
            side_effect=lambda **kwargs: {"id": 1, **kwargs},
        ).start()
        self.addCleanup(self.add_video.stop)

    @staticmethod
    def upload(content=b"video", filename="source.mp4"):
        return UploadFile(io.BytesIO(content), filename=filename)

    async def test_upload_rejects_file_without_video_stream_and_cleans_temp(self):
        self.probe.return_value = {"duration": 2.0, "has_audio": True}

        with self.assertRaises(HTTPException) as caught:
            await videos.upload_video(self.upload())

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(list(self.videos_dir.iterdir()), [])
        self.assertEqual(list(self.tmp_dir.iterdir()), [])
        self.add_video.assert_not_called()

    async def test_upload_uses_uuid_filename_and_runs_media_work_in_threads(self):
        expected_uuid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        with patch("uuid.uuid4", return_value=expected_uuid), \
                patch("asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
            result = await videos.upload_video(self.upload(), title="示例")

        self.assertEqual(result["filename"], f"{expected_uuid.hex}.mp4")
        self.assertTrue((self.videos_dir / result["filename"]).exists())
        self.assertEqual(list(self.tmp_dir.iterdir()), [])
        threaded_functions = [call.args[0] for call in to_thread.call_args_list]
        self.assertIn(self.probe, threaded_functions)
        self.assertIn(self.make_cover, threaded_functions)

    async def test_non_h264_upload_is_transcoded_in_thread(self):
        self.probe.return_value = {"duration": 2.0, "width": 640, "height": 360, "codec": "hevc"}
        self.ensure_h264.return_value = True

        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as to_thread:
            await videos.upload_video(self.upload())

        self.ensure_h264.assert_called_once()
        threaded_functions = [call.args[0] for call in to_thread.call_args_list]
        self.assertIn(self.ensure_h264, threaded_functions)

    async def test_processing_failure_removes_partial_upload(self):
        self.probe.return_value = {"duration": 2.0, "width": 640, "height": 360, "codec": "hevc"}
        self.ensure_h264.side_effect = RuntimeError("transcode failed")

        with self.assertRaises(RuntimeError):
            await videos.upload_video(self.upload())

        self.assertEqual(list(self.videos_dir.iterdir()), [])
        self.assertEqual(list(self.tmp_dir.iterdir()), [])
        self.add_video.assert_not_called()


if __name__ == "__main__":
    unittest.main()
