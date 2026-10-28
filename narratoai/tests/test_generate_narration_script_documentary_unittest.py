import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.generate_narration_script import parse_frame_analysis_to_markdown


class GenerateNarrationMarkdownTests(unittest.TestCase):
    def test_markdown_keeps_batches_without_summary_and_sorts_by_time(self):
        artifact = {
            "batches": [
                {
                    "batch_index": 1,
                    "time_range": "00:00:03,000-00:00:06,000",
                    "overall_activity_summary": "人物转身跑向远处",
                    "fallback_summary": "",
                    "frame_observations": [
                        {
                            "timestamp": "00:00:03,000",
                            "observation": "人物突然回头",
                        }
                    ],
                },
                {
                    "batch_index": 0,
                    "time_range": "00:00:00,000-00:00:03,000",
                    "overall_activity_summary": "",
                    "fallback_summary": "原始响应回退摘要",
                    "frame_observations": [
                        {
                            "timestamp": "00:00:00,000",
                            "observation": "镜头里有一只猫",
                        }
                    ],
                },
            ]
        }

        with TemporaryDirectory() as temp_dir:
            analysis_path = Path(temp_dir) / "frame-analysis.json"
            analysis_path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
            markdown = parse_frame_analysis_to_markdown(str(analysis_path))

        first_range_index = markdown.find("00:00:00,000-00:00:03,000")
        second_range_index = markdown.find("00:00:03,000-00:00:06,000")

        self.assertIn("原始响应回退摘要", markdown)
        self.assertIn("镜头里有一只猫", markdown)
        self.assertIn("人物转身跑向远处", markdown)
        self.assertIn("人物突然回头", markdown)
        self.assertNotEqual(-1, first_range_index)
        self.assertNotEqual(-1, second_range_index)
        self.assertLess(first_range_index, second_range_index)


if __name__ == "__main__":
    unittest.main()
