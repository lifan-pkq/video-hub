import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class VendorBoundaryTests(unittest.TestCase):
    def test_expected_service_sources_exist(self):
        for relative in (
            "narratoai/webui.py",
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


if __name__ == "__main__":
    unittest.main()
