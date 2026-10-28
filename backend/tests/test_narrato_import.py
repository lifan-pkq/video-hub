import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import narrato_import


class NarratoImportTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.tasks_dir = root / "tasks"
        self.videos_dir = root / "videos"
        self.covers_dir = root / "covers"
        self.tasks_dir.mkdir()
        self.videos_dir.mkdir()
        self.covers_dir.mkdir()
        self.existing = {}

        narrato_import._observed_sizes.clear()
        for target, value in (
            ("VIDEOS_DIR", self.videos_dir),
            ("COVERS_DIR", self.covers_dir),
        ):
            patcher = patch(f"app.services.narrato_import.{target}", value)
            patcher.start()
            self.addCleanup(patcher.stop)

        probe_patcher = patch(
            "app.services.narrato_import.probe_video",
            return_value={"duration": 4.0, "width": 640, "height": 360},
        )
        self.probe = probe_patcher.start()
        self.addCleanup(probe_patcher.stop)
        cover = patch("app.services.narrato_import.make_cover", return_value=True)
        cover.start()
        self.addCleanup(cover.stop)

        def fake_add_video(**fields):
            video = {"id": len(self.existing) + 1, **fields}
            self.existing[(fields["source_platform"], fields["source_url"])] = video
            return video

        add_video = patch("app.services.narrato_import.add_video", side_effect=fake_add_video)
        self.add_video = add_video.start()
        self.addCleanup(add_video.stop)
        get_video = patch(
            "app.services.narrato_import.get_video_by_source",
            side_effect=lambda platform, url: self.existing.get((platform, url)),
        )
        get_video.start()
        self.addCleanup(get_video.stop)

    def tearDown(self):
        narrato_import._observed_sizes.clear()
        self.tempdir.cleanup()

    def make_final(self, task_id="task-1234", content=b"video"):
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir()
        source = task_dir / "combined.mp4"
        source.write_bytes(content)
        return source

    def test_stable_final_imports_once_and_preserves_source(self):
        source = self.make_final()

        self.assertEqual(narrato_import.scan_once(self.tasks_dir), [])
        imported = narrato_import.scan_once(self.tasks_dir)
        self.assertEqual(len(imported), 1)
        self.assertEqual(narrato_import.scan_once(self.tasks_dir), [])

        self.assertEqual(self.add_video.call_count, 1)
        fields = self.add_video.call_args.kwargs
        self.assertEqual(fields["source_platform"], "narrato")
        self.assertEqual(fields["source_url"], "narrato://task/task-1234")
        self.assertTrue(source.exists())
        self.assertTrue((self.videos_dir / fields["filename"]).exists())

    def test_intermediate_files_are_ignored(self):
        task_dir = self.tasks_dir / "task-intermediate"
        task_dir.mkdir()
        (task_dir / "merger.mp4").write_bytes(b"video")
        (task_dir / "combined_without_auto_subtitles.mp4").write_bytes(b"video")

        self.assertEqual(narrato_import.scan_once(self.tasks_dir), [])
        self.assertEqual(narrato_import.scan_once(self.tasks_dir), [])
        self.add_video.assert_not_called()

    def test_changed_size_waits_for_another_stable_scan(self):
        source = self.make_final()
        narrato_import.scan_once(self.tasks_dir)
        source.write_bytes(b"video-with-more-data")

        self.assertEqual(narrato_import.scan_once(self.tasks_dir), [])
        self.assertEqual(len(narrato_import.scan_once(self.tasks_dir)), 1)

    def test_probe_failure_keeps_source_and_can_retry(self):
        source = self.make_final()
        narrato_import.scan_once(self.tasks_dir)
        self.probe.side_effect = RuntimeError("incomplete")
        self.assertEqual(narrato_import.scan_once(self.tasks_dir), [])
        self.assertTrue(source.exists())
        self.add_video.assert_not_called()

        self.probe.side_effect = None
        self.assertEqual(len(narrato_import.scan_once(self.tasks_dir)), 1)

    def test_existing_target_name_does_not_overwrite(self):
        self.make_final()
        (self.videos_dir / "narrato_task-1234.mp4").write_bytes(b"existing")
        narrato_import.scan_once(self.tasks_dir)

        imported = narrato_import.scan_once(self.tasks_dir)
        self.assertEqual(len(imported), 1)
        self.assertNotEqual(imported[0]["filename"], "narrato_task-1234.mp4")
        self.assertEqual((self.videos_dir / "narrato_task-1234.mp4").read_bytes(), b"existing")


if __name__ == "__main__":
    unittest.main()
