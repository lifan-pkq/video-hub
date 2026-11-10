import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from app.main import app
from app.services import ai_drama, ai_images, ai_media, drama_pipeline, drama_store


PROJECT_ID = "abc123def456"
REF_NAME = "ref_0123456789abcdef0123456789abcdef.png"
LIB_NAME = "lib_0123456789abcdef0123456789abcdef.webp"


def project_fixture():
    shots = []
    for index in (1, 2):
        shot_id = f"e1s{index}"
        shots.append({
            "id": shot_id,
            "index": index,
            "revision": 1,
            "status": "done",
            "prompt": f"镜头 {index}",
            "scene": "房间",
            "action": "转身",
            "expression": "平静",
            "camera": "固定",
            "seconds": 5,
            "dialogue": "",
            "characters": [],
            "image": {
                "name": f"{shot_id}.jpg",
                "url": f"/api/drama/projects/{PROJECT_ID}/files/{shot_id}.jpg",
                "status": "ready",
                "revision": 1,
                "quality": {"score": 85, "passed": True, "reasons": [], "checker": "vision"},
            },
            "video": {
                "name": f"{shot_id}.mp4",
                "url": f"/api/drama/projects/{PROJECT_ID}/files/{shot_id}.mp4",
                "status": "ready",
                "revision": 1,
                "quality": {"score": 88, "passed": True, "reasons": [], "checker": "vision"},
            },
        })
    return {
        "id": PROJECT_ID,
        "title": "项目",
        "status": "success",
        "options": {"quality_threshold": 70, "aspect_ratio": "9:16"},
        "episodes": [{
            "index": 1,
            "status": "done",
            "video": "episode_1.mp4",
            "shots": shots,
        }],
        "characters": [],
        "logs": [],
        "created_at": "2026-01-01 00:00:00",
        "cancel_requested": False,
    }


class DramaShotAPITests(unittest.TestCase):
    @patch("app.routers.drama.ai_drama.update_shot")
    def test_update_only_target_shot(self, update):
        update.return_value = {"id": "e1s1", "revision": 2, "prompt": "新提示词"}
        response = TestClient(app).put(
            "/api/drama/projects/abc123def456/shots/e1s1",
            json={"prompt": "新提示词", "seconds": 8, "reference_images": [REF_NAME]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        update.assert_called_once_with(
            "abc123def456", "e1s1", prompt="新提示词", seconds=8,
            reference_images=[REF_NAME],
        )

    @patch("app.routers.drama.ai_drama.retry_shot")
    def test_retry_video_stage(self, retry):
        retry.return_value = {"id": "e1s1", "status": "running"}
        response = TestClient(app).post(
            "/api/drama/projects/abc123def456/shots/e1s1/retry",
            json={"stage": "video"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        retry.assert_called_once_with("abc123def456", "e1s1", "video")

    @patch("app.routers.drama.ai_drama.cancel_shot")
    def test_cancel_shot(self, cancel):
        cancel.return_value = {"id": "e1s1", "status": "cancelled"}
        response = TestClient(app).post("/api/drama/projects/abc123def456/shots/e1s1/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        cancel.assert_called_once_with("abc123def456", "e1s1")


class DramaShotServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.drama_dir = patch.object(drama_store, "DRAMA_DIR", Path(self.temp.name) / "drama")
        self.drama_dir.start()
        drama_store.save_project(project_fixture())

    def tearDown(self):
        with drama_pipeline._SHOT_RUNTIMES_LOCK:
            for runtime in drama_pipeline._SHOT_RUNTIMES.values():
                runtime.cancel.set()
            drama_pipeline._SHOT_RUNTIMES.clear()
        self.drama_dir.stop()
        self.temp.cleanup()

    def test_prompt_update_invalidates_only_target_and_cancels_old_worker(self):
        before = drama_store.load_project(PROJECT_ID)
        runtime = SimpleNamespace(cancel=threading.Event(), thread=None)
        with patch.dict(drama_pipeline._SHOT_RUNTIMES, {(PROJECT_ID, "e1s1"): runtime}, clear=True):
            result = ai_drama.update_shot(PROJECT_ID, "e1s1", prompt="新提示词")

        project = drama_store.load_project(PROJECT_ID)
        target, other = project["episodes"][0]["shots"]
        self.assertEqual(result["revision"], 2)
        self.assertEqual(target["prompt"], "新提示词")
        self.assertEqual(target["image"]["status"], "stale")
        self.assertIsNone(target["image"]["quality"])
        self.assertEqual(target["video"]["status"], "stale")
        self.assertEqual(other, before["episodes"][0]["shots"][1])
        self.assertEqual(project["episodes"][0]["status"], "stale")
        self.assertEqual(project["task_id"], "drama-abc123def456")
        self.assertTrue(runtime.cancel.is_set())

    def test_duration_update_retains_qualified_image(self):
        ai_drama.update_shot(PROJECT_ID, "e1s1", seconds=8)
        shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
        self.assertEqual(shot["seconds"], 8)
        self.assertEqual(shot["revision"], 2)
        self.assertEqual(shot["image"]["status"], "ready")
        self.assertEqual(shot["image"]["revision"], 2)
        self.assertTrue(drama_pipeline.shot_image_eligible(shot, 2))
        self.assertEqual(shot["video"]["status"], "stale")
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 2))

    def test_seconds_update_preserves_legacy_image_and_allows_video_retry(self):
        project = project_fixture()
        project["episodes"][0]["shots"][0]["image"] = {
            "name": "legacy.jpg",
            "status": "ready",
            "revision": 1,
            "quality": None,
            "legacy_accepted": True,
        }
        drama_store.save_project(project)

        ai_drama.update_shot(PROJECT_ID, "e1s1", seconds=8)
        shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
        self.assertEqual(shot["image"]["revision"], 2)
        self.assertTrue(shot["image"]["legacy_accepted"])
        self.assertTrue(drama_pipeline.shot_image_eligible(shot, 2))
        self.assertEqual(shot["video"]["status"], "stale")
        with patch("app.services.drama_pipeline._spawn_shot") as spawn:
            response = TestClient(app).post(
                f"/api/drama/projects/{PROJECT_ID}/shots/e1s1/retry",
                json={"stage": "video"},
            )
        self.assertEqual(response.status_code, 200, response.text)
        spawn.assert_called_once_with(PROJECT_ID, "e1s1", "video")
        shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
        self.assertEqual(shot["revision"], 3)
        self.assertEqual(shot["image"]["revision"], 3)
        self.assertTrue(shot["image"]["legacy_accepted"])
        self.assertTrue(drama_pipeline.shot_image_eligible(shot, 3))

    def test_empty_update_does_not_stop_existing_worker(self):
        runtime = SimpleNamespace(cancel=threading.Event(), thread=None)
        with patch.dict(drama_pipeline._SHOT_RUNTIMES, {(PROJECT_ID, "e1s1"): runtime}, clear=True):
            with self.assertRaises(ai_drama.DramaError):
                ai_drama.update_shot(PROJECT_ID, "e1s1")
        self.assertFalse(runtime.cancel.is_set())
        shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
        self.assertEqual(shot["revision"], 1)
        self.assertEqual(shot["status"], "done")

    def test_late_worker_result_cannot_overwrite_edited_shot(self):
        ai_drama.update_shot(PROJECT_ID, "e1s1", prompt="新提示词")
        late = Path(self.temp.name) / "late.jpg"
        late.write_bytes(b"late")
        accepted = drama_store.accept_artifact(PROJECT_ID, "e1s1", "image", 1, {
            "name": "late.jpg",
            "path": str(late),
            "quality": {"score": 99, "passed": True},
        })
        shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
        self.assertFalse(accepted)
        self.assertFalse(late.exists())
        self.assertEqual(shot["prompt"], "新提示词")
        self.assertEqual(shot["revision"], 2)
        self.assertEqual(shot["image"]["status"], "stale")

    def test_video_retry_without_current_qualified_image_returns_409(self):
        ai_drama.update_shot(PROJECT_ID, "e1s1", prompt="新提示词")
        with patch("app.services.drama_pipeline._spawn_shot") as spawn:
            response = TestClient(app).post(
                f"/api/drama/projects/{PROJECT_ID}/shots/e1s1/retry",
                json={"stage": "video"},
            )
        self.assertEqual(response.status_code, 409, response.text)
        spawn.assert_not_called()

    def test_video_retry_uses_existing_worker_and_keeps_parent_task(self):
        with patch("app.services.drama_pipeline._spawn_shot") as spawn, \
                patch("app.services.ai_drama.task_manager.create") as create:
            result = ai_drama.retry_shot(PROJECT_ID, "e1s1", "video")
        self.assertEqual(result["id"], "e1s1")
        self.assertEqual(result["status"], "running")
        spawn.assert_called_once_with(PROJECT_ID, "e1s1", "video")
        create.assert_not_called()
        project = drama_store.load_project(PROJECT_ID)
        self.assertEqual(project["task_id"], "drama-abc123def456")
        self.assertEqual(project["episodes"][0]["shots"][0]["status"], "pending")
        self.assertEqual(project["episodes"][0]["shots"][1]["status"], "done")

    def test_cancelled_shot_with_qualified_image_can_retry_video(self):
        drama_store.mark_shot_cancelled(PROJECT_ID, "e1s1")
        with patch("app.services.drama_pipeline._spawn_shot") as spawn:
            result = ai_drama.retry_shot(PROJECT_ID, "e1s1", "video")
        self.assertEqual(result["status"], "running")
        spawn.assert_called_once_with(PROJECT_ID, "e1s1", "video")

    def test_invalid_reference_names_return_400_without_persisting_payload(self):
        invalid = [
            "ref_new.png",
            "../" + REF_NAME,
            "data:image/png;base64," + "a" * 1000,
            "/tmp/" + LIB_NAME,
        ]
        for value in invalid:
            with self.subTest(value=value[:40]):
                response = TestClient(app).put(
                    f"/api/drama/projects/{PROJECT_ID}/shots/e1s1",
                    json={"reference_images": [value]},
                )
                self.assertEqual(response.status_code, 400, response.text)
                shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
                self.assertEqual(shot["revision"], 1)
                self.assertEqual(shot["reference_images"], [])

    def test_reference_names_resolve_only_from_their_owned_directories(self):
        reference_dir = Path(self.temp.name) / "references"
        library_dir = Path(self.temp.name) / "library"
        reference_dir.mkdir()
        library_dir.mkdir()
        (reference_dir / REF_NAME).write_bytes(b"image")
        (library_dir / LIB_NAME).write_bytes(b"library")
        project = drama_store.load_project(PROJECT_ID)
        shot = project["episodes"][0]["shots"][0]
        shot["reference_images"] = [REF_NAME, LIB_NAME]
        with patch.object(ai_media, "GENERATED_REFERENCE_DIR", reference_dir), \
                patch.object(ai_images, "AI_IMAGES_DIR", library_dir):
            references = drama_pipeline._image_references(project, shot)
        self.assertEqual(references, [
            "data:image/png;base64,aW1hZ2U=",
            "data:image/webp;base64,bGlicmFyeQ==",
        ])

    def test_pipeline_rejects_arbitrary_reference_path_from_legacy_json(self):
        project = drama_store.load_project(PROJECT_ID)
        shot = project["episodes"][0]["shots"][0]
        shot["reference_images"] = ["../../etc/passwd"]
        with self.assertRaises(drama_pipeline.DramaPipelineError):
            drama_pipeline._image_references(project, shot)

    def test_cancel_records_state_without_waiting_for_worker(self):
        worker = SimpleNamespace(
            cancel=threading.Event(),
            thread=SimpleNamespace(is_alive=lambda: True, join=Mock(side_effect=AssertionError("不得等待"))),
        )
        with patch.dict(drama_pipeline._SHOT_RUNTIMES, {(PROJECT_ID, "e1s1"): worker}, clear=True):
            project = ai_drama.cancel_shot(PROJECT_ID, "e1s1")
        self.assertEqual(project["status"], "cancelled")
        self.assertTrue(worker.cancel.is_set())
        worker.thread.join.assert_not_called()

    def test_public_video_retry_turns_missing_frame_into_recoverable_failure(self):
        def run_inline(project_id, shot_id, stage):
            drama_pipeline.run_shot(project_id, shot_id, stage)
            return SimpleNamespace()

        with patch("app.services.drama_pipeline._spawn_shot", side_effect=run_inline):
            result = ai_drama.retry_shot(PROJECT_ID, "e1s1", "video")
        self.assertEqual(result["status"], "running")
        project = drama_store.load_project(PROJECT_ID)
        shot = project["episodes"][0]["shots"][0]
        self.assertEqual(project["status"], "failed")
        self.assertEqual(shot["status"], "failed")
        self.assertTrue(shot["recoverable"])
        self.assertIn("首帧文件缺失", shot["error"])

    def test_public_retry_contains_unexpected_worker_error(self):
        def run_inline(project_id, shot_id, stage):
            drama_pipeline.run_shot(project_id, shot_id, stage)
            return SimpleNamespace()

        with patch("app.services.drama_pipeline._spawn_shot", side_effect=run_inline), \
                patch("app.services.drama_pipeline._run_video_loop", side_effect=RuntimeError("磁盘意外错误")):
            ai_drama.retry_shot(PROJECT_ID, "e1s1", "video")
        project = drama_store.load_project(PROJECT_ID)
        self.assertEqual(project["status"], "failed")
        self.assertTrue(project["recoverable"])
        self.assertIn("磁盘意外错误", project["error"])


if __name__ == "__main__":
    unittest.main()
