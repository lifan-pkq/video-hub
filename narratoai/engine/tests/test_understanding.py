import sys
import unittest
from array import array
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine import understanding


class UnderstandingTests(unittest.TestCase):
    def test_parse_markdown_keeps_absolute_timeline(self):
        rows = understanding.parse_markdown(
            "# 片段\n- 00:00:01,000-00:00:03,500：陈默在算命摊前停下",
            "visual",
        )
        self.assertEqual(rows[0]["start"], 1.0)
        self.assertEqual(rows[0]["end"], 3.5)
        self.assertEqual(rows[0]["text"], "陈默在算命摊前停下")

    @patch("engine.understanding._beats", return_value=[1.2])
    @patch("engine.understanding._silence_and_loudness", return_value=([], {"status": "ok"}))
    def test_fuses_speech_visual_audio_and_speaker(self, _silence, _beats):
        transcript = "- 00:00:00,000-00:00:02,000：你别急"
        visual = "- 00:00:00,000-00:00:02,000：回声把纸板放在桌上"
        result = understanding.build_understanding(
            "clip.mp4",
            transcript,
            visual,
            {"segments": [{"speaker": "A", "words": [{"word": "你", "start": 0, "end": 0.3}]}]},
        )
        self.assertEqual(result["tracks"]["speech"][0]["speaker"], "A")
        self.assertEqual(result["tracks"]["visual"][0]["text"], "回声把纸板放在桌上")
        self.assertEqual(result["tracks"]["audio"][0]["kind"], "beat")
        markdown = understanding.timeline_markdown(result)
        self.assertIn("口播", markdown)
        self.assertIn("画面", markdown)

    def test_energy_beats_returns_candidates_not_bpm(self):
        samples = array("h", [0] * 8000)
        for offset in (1200, 3600, 6000):
            for index in range(offset, offset + 300):
                samples[index] = 5000
        beats = understanding.energy_beats(samples)
        self.assertTrue(beats)
        self.assertTrue(all(isinstance(value, float) for value in beats))


if __name__ == "__main__":
    unittest.main()
