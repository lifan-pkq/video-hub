import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app import main
from app.services import ai_drama, ai_media, drama_store, job_bridge, media_generation
from app.services.tasks import Task


def project_fixture(**overrides):
    project = {
        "id": "abc123def456",
        "title": "项目",
        "status": "failed",
        "stage": "中断",
        "progress": 10,
        "error": "",
        "options": {"episodes_count": 1, "aspect_ratio": "9:16"},
        "episodes": [],
        "characters": [],
        "logs": [],
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
        "cancel_requested": False,
    }
    project.update(overrides)
    return project


class FakeThread:
    def __init__(self, *, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        return None

    def is_alive(self):
        return False


class DramaTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir_patch = patch.object(drama_store, "DRAMA_DIR", Path(self.temp.name) / "drama")
        self.dir_patch.start()
        self.jobs_patch = patch.object(job_bridge, "list_jobs", return_value=[])
        self.jobs_patch.start()
        self.memory_list_patch = patch.object(job_bridge.task_manager, "list", return_value=[])
        self.memory_list_patch.start()
        self.memory_get_patch = patch.object(job_bridge.task_manager, "get", return_value=None)
        self.memory_get = self.memory_get_patch.start()
        self.memory_cancel_patch = patch.object(ai_drama.task_manager, "cancel", return_value=False)
        self.memory_cancel = self.memory_cancel_patch.start()
        self.client = TestClient(main.app)

    def tearDown(self):
        ai_drama.THREADS.clear()
        self.memory_cancel_patch.stop()
        self.memory_get_patch.stop()
        self.memory_list_patch.stop()
        self.jobs_patch.stop()
        self.dir_patch.stop()
        self.temp.cleanup()

    def test_repeated_task_reads_keep_one_stable_parent(self):
        drama_store.save_project(project_fixture(status="running"))
        first = job_bridge.merged_tasks()
        second = job_bridge.merged_tasks()
        self.assertEqual([item["id"] for item in first], ["drama-abc123def456"])
        self.assertEqual([item["id"] for item in second], ["drama-abc123def456"])
        self.assertEqual(first[0]["type"], "ai_drama")
        self.assertNotIn("ai_video", [item["type"] for item in first])
        self.assertEqual(job_bridge.load_task_view("drama-abc123def456")["input"], {
            "project_id": "abc123def456"
        })

    def test_resume_reuses_task_id_without_creating_global_tasks(self):
        drama_store.save_project(project_fixture())
        create = Mock()
        with patch.object(ai_drama.task_manager, "create", create), \
                patch.object(ai_drama.threading, "Thread", FakeThread):
            result = ai_drama.resume_project("abc123def456")
        self.assertEqual(result["id"], "abc123def456")
        self.assertEqual(drama_store.load_project("abc123def456")["task_id"], "drama-abc123def456")
        create.assert_not_called()

    def test_storyboard_creates_normalized_artifacts_for_immediate_frame_build(self):
        project = drama_store.save_project(project_fixture(
            status="running",
            options={
                "episodes_count": 1,
                "aspect_ratio": "9:16",
                "style": "realistic",
            },
            episodes=[{"index": 1, "title": "第一集", "summary": "剧情", "shots": []}],
        ))
        with patch.object(ai_drama.drama_planner, "_request_json", return_value={"shots": [{
            "scene": "房间", "action": "转身", "expression": "平静", "camera": "固定",
            "seconds": 5, "dialogue": "", "characters": [],
        }]}):
            ai_drama._storyboard(project)
        shot = project["episodes"][0]["shots"][0]
        self.assertEqual(shot["id"], "e1s1")
        self.assertEqual(shot["image"]["status"], "pending")
        self.assertEqual(shot["video"]["attempts"], 0)
        self.assertEqual(shot["quality_history"], [])

    def test_legacy_video_build_uses_core_without_global_shot_task(self):
        project = drama_store.save_project(project_fixture(
            status="running",
            options={"aspect_ratio": "9:16", "max_attempts": 1},
            episodes=[{
                "index": 1,
                "title": "第一集",
                "status": "framed",
                "shots": [{
                    "index": 1,
                    "scene": "房间",
                    "action": "转身",
                    "expression": "平静",
                    "camera": "固定",
                    "seconds": 5,
                    "dialogue": "",
                    "characters": [],
                    "frame": "e1s1.jpg",
                    "video": "",
                }],
            }],
        ))
        root = Path(self.temp.name)
        assets = root / "drama" / project["id"] / "assets"
        assets.mkdir(parents=True)
        (assets / "e1s1.jpg").write_bytes(b"image")
        source = root / "generated.mp4"
        source.write_bytes(b"video")
        generate = Mock(return_value=media_generation.GeneratedMedia(source, "", "test", {}))
        create = Mock()
        with patch.object(ai_drama.ai_media, "generate_video_asset", generate), \
                patch.object(ai_drama.ai_media, "start_video_generation") as legacy_start, \
                patch.object(ai_drama.task_manager, "create", create):
            ai_drama._build_videos(project)
        self.assertEqual(project["episodes"][0]["shots"][0]["video"]["name"], "e1s1.mp4")
        self.assertTrue((assets / "e1s1.mp4").is_file())
        generate.assert_called_once()
        legacy_start.assert_not_called()
        create.assert_not_called()

    def test_stale_worker_save_cannot_erase_route_cancellation(self):
        drama_store.save_project(project_fixture(status="running"))
        worker_copy = drama_store.load_project("abc123def456")
        ai_drama._check_cancel(worker_copy)
        response = self.client.post("/api/tasks/drama-abc123def456/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(worker_copy["cancel_requested"])
        ai_drama._save(worker_copy)
        self.assertTrue(drama_store.load_project("abc123def456")["cancel_requested"])

    def test_resume_explicitly_clears_persisted_cancellation(self):
        drama_store.save_project(project_fixture(cancel_requested=True))
        with patch.object(ai_drama.threading, "Thread", FakeThread):
            ai_drama.resume_project("abc123def456")
        self.assertFalse(drama_store.load_project("abc123def456")["cancel_requested"])

    def test_running_project_observes_disk_cancellation(self):
        drama_store.save_project(project_fixture(status="running"))
        running_copy = drama_store.load_project("abc123def456")
        ai_drama.cancel_project("abc123def456")
        with self.assertRaisesRegex(ai_drama.DramaError, "项目已取消"):
            ai_drama._check_cancel(running_copy)

    def test_cancel_route_dispatches_to_disk_project_before_memory_tasks(self):
        drama_store.save_project(project_fixture(status="running"))
        response = self.client.post("/api/tasks/drama-abc123def456/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["task"]["id"], "drama-abc123def456")
        self.assertTrue(drama_store.load_project("abc123def456")["cancel_requested"])
        self.memory_get.assert_not_called()
        self.memory_cancel.assert_not_called()

    def test_cancel_route_keeps_ordinary_ai_video_on_memory_task_manager(self):
        task = Task("ai_video", "AI 视频", input={"kind": "ai_video"})
        task.status = "running"
        self.memory_get.return_value = task
        self.memory_cancel.return_value = True
        response = self.client.post(f"/api/tasks/{task.id}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.memory_cancel.assert_called_once_with(task.id)

    def test_legacy_ai_video_task_remains_readable(self):
        task = Task("ai", "旧 AI 视频", input={"kind": "ai"})
        task.result = {"video_url": "/api/ai/media/videos/legacy.mp4"}
        self.memory_get.return_value = task
        response = self.client.get(f"/api/tasks/{task.id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["type"], "ai")
        self.assertEqual(response.json()["result"]["video_url"], "/api/ai/media/videos/legacy.mp4")

    def test_video_batch_assigns_one_stable_batch_id_to_every_task(self):
        created = []

        def create_task(type_, title, input):
            task = Mock(id=f"task{len(created) + 1}")
            created.append(task)
            return task

        with patch.object(ai_media, "_require_keys"), \
                patch.object(ai_media, "_profile_context", side_effect=lambda prompt, *args: (prompt, [], None)), \
                patch.object(ai_media.threading, "Thread", FakeThread), \
                patch.object(ai_media.task_manager, "create", side_effect=create_task) as create:
            result = ai_media.start_video_batch([{"prompt": "镜头一"}, {"prompt": "镜头二"}], {})
        batch_ids = [item.get("batch_id") for item in result["tasks"]]
        self.assertTrue(all(batch_ids))
        self.assertEqual(len(set(batch_ids)), 1)
        self.assertEqual(len(batch_ids[0]), 12)
        self.assertEqual(
            [call.kwargs["input"]["batch_id"] for call in create.call_args_list],
            batch_ids,
        )

    def test_batch_query_includes_members_outside_newest_50_tasks(self):
        batch_tasks = [
            Task("ai_video", f"批量镜头 {index}", input={"kind": "ai_video", "batch_id": "older-batch"})
            for index in range(2)
        ]
        newer_tasks = [Task("ai_video", f"新任务 {index}") for index in range(60)]
        retained = [*newer_tasks, *batch_tasks]
        with patch.object(ai_media.task_manager, "list", side_effect=lambda limit=30: retained[:limit]):
            global_response = self.client.get("/api/tasks")
            batch_response = self.client.get("/api/ai/media/batches/older-batch")
        self.assertEqual(global_response.status_code, 200, global_response.text)
        self.assertEqual(len(global_response.json()), 50)
        self.assertNotIn(batch_tasks[0].id, {task["id"] for task in global_response.json()})
        self.assertEqual(batch_response.status_code, 200, batch_response.text)
        self.assertEqual(batch_response.json()["batch_id"], "older-batch")
        self.assertEqual(batch_response.json()["count"], 2)
        self.assertEqual(
            {task["id"] for task in batch_response.json()["tasks"]},
            {task.id for task in batch_tasks},
        )

    def test_startup_interrupts_real_running_project_and_allows_resume(self):
        import asyncio

        drama_store.save_project(project_fixture(status="running", progress=55))
        with patch.object(main, "ensure_dirs"), \
                patch.object(main.event_hub, "bind_loop"), \
                patch.object(main, "fail_interrupted_recordings"), \
                patch.object(main, "fail_interrupted_analyses"), \
                patch.object(main.asyncio, "create_task") as create_task:
            create_task.side_effect = lambda coroutine: coroutine.close()
            asyncio.run(main._startup())
        self.assertEqual(create_task.call_count, 2)

        project = drama_store.load_project("abc123def456")
        self.assertEqual(project["status"], "interrupted")
        self.assertTrue(project["recoverable"])
        self.assertIn("服务重启", project["error"])
        self.assertEqual(project["task_id"], "drama-abc123def456")
        with patch.object(ai_drama.threading, "Thread", FakeThread):
            resumed = ai_drama.resume_project("abc123def456")
        self.assertEqual(resumed["id"], "abc123def456")


if __name__ == "__main__":
    unittest.main()
