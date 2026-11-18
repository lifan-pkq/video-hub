"""抖音备用下载：短视频时长单位，以及取消和大小上限能停掉数据流。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.douyin_fallback import (
    FallbackCancelled,
    _info_from_item,
    douyin_duration_seconds,
    download_douyin,
)


def _item(duration):
    return {
        "aweme_id": "1234567890123456789",
        "desc": "一条短视频",
        "video": {
            "duration": duration,
            "width": 1080,
            "height": 1920,
            "play_addr": {"url_list": ["https://v.douyinvod.com/play.mp4"], "width": 1080, "height": 1920},
        },
    }


class DurationTests(unittest.TestCase):
    def test_short_aweme_duration_is_milliseconds(self):
        self.assertEqual(douyin_duration_seconds(8000), 8.0)
        self.assertEqual(douyin_duration_seconds(10000), 10.0)
        info = _info_from_item(_item(8000), "1234567890123456789", "https://www.douyin.com/video/1")
        self.assertEqual(info["duration"], 8.0)

    def test_longer_aweme_duration_stays_in_seconds(self):
        self.assertEqual(douyin_duration_seconds(15034), 15.034)
        info = _info_from_item(_item(15034), "1234567890123456789", "https://www.douyin.com/video/1")
        self.assertEqual(info["duration"], 15.034)

    def test_already_converted_seconds_under_one_thousand_are_kept(self):
        self.assertEqual(douyin_duration_seconds(45), 45.0)
        self.assertIsNone(douyin_duration_seconds(0))
        self.assertIsNone(douyin_duration_seconds("nope"))


class _Body:
    def __init__(self, chunks, headers=None):
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.status_code = 200

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=0):
        yield from self._chunks

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Session:
    def __init__(self, body):
        self.body = body
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.body


class DownloadBoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "out.mp4"
        self.disk = patch(
            "app.services.douyin_fallback.shutil.disk_usage",
            return_value=SimpleNamespace(free=2 * 1024 ** 3),
        )
        self.disk.start()
        self.addCleanup(self.disk.stop)

    def _download(self, body, **kwargs):
        session = _Session(body)
        info = {"_play_url": "https://v.douyinvod.com/play.mp4", "id": "123", "title": "短视频"}
        with patch("app.services.douyin_fallback.parse_douyin", return_value=info), \
                patch("app.services.douyin_fallback._session", return_value=session):
            return download_douyin("https://v.douyin.com/abc", self.out, **kwargs), session

    def test_cancel_between_chunks_deletes_the_partial_file(self):
        seen = {"n": 0}

        def should_cancel():
            seen["n"] += 1
            return seen["n"] > 1

        with self.assertRaises(FallbackCancelled):
            self._download(_Body([b"a" * 1000, b"b" * 1000]), should_cancel=should_cancel, max_bytes=10_000_000, deadline_s=30)
        self.assertFalse(self.out.exists())

    def test_byte_cap_deletes_the_partial_file(self):
        with self.assertRaises(RuntimeError) as caught:
            self._download(_Body([b"a" * 800, b"b" * 800]), max_bytes=1000, deadline_s=30)
        self.assertIn("大小上限", str(caught.exception))
        self.assertFalse(self.out.exists())

    def test_declared_content_length_is_refused_before_writing(self):
        body = _Body([b"a" * 200_000], headers={"Content-Length": "9000000000"})
        with self.assertRaises(RuntimeError):
            self._download(body, max_bytes=1024, deadline_s=30)
        self.assertFalse(self.out.exists())

    def test_complete_small_file_is_kept(self):
        payload = b"\x00" * 100_000
        result, session = self._download(_Body([payload]), max_bytes=200_000, deadline_s=30)
        self.assertEqual(self.out.read_bytes(), payload)
        self.assertNotIn("_play_url", result)
        self.assertEqual(result["id"], "123")
        self.assertEqual(session.calls, 1)
        self.assertEqual(session.body.headers, {})
