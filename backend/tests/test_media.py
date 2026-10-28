import tempfile
import unittest
from pathlib import Path

from app.services.media import range_stream


async def read_body(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return b"".join(chunks)


class RangeStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "video.mp4"
        self.path.write_bytes(b"0123456789")

    async def test_suffix_range_returns_last_bytes(self):
        response = range_stream(self.path, "bytes=-3")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["Content-Range"], "bytes 7-9/10")
        self.assertEqual(response.headers["Content-Length"], "3")
        self.assertEqual(await read_body(response), b"789")

    async def test_suffix_longer_than_file_returns_entire_file(self):
        response = range_stream(self.path, "bytes=-20")

        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["Content-Range"], "bytes 0-9/10")
        self.assertEqual(await read_body(response), b"0123456789")

    async def test_open_ended_and_bounded_ranges(self):
        for header, content_range, body in [
            ("bytes=3-", "bytes 3-9/10", b"3456789"),
            ("bytes=2-4", "bytes 2-4/10", b"234"),
            ("bytes=8-100", "bytes 8-9/10", b"89"),
        ]:
            with self.subTest(header=header):
                response = range_stream(self.path, header)
                self.assertEqual(response.status_code, 206)
                self.assertEqual(response.headers["Content-Range"], content_range)
                self.assertEqual(await read_body(response), body)

    async def test_invalid_or_unsatisfiable_ranges_return_416(self):
        for header in [
            "bytes=abc",
            "bytes=1-2,4-5",
            "bytes=1-2 trailing",
            "bytes=-0",
            "bytes=-",
            "bytes=5-4",
            "bytes=10-",
            "bytes=20-30",
        ]:
            with self.subTest(header=header):
                response = range_stream(self.path, header)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response.headers["Content-Range"], "bytes */10")
                self.assertEqual(response.body, b"")

    async def test_empty_file_rejects_every_range_without_negative_offsets(self):
        self.path.write_bytes(b"")
        for header in ["bytes=0-", "bytes=-1", "bytes=0-0"]:
            with self.subTest(header=header):
                response = range_stream(self.path, header)
                self.assertEqual(response.status_code, 416)
                self.assertEqual(response.headers["Content-Range"], "bytes */0")
                self.assertEqual(response.body, b"")


class ConcatClipsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.out_path = Path(self.temp_dir.name) / "joined.mp4"
        self.clips = [{"path": Path("input.mp4"), "start": 0, "end": 1}]

    def _patches(self):
        from unittest.mock import patch

        return [
            patch("app.services.media.shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("app.services.media.probe_video", return_value={"width": 640, "height": 480}),
            patch("app.services.media.probe_has_audio", return_value=True),
        ]

    def test_large_stderr_does_not_deadlock(self):
        import concurrent.futures
        import subprocess
        import sys
        from unittest.mock import patch

        script = (
            "import os, pathlib, sys; "
            "os.write(2, b'x' * 2000000); "
            "pathlib.Path(sys.argv[1]).write_bytes(b'mp4'); "
            "sys.stdout.write('progress=end\\n'); sys.stdout.flush()"
        )
        processes = []

        def start_process(cmd, _popen=subprocess.Popen, **kwargs):
            proc = _popen([sys.executable, "-c", script, str(self.out_path)], **kwargs)
            processes.append(proc)
            return proc

        patches = self._patches()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with patch("app.services.media.subprocess.Popen", side_effect=start_process):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(__import__("app.services.media", fromlist=["concat_clips"]).concat_clips,
                                         self.clips, self.out_path)
                try:
                    future.result(timeout=2)
                except concurrent.futures.TimeoutError:
                    for proc in processes:
                        proc.kill()
                        proc.wait()
                    with self.assertRaises(RuntimeError):
                        future.result(timeout=2)
                    self.fail("大量 stderr 导致 concat_clips 死锁")
        self.assertEqual(self.out_path.read_bytes(), b"mp4")

    def test_timeout_kills_waits_and_removes_partial_output(self):
        import subprocess
        import sys
        from unittest.mock import Mock, patch

        script = (
            "import pathlib, sys, time; "
            "pathlib.Path(sys.argv[1]).write_bytes(b'partial'); "
            "sys.stdout.write('out_time_ms=1\\n'); sys.stdout.flush(); time.sleep(60)"
        )
        processes = []

        def start_process(cmd, _popen=subprocess.Popen, **kwargs):
            proc = _popen([sys.executable, "-c", script, str(self.out_path)], **kwargs)
            proc.wait = Mock(wraps=proc.wait)
            processes.append(proc)
            return proc

        clock_values = iter([0.0, 1801.0])

        def fake_time():
            return next(clock_values, 1801.0)

        patches = self._patches()
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        try:
            with patch("app.services.media.subprocess.Popen", side_effect=start_process), \
                    patch("app.services.media.time.time", side_effect=fake_time):
                with self.assertRaisesRegex(RuntimeError, "渲染超时"):
                    __import__("app.services.media", fromlist=["concat_clips"]).concat_clips(
                        self.clips, self.out_path)
            self.assertFalse(self.out_path.exists())
            self.assertEqual(processes[0].wait.call_count, 1)
        finally:
            for proc in processes:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()


if __name__ == "__main__":
    unittest.main()
