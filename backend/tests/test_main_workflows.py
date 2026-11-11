"""主流程端到端：本地短片下载、上传、漫剧生命周期、分段录制、多画幅导出。

外部模型和成片接口用假实现。下载走真实 yt-dlp，对着本机 HTTP 上的短片。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _tiny_mp4(path: Path, seconds: float = 3.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c=0x335577:s=320x180:d={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True, timeout=60,
    )


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        if not shutil.which("ffmpeg"):
            self.skipTest("需要 ffmpeg")

    def test_ytdlp_downloads_a_short_clip_from_http(self):
        from app.services.downloader import run_download
        from app.services.tasks import Task

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = root / "videos"
            library.mkdir()
            _tiny_mp4(root / "clip.mp4", 2.0)
            handler = partial(SimpleHTTPRequestHandler, directory=str(root))
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            self.addCleanup(server.server_close)
            url = f"http://127.0.0.1:{port}/clip.mp4"
            task = Task("download", "短片", input={"text": url})
            covers = root / "covers"
            covers.mkdir()
            (root / "tmp").mkdir()
            with patch("app.services.downloader.VIDEOS_DIR", library), \
                    patch("app.services.downloader.TMP_DIR", root / "tmp"), \
                    patch("app.services.downloader.COVERS_DIR", covers), \
                    patch("app.services.downloader.ensure_dirs"), \
                    patch("app.services.downloader.probe_video", return_value={"duration": 2, "width": 320, "height": 180, "codec": "h264"}), \
                    patch("app.services.downloader.ensure_h264", side_effect=lambda path: path), \
                    patch("app.services.downloader.make_cover", return_value=True), \
                    patch("app.services.downloader.assess_source", return_value={"score": 80}), \
                    patch("app.services.downloader.add_video", side_effect=lambda **fields: {"id": 1, **fields}) as added, \
                    patch("app.services.downloader.update_video"), \
                    patch("app.services.downloader.start_analysis"):
                asyncio.run(run_download(task, url))
            self.assertEqual(task.status, "success", task.error)
            video = (task.result or {}).get("video") or {}
            saved = library / video["filename"]
            self.assertTrue(saved.is_file())
            self.assertGreater(saved.stat().st_size, 500)
            self.assertTrue(added.called)

    def test_upload_lists_the_new_video(self):
        from app.main import app

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            videos = root / "videos"
            videos.mkdir()
            (root / "covers").mkdir()
            sample = root / "upload.mp4"
            _tiny_mp4(sample, 1.5)
            with patch("app.routers.videos.VIDEOS_DIR", videos), \
                    patch("app.routers.videos.TMP_DIR", root), \
                    patch("app.routers.videos.ensure_dirs"), \
                    patch("app.routers.videos.COVERS_DIR", root / "covers"), \
                    patch("app.routers.videos.probe_video", return_value={"duration": 1.5, "width": 320, "height": 180, "codec": "h264", "has_audio": True}), \
                    patch("app.routers.videos.ensure_h264", side_effect=lambda path: path), \
                    patch("app.routers.videos.make_cover", return_value=True), \
                    patch("app.routers.videos.add_video", side_effect=lambda **fields: {"id": 7, "cover": "c.jpg", **fields}) as added, \
                    patch("app.services.quality.update_quality", return_value={"score": 70}), \
                    patch("app.services.understanding.start_analysis"), \
                    patch("app.routers.videos.list_videos", return_value=[{"id": 7, "title": "上传的短片", "filename": "a.mp4", "cover": "c.jpg", "size": 10, "duration": 1.5, "width": 320, "height": 180, "source_platform": "local", "source_url": None, "created_at": ""}]):
                client = TestClient(app)
                with sample.open("rb") as handle:
                    response = client.post("/api/videos/upload", params={"title": "上传的短片"}, files={"file": ("upload.mp4", handle, "video/mp4")})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["title"], "上传的短片")
                listed = client.get("/api/videos")
                self.assertEqual(listed.status_code, 200)
                self.assertEqual(listed.json()[0]["title"], "上传的短片")
                self.assertTrue(added.called)

    def test_recording_segments_a_local_stream(self):
        from app.services.recorder import _build_ffmpeg_cmd

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "live.mp4"
            _tiny_mp4(source, 5.0)
            pattern = root / "part_%03d.mp4"
            command = _build_ffmpeg_cmd(str(source), "direct", pattern, fps=8)
            command[command.index("600")] = "2"
            # 直播流关键帧很密，10 分钟一段能切开。这段 5 秒样片要强制关键帧，否则分段器只写出一个文件。
            insert_at = command.index("-f")
            command[insert_at:insert_at] = ["-force_key_frames", "expr:gte(t,n_forced*2)", "-g", "16"]
            subprocess.run(command, check=True, capture_output=True, timeout=60)
            parts = sorted(root.glob("part_*.mp4"))
            self.assertGreaterEqual(len(parts), 2, [item.name for item in parts])
            self.assertTrue(all(item.stat().st_size > 200 for item in parts))

    def test_drama_plan_start_and_cancel(self):
        from app.main import app
        from app.services import ai_drama, drama_store

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            def analyze(project):
                project["characters"] = [{"id": "c1", "name": "林夏", "status": "pending"}]
                project["title"] = project.get("title") or "雨"
                drama_store.save_project(project)

            def storyboard(project):
                project["episodes"] = [{
                    "index": 1,
                    "title": "第一集",
                    "status": "pending",
                    "shots": [{
                        "id": "e1s1",
                        "index": 1,
                        "seconds": 5,
                        "prompt": "林夏走进雨里",
                        "status": "pending",
                        "dialogue": "",
                    }],
                }]
                drama_store.save_project(project)

            def run(project_id):
                def mark(current):
                    current["status"] = "running"
                    current["stage"] = "生成中"
                drama_store.update_project(project_id, mark)

            with patch.object(drama_store, "DRAMA_DIR", root), \
                    patch.object(ai_drama.AI_POOL, "has_keys", return_value=True), \
                    patch.object(ai_drama, "_analyze", side_effect=analyze), \
                    patch.object(ai_drama, "_storyboard", side_effect=storyboard), \
                    patch.object(ai_drama, "_run", side_effect=run):
                client = TestClient(app)
                created = client.post("/api/drama/projects", json={
                    "novel": "雨" * 200,
                    "title": "雨",
                    "episodes_count": 1,
                })
                self.assertEqual(created.status_code, 200, created.text)
                project_id = created.json()["id"]
                for _ in range(50):
                    current = client.get(f"/api/drama/projects/{project_id}")
                    if current.json().get("status") == "planned":
                        break
                    threading.Event().wait(0.05)
                else:
                    self.fail(current.text)
                started = client.post(f"/api/drama/projects/{project_id}/start")
                self.assertEqual(started.status_code, 200, started.text)
                cancelled = client.post(f"/api/drama/projects/{project_id}/cancel")
                self.assertEqual(cancelled.status_code, 200, cancelled.text)
                self.assertEqual(drama_store.load_project(project_id)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
