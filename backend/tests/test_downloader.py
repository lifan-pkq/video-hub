import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import downloader


class FakeTask:
    next_id = 0

    def __init__(self):
        type(self).next_id += 1
        self.id = f"task-{type(self).next_id}"
        self.status = "pending"
        self.progress = 0.0
        self.logs = []
        self.result = {}

    def log(self, message):
        self.logs.append(message)

    def finish(self, status):
        self.status = status


class DownloaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        root = Path(self.temp_dir.name)
        self.tmp_root = root / "tmp"
        self.videos_root = root / "videos"
        self.tmp_root.mkdir()
        self.videos_root.mkdir()
        for target, value in [("TMP_DIR", self.tmp_root), ("VIDEOS_DIR", self.videos_root)]:
            item = patch(f"app.services.downloader.{target}", value)
            item.start()
            self.addCleanup(item.stop)
        item = patch("app.services.downloader.ensure_dirs")
        item.start()
        self.addCleanup(item.stop)

    async def test_each_download_uses_and_cleans_its_own_tmp_directory(self):
        output_parents = []
        counter = 0

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts
                output_parents.append(Path(self.opts["outtmpl"]).parent)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download):
                nonlocal counter
                counter += 1
                info = {"id": f"video{counter}", "title": f"标题{counter}"}
                Path(self.prepare_filename(info)).write_bytes(b"video")
                return info

            def prepare_filename(self, info):
                return self.opts["outtmpl"] % {"id": info["id"], "ext": "mp4"}

        with patch("app.services.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL):
            await asyncio.to_thread(downloader._do_download, FakeTask(), "https://example.test/one")
            await asyncio.to_thread(downloader._do_download, FakeTask(), "https://example.test/two")

        self.assertEqual(len(output_parents), 2)
        self.assertNotEqual(output_parents[0], output_parents[1])
        for parent in output_parents:
            self.assertEqual(parent.parent, self.tmp_root)
            self.assertFalse(parent.exists())

    async def test_timeout_cancels_hook_waits_for_thread_and_cleans_tmp(self):
        thread_finished = threading.Event()
        hook_cancelled = threading.Event()

        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download):
                info = {"id": "slow", "title": "慢视频"}
                deadline = time.monotonic() + 0.4
                try:
                    while time.monotonic() < deadline:
                        for hook in self.opts["progress_hooks"]:
                            hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 2})
                        time.sleep(0.005)
                except Exception:
                    hook_cancelled.set()
                    raise
                finally:
                    thread_finished.set()
                Path(self.prepare_filename(info)).write_bytes(b"video")
                return info

            def prepare_filename(self, info):
                return self.opts["outtmpl"] % {"id": info["id"], "ext": "mp4"}

        task = FakeTask()
        with patch("app.services.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL), \
                patch("app.services.downloader.DOWNLOAD_TIMEOUT", 0.05):
            await downloader.run_download(task, "https://example.test/slow")
            finished_when_returned = thread_finished.is_set()
            await asyncio.to_thread(thread_finished.wait, 1)

        self.assertTrue(finished_when_returned, "超时返回时下载线程仍在运行")
        self.assertTrue(hook_cancelled.is_set())
        self.assertEqual(task.status, "failed")
        self.assertEqual(list(self.tmp_root.iterdir()), [])

    async def test_direct_and_batch_downloads_share_one_queue_lock(self):
        active = 0
        max_active = 0
        state_lock = threading.Lock()

        def fake_download(task, text):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            with state_lock:
                active -= 1
            return Path("unused.mp4"), {}

        tasks = [FakeTask(), FakeTask(), FakeTask()]
        with patch("app.services.downloader._do_download", side_effect=fake_download), \
                patch("app.services.downloader._post_process", return_value={"title": "完成"}):
            await asyncio.gather(
                downloader.run_download(tasks[0], "https://example.test/1"),
                downloader.run_download(tasks[1], "https://example.test/2"),
                downloader.run_download_queued(tasks[2], "https://example.test/3"),
            )

        self.assertEqual(max_active, 1)
        self.assertTrue(all(task.status == "success" for task in tasks))


if __name__ == "__main__":
    unittest.main()
