import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine import quality


BLACK_LINE = "[Parsed_blackdetect_0 @ 0x1] black_start:0 black_end:{end} black_duration:{end}"
PEAK_LINE = "[Parsed_ebur128_1 @ 0x1]     Peak:      {peak} dBFS"
LOUDNESS_LINE = "[Parsed_ebur128_1 @ 0x1] t: 1.0   I: -20.0 LUFS"
PEAK_OK = PEAK_LINE.format(peak="-1.2")
PEAK_CLIPPED = PEAK_LINE.format(peak="-0.02")
SILENCE_WITHOUT_END = "silence_start: 7"


class QualityGateTests(unittest.TestCase):
    def _spec(self):
        return {"video_duration": 10, "subtitle_max_chars": 12}

    def _script(self, flags=None):
        return [{
            "_id": 1,
            "segment_id": "opening",
            "timestamp": "00:00:00,000-00:00:05,000",
            "story_role": "hook",
            "narration": "他停在镜子前。",
            "estimated_duration": 2,
            "quality_flags": flags or [],
        }]

    def _fake_run(self, stderr="", stdout=""):
        class Result:
            returncode = 0
        result = Result()
        result.stderr = stderr
        result.stdout = stdout
        return result

    def _report_from_stderr(self, stderr, duration=10):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "out.mp4"
            video.write_bytes(b"fake")
            with patch("engine.quality.subprocess.run", return_value=self._fake_run(stderr=stderr)):
                return quality._video_report(video), quality._audio_report(video, duration)

    @patch("engine.quality._video_report", return_value=[{"check": "visual", "severity": "ok", "message": "ok", "details": {}}])
    @patch("engine.quality._audio_report", return_value=[{"check": "audio", "severity": "ok", "message": "ok", "details": {}}])
    def test_clean_render_passes(self, _audio, _video):
        with tempfile.TemporaryDirectory() as temp:
            report = quality.evaluate(Path(temp), self._spec(), self._script(), Path(temp) / "combined.mp4")
        self.assertTrue(report["passed"])

    @patch("engine.quality._video_report", return_value=[{"check": "visual", "severity": "ok", "message": "ok", "details": {}}])
    @patch("engine.quality._audio_report", return_value=[{"check": "audio", "severity": "ok", "message": "ok", "details": {}}])
    def test_unresolved_script_flag_blocks_success(self, _audio, _video):
        with tempfile.TemporaryDirectory() as temp:
            report = quality.evaluate(Path(temp), self._spec(), self._script(["rewrite_missing"]), Path(temp) / "combined.mp4")
        self.assertFalse(report["passed"])
        self.assertEqual(report["status"], "review")

    def test_long_black_frame_is_serious(self):
        video_issues, _ = self._report_from_stderr(BLACK_LINE.format(end=3))
        self.assertEqual(video_issues[0]["severity"], "error")
        self.assertEqual(video_issues[0]["details"]["black_seconds"], 3.0)

    def test_short_black_frame_is_only_a_warning(self):
        video_issues, _ = self._report_from_stderr(BLACK_LINE.format(end=1))
        self.assertEqual([item["severity"] for item in video_issues], ["warning"])
        self.assertEqual(video_issues[0]["details"]["black_seconds"], 1.0)

    def test_black_frame_without_duration_is_unknown(self):
        video_issues, _ = self._report_from_stderr("black_start:0")
        self.assertEqual([item["severity"] for item in video_issues], ["unknown"])

    def test_mastering_peak_is_not_an_error(self):
        _, audio_issues = self._report_from_stderr("\n".join([LOUDNESS_LINE, PEAK_OK]))
        self.assertEqual([item["severity"] for item in audio_issues], ["ok"])

    def test_true_clipping_peak_is_an_error(self):
        _, audio_issues = self._report_from_stderr("\n".join([LOUDNESS_LINE, PEAK_CLIPPED]))
        self.assertEqual([item["severity"] for item in audio_issues], ["error"])

    def test_trailing_silence_without_end_counts(self):
        stderr = "\n".join(["silence_start: 7", LOUDNESS_LINE])
        _, audio_issues = self._report_from_stderr(stderr, duration=12)
        self.assertEqual(audio_issues[0]["severity"], "error")
        self.assertEqual(audio_issues[0]["details"]["longest_silence"], 5.0)

    def test_render_duration_drives_silence_threshold(self):
        stderr = "\n".join(["silence_start: 7", LOUDNESS_LINE])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "combined.mp4"
            output.write_bytes(b"fake")
            with patch("engine.quality.subprocess.run", return_value=self._fake_run(stderr=stderr)):
                short_source = quality.evaluate(Path(temp), self._spec(), self._script(), output, 12.0)
                long_source = quality.evaluate(
                    Path(temp), {**self._spec(), "video_duration": 600}, self._script(), output, 12.0
                )
        first = [item for item in short_source["checks"] if item["check"] == "audio"]
        second = [item for item in long_source["checks"] if item["check"] == "audio"]
        self.assertEqual(first, second)
        self.assertEqual(first[0]["details"]["longest_silence"], 5.0)

    def test_source_duration_still_bounds_script_timestamps(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "combined.mp4"
            output.write_bytes(b"fake")
            with patch("engine.quality._video_report", return_value=[{"check": "visual", "severity": "ok", "message": "ok", "details": {}}]), patch(
                "engine.quality._audio_report", return_value=[{"check": "audio", "severity": "ok", "message": "ok", "details": {}}]
            ):
                report = quality.evaluate(Path(temp), {"video_duration": 3}, self._script(), output, 600.0)
        alignment = [item for item in report["checks"] if item["check"] == "alignment"]
        self.assertEqual([item["severity"] for item in alignment], ["error"])

    def test_missing_subtitle_timeline_is_unknown(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "combined.mp4"
            output.write_bytes(b"fake")
            with patch("engine.quality._video_report", return_value=[{"check": "visual", "severity": "ok", "message": "ok", "details": {}}]), patch(
                "engine.quality._audio_report", return_value=[{"check": "audio", "severity": "ok", "message": "ok", "details": {}}]
            ):
                report = quality.evaluate(Path(temp), self._spec(), self._script(), output, 10.0)
        subtitles = [item for item in report["checks"] if item["check"] == "subtitle"]
        self.assertEqual([item["severity"] for item in subtitles], ["unknown"])

    def test_real_subtitle_timing_reports_short_cue(self):
        with tempfile.TemporaryDirectory() as temp:
            task_dir = Path(temp)
            output = task_dir / "combined.mp4"
            output.write_bytes(b"fake")
            (task_dir / "script_subtitles.srt").write_text(
                "1\n00:00:00,000 --> 00:00:04,000\n这是一句正常的字幕\n\n"
                "2\n00:00:04,000 --> 00:00:04,200\n短\n",
                encoding="utf-8",
            )
            with patch("engine.quality._video_report", return_value=[{"check": "visual", "severity": "ok", "message": "ok", "details": {}}]), patch(
                "engine.quality._audio_report", return_value=[{"check": "audio", "severity": "ok", "message": "ok", "details": {}}]
            ):
                report = quality.evaluate(task_dir, self._spec(), self._script(), output, 10.0)
        subtitles = [item for item in report["checks"] if item["check"] == "subtitle"]
        self.assertEqual([item["severity"] for item in subtitles], ["warning"])
        self.assertEqual(subtitles[0]["details"]["shortest_seconds"], 0.2)

    def test_freeze_running_to_end_of_file_is_measured(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "out.mp4"
            video.write_bytes(b"fake")
            with patch("engine.quality.subprocess.run", return_value=self._fake_run(stderr="lavfi.freezedetect.freeze_start: 3")):
                issues = quality._video_report(video, 12.0)
        self.assertEqual([item["severity"] for item in issues], ["warning"])
        self.assertEqual(issues[0]["details"]["freeze_seconds"], 9.0)

    def test_static_content_freeze_is_relaxed(self):
        freeze = "lavfi.freezedetect.freeze_start: 0\nfreeze_duration: 8\nfreeze_end: 8"
        video_issues, _ = self._report_from_stderr(freeze)
        self.assertEqual([item["severity"] for item in video_issues], ["warning"])
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / "out.mp4"
            video.write_bytes(b"fake")
            with patch("engine.quality.subprocess.run", return_value=self._fake_run(stderr=freeze)):
                relaxed = quality._video_report(video, content_type="tutorial")
        self.assertEqual([item["severity"] for item in relaxed], ["ok"])


if __name__ == "__main__":
    unittest.main()
