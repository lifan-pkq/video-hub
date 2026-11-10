import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import ai_media, quality


class AIMediaQualityTests(unittest.TestCase):
    @patch("app.services.profiles.resolve_bundle")
    def test_morph_prompt_preserves_character_and_scene(self, bundle):
        bundle.return_value = {
            "prompt": "陈默左眼下有痣；家里灰沙发",
            "references": [],
            "character": {"name": "陈默"},
        }
        prompt, _refs, character = ai_media._profile_context("镜子里的人变老", transition="morph")
        self.assertIn("左眼下有痣", prompt)
        self.assertIn("一对一缓慢 morph", prompt)
        self.assertEqual(character["name"], "陈默")

    @patch("app.services.profiles.resolve_bundle", return_value={"prompt": "", "references": [], "character": None})
    def test_person_prompt_requires_profile(self, _bundle):
        with self.assertRaises(ai_media.MediaAIError):
            ai_media._profile_context("一个男人走到镜子前")

    @patch("app.services.ai_media._require_keys")
    @patch("app.services.ai_media._image_consistency", side_effect=[{"status": "review"}, {"status": "pass", "score": 90}])
    @patch("app.services.ai_media._generate_image_once", side_effect=[{"name": "first.png"}, {"name": "second.png"}])
    @patch("app.services.ai_media._profile_context", return_value=("prompt", [], None))
    def test_image_retries_once_after_consistency_failure(self, _profile, generate, _check, _keys):
        result = ai_media.generate_image("测试")
        self.assertEqual(generate.call_count, 2)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(result["consistency"]["status"], "pass")

    @patch("app.services.quality.analyze_loudness", return_value={"integrated_lufs": -16})
    @patch("app.services.quality._picture_metrics", return_value={"YAVG": 30, "YHIGH": 80, "YLOW": 10})
    @patch("app.services.quality._probe")
    def test_source_quality_reports_flags_and_unknown_watermark(self, probe, _picture, _loudness):
        probe.return_value = {
            "format": {"bit_rate": "400000"},
            "streams": [{"codec_type": "video", "width": 640, "height": 360}],
        }
        with tempfile.TemporaryDirectory() as temp:
            result = quality.assess_source(Path(temp) / "clip.mp4")
        self.assertIn("过暗", result["flags"])
        self.assertIn("码率偏低", result["flags"])
        self.assertEqual(result["checks"]["watermark"], "unknown")
        self.assertEqual(result["level"], "low")


if __name__ == "__main__":
    unittest.main()
