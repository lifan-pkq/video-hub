import unittest

from fastapi.testclient import TestClient

from app.main import app


class NarrationRouterTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_is_local_and_does_not_call_narrato(self):
        response = self.client.get("/api/narrate/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertIn("llm", body)
        self.assertIn("tts", body)
        self.assertNotIn("available", body)

    def test_old_capabilities_route_is_gone(self):
        response = self.client.get("/api/narrate/capabilities")
        self.assertEqual(response.status_code, 404)

    def test_task_list_does_not_need_external_engine(self):
        response = self.client.get("/api/narrate/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_create_rejects_unknown_video(self):
        response = self.client.post("/api/narrate/tasks", json={"video_id": 999999})
        self.assertEqual(response.status_code, 404)
        detail = response.json()["detail"]
        self.assertIn("不存在", detail["message"])
        self.assertTrue(detail["action"])

    def test_unknown_job_cancel_is_404(self):
        response = self.client.post("/api/jobs/doesnotexist00/cancel")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
