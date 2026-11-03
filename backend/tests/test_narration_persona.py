import unittest

from app.services.narration import _engine_spec


class NarrationPersonaTests(unittest.TestCase):
    def setUp(self):
        self.video = {
            "id": 1,
            "title": "赛车素材",
            "path": "/tmp/racing.mp4",
            "duration": 20,
            "width": 1080,
            "height": 1920,
        }

    def test_engine_spec_forwards_persona_and_strength(self):
        item = _engine_spec(self.video, {
            "persona": "CHENBO",
            "persona_strength": "strong",
        }, "narrate-test")

        self.assertEqual(item.get("persona"), "chenbo")
        self.assertEqual(item.get("persona_strength"), "strong")

    def test_engine_spec_defaults_unknown_persona_to_generic(self):
        item = _engine_spec(self.video, {
            "persona": "someone-else",
            "persona_strength": "extreme",
        }, "narrate-test")

        self.assertEqual(item.get("persona"), "auto")
        self.assertEqual(item.get("persona_strength"), "natural")


if __name__ == "__main__":
    unittest.main()
