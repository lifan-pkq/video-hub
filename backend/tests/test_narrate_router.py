import sqlite3
import unittest
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from app import db
from app.main import app


class NarrationRouterTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init_schema(self.conn)
        self.db_patch = patch("app.db._get_conn", return_value=self.conn)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.conn.close)
        self.client = TestClient(app)

    def test_health_reports_unavailable_without_engine(self):
        with patch("app.services.narration.requests.request", side_effect=requests.ConnectionError("down")):
            response = self.client.get("/api/narrate/health")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["available"])

    def test_capabilities_returns_503_without_engine(self):
        with patch("app.services.narration.requests.request", side_effect=requests.ConnectionError("down")):
            response = self.client.get("/api/narrate/capabilities")
        self.assertEqual(response.status_code, 503)

    def test_task_list_is_empty_and_does_not_need_engine(self):
        response = self.client.get("/api/narrate/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_create_rejects_unknown_video(self):
        response = self.client.post("/api/narrate/tasks", json={"video_id": 999})
        self.assertEqual(response.status_code, 400)
        self.assertIn("视频不存在", response.json()["detail"])

    def test_cancel_unknown_task_is_404(self):
        response = self.client.post("/api/narrate/tasks/narrate-missing/cancel")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
