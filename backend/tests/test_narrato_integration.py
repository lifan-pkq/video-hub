import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class VendorBoundaryTests(unittest.TestCase):
    def test_expected_service_sources_exist(self):
        for relative in (
            "narratoai/webui.py",
            "narratoai/app/models/schema.py",
            "narratoai/LICENSE",
            "narratoai/engine/server.py",
            "narratoai/engine/runner.py",
            "narratoai/engine/script.py",
            "backend/app/services/narration.py",
            "backend/app/routers/narrate.py",
            "services/subtitle-preview/main.py",
            "services/asr/main.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_runtime_configuration_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text()
        for pattern in ("narratoai/config.toml", "narratoai/storage/", "services/.venv/"):
            self.assertIn(pattern, ignored)
        self.assertEqual(
            subprocess.run(["git", "check-ignore", "-q", "narratoai/config.toml"], cwd=ROOT).returncode,
            0,
        )
    def test_main_module_imports_and_health_is_callable(self):
        from app.main import health

        self.assertEqual(health(), {"status": "ok"})


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
        self.assertIn("createNarrationTask", api)
        self.assertIn("fetchNarrationCapabilities", api)

    def test_narration_page_is_native_not_iframe(self):
        page = (ROOT / "frontend/src/pages/NarratePage.tsx").read_text()
        self.assertNotIn("<iframe", page)
        self.assertNotIn("_stcore/health", page)
        self.assertIn("createNarrationTask", page)
        self.assertIn("内容类型", page)
        self.assertIn("useSearchParams", page)
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
