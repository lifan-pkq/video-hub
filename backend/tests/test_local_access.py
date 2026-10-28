import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main
from app.routers import sms


class LocalAccessTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_api_allows_local_requests_without_credentials(self):
        response = self.client.get("/api/tasks")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_cors_allows_any_origin(self):
        response = self.client.options(
            "/api/tasks",
            headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "*")

    def test_sms_accepts_local_webhook_without_token(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sms, "PUBLISHER_DIR", Path(directory)):
                response = self.client.post("/api/sms-code", json={"code": "123456"})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertNotIn("123456", response.text)


if __name__ == "__main__":
    unittest.main()
