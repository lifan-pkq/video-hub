"""确认旧解说链路已经从主应用拆掉，健康检查仍然可用。"""
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RemovedLegacyTests(unittest.TestCase):
    def test_legacy_trees_are_gone(self):
        for relative in (
            "narratoai",
            "edit-narrate",
            "browser-use-demo",
            "services/asr",
            "services/subtitle-preview",
            "backend/app/routers/narrate.py",
            "backend/app/services/narrate_flow.py",
            "backend/app/services/narration.py",
            "backend/app/services/douyin_fallback.py",
            "engine/tts.py",
            "engine/render.py",
            "engine/server.py",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_export_runs_in_process(self):
        exporter = (ROOT / "backend/app/services/exporter.py").read_text()
        self.assertIn("export_bundle", exporter)
        self.assertNotIn("NARRATOAI_ROOT", exporter)
        self.assertNotIn("engine.export_task", exporter)
        self.assertTrue((ROOT / "backend/app/services/export_bundle.py").is_file())
        self.assertTrue((ROOT / "backend/assets/fonts/DroidSansFallbackFull.ttf").is_file())

    def test_main_module_imports_and_health_is_callable(self):
        from app.main import health

        body = health()
        self.assertEqual(body["status"], "ok")
        self.assertIn("ffmpeg", body)
        self.assertIn("llm", body)
        self.assertNotIn("tts", body)

    def test_narrate_surface_is_not_in_the_workbench(self):
        app = (ROOT / "frontend/src/App.tsx").read_text()
        nav = (ROOT / "frontend/src/nav.ts").read_text()
        api = (ROOT / "frontend/src/api.ts").read_text()
        self.assertIn('path="/narrate"', app)
        self.assertIn('Navigate to="/edit"', app)
        self.assertNotIn("NarratePage", app)
        self.assertNotIn("/narrate", nav)
        self.assertNotIn("fetchNarrateVoices", api)
        self.assertIn("retryDouyinProduce", api)
        self.assertIn("智能剪辑", nav)


if __name__ == "__main__":
    unittest.main()
