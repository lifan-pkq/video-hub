import unittest

from fastapi.testclient import TestClient

from app import main


class LocalAccessTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_api_allows_local_requests_without_credentials(self):
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_cors_uses_local_frontend_allowlist(self):
        response = self.client.options(
            "/api/tasks",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "http://localhost:5173")

    def test_removed_automation_routes_are_not_mounted(self):
        self.assertEqual(self.client.post("/api/sms-code", json={"code": "123456"}).status_code, 404)
        self.assertEqual(self.client.get("/api/accounts").status_code, 404)
        self.assertEqual(self.client.get("/api/watchers").status_code, 404)
        self.assertEqual(self.client.get("/api/narrate/legacy-tasks").status_code, 404)


if __name__ == "__main__":
    unittest.main()
