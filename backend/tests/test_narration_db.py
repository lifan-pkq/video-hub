import sqlite3
import unittest
from unittest.mock import patch

from app import db


class NarrationDbTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        db._init_schema(self.conn)
        self.patcher = patch("app.db._get_conn", return_value=self.conn)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.addCleanup(self.conn.close)

    def test_create_update_list_and_link(self):
        task = db.create_narration_task(
            id="narrate-abc",
            video_id=1,
            title="测试解说",
            content_type="knowledge",
            mode="auto",
            request_json={"content_type": "knowledge"},
        )
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["request"]["content_type"], "knowledge")

        updated = db.update_narration_task("narrate-abc", status="running", progress=37, result_json={"duration": 12})
        self.assertEqual(updated["status"], "running")
        self.assertEqual(updated["progress"], 37)
        self.assertEqual(updated["result"]["duration"], 12)

        self.assertEqual(db.get_narration_task("narrate-abc")["id"], "narrate-abc")
        self.assertEqual(len(db.list_narration_tasks()), 1)

        linked = db.link_narration_video("narrate-abc", 99)
        self.assertEqual(linked["output_video_id"], 99)

    def test_list_is_sorted_and_limited(self):
        for index in range(3):
            db.create_narration_task(
                id=f"narrate-{index}",
                video_id=index,
                title=f"任务 {index}",
                created_at=f"2026-01-0{index + 1}T00:00:00",
                updated_at=f"2026-01-0{index + 1}T00:00:00",
            )
        tasks = db.list_narration_tasks(limit=2)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["id"], "narrate-2")


if __name__ == "__main__":
    unittest.main()
