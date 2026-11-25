"""能力摘要只描述配置和本机文件，不回显秘密，也不发请求。"""
import json
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from app.main import health


class CapabilityStatusTests(unittest.TestCase):
    def test_health_keeps_old_fields_and_adds_safe_summary(self):
        secret = "sk-test-super-secret-value-should-not-leak"
        leaked_url = "http://user:s3cret-pass@example.invalid/v1"
        with patch.dict(os.environ, {
            "VIDEO_HUB_AI_BASE_URL": leaked_url,
            "VIDEO_HUB_AI_API_KEY": secret,
            "VIDEO_HUB_AI_MODEL": "demo-model",
            "VIDEO_HUB_VISION_MODEL": "",
            "VIDEO_HUB_ASR_URL": "",
            "VIDEO_HUB_ASR_ENGINE": "sensevoice",
        }):
            body = health()
        raw = json.dumps(body, ensure_ascii=False)
        self.assertEqual(body["status"], "ok")
        self.assertIn("ffmpeg", body)
        self.assertTrue(body["llm"])
        self.assertFalse(body["vision"])
        self.assertEqual(body["asr"], "sensevoice")
        self.assertNotIn(secret, raw)
        self.assertNotIn("s3cret-pass", raw)
        self.assertNotIn("example.invalid", raw)
        self.assertNotIn("user:s3cret", raw)
        by_id = {item["id"]: item for item in body["capabilities"]}
        self.assertEqual(by_id["llm"]["state"], "configured")
        self.assertIn("没有向服务商发请求", by_id["llm"]["summary"])
        self.assertEqual(by_id["vision"]["state"], "unconfigured")
        self.assertIn("VIDEO_HUB_VISION_MODEL", by_id["vision"]["next"])
        self.assertIn(by_id["asr"]["state"], {"configured", "unconfigured", "unknown"})
        self.assertNotIn("ready", by_id["asr"]["state"])
        self.assertNotIn(secret, by_id["llm"]["summary"] + by_id["llm"]["next"])

    def test_missing_llm_is_unconfigured_without_a_probe(self):
        with patch.dict(os.environ, {
            "VIDEO_HUB_AI_BASE_URL": "",
            "VIDEO_HUB_AI_API_KEY": "",
            "VIDEO_HUB_AI_API_KEYS": "",
            "AGNES_API_KEY": "",
            "AGNES_API_KEYS": "",
            "VIDEO_HUB_VISION_API_KEY": "",
            "VIDEO_HUB_VISION_API_KEYS": "",
            "VIDEO_HUB_ASR_URL": "",
        }):
            body = health()
        self.assertFalse(body["llm"])
        llm = next(item for item in body["capabilities"] if item["id"] == "llm")
        self.assertEqual(llm["state"], "unconfigured")
        self.assertIn("不会调用付费接口", llm["summary"])

    def test_whisper_import_is_not_treated_as_a_finished_model(self):
        with patch.dict(os.environ, {"VIDEO_HUB_ASR_ENGINE": "whisper", "VIDEO_HUB_ASR_URL": ""}):
            body = health()
        self.assertEqual(body["asr"], "whisper")
        asr = next(item for item in body["capabilities"] if item["id"] == "asr")
        self.assertIn(asr["state"], {"configured", "unconfigured"})
        self.assertNotEqual(asr["state"], "ready")
        self.assertTrue("不等于" in asr["summary"] or "导入不了" in asr["summary"])


if __name__ == "__main__":
    unittest.main()
