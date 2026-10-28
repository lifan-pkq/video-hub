import os
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.utils.video_processor import VideoProcessor


class VideoProcessorDocumentaryTests(unittest.TestCase):
    @patch.object(VideoProcessor, "_extract_frames_fast_path", return_value=["a.jpg"])
    def test_extract_frames_by_interval_prefers_fast_path(self, fast_path):
        processor = VideoProcessor.__new__(VideoProcessor)
        processor.video_path = "demo.mp4"
        processor.duration = 6.0
        processor.fps = 25.0

        result = processor.extract_frames_by_interval_with_fallback("/tmp/out", interval_seconds=3.0)

        self.assertEqual(["a.jpg"], result)
        fast_path.assert_called_once_with("/tmp/out", interval_seconds=3.0)

    def test_extract_frames_by_interval_falls_back_to_ultra_compatible(self):
        processor = VideoProcessor.__new__(VideoProcessor)
        processor.video_path = "demo.mp4"
        processor.duration = 6.0
        processor.fps = 25.0

        with TemporaryDirectory() as output_dir:
            expected_frame_path = os.path.join(output_dir, "keyframe_000000_000000000.jpg")

            def ultra_compatible_fallback(self, output_dir_arg, interval_seconds=5.0):
                with open(expected_frame_path, "wb") as frame_file:
                    frame_file.write(b"frame")
                return [0]

            with patch.object(VideoProcessor, "_extract_frames_fast_path", side_effect=RuntimeError("fast path failed")) as fast_path, patch.object(
                VideoProcessor,
                "extract_frames_by_interval_ultra_compatible",
                side_effect=ultra_compatible_fallback,
                autospec=True,
            ) as fallback:
                result = processor.extract_frames_by_interval_with_fallback(output_dir, interval_seconds=3.0)

        self.assertEqual([expected_frame_path], result)
        fast_path.assert_called_once_with(output_dir, interval_seconds=3.0)
        fallback.assert_called_once_with(processor, output_dir, interval_seconds=3.0)

    def test_extract_frames_by_interval_rejects_non_positive_interval(self):
        processor = VideoProcessor.__new__(VideoProcessor)
        processor.video_path = "demo.mp4"
        processor.duration = 6.0
        processor.fps = 25.0

        with patch.object(VideoProcessor, "extract_frames_by_interval_ultra_compatible", autospec=True) as fallback:
            with self.assertRaises(ValueError):
                processor.extract_frames_by_interval_with_fallback("/tmp/out", interval_seconds=0)

        fallback.assert_not_called()

    def test_extract_frames_by_interval_fallback_cleans_partial_fast_path_artifacts(self):
        processor = VideoProcessor.__new__(VideoProcessor)
        processor.video_path = "demo.mp4"
        processor.duration = 6.0
        processor.fps = 25.0

        with TemporaryDirectory() as output_dir:
            stale_fastframe = os.path.join(output_dir, "fastframe_000000.jpg")
            expected_keyframe = os.path.join(output_dir, "keyframe_000000_000000000.jpg")

            def fast_path_with_partial_output(_output_dir, interval_seconds=5.0):
                with open(stale_fastframe, "wb") as frame_file:
                    frame_file.write(b"stale")
                raise RuntimeError("simulated fast-path failure")

            def ultra_compatible_fallback(self, output_dir_arg, interval_seconds=5.0):
                with open(expected_keyframe, "wb") as frame_file:
                    frame_file.write(b"frame")
                return [0]

            with patch.object(VideoProcessor, "_extract_frames_fast_path", side_effect=fast_path_with_partial_output) as fast_path, patch.object(
                VideoProcessor,
                "extract_frames_by_interval_ultra_compatible",
                side_effect=ultra_compatible_fallback,
                autospec=True,
            ) as fallback:
                result = processor.extract_frames_by_interval_with_fallback(output_dir, interval_seconds=3.0)

            self.assertEqual([expected_keyframe], result)
            self.assertFalse(os.path.exists(stale_fastframe))
            fast_path.assert_called_once_with(output_dir, interval_seconds=3.0)
            fallback.assert_called_once_with(processor, output_dir, interval_seconds=3.0)
