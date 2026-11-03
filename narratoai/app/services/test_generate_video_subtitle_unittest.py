import unittest

from app.services.generate_video import _select_subtitle_renderer


class SubtitleRendererTests(unittest.TestCase):
    def test_png_fallback_avoids_libass_when_drawtext_is_unavailable(self):
        renderer = _select_subtitle_renderer(
            has_drawtext=False,
            has_subtitles=True,
            has_overlay=True,
        )
        self.assertEqual(renderer, "png")

    def test_libass_is_last_resort_when_png_overlay_is_unavailable(self):
        renderer = _select_subtitle_renderer(
            has_drawtext=False,
            has_subtitles=True,
            has_overlay=False,
        )
        self.assertEqual(renderer, "subtitles")


if __name__ == "__main__":
    unittest.main()
