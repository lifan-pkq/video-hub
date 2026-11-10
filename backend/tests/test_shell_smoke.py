"""壳层接口冒烟：健康检查、下载解析校验、导出缺视频。"""
import os
import unittest

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from fastapi.testclient import TestClient

from app.main import app


class ShellSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_keeps_fused_fields(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body.get("status"), "ok")
        for key in ("ffmpeg", "llm", "vision", "asr", "tts"):
            self.assertIn(key, body)
        self.assertIsInstance(body["tts"], list)

    def test_parse_rejects_blank_link(self):
        response = self.client.post("/api/download/parse", json={"text": "   "})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "url_missing")
        self.assertIn("链接", detail["message"])
        self.assertTrue(detail["action"])

    def test_parse_rejects_text_without_url(self):
        response = self.client.post("/api/download/parse", json={"text": "这不是链接"})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "url_missing")
        self.assertTrue(detail["action"])

    def test_export_missing_video_is_400(self):
        response = self.client.post("/api/exports", json={"video_id": 999999999})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        text = detail if isinstance(detail, str) else str(detail.get("message", ""))
        self.assertIn("不存在", text)


if __name__ == "__main__":
    unittest.main()
