import json
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.routers import exports as exports_router
from app.services import exporter


def _manifest(files=None, archive="video-package.zip"):
    return {
        "schema_version": 2,
        "title": "测试成片",
        "files": files if files is not None else {
            "landscape": "landscape.mp4", "portrait": "portrait.mp4", "square": "square.mp4",
            "srt": "subtitles.srt", "covers": ["cover-1.jpg"], "titles": "titles.json",
        },
        "title_options": [{"kind": "hook", "title": "标题", "description": "描述"}],
        "selected_cover": "cover-1.jpg",
        "selected_title": "标题",
        "complete": True,
        "archive": archive,
        "archive_size": 128,
    }


class ExportServiceTests(unittest.TestCase):
    """exporter 服务层：异步派发、去重、状态收口、--subject-track 传参。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "videos").mkdir()
        (self.root / "videos" / "clip.mp4").write_bytes(b"video")
        (self.root / "exports").mkdir()
        (self.root / "understanding").mkdir()
        for name, value in (("EXPORTS_DIR", self.root / "exports"),
                            ("UNDERSTANDING_DIR", self.root / "understanding"),
                            ("VIDEOS_DIR", self.root / "videos")):
            item = patch.object(exporter, name, value, create=True)
            item.start()
            self.addCleanup(item.stop)
        item = patch.object(exporter, "_running", {}, create=True)
        item.start()
        self.addCleanup(item.stop)
        self.video = {"id": 7, "filename": "clip.mp4", "title": "测试成片", "source_url": ""}
        item = patch.object(exporter, "get_video", return_value=self.video)
        item.start()
        self.addCleanup(item.stop)

    def _fake_run(self, calls, *, returncode=0, stderr="", write_manifest=True, gate=None):
        def fake(source, work_dir, title, task_dir, subject_track, on_progress=None):
            calls.append({
                "source": source,
                "work_dir": work_dir,
                "title": title,
                "task_dir": task_dir,
                "subject_track": subject_track,
            })
            if gate is not None:
                gate.wait(10)
            if returncode:
                raise RuntimeError(stderr or "导出失败")
            if on_progress:
                on_progress(40, "生成画幅")
            if write_manifest:
                return _manifest()
            return {}
        return fake

    def test_create_returns_running_before_slow_export_finishes(self):
        gate = threading.Event()
        calls = []
        with patch.object(exporter, "run_bundle", side_effect=self._fake_run(calls, gate=gate)):
            record = exporter.create_export(7)
            self.assertEqual(record["status"], "running")
            self.assertTrue(record["export_id"])
            self._wait(lambda: len(calls) >= 1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(exporter.get_export(record["export_id"])["status"], "running")
            gate.set()
            for _ in range(100):
                if exporter.get_export(record["export_id"])["status"] != "running":
                    break
                threading.Event().wait(0.05)
        done = exporter.get_export(record["export_id"])
        self.assertEqual(done["status"], "success")
        self.assertEqual(done["archive"], "video-package.zip")

    def test_repeat_create_reuses_running_export_and_spawns_one_subprocess(self):
        gate = threading.Event()
        calls = []
        with patch.object(exporter, "run_bundle", side_effect=self._fake_run(calls, gate=gate)):
            first = exporter.create_export(7)
            second = exporter.create_export(7)
            self.assertEqual(first["export_id"], second["export_id"])
            self.assertEqual(second["status"], "running")
            self._wait(lambda: len(calls) >= 1)
            self.assertEqual(len(calls), 1)
            gate.set()
            self._wait(lambda: not exporter._running)
            third = exporter.create_export(7)
            self._wait(lambda: not exporter._running)
        self.assertNotEqual(third["export_id"], first["export_id"])
        self.assertEqual(len(calls), 2)

    def test_failed_export_reports_reason(self):
        calls = []
        with patch.object(exporter, "run_bundle",
                          side_effect=self._fake_run(calls, returncode=1, stderr="ffmpeg 挂了", write_manifest=False)):
            record = exporter.create_export(7)
            for _ in range(100):
                if exporter.get_export(record["export_id"])["status"] != "running":
                    break
                threading.Event().wait(0.05)
        failed = exporter.get_export(record["export_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("ffmpeg 挂了", failed["error"])

    def test_subject_track_passed_only_when_understanding_exists(self):
        calls = []
        with patch.object(exporter, "run_bundle", side_effect=self._fake_run(calls)):
            exporter.create_export(7)
            self._wait(lambda: not exporter._running)
            self.assertIsNone(calls[0]["subject_track"])
            track = exporter.UNDERSTANDING_DIR / "7.json"
            track.parent.mkdir(parents=True, exist_ok=True)
            track.write_text(json.dumps({"tracks": {"visual": [{"timestamp": 1, "subject_box": {}}]}}), encoding="utf-8")
            exporter.create_export(7)
            self._wait(lambda: len(calls) >= 2)
        self.assertEqual(calls[1]["subject_track"], track)

    def test_get_export_reconciles_stale_running_after_restart(self):
        export_id = uuid.uuid4().hex[:16]
        (exporter.EXPORTS_DIR / export_id).mkdir(parents=True, exist_ok=True)
        (exporter.EXPORTS_DIR / export_id / "status.json").write_text(
            json.dumps({"status": "running", "video_id": 7}), encoding="utf-8")
        record = exporter.get_export(export_id)
        self.assertEqual(record["status"], "failed")
        self.assertIn("重启", record["error"])

    def _wait(self, done):
        for _ in range(100):
            if done():
                return
            threading.Event().wait(0.05)
        raise AssertionError("导出线程没有在预期时间内进入预期状态")


class ExportRouterTests(unittest.TestCase):
    """HTTP 层：POST 立刻 202/200 返回 running，GET 反映终态。"""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init_schema(self.conn)
        item = patch("app.db._get_conn", return_value=self.conn)
        item.start()
        self.addCleanup(item.stop)
        self.addCleanup(self.conn.close)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "videos").mkdir()
        (self.root / "exports").mkdir()
        (self.root / "understanding").mkdir()
        (self.root / "videos" / "clip.mp4").write_bytes(b"video")
        for name, value in (("EXPORTS_DIR", self.root / "exports"),
                            ("UNDERSTANDING_DIR", self.root / "understanding"),
                            ("VIDEOS_DIR", self.root / "videos"),
                            ("_running", {})):
            patcher = patch.object(exporter, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)
        item = patch.object(exporter, "get_video", return_value={
            "id": 7, "filename": "clip.mp4", "title": "测试成片", "source_url": ""})
        item.start()
        self.addCleanup(item.stop)
        self.client = TestClient(app)

    def test_post_returns_running_immediately(self):
        gate = threading.Event()
        calls = []

        def fake(source, work_dir, title, task_dir, subject_track, on_progress=None):
            calls.append(work_dir)
            gate.wait(10)
            return _manifest()

        with patch.object(exporter, "run_bundle", side_effect=fake):
            response = self.client.post("/api/exports", json={"video_id": 7})
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["status"], "running")
            self.assertTrue(body["export_id"])
            for _ in range(100):
                if calls:
                    break
                threading.Event().wait(0.05)
            self.assertEqual(len(calls), 1)
            again = self.client.post("/api/exports", json={"video_id": 7})
            self.assertEqual(again.json()["export_id"], body["export_id"])
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.client.get(f"/api/exports/{body['export_id']}").json()["status"], "running")
            gate.set()
            for _ in range(100):
                poll = self.client.get(f"/api/exports/{body['export_id']}")
                if poll.json()["status"] != "running":
                    break
                threading.Event().wait(0.05)
        done = self.client.get(f"/api/exports/{body['export_id']}").json()
        self.assertEqual(done["status"], "success")
        self.assertEqual(done["files"]["landscape"], "landscape.mp4")
        self.assertEqual(done["download_url"], f"/api/exports/{body['export_id']}/download")

    def test_get_reports_failure_with_reason(self):
        def fake(source, work_dir, title, task_dir, subject_track, on_progress=None):
            raise RuntimeError("导出进程爆炸了")

        with patch.object(exporter, "run_bundle", side_effect=fake):
            export_id = self.client.post("/api/exports", json={"video_id": 7}).json()["export_id"]
            for _ in range(100):
                poll = self.client.get(f"/api/exports/{export_id}")
                if poll.json()["status"] != "running":
                    break
                threading.Event().wait(0.05)
        body = self.client.get(f"/api/exports/{export_id}").json()
        self.assertEqual(body["status"], "failed")
        self.assertIn("导出进程爆炸了", body["error"])

    def test_get_unknown_export_is_404(self):
        self.assertEqual(self.client.get("/api/exports/0000000000000000").status_code, 404)


if __name__ == "__main__":
    unittest.main()
