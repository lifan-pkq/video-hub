import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.audio_analysis import detect_silence, parse_silencedetect


class SilenceDetectParsingTests(unittest.TestCase):
    def test_parses_start_end_pairs_into_audio_suggestions(self):
        stderr = """
[Parsed_silencedetect_0 @ 0x1] silence_start: 1.25
[Parsed_silencedetect_0 @ 0x1] silence_end: 2.75 | silence_duration: 1.5
[Parsed_silencedetect_0 @ 0x1] silence_start: 8
[Parsed_silencedetect_0 @ 0x1] silence_end: 9.25 | silence_duration: 1.25
[Parsed_silencedetect_0 @ 0x1] silence_start: 20
"""

        items = parse_silencedetect(stderr)

        self.assertEqual([(item["start"], item["end"]) for item in items], [(1.25, 2.75), (8.0, 9.25)])
        for item in items:
            self.assertEqual(item["kind"], "silence")
            self.assertEqual(item["source"], "audio")
            self.assertTrue(item["reason"])

    def test_ffmpeg_uses_required_threshold_and_minimum_duration(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0,
            stderr="silence_start: 3\nsilence_end: 4.2 | silence_duration: 1.2\n",
        )
        with patch("app.services.audio_analysis.subprocess.run", return_value=completed) as run:
            items = detect_silence(Path("clip.mp4"))

        command = run.call_args.args[0]
        self.assertIn("silencedetect=noise=-35dB:d=0.6", command)
        self.assertEqual(command[0], "ffmpeg")
        self.assertEqual(items[0]["start"], 3.0)
        self.assertEqual(items[0]["end"], 4.2)

    def test_ffmpeg_failure_is_not_silently_treated_as_no_silence(self):
        completed = subprocess.CompletedProcess(args=[], returncode=1, stderr="decoder failed")
        with patch("app.services.audio_analysis.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "decoder failed"):
                detect_silence(Path("broken.mp4"))


if __name__ == "__main__":
    unittest.main()
