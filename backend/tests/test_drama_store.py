import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import drama_store


def project_fixture(**overrides):
    project = {
        "id": "abc123def456",
        "title": "旧项目",
        "status": "pending",
        "options": {"episodes_count": 1},
        "episodes": [{"index": 1, "shots": [{"index": 1, "video": ""}]}],
        "characters": [],
        "logs": [],
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
    }
    project.update(overrides)
    return project


class DramaStoreTests(unittest.TestCase):
    def test_task_id_is_stable(self):
        self.assertEqual(drama_store.task_id_for("abc123def456"), "drama-abc123def456")
        with self.assertRaises(ValueError):
            drama_store.task_id_for("../abc123def456")

    def test_old_project_gets_schema_and_shot_identity(self):
        old = project_fixture()
        project = drama_store.normalize_project(old)
        shot = project["episodes"][0]["shots"][0]
        self.assertEqual(project["schema_version"], 2)
        self.assertEqual(project["task_id"], "drama-abc123def456")
        self.assertEqual(project["options"]["quality_threshold"], 70)
        self.assertEqual(shot["id"], "e1s1")
        self.assertEqual(shot["revision"], 1)
        self.assertEqual(shot["status"], "pending")
        self.assertEqual(shot["image"]["attempts"], 0)
        self.assertEqual(shot["video"]["attempts"], 0)
        self.assertEqual(shot["prompt"], "")
        self.assertEqual(shot["reference_images"], [])
        self.assertEqual(shot["characters"], [])
        self.assertEqual(shot["quality_history"], [])
        self.assertEqual(shot["scene"], "")
        self.assertEqual(shot["action"], "")
        self.assertEqual(shot["expression"], "")
        self.assertEqual(shot["camera"], "固定")
        self.assertEqual(shot["dialogue"], "")
        self.assertEqual(shot["seconds"], 5)

    def test_legacy_media_is_accepted_without_inventing_quality(self):
        old = project_fixture(episodes=[{
            "index": 1,
            "shots": [{
                "index": 1,
                "frame": "e1s1.jpg",
                "video": "e1s1.mp4",
                "attempts": 2,
                "status": "done",
            }],
        }])
        project = drama_store.normalize_project(old)
        shot = project["episodes"][0]["shots"][0]
        self.assertEqual(shot["image"], {
            "name": "e1s1.jpg",
            "url": "/api/drama/projects/abc123def456/files/e1s1.jpg",
            "status": "ready",
            "attempts": 2,
            "quality": None,
            "revision": 1,
            "legacy_accepted": True,
        })
        self.assertEqual(shot["video"]["name"], "e1s1.mp4")
        self.assertIsNone(shot["video"]["quality"])
        self.assertTrue(shot["video"]["legacy_accepted"])
        self.assertNotIn("schema_version", old)
        self.assertEqual(old["episodes"][0]["shots"][0]["video"], "e1s1.mp4")

    def test_revision_change_clears_legacy_quality_exemption(self):
        old = project_fixture(episodes=[{
            "index": 1,
            "shots": [{
                "index": 1,
                "revision": 2,
                "frame": "e1s1.jpg",
                "video": "e1s1.mp4",
            }],
        }])
        shot = drama_store.normalize_project(old)["episodes"][0]["shots"][0]
        self.assertFalse(shot["image"]["legacy_accepted"])
        self.assertFalse(shot["video"]["legacy_accepted"])

    def test_save_load_and_update_round_trip_normalized_project(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            saved = drama_store.save_project(project_fixture())
            self.assertEqual(saved["task_id"], "drama-abc123def456")
            path = Path(temp) / "abc123def456" / "project.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["schema_version"], 2)
            self.assertEqual(list(Path(temp).rglob("*.tmp")), [])

            def update(project):
                project["title"] = "已更新"
                project["progress"] = 25

            updated = drama_store.update_project("abc123def456", update)
            self.assertEqual(updated["title"], "已更新")
            self.assertEqual(drama_store.load_project("abc123def456")["progress"], 25)

    def test_update_project_serializes_same_project_mutations(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            threads = [threading.Thread(target=lambda: [
                drama_store.update_project("abc123def456", lambda project: project.__setitem__(
                    "progress", project.get("progress", 0) + 1
                )) for _ in range(10)
            ]) for _ in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(drama_store.load_project("abc123def456")["progress"], 40)

    def test_task_view_uses_drama_type_and_project_id(self):
        project = drama_store.normalize_project(project_fixture(status="running", logs=["one", "two"]))
        view = drama_store.task_view(project, with_logs=True)
        self.assertEqual(view["id"], "drama-abc123def456")
        self.assertEqual(view["type"], "ai_drama")
        self.assertEqual(view["kind"], "ai_drama")
        self.assertEqual(view["input"], {"project_id": "abc123def456"})
        self.assertEqual(view["result"], {"project_id": "abc123def456"})
        self.assertIsNone(view["finished_at"])
        self.assertEqual(view["logs"], ["one", "two"])

    def test_list_and_find_use_stable_disk_identity(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            self.assertEqual([item["id"] for item in drama_store.list_task_views()], ["drama-abc123def456"])
            found = drama_store.find_project_by_task_id("drama-abc123def456")
            self.assertEqual(found["id"], "abc123def456")
            self.assertIsNone(drama_store.find_project_by_task_id("drama-not-valid"))
            self.assertIsNone(drama_store.find_project_by_task_id("other-task"))


if __name__ == "__main__":
    unittest.main()
