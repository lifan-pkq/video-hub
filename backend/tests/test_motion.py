"""视频转动图：GIF / WebP、参数、非法 id、取消。"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from fastapi.testclient import TestClient

from engine.errors import EngineError
from app.jobs import create, get, update
from app.main import app
from app.services import motion


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=60)


class MotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.TemporaryDirectory()
        cls.clip = Path(cls._media.name) / "clip.mp4"
        if shutil.which("ffmpeg") and shutil.which("ffprobe"):
            _ffmpeg(
                "-f", "lavfi", "-i", "testsrc=size=320x180:rate=10:duration=0.8",
                "-pix_fmt", "yuv420p", str(cls.clip),
            )
        else:
            cls.clip = None

    @classmethod
    def tearDownClass(cls):
        cls._media.cleanup()

    def setUp(self):
        self._data = os.environ.get("VIDEO_HUB_DATA_DIR")
        self._inline = os.environ.get("VIDEO_HUB_JOBS_INLINE")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VIDEO_HUB_DATA_DIR"] = self.tmp.name
        os.environ["VIDEO_HUB_JOBS_INLINE"] = "1"
        self.client = TestClient(app)

    def tearDown(self):
        if self._data is None:
            os.environ.pop("VIDEO_HUB_DATA_DIR", None)
        else:
            os.environ["VIDEO_HUB_DATA_DIR"] = self._data
        if self._inline is None:
            os.environ.pop("VIDEO_HUB_JOBS_INLINE", None)
        else:
            os.environ["VIDEO_HUB_JOBS_INLINE"] = self._inline
        self.tmp.cleanup()

    def _require_ffmpeg(self):
        if self.clip is None or not self.clip.is_file():
            self.skipTest("需要 ffmpeg 和 ffprobe")

    def _assert_animated(self, path: Path, fmt: str, width: int):
        from PIL import Image

        self.assertGreater(path.stat().st_size, 0)
        with Image.open(path) as image:
            self.assertEqual(image.format, fmt)
            self.assertGreaterEqual(getattr(image, "n_frames", 1), 2)
            self.assertEqual(image.size[0], width)
            self.assertGreater(image.size[1], 0)

    def test_renders_gif_and_webp(self):
        self._require_ffmpeg()
        spec = {"start": 0, "duration": 0.5, "width": 160, "fps": 8, "colors": 32, "quality": 70}
        gif = motion.start({**spec, "format": "gif", "dither": True}, source=self.clip, source_title="样片")
        self.assertEqual(gif["status"], "success")
        self.assertEqual(gif["type"], "motion")
        self.assertEqual(gif["kind"], "motion")
        gif_path = Path(gif["result"]["output_path"])
        self.assertTrue(gif_path.read_bytes().startswith(b"GIF"))
        self._assert_animated(gif_path, "GIF", 160)
        self.assertFalse((gif_path.parent / "palette.png").exists())

        webp = motion.start({**spec, "format": "webp"}, source=self.clip, source_title="样片")
        self.assertEqual(webp["status"], "success", webp.get("error"))
        webp_path = Path(webp["result"]["output_path"])
        raw = webp_path.read_bytes()
        self.assertTrue(raw.startswith(b"RIFF") and b"WEBP" in raw[:16])
        self._assert_animated(webp_path, "WEBP", 160)
        self.assertLess(webp_path.stat().st_size, gif_path.stat().st_size)

        listed = self.client.get("/api/tasks").json()
        ids = {item["id"]: item["type"] for item in listed}
        self.assertEqual(ids.get(gif["id"]), "motion")
        self.assertEqual(ids.get(webp["id"]), "motion")

    def test_upload_download_retry_and_ingest(self):
        self._require_ffmpeg()
        with self.clip.open("rb") as handle:
            response = self.client.post(
                "/api/motion/upload",
                files={"file": ("../../etc/passwd.mp4", handle, "video/mp4")},
                data={
                    "start": "0",
                    "duration": "0.5",
                    "width": "160",
                    "fps": "8",
                    "format": "gif",
                    "loop": "0",
                    "colors": "32",
                    "dither": "false",
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "success")
        self.assertEqual(body["input"]["upload_name"], "source.mp4")
        output = Path(body["result"]["output_path"]).resolve()
        root = Path(self.tmp.name).resolve()
        self.assertTrue(str(output).startswith(str(root)))
        self.assertEqual(output.name, "motion.gif")
        download = self.client.get(f"/api/motion/{body['id']}/file")
        self.assertEqual(download.status_code, 200)
        self.assertTrue(download.headers["content-type"].startswith("image/gif"))
        self.assertTrue(download.content.startswith(b"GIF"))

        images = Path(self.tmp.name) / "images"

        def fake_add(**fields):
            return {"id": 4, "created_at": "now", **fields}

        with patch("app.services.ai_images.AI_IMAGES_DIR", images), \
                patch("app.services.ai_images.add_ai_image", side_effect=fake_add):
            saved = self.client.post(f"/api/motion/{body['id']}/ingest")
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["id"], 4)
        self.assertTrue((images / saved.json()["filename"]).is_file())
        self.assertTrue(saved.json()["filename"].endswith(".gif"))

        again = self.client.post(f"/api/tasks/{body['id']}/retry")
        self.assertEqual(again.status_code, 200, again.text)
        retried = again.json()["task"]
        self.assertNotEqual(retried["id"], body["id"])
        self.assertEqual(retried["status"], "success")
        self.assertEqual(retried["kind"], "motion")
        self.assertTrue(Path(retried["result"]["output_path"]).read_bytes().startswith(b"GIF"))

    def test_cancel_stops_the_running_job(self):
        self._require_ffmpeg()
        os.environ["VIDEO_HUB_JOBS_INLINE"] = "0"
        started = threading.Event()

        def hang(*_args, **kwargs):
            started.set()
            should = kwargs.get("should_cancel")
            deadline = time.time() + 5
            while time.time() < deadline:
                if should and should():
                    raise EngineError("任务已取消", code="cancelled", action="可以重新生成。", status=409)
                time.sleep(0.05)
            raise EngineError("取消没有生效", code="ffmpeg", action="再试一次。", status=500)

        with patch("app.services.motion.ffmpeg", side_effect=hang):
            task = motion.start(
                {"duration": 0.5, "format": "gif", "width": 160, "fps": 8},
                source=self.clip,
                source_title="样片",
            )
            self.assertTrue(started.wait(5))
            response = self.client.post(f"/api/tasks/{task['id']}/cancel")
            self.assertEqual(response.status_code, 200, response.text)
            current = get(task["id"])
            deadline = time.time() + 3
            while time.time() < deadline:
                current = get(task["id"])
                if current.get("status") == "cancelled" and (current.get("error") or {}).get("code") == "cancelled":
                    break
                time.sleep(0.05)
        self.assertEqual(current["status"], "cancelled")
        self.assertEqual((current.get("error") or {}).get("code"), "cancelled")

    def test_validation_illegal_id_and_missing_video(self):
        response = self.client.post("/api/motion", json={"video_id": "../etc/passwd", "duration": 1})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "id")
        self.assertTrue(detail["message"])
        self.assertTrue(detail["action"])

        response = self.client.post("/api/motion", json={"video_id": "12/3", "duration": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "id")

        response = self.client.post("/api/motion", json={"video_id": 1, "fps": 4, "duration": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "input")

        response = self.client.post("/api/motion", json={"video_id": 1, "fps": 31, "duration": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "input")

        response = self.client.post("/api/motion", json={"video_id": 1, "duration": 31})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "range")

        response = self.client.post("/api/motion", json={"video_id": 1, "start": 1, "end": 4, "duration": 2})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "range")

        response = self.client.post("/api/motion", json={"video_id": 1, "format": "bmp", "duration": 1})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "format")

        response = self.client.get("/api/motion/abcd..ef/file")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "id")

        missing = self.client.post("/api/motion", json={"video_id": 987654321, "duration": 1, "format": "gif"})
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["detail"]["code"], "not_found")
        self.assertIn("action", missing.json()["detail"])

    def test_download_rejects_paths_outside_the_export_dir(self):
        job = create("motion", "转动图", {"format": "gif"})
        update(
            job["id"],
            status="success",
            result={"output_path": "/etc/passwd", "filename": "motion.gif", "content_type": "image/gif"},
        )
        response = self.client.get(f"/api/motion/{job['id']}/file")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"]["code"], "path")
        self.assertFalse(response.content.startswith(b"root:"))

    def test_low_disk_is_rejected_before_a_job(self):
        self._require_ffmpeg()
        with patch.object(motion, "ensure_free_mb", side_effect=OSError("磁盘剩余 3MB，低于 512MB")):
            with self.assertRaises(EngineError) as caught:
                motion.start({"duration": 0.4, "format": "gif", "width": 160, "fps": 8}, source=self.clip)
        self.assertEqual(caught.exception.code, "disk")
        self.assertIn("512", caught.exception.action)
        jobs = Path(self.tmp.name) / "jobs"
        self.assertFalse(jobs.exists() and any(jobs.glob("*.json")))

    def test_render_checks_disk_and_takes_a_slot(self):
        self._require_ffmpeg()
        calls = {"disk": 0, "entered": 0}
        real = motion.render_semaphore

        class _Slot:
            def __enter__(self):
                calls["entered"] += 1
                real.acquire()
                return self

            def __exit__(self, *_args):
                real.release()
                return False

        def fake_ffmpeg(args, **_kwargs):
            dest = Path(args[-1])
            dest.write_bytes(b"\x89PNG\r\n" if dest.suffix == ".png" else b"GIF89a")
            return ""

        with patch.object(motion, "ensure_free_mb", side_effect=lambda *_a, **_k: calls.__setitem__("disk", calls["disk"] + 1)), \
                patch.object(motion, "render_semaphore", _Slot()), \
                patch.object(motion, "ffmpeg", side_effect=fake_ffmpeg):
            task = motion.start(
                {"duration": 0.4, "format": "gif", "width": 160, "fps": 8},
                source=self.clip,
                source_title="样片",
            )
        self.assertEqual(task["status"], "success", task.get("error"))
        self.assertGreaterEqual(calls["disk"], 1)
        self.assertGreaterEqual(calls["entered"], 1)

    def test_webp_without_libwebp_tells_the_user_to_use_gif(self):
        with patch.object(motion, "webp_supported", return_value=False):
            with self.assertRaises(EngineError) as caught:
                motion.parse_options({"format": "webp", "duration": 1, "video_id": 1})
        self.assertEqual(caught.exception.code, "webp_unsupported")
        self.assertIn("GIF", caught.exception.action)
        with patch.object(motion, "webp_supported", return_value=False):
            response = self.client.post("/api/motion", json={"video_id": 1, "format": "webp", "duration": 1})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "webp_unsupported")
        self.assertIn("GIF", detail["action"])


if __name__ == "__main__":
    unittest.main()
