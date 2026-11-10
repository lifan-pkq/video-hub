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
    def test_narrate_route_navigation_and_source_label(self):
        app = (ROOT / "frontend/src/App.tsx").read_text()
        nav = (ROOT / "frontend/src/nav.ts").read_text()
        api = (ROOT / "frontend/src/api.ts").read_text()
        self.assertIn('path="/narrate"', app)
        self.assertIn("/narrate", nav)
        self.assertIn("AI 解说", nav)
        self.assertIn("制作", nav)
        self.assertIn("narrato", api)
        self.assertIn("startNarrateUnderstand", api)
        self.assertIn("analyzeEdit", api)

    def test_narration_page_is_native_not_iframe(self):
        page = (ROOT / "frontend/src/pages/NarratePage.tsx").read_text()
        self.assertNotIn("<iframe", page)
        self.assertNotIn("_stcore/health", page)
        self.assertIn("开始理解", page)
        self.assertIn("逐句骨架", page)
        self.assertIn("useSearchParams", page)
        self.assertIn("剪映草稿暂未接入", page)
        card = (ROOT / "frontend/src/components/VideoCard.tsx").read_text()
        self.assertIn("onNarrate", card)
        self.assertIn("AI 解说", card)

    def test_backend_owns_narration_tasks(self):
        schema = (ROOT / "backend/app/db.py").read_text()
        service = (ROOT / "backend/app/services/narration.py").read_text()
        self.assertIn("narration_tasks", schema)
        self.assertIn("narrato_import.import_task", service)
        self.assertIn("NARRATO_TASKS_DIR", service)


if __name__ == "__main__":
    unittest.main()
