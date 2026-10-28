import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class VendorBoundaryTests(unittest.TestCase):
    def test_expected_service_sources_exist(self):
        for relative in (
            "narratoai/webui.py",
            "narratoai/app/models/schema.py",
            "narratoai/LICENSE",
            "services/subtitle-preview/main.py",
            "services/asr/main.py",
        ):
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_runtime_configuration_is_ignored(self):
        ignored = (ROOT / ".gitignore").read_text()
        for pattern in ("narratoai/config.toml", "narratoai/storage/", "services/.venv/"):
            self.assertIn(pattern, ignored)
        self.assertFalse((ROOT / "narratoai/config.toml").exists())
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


if __name__ == "__main__":
    unittest.main()
