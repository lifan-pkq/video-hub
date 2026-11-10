import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import understanding


class UnderstandingTests(unittest.TestCase):
    def test_extract_json_from_fence(self):
        self.assertEqual(understanding._extract_json('```json\n{"summary":"测试"}\n```')["summary"], "测试")

    @patch("app.services.understanding.update_video")
    @patch("app.services.understanding.get_video")
    @patch("app.services.understanding._text_summary", return_value={"summary": "陈默遇到回声", "best_segments": []})
    @patch("app.services.understanding._visual_understanding", return_value=([{"start": 0, "end": 2, "text": "算命摊"}], {"status": "ok"}))
    @patch("app.services.understanding.detect_beats", return_value=[1.2])
    @patch("app.services.understanding.analyze_loudness", return_value={"integrated_lufs": -16})
    @patch("app.services.understanding.detect_silence", return_value=[])
    @patch("app.services.understanding.detect_scene_cuts", return_value=[2.0])
    @patch("app.services.ai_edit.transcribe", return_value=[{"start": 0, "end": 2, "text": "你别急"}])
    def test_analyze_writes_unified_material_card(self, _transcribe, _cuts, _silence, _loudness, _beats, _visual, _summary, get_video, update):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.mp4"
            path.write_bytes(b"video")
            get_video.return_value = {"id": 1, "filename": path.name, "duration": 2}
            with patch("app.services.understanding.VIDEOS_DIR", Path(temp)), patch(
                "app.services.understanding.UNDERSTANDING_DIR", Path(temp) / "analysis"
            ):
                result = understanding.analyze_video(1)
        self.assertEqual(result["tracks"]["speech"][0]["text"], "你别急")
        self.assertEqual(result["tracks"]["visual"][0]["text"], "算命摊")
        self.assertEqual(result["tracks"]["audio"][0]["kind"], "beat")
        self.assertEqual(result["summary"], "陈默遇到回声")
        self.assertEqual(update.call_args.kwargs["analysis_status"], "success")


class TextSummaryBudgetTests(unittest.TestCase):
    def _speech(self, count):
        return [
            {"start": i * 3.0, "end": i * 3.0 + 2.8, "kind": "speech",
             "text": f"第{i}段口播：陈默在夜市摆摊，回声一次比一次清晰，他决定去查清这座城的旧事。"}
            for i in range(count)
        ]

    def test_long_material_is_split_within_budget_and_keeps_speech(self):
        speech = self._speech(200)
        visual = [{"start": i * 3.0 + 1.0, "end": i * 3.0 + 2.0, "kind": "visual", "text": "摊位", "ocr": []} for i in range(20)]
        batches = []
        raws = []
        prompts = []

        def fake_request(messages, *, vision=False):
            self.assertFalse(vision)
            prompt = messages[0]["content"]
            prompts.append(prompt)
            if prompt.startswith("这是同一条视频时间线的第"):
                raw = prompt.rsplit("\n", 1)[1]
                raws.append(raw)
                batches.append(json.loads(raw))
                return {"points": [{"start": 0, "end": 1, "note": "口播要点"}], "people": ["陈默"]}
            self.assertTrue(prompt.startswith("以下是同一条视频按时间分"))
            return {"summary": "口播结构已保留", "people": ["陈默"], "scenes": ["夜市"],
                    "emotion_tags": ["紧张"], "best_segments": [], "structure": [], "emotion_curve": []}

        with patch("app.services.understanding._request_json", side_effect=fake_request):
            result = understanding._text_summary(speech, visual)

        self.assertGreater(len(batches), 1, "长素材必须走分段路径")
        self.assertEqual(result["summary"], "口播结构已保留")
        flat = [entry for batch in batches for entry in batch]
        self.assertEqual(sum(1 for entry in flat if entry.get("kind") == "speech"), len(speech))
        self.assertEqual(flat[0]["text"], speech[0]["text"])
        self.assertEqual(flat[-1]["text"], speech[-1]["text"])
        self.assertEqual(len(prompts), len(batches) + 1)
        for raw in raws:
            self.assertLessEqual(len(raw), understanding.SUMMARY_BUDGET_CHARS, "每段提示词不得超过预算")

    def test_short_material_uses_single_conversation_turn(self):
        prompts = []

        def fake_request(messages, *, vision=False):
            prompts.append(messages[0]["content"])
            return {"summary": "短片", "people": [], "scenes": [], "emotion_tags": [],
                    "best_segments": [], "structure": [], "emotion_curve": []}

        with patch("app.services.understanding._request_json", side_effect=fake_request):
            result = understanding._text_summary([{"start": 0, "end": 2, "text": "你别急"}], [])

        self.assertEqual(result["summary"], "短片")
        self.assertEqual(len(prompts), 1)
        self.assertIn("你别急", prompts[0])

    def test_batches_never_exceed_budget(self):
        entries = [{"text": "x" * 5000} for _ in range(3)] + [{"text": "q" * 20000}]
        batches = understanding._timeline_batches(entries)
        self.assertGreaterEqual(len(batches), 2)
        for batch in batches:
            self.assertLessEqual(len(batch), understanding.SUMMARY_BUDGET_CHARS)
            json.loads(batch)  # 切段后仍是合法 JSON


class VisualAlignmentTests(unittest.TestCase):
    def _run(self, returned_frames, cuts):
        captured = {}

        def fake_frame_at(path, timestamp, target):
            target.write_bytes(b"jpeg")
            return True

        def fake_request(messages, *, vision=False):
            captured["vision"] = vision
            captured["prompt"] = messages[0]["content"]
            return {"frames": returned_frames, "summary": "全片一句话"}

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.mp4"
            path.write_bytes(b"video")
            with patch("app.services.understanding.VISION_MODEL", "vision-model"), \
                    patch("app.services.understanding.TMP_DIR", Path(temp)), \
                    patch("app.services.understanding._frame_at", side_effect=fake_frame_at), \
                    patch("app.services.understanding._request_json", side_effect=fake_request):
                tracks, result = understanding._visual_understanding(path, 80.0, cuts)
        return tracks, result, captured

    def test_misaligned_timestamps_land_on_nearest_shot_with_warning(self):
        frames = [{"timestamp": 1.3, "description": "夜市开场"}, {"timestamp": 41.9, "description": "算命摊"},
                  {"timestamp": 90.0, "description": "远跳的描述", "ocr": ["回声"]}]
        tracks, result, captured = self._run(frames, [20.0, 40.0, 60.0])
        self.assertTrue(captured["vision"])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(tracks), 3, "模型返回的帧都应落到轨迹上")
        self.assertTrue(all(track["kind"] == "visual" for track in tracks))
        aligned = {track["track_id"]: track["text"] for track in tracks}
        self.assertEqual(aligned["shot-3"], "远跳的描述")
        self.assertEqual(tracks[-1]["end"], 80.0)
        self.assertTrue(any("按最近镜头对齐" in item for item in result["warnings"]))

    def test_unmatchable_frames_record_warning_and_never_report_ok(self):
        frames = [{"timestamp": None, "description": "无时间戳"}, {"timestamp": "nan?", "description": "坏时间戳"}]
        tracks, result, _ = self._run(frames, [20.0, 40.0])
        self.assertEqual(tracks, [])
        self.assertNotEqual(result["status"], "ok")
        self.assertIn("没有返回可对齐的帧", result["reason"])
        self.assertTrue(any("未能落到轨迹" in item for item in result["warnings"]))


class StartAnalysisDedupeTests(unittest.TestCase):
    def test_running_video_does_not_start_second_thread(self):
        started = []

        class FakeThread:
            def __init__(self, *, target=None, daemon=None, name=None):
                self.target = target
                self.name = name

            def start(self):
                started.append(self.name)

        videos = [{"id": 7, "analysis_status": "running"}, {"id": 8, "analysis_status": "pending"}]
        with patch("app.services.understanding.list_videos", return_value=videos), \
                patch("app.services.understanding.threading.Thread", FakeThread):
            understanding.start_analysis(7)
            self.assertEqual(started, [], "已有 running 时不得再起线程")
            understanding.start_analysis(8)
            self.assertEqual(started, ["understanding-8"])
            understanding.start_analysis(7)
            self.assertEqual(started, ["understanding-8"], "重复点击 running 视频仍不新增线程")

    def test_worker_runs_analysis_once(self):
        calls = []

        class SyncThread:
            def __init__(self, *, target=None, daemon=None, name=None):
                self.target = target

            def start(self):
                self.target()

        with patch("app.services.understanding.list_videos", return_value=[{"id": 3, "analysis_status": "pending"}]), \
                patch("app.services.understanding.threading.Thread", SyncThread), \
                patch("app.services.understanding.analyze_video", side_effect=lambda video_id: calls.append(video_id)):
            understanding.start_analysis(3)
        self.assertEqual(calls, [3])


class InterruptedAnalysisRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        from app import db as db_module

        db_module._init_schema(self.conn)
        for video_id, status in ((1, "running"), (2, "success"), (3, "running")):
            self.conn.execute(
                "INSERT INTO videos (id, title, filename, analysis_status, created_at) VALUES (?,?,?,?,?)",
                (video_id, f"素材{video_id}", f"{video_id}.mp4", status, "2024-01-01T00:00:00"),
            )
        self.conn.commit()
        patcher = patch("app.db._get_conn", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.conn.close)

    def test_startup_hook_settles_stuck_running_rows(self):
        settled = understanding.fail_interrupted_analyses()
        self.assertEqual(settled, 2)
        statuses = {row[0]: row[1] for row in self.conn.execute("SELECT id, analysis_status FROM videos")}
        self.assertEqual(statuses, {1: "failed", 2: "success", 3: "failed"})
        self.assertEqual(list(self.conn.execute("SELECT analysis_error FROM videos WHERE id=1"))[0][0],
                         "服务重启，素材理解中断")

    def test_second_run_is_noop(self):
        understanding.fail_interrupted_analyses()
        self.assertEqual(understanding.fail_interrupted_analyses(), 0)


class EmptyVisualStatusTests(unittest.TestCase):
    @patch("app.services.understanding.update_video")
    @patch("app.services.understanding.get_video")
    @patch("app.services.understanding._text_summary", return_value={"summary": "只有口播"})
    @patch("app.services.understanding._visual_understanding",
           return_value=([], {"status": "ok", "analysis": {"summary": "画面摘要"}, "warnings": ["3 条帧描述与最近抽帧时间差超过 1.5 秒，已按最近镜头对齐"]}))
    @patch("app.services.understanding.detect_beats", return_value=[])
    @patch("app.services.understanding.analyze_loudness", return_value={})
    @patch("app.services.understanding.detect_silence", return_value=[])
    @patch("app.services.understanding.detect_scene_cuts", return_value=[])
    @patch("app.services.ai_edit.transcribe", return_value=[])
    def test_empty_tracks_never_marked_ok_and_alignment_warning_surfaces(
        self, _transcribe, _cuts, _silence, _loudness, _beats, _visual, _summary, get_video, update
    ):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "clip.mp4"
            path.write_bytes(b"video")
            get_video.return_value = {"id": 1, "filename": path.name, "duration": 2}
            with patch("app.services.understanding.VIDEOS_DIR", Path(temp)), \
                    patch("app.services.understanding.UNDERSTANDING_DIR", Path(temp) / "analysis"):
                result = understanding.analyze_video(1)
        self.assertEqual(result["tracks"]["visual"], [])
        self.assertNotEqual(result["visual_status"], "ok")
        self.assertEqual(result["visual_status"], "unknown")
        self.assertTrue(any("按最近镜头对齐" in item for item in result["warnings"]))
        self.assertEqual(update.call_args.kwargs["analysis_status"], "partial")


class PartialSegmentFailureTests(unittest.TestCase):
    def test_one_failed_segment_keeps_speech_structure(self):
        speech = [{"start": i * 3.0, "end": i * 3.0 + 2.0, "kind": "speech",
                   "text": f"第{i}段口播：夜里回声越来越清楚，他决定查清旧事。" * 3} for i in range(120)]
        calls = {"segment": 0}

        def fake_request(messages, *, vision=False):
            prompt = messages[0]["content"]
            if prompt.startswith("这是同一条视频时间线的第"):
                calls["segment"] += 1
                if calls["segment"] == 2:
                    raise understanding.UnderstandingError("HTTP 500")
                return {"points": [{"start": 0, "end": 1, "note": "口播要点"}], "people": ["陈默"]}
            self.assertIn("下列分段未能取出", prompt)
            return {"summary": "口播结构仍在", "people": ["陈默"], "scenes": [], "emotion_tags": [],
                    "best_segments": [], "structure": [], "emotion_curve": []}

        with patch("app.services.understanding._request_json", side_effect=fake_request):
            result = understanding._text_summary(speech, [])
        self.assertEqual(result["summary"], "口播结构仍在")
        self.assertGreater(calls["segment"], 2)

    def test_all_segments_failed_raises_instead_of_silent_visual_fallback(self):
        speech = [{"start": i * 3.0, "end": i * 3.0 + 2.0, "kind": "speech",
                   "text": f"第{i}段口播素材内容。" * 5} for i in range(120)]

        with patch("app.services.understanding._request_json",
                   side_effect=understanding.UnderstandingError("HTTP 500")):
            with self.assertRaises(understanding.UnderstandingError):
                understanding._text_summary(speech, [])


if __name__ == "__main__":
    unittest.main()