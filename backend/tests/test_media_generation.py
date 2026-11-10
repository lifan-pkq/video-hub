import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, call, patch

from app.services import ai_media, media_generation


class MediaGenerationTests(unittest.TestCase):
    def test_video_retry_sleep_observes_cancellation(self):
        should_cancel = Mock(side_effect=[False, True])
        with patch("app.services.media_generation.time.sleep") as sleep:
            with self.assertRaises(media_generation.GenerationCancelled):
                media_generation._cancellable_sleep(30, should_cancel)
        sleep.assert_called_once_with(0.2)

    @patch("app.services.media_generation._image_request")
    def test_image_core_has_no_task_side_effect(self, request):
        request.return_value = media_generation.GeneratedMedia(
            path=Path("image.png"), source_url="", model="test", metadata={}
        )
        with patch("app.services.tasks.task_manager.create") as create:
            result = media_generation.generate_image_asset("一只猫")
        self.assertEqual(result.path, Path("image.png"))
        create.assert_not_called()

    @patch("app.services.media_generation._video_request")
    def test_video_core_has_no_task_or_library_side_effect(self, request):
        request.return_value = media_generation.GeneratedMedia(
            path=Path("video.mp4"), source_url="https://media.test/v", model="test", metadata={}
        )
        with patch("app.services.tasks.task_manager.create") as create, \
             patch("app.db.add_video") as add_video:
            result = media_generation.generate_video_asset("猫转身", 5)
        self.assertEqual(result.path, Path("video.mp4"))
        create.assert_not_called()
        add_video.assert_not_called()

    @patch("app.services.media_generation._image_request")
    def test_image_core_forwards_options_and_reports_progress(self, request):
        request.return_value = media_generation.GeneratedMedia(
            path=Path("image.png"), source_url="", model="test", metadata={}
        )
        progress = Mock()
        result = media_generation.generate_image_asset(
            "一只猫", size="4K", ratio="1:1", images=["data:image/png;base64,abc"],
            progress=progress,
        )
        self.assertEqual(result.model, "test")
        request.assert_called_once_with(
            "一只猫", size="4K", ratio="1:1", images=["data:image/png;base64,abc"],
            should_cancel=None,
        )
        self.assertEqual(progress.call_args_list, [call(0, "提交图片生成"), call(100, "图片已下载")])

    @patch("app.services.media_generation._image_request")
    def test_image_core_deletes_download_when_cancelled(self, request):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "image.png"
            path.write_bytes(b"image")
            request.return_value = media_generation.GeneratedMedia(path, "", "test", {})
            should_cancel = Mock(side_effect=[False, True])
            with self.assertRaises(media_generation.GenerationCancelled):
                media_generation.generate_image_asset("一只猫", should_cancel=should_cancel)
            self.assertFalse(path.exists())

    @patch("app.services.media_generation._video_request")
    def test_video_core_deletes_download_when_cancelled(self, request):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "video.mp4"
            path.write_bytes(b"video")
            request.return_value = media_generation.GeneratedMedia(path, "", "test", {})
            should_cancel = Mock(side_effect=[False, True])
            with self.assertRaises(media_generation.GenerationCancelled):
                media_generation.generate_video_asset("猫转身", 5, should_cancel=should_cancel)
            self.assertFalse(path.exists())

    @patch("app.services.media_generation._video_request")
    def test_video_core_rejects_duration_outside_limits(self, request):
        for seconds in (3, 13):
            with self.subTest(seconds=seconds), self.assertRaises(media_generation.MediaAIError):
                media_generation.generate_video_asset("猫转身", seconds)
        request.assert_not_called()

    def test_concurrent_video_creation_keeps_each_request_key(self):
        import threading

        barrier = threading.Barrier(2, timeout=5)
        pool = Mock()
        expected = {"first": ("video-1", "key-1"), "second": ("video-2", "key-2")}

        def request(_method, _url, *, json_body, **_kwargs):
            prompt = json_body["prompt"]
            video_id, key = expected[prompt]
            other = "second" if prompt == "first" else "first"
            pool.last_key = expected[other][1]
            response = SimpleNamespace(
                status_code=200,
                json=lambda: {"video_id": video_id},
                _ai_pool_key=key,
            )
            barrier.wait()
            return response

        pool.request.side_effect = request
        results = {}
        errors = []

        def create(prompt):
            try:
                results[prompt] = media_generation._create_video(prompt, 5, "16:9")
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(media_generation, "AI_POOL", pool):
            threads = [threading.Thread(target=create, args=(prompt,)) for prompt in expected]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertFalse(errors)
        self.assertEqual(results, {
            prompt: {"video_id": video_id, "_key": key}
            for prompt, (video_id, key) in expected.items()
        })

    def test_pool_attaches_key_to_each_successful_response(self):
        from app.services.ai_pool import AIPool

        pool = AIPool(keys=["key-1"])
        response = SimpleNamespace(status_code=200)
        with patch("app.services.ai_pool.requests.request", return_value=response):
            result = pool.request("POST", "https://media.test/videos")
        self.assertIs(result, response)
        self.assertEqual(response._ai_pool_key, "key-1")

    def test_http_request_errors_distinguish_permanent_and_transient(self):
        pool = Mock()
        pool.request.side_effect = [
            SimpleNamespace(status_code=400, text="invalid request"),
            SimpleNamespace(status_code=500, text="upstream unavailable"),
        ]
        with patch.object(media_generation, "AI_POOL", pool):
            with self.assertRaises(media_generation.MediaAIError) as permanent:
                media_generation._image_request("猫", size="2K", ratio="16:9", images=[], should_cancel=None)
            self.assertNotIsInstance(
                permanent.exception, media_generation.TransientMediaAIError
            )
            with self.assertRaises(media_generation.TransientMediaAIError):
                media_generation._image_request("猫", size="2K", ratio="16:9", images=[], should_cancel=None)

    def test_pool_authentication_error_is_permanent(self):
        pool = Mock()
        pool.request.side_effect = media_generation.AIPoolError("HTTP 401: unauthorized")
        with patch.object(media_generation, "AI_POOL", pool):
            with self.assertRaises(media_generation.MediaAIError) as raised:
                media_generation._image_request("猫", size="2K", ratio="16:9", images=[], should_cancel=None)
        self.assertNotIsInstance(raised.exception, media_generation.TransientMediaAIError)

    def test_download_http_400_is_permanent(self):
        response = Mock(status_code=400)
        response.raise_for_status.side_effect = media_generation.requests.HTTPError(
            "bad request", response=response
        )
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.services.media_generation.requests.get", return_value=response
        ):
            with self.assertRaises(media_generation.MediaAIError) as raised:
                media_generation._download("https://media.test/video", Path(temp) / "video.mp4")
        self.assertNotIsInstance(raised.exception, media_generation.TransientMediaAIError)

    def test_download_network_error_is_transient(self):
        with tempfile.TemporaryDirectory() as temp, patch(
            "app.services.media_generation.requests.get",
            side_effect=media_generation.requests.RequestException("network down"),
        ):
            with self.assertRaises(media_generation.TransientMediaAIError):
                media_generation._download("https://media.test/video", Path(temp) / "video.mp4")


class AIMediaCompatibilityTests(unittest.TestCase):
    def test_media_ai_error_is_reexported_from_core(self):
        self.assertIs(ai_media.MediaAIError, media_generation.MediaAIError)

    @patch("app.services.ai_media._require_keys")
    @patch("app.services.ai_media._image_consistency", return_value={"status": "skipped"})
    @patch("app.services.ai_media.generate_image_asset")
    @patch("app.services.ai_media._profile_context", return_value=("prompt", [], None))
    def test_image_wrapper_uses_core_and_keeps_response_shape(
        self, _profile, generate, _check, _keys
    ):
        generate.return_value = media_generation.GeneratedMedia(
            Path("image.png"), "https://media.test/image", "test", {"size": 123}
        )
        result = ai_media.generate_image("测试", size="4K", ratio="1:1", images=["ref"])
        generate.assert_called_once_with(
            "prompt", size="4K", ratio="1:1", images=["ref"]
        )
        self.assertEqual(result["url"], "/api/ai/media/images/image.png")
        self.assertEqual(result["name"], "image.png")
        self.assertEqual(result["model"], "test")
        self.assertEqual(result["size"], 123)
        self.assertTrue(result["ok"])

    @patch("app.services.ai_media.threading.Thread")
    @patch("app.services.ai_media.task_manager.create")
    @patch("app.services.ai_media._profile_context", return_value=("猫转身", [], None))
    @patch("app.services.ai_media._require_keys")
    def test_video_tasks_use_ai_video_type(self, _keys, _profile, create, _thread):
        create.return_value = SimpleNamespace(id="task1")
        result = ai_media.start_video_generation("猫转身", 5)
        create.assert_called_once_with(
            "ai_video", "AI 视频 · 猫转身", input={"kind": "ai_video"}
        )
        self.assertEqual(result, {"task_id": "task1", "mode": "text"})

    @patch("app.services.ai_media.probe_video", return_value={"duration": 5})
    @patch("app.services.ai_media._video_consistency", return_value={"status": "pass"})
    @patch("app.services.ai_media.generate_video_asset")
    def test_video_worker_uses_core_without_library_side_effect(self, generate, _check, _probe):
        task = Mock(cancelled=False)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            generate.return_value = media_generation.GeneratedMedia(
                source, "https://media.test/video", "test", {}
            )
            with patch("app.services.ai_media.GENERATED_VIDEO_DIR", root):
                ai_media._generate_video_task(task, "猫转身", "5", "16:9", save_to_library=False)
            self.assertFalse(source.exists())
            self.assertEqual(len(list(root.glob("ai_*.mp4"))), 1)
        generate.assert_called_once_with(
            "猫转身", 5, aspect_ratio="16:9", first_frame="", last_frame="", images=None,
            should_cancel=ANY, progress=ANY,
        )
        self.assertFalse(task.result["saved_to_library"])
        self.assertEqual(task.result["model"], "test")
        task.finish.assert_called_once_with("success")


if __name__ == "__main__":
    unittest.main()
