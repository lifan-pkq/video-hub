"""封面导出的纯函数：不跑 ffmpeg。"""
import unittest
from unittest.mock import patch

from app.services.export_bundle import crop_filter, format_clock, parse_timestamp_range


class ExportBundlePureTests(unittest.TestCase):
    def test_clock_roundtrip(self):
        self.assertEqual(format_clock(65.25), "00:01:05,250")
        start, end = parse_timestamp_range("00:01:05,250-00:01:08,000")
        self.assertAlmostEqual(start, 65.25)
        self.assertAlmostEqual(end, 68.0)

    def test_center_crop_without_subject(self):
        graph, tracking = crop_filter(1920, 1080, "9:16", None)
        self.assertEqual(tracking, "safe_center")
        self.assertIn("crop=", graph)
        self.assertNotIn("subject", graph)

    def test_subject_track_moves_the_crop(self):
        graph, tracking = crop_filter(1920, 1080, "9:16", [{
            "timestamp": 1.5,
            "subject_box": {"x": 100, "y": 200, "width": 80, "height": 120},
        }])
        self.assertEqual(tracking, "subject_track")
        self.assertIn("x=", graph)

    def test_titles_fall_back_when_the_model_is_unavailable(self):
        from app.services.export_bundle import _titles

        with patch("app.services.export_bundle._model_titles", side_effect=RuntimeError("no model")):
            titles = _titles("原标题", [{"narration": "开头这句话很长很长"}], {})
        self.assertEqual(len(titles), 3)
        self.assertTrue(all(item["generated"] is False for item in titles))
        self.assertIn("开头", titles[0]["title"])


if __name__ == "__main__":
    unittest.main()
