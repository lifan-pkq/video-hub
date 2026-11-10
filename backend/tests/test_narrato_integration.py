import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class VendorBoundaryTests(unittest.TestCase):
    def test_expected_service_sources_exist(self):
        for relative in (
            "engine/render.py",
            "engine/signals.py",
            "engine/scripting.py",
            "engine/tts.py",
            "engine/asr.py",
            "backend/app/services/narrate_flow.py",
            "backend/app/services/edit_flow.py",
            "backend/app/services/narration.py",
            "backend/app/routers/narrate.py",
            "backend/app/routers/edit.py",
            "backend/app/jobs.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)
        router = (ROOT / "backend/app/routers/narrate.py").read_text()
        self.assertNotIn("NARRATO_ENGINE_URL", router)
        self.assertIn("engine.render", (ROOT / "backend/app/services/narrate_flow.py").read_text())

    def test_runtime_configuration_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text()
        for pattern in ("narratoai/config.toml", "narratoai/storage/", "services/.venv/"):
            self.assertIn(pattern, ignored)
    def test_main_module_imports_and_health_is_callable(self):
        from app.main import health

        body = health()
        self.assertEqual(body["status"], "ok")
        self.assertIn("ffmpeg", body)
        self.assertIn("llm", body)


class FrontendIntegrationTests(unittest.TestCase):
    def test_narrate_surface_is_not_in_the_workbench(self):
        app = (ROOT / "frontend/src/App.tsx").read_text()
        nav = (ROOT / "frontend/src/nav.ts").read_text()
        api = (ROOT / "frontend/src/api.ts").read_text()
        self.assertIn('path="/narrate"', app)
        self.assertIn('Navigate to="/edit"', app)
        self.assertNotIn("NarratePage", app)
        self.assertNotIn("/narrate", nav)
        self.assertNotIn("AI 解说", nav)
        self.assertIn("智能剪辑", nav)
        self.assertIn("历史解说", api)
        self.assertIn("analyzeEdit", api)
        self.assertFalse((ROOT / "frontend/src/pages/NarratePage.tsx").exists())
        card = (ROOT / "frontend/src/components/VideoCard.tsx").read_text()
        self.assertNotIn("onNarrate", card)
        self.assertNotIn("AI 解说", card)

    def test_backend_owns_narration_tasks(self):
        schema = (ROOT / "backend/app/db.py").read_text()
        service = (ROOT / "backend/app/services/narration.py").read_text()
        self.assertIn("narration_tasks", schema)
        self.assertIn("narrato_import.import_task", service)
        self.assertIn("NARRATO_TASKS_DIR", service)


if __name__ == "__main__":
    unittest.main()
