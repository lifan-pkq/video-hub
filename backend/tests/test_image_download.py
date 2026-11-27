"""图文作品下载：原图入库，视频下载路径保持不变，抖音免 Cookie。"""
import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yt_dlp

from app.services import downloader
from app.services.douyin_fallback import _from_detail_api, _from_share_page, _info_from_item, original_images
from app.services.xiaohongshu_images import parse_page


def _jpeg() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), (20, 140, 200)).save(buffer, format="JPEG")
    return buffer.getvalue()


JPEG = _jpeg()


class FakeTask:
    def __init__(self):
        self.id = "task-image"
        self.status = "pending"
        self.progress = 0.0
        self.logs = []
        self.result = {}
        self.error = None
        self.cancelled = False

    def log(self, message):
        self.logs.append(message)

    def finish(self, status):
        self.status = status


class ImageSelectionTests(unittest.TestCase):
    def test_douyin_session_does_not_send_cookie(self):
        from app.services.douyin_fallback import _session

        session = _session()
        self.assertNotIn("Cookie", {key.title() for key in session.headers})
        self.assertNotIn("cookie", {key.lower() for key in session.headers})
    def test_clean_urls_win_over_watermark_list(self):
        slides = original_images({
            "images": [
                {
                    "url_list": ["https://cdn.example/original-1.jpeg"],
                    "download_url_list": ["https://cdn.example/watermark-1.jpeg"],
                },
                {
                    "watermark_free_download_url_list": ["https://cdn.example/original-2.jpeg"],
                    "url_list": ["https://cdn.example/display-2.jpeg"],
                    "download_url_list": ["https://cdn.example/watermark-2.jpeg"],
                },
            ],
        })
        self.assertEqual(slides[0]["urls"][0], "https://cdn.example/original-1.jpeg")
        self.assertEqual(slides[1]["urls"][0], "https://cdn.example/original-2.jpeg")
        self.assertNotIn("https://cdn.example/watermark-1.jpeg", slides[0]["urls"][:1])

    def test_watermark_container_is_not_the_original(self):
        slides = original_images({
            "image_post_info": {
                "images": [{
                    "display_image": {"url_list": ["https://cdn.example/display.jpeg"], "width": 1080, "height": 1440},
                    "owner_watermark_image": {"url_list": ["https://cdn.example/wm.jpeg"]},
                    "thumbnail": {"url_list": ["https://cdn.example/thumb.jpeg"]},
                }],
            },
        })
        self.assertEqual(slides[0]["urls"], ["https://cdn.example/display.jpeg"])

    def test_image_post_ignores_placeholder_video(self):
        info = _info_from_item({
            "aweme_id": "7123456789012345678",
            "desc": "春日图文",
            "images": [{
                "url_list": ["https://cdn.example/original.jpeg"],
                "download_url_list": ["https://cdn.example/wm.jpeg"],
            }],
            "video": {
                "play_addr": {"url_list": ["https://cdn.example/playwm/slide.mp4"], "width": 720, "height": 1280},
                "duration": 3000,
            },
        }, "7123456789012345678", "https://www.douyin.com/video/7123456789012345678")
        self.assertEqual(info["media_kind"], "images")
        self.assertIsNone(info["_play_url"])
        self.assertEqual(info["_image_slides"][0]["urls"][0], "https://cdn.example/original.jpeg")
        self.assertTrue(info["webpage_url"].endswith("/note/7123456789012345678"))

    def test_video_item_stays_a_video(self):
        info = _info_from_item({
            "aweme_id": "7001",
            "desc": "一条视频",
            "images": None,
            "video": {
                "play_addr": {"url_list": ["https://cdn.example/playwm/a.mp4"], "width": 720, "height": 1280},
                "duration": 5000,
            },
        }, "7001", "https://www.douyin.com/video/7001")
        self.assertNotIn("media_kind", info)
        self.assertIn("/play/", info["_play_url"])
        self.assertNotIn("playwm", info["_play_url"])

    def test_share_page_accepts_image_post(self):
        item = {
            "aweme_id": "7123456789012345678",
            "desc": "春日图文",
            "author": {"nickname": "tester"},
            "images": [{
                "url_list": ["https://cdn.example/original-1.jpeg", "https://cdn.example/original-1b.jpeg"],
                "download_url_list": ["https://cdn.example/watermark-1.jpeg"],
            }],
        }
        html = "window._ROUTER_DATA = " + json.dumps({
            "loaderData": {"video": {"videoInfoRes": {"item_list": [item]}}},
        })

        class Response:
            text = html
            status_code = 200

            def raise_for_status(self):
                return None

        class Session:
            headers = {}

            def get(self, url, timeout=None):
                self.url = url
                return Response()

        info = _from_share_page("7123456789012345678", Session())
        self.assertEqual(info["media_kind"], "images")
        self.assertEqual(info["image_count"], 1)
        self.assertEqual(info["_image_slides"][0]["urls"][0], "https://cdn.example/original-1.jpeg")

    def test_detail_api_accepts_image_post(self):
        item = {
            "aweme_id": "7123456789012345678",
            "desc": "春日图文",
            "images": [{"url_list": ["https://cdn.example/original.jpeg"]}],
        }

        class Response:
            status_code = 200

            def __init__(self, payload=None):
                self.payload = payload or {}

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class Session:
            def get(self, url, timeout=None, headers=None):
                if "aweme/detail" in url:
                    return Response({"aweme_detail": item})
                return Response({})

        info = _from_detail_api("7123456789012345678", Session())
        self.assertEqual(info["media_kind"], "images")
        self.assertEqual(info["_image_slides"][0]["urls"][0], "https://cdn.example/original.jpeg")


class XiaohongshuImageTests(unittest.TestCase):
    def test_image_note_keeps_display_order_and_skips_preview(self):
        note_id = "abcdef0123456789abcdef01"
        state = {
            "note": {
                "noteDetailMap": {
                    note_id: {
                        "note": {
                            "type": "normal",
                            "noteId": note_id,
                            "title": "穿搭",
                            "user": {"nickname": "小红"},
                            "imageList": [
                                {
                                    "urlDefault": "https://cdn.example/a.jpg",
                                    "urlPre": "https://cdn.example/a-pre.jpg",
                                    "infoList": [{"imageScene": "WB_DFT", "url": "https://cdn.example/a-dft.jpg"}],
                                },
                                {
                                    "urlDefault": "https://cdn.example/b.jpg",
                                    "urlPre": "https://cdn.example/b-pre.jpg",
                                },
                            ],
                        },
                    },
                },
            },
        }
        html = "<script>window.__INITIAL_STATE__ = " + json.dumps(state) + "</script>"
        info = parse_page(html, f"https://www.xiaohongshu.com/explore/{note_id}")
        self.assertEqual(info["media_kind"], "images")
        self.assertEqual(
            [slide["urls"][0] for slide in info["_image_slides"]],
            ["https://cdn.example/a-dft.jpg", "https://cdn.example/b.jpg"],
        )

    def test_video_note_is_not_a_gallery(self):
        note_id = "abcdef0123456789abcdef01"
        state = {
            "note": {
                "noteDetailMap": {
                    note_id: {
                        "note": {
                            "type": "video",
                            "noteId": note_id,
                            "title": "视频",
                            "imageList": [{"urlDefault": "https://cdn.example/cover.jpg"}],
                            "video": {"media": {"stream": {"h264": [{"masterUrl": "https://cdn.example/a.mp4"}]}}},
                        },
                    },
                },
            },
        }
        html = "window.__INITIAL_STATE__=" + json.dumps(state)
        info = parse_page(html, f"https://www.xiaohongshu.com/explore/{note_id}")
        self.assertIsNone(info)


class _ImageHttp:
    def __init__(self, body: bytes):
        self.body = body
        self.urls: list[str] = []
        self.headers = {"User-Agent": "test", "Referer": "https://www.douyin.com/"}

    def get(self, url, timeout=None, stream=None, allow_redirects=None, headers=None):
        self.urls.append(url)
        if (headers or {}).get("Cookie") or self.headers.get("Cookie"):
            raise AssertionError("image download sent a cookie")
        parent = self

        class Response:
            headers = {
                "Content-Type": "image/jpeg" if "original" in url else "text/plain",
                "Content-Length": str(len(parent.body if "original" in url else b"watermark")),
            }

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=1024):
                yield parent.body if "original" in url else b"watermark"

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        return Response()


class DownloadFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.tmp_root = root / "tmp"
        self.videos_root = root / "videos"
        self.images_root = root / "images"
        self.tmp_root.mkdir()
        self.videos_root.mkdir()
        self.images_root.mkdir()
        for name, value in (
            ("app.services.downloader.TMP_DIR", self.tmp_root),
            ("app.services.downloader.VIDEOS_DIR", self.videos_root),
            ("app.services.ai_images.AI_IMAGES_DIR", self.images_root),
        ):
            item = patch(name, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch("app.services.downloader.ensure_dirs")
        item.start()
        self.addCleanup(item.stop)
        self.rows: list[dict] = []

        def fake_add(**fields):
            row = {"id": len(self.rows) + 1, "created_at": "t", **fields}
            self.rows.append(row)
            return row

        item = patch("app.services.ai_images.add_ai_image", side_effect=fake_add)
        item.start()
        self.addCleanup(item.stop)

    def _slides(self):
        return {
            "id": "7123456789012345678",
            "title": "春日图文",
            "uploader": "tester",
            "webpage_url": "https://www.douyin.com/note/7123456789012345678",
            "extractor_key": "douyin",
            "media_kind": "images",
            "image_count": 2,
            "_image_slides": [
                {"urls": ["https://img.example/original-1.jpeg", "https://img.example/watermark-1.jpeg"]},
                {"urls": ["https://img.example/original-2.jpeg", "https://img.example/watermark-2.jpeg"]},
            ],
        }

    def _failing_ytdlp(self, seen: list):
        class FakeYoutubeDL:
            def __init__(self, opts):
                seen.append(opts.get("cookiefile"))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, url, download):
                raise yt_dlp.utils.DownloadError("No video formats found")

        return FakeYoutubeDL

    async def _run_douyin_images(self, cookiefile):
        seen: list = []
        http = _ImageHttp(JPEG)
        task = FakeTask()
        with patch("app.services.downloader.yt_dlp.YoutubeDL", self._failing_ytdlp(seen)), \
                patch("app.services.downloader._cookiefile_for", return_value=cookiefile), \
                patch("app.services.cookies.storage_state_to_requests_cookies", side_effect=AssertionError("should not read cookies")), \
                patch("app.services.douyin_fallback.parse_douyin", return_value=self._slides()), \
                patch("app.services.douyin_fallback._session", return_value=http), \
                patch("app.services.downloader.add_video", side_effect=AssertionError("video path used")):
            await downloader.run_download(task, "https://v.douyin.com/imagePost/")
        return task, http, seen

    async def test_image_post_saves_originals_without_cookie(self):
        task, http, seen = await self._run_douyin_images(cookiefile=None)
        self.assertEqual(task.status, "success", task.error)
        self.assertEqual(task.result["gallery"]["count"], 2)
        self.assertIn("7123456789012345678", task.result["gallery"]["query"])
        self.assertEqual(len(task.result["images"]), 2)
        self.assertTrue(all(image["download_url"].endswith("/download") for image in task.result["images"]))
        saved = list(self.images_root.iterdir())
        self.assertEqual(len(saved), 2)
        self.assertTrue(all(path.name.startswith("gallery_") and path.read_bytes() == JPEG for path in saved))
        self.assertEqual(http.urls, [
            "https://img.example/original-1.jpeg",
            "https://img.example/original-2.jpeg",
        ])
        self.assertNotIn("Cookie", http.headers)
        self.assertEqual(seen, [None])
        self.assertEqual(list(self.videos_root.iterdir()), [])
        self.assertTrue(any("原图" in line for line in task.logs))

    async def test_cookie_retry_is_optional_and_images_still_need_no_cookie(self):
        task, http, seen = await self._run_douyin_images(cookiefile="/tmp/douyin-cookies.txt")
        self.assertEqual(task.status, "success", task.error)
        self.assertEqual(seen, [None, "/tmp/douyin-cookies.txt"])
        self.assertEqual(len(list(self.images_root.iterdir())), 2)
        self.assertNotIn("Cookie", http.headers)
        self.assertTrue(all("original" in url for url in http.urls))

    async def test_video_download_does_not_save_a_gallery(self):
        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, url, download):
                info = {"id": "clip7001", "title": "普通视频", "webpage_url": "https://www.douyin.com/video/7001"}
                Path(self.prepare_filename(info)).write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 800)
                return info

            def prepare_filename(self, info):
                return self.opts["outtmpl"] % {"id": info["id"], "ext": "mp4"}

        with patch("app.services.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL), \
                patch("app.services.downloader.download_douyin", side_effect=AssertionError("fallback used")), \
                patch("app.services.ai_images.import_downloaded", side_effect=AssertionError("gallery used")):
            path, info = await asyncio.to_thread(
                downloader._do_download, FakeTask(), "https://www.douyin.com/video/7001",
            )
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.videos_root)
        self.assertIn(b"ftyp", path.read_bytes()[:32])
        self.assertNotEqual(info.get("media_kind"), "images")
        self.assertEqual(list(self.images_root.iterdir()), [])

    async def test_note_link_keeps_stills_when_ytdlp_returns_a_video(self):
        class FakeYoutubeDL:
            def __init__(self, opts):
                self.opts = opts

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, url, download):
                info = {"id": "note1", "title": "占位幻灯片", "webpage_url": "https://www.douyin.com/note/7123"}
                Path(self.prepare_filename(info)).write_bytes(b"slideshow-video")
                return info

            def prepare_filename(self, info):
                return self.opts["outtmpl"] % {"id": info["id"], "ext": "mp4"}

        def fake_download(url, out_path, **_kwargs):
            dest = Path(out_path).parent / "slides"
            dest.mkdir(parents=True, exist_ok=True)
            first = dest / "01.jpg"
            second = dest / "02.jpg"
            first.write_bytes(JPEG)
            second.write_bytes(JPEG)
            return {
                "id": "7123",
                "title": "春日图文",
                "webpage_url": "https://www.douyin.com/note/7123",
                "extractor_key": "douyin",
                "media_kind": "images",
                "image_count": 2,
                "image_files": [first, second],
            }

        task = FakeTask()
        with patch("app.services.downloader.yt_dlp.YoutubeDL", FakeYoutubeDL), \
                patch("app.services.downloader._cookiefile_for", return_value=None), \
                patch("app.services.downloader.download_douyin", side_effect=fake_download):
            await downloader.run_download(task, "https://www.douyin.com/note/7123")
        self.assertEqual(task.status, "success", task.error)
        self.assertEqual(task.result["media_kind"], "images")
        self.assertEqual(task.result["gallery"]["count"], 2)
        self.assertNotIn("video", task.result)
        self.assertEqual(list(self.videos_root.iterdir()), [])
        self.assertTrue(all(path.read_bytes() == JPEG for path in self.images_root.iterdir()))

    async def test_xiaohongshu_failure_saves_image_note_on_the_same_path(self):
        def fake_download(url, out_dir, **_kwargs):
            dest = Path(out_dir) / "slides"
            dest.mkdir(parents=True, exist_ok=True)
            image = dest / "01.jpg"
            image.write_bytes(JPEG)
            return {
                "id": "abcdef0123456789abcdef01",
                "title": "穿搭",
                "webpage_url": "https://www.xiaohongshu.com/explore/abcdef0123456789abcdef01",
                "extractor_key": "xiaohongshu",
                "media_kind": "images",
                "image_count": 1,
                "image_files": [image],
            }

        task = FakeTask()
        seen: list = []
        with patch("app.services.downloader.yt_dlp.YoutubeDL", self._failing_ytdlp(seen)), \
                patch("app.services.downloader._cookiefile_for", return_value=None), \
                patch("app.services.downloader.download_xiaohongshu_images", side_effect=fake_download), \
                patch("app.services.downloader.add_video", side_effect=AssertionError("video path used")):
            await downloader.run_download(task, "https://www.xiaohongshu.com/explore/abcdef0123456789abcdef01")
        self.assertEqual(task.status, "success", task.error)
        self.assertEqual(task.result["gallery"]["count"], 1)
        self.assertEqual(task.result["gallery"]["platform"], "xiaohongshu")
        self.assertEqual(list(self.videos_root.iterdir()), [])
        self.assertEqual([path.read_bytes() for path in self.images_root.iterdir()], [JPEG])
        self.assertEqual(seen, [None])

    def test_error_copy_no_longer_says_upload_locally(self):
        problem = downloader.explain_download_error("该抖音链接是图文作品，当前备用下载器仅支持视频")
        self.assertNotIn("本地上传", problem["action"])
        self.assertNotIn("请改用本地上传", problem["action"])
        self.assertIn("原图", problem["action"])
        self.assertIn("图片素材库", problem["action"])
        video_problem = downloader.explain_download_error("yt-dlp 与 iesdouyin 备用下载均失败：超时")
        self.assertNotIn("图文作品请改用本地上传", video_problem["action"])


if __name__ == "__main__":
    unittest.main()
