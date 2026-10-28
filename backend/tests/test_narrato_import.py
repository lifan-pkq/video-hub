import sqlite3
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

        def fake_add_narrato_video(task_id, **fields):
            key = (fields["source_platform"], fields["source_url"])
            if key in self.existing:
                return self.existing[key]
            video = {"id": len(self.existing) + 1, **fields}
            self.existing[key] = video
            return video

        add_narrato_video = patch("app.services.narrato_import.add_narrato_video", side_effect=fake_add_narrato_video)
        self.add_narrato_video = add_narrato_video.start()
        self.addCleanup(add_narrato_video.stop)
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

        self.assertEqual(self.add_narrato_video.call_count, 1)
        fields = self.add_narrato_video.call_args.kwargs
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
        self.add_narrato_video.assert_not_called()

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
        self.add_narrato_video.assert_not_called()

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

    def test_source_change_during_copy_is_not_committed(self):
        source = self.make_final()
        narrato_import.scan_once(self.tasks_dir)
        original_copy = narrato_import.shutil.copy2

        def changing_copy(src, dst):
            original_copy(src, dst)
            source.write_bytes(b"changed-during-copy")

        with patch("app.services.narrato_import.shutil.copy2", side_effect=changing_copy):
            self.assertEqual(narrato_import.scan_once(self.tasks_dir), [])

        self.add_narrato_video.assert_not_called()
        self.assertEqual(list(self.videos_dir.iterdir()), [])


class NarratoImportDatabaseTests(unittest.TestCase):
    def test_task_ledger_and_video_insert_are_atomic(self):
        from app import db

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        db._init_schema(conn)
        fields = {
            "title": "AI 解说",
            "filename": "narrato_task.mp4",
            "source_platform": "narrato",
            "source_url": "narrato://task/task-1",
        }
        with patch("app.db._get_conn", return_value=conn):
            first = db.add_narrato_video("task-1", **fields)
            second = db.add_narrato_video("task-1", **fields)

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM narrato_imports").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
