import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import ai_drama, drama_pipeline, drama_store
from app.services.media_generation import GeneratedMedia, GenerationCancelled
from app.services.media_quality import QualityResult, QualityUnavailable


PROJECT_ID = "abc123def456"


def project_fixture(**overrides):
    project = {
        "id": PROJECT_ID,
        "title": "项目",
        "status": "running",
        "options": {"quality_threshold": 70, "aspect_ratio": "9:16"},
        "episodes": [{
            "index": 1,
            "status": "storyboard",
            "shots": [{
                "id": "e1s1",
                "index": 1,
                "revision": 1,
                "status": "pending",
                "prompt": "角色转身",
                "scene": "房间",
                "action": "转身",
                "expression": "平静",
                "camera": "固定",
                "seconds": 5,
                "dialogue": "",
                "characters": [],
            }],
        }],
        "characters": [],
        "logs": [],
        "created_at": "2026-01-01 00:00:00",
        "cancel_requested": False,
    }
    project.update(overrides)
    return project


class DramaPipelineTests(unittest.TestCase):
    def tearDown(self):
        with drama_pipeline._SHOT_RUNTIMES_LOCK:
            for runtime in drama_pipeline._SHOT_RUNTIMES.values():
                runtime.cancel.set()
            drama_pipeline._SHOT_RUNTIMES.clear()

    @patch("app.services.drama_pipeline._spawn_shot")
    def test_all_shots_start_before_waiting(self, spawn):
        barrier = threading.Barrier(3, timeout=5)

        def spawn_worker(project_id, shot_id, stage, **_kwargs):
            worker = threading.Thread(target=barrier.wait)
            worker.start()
            return worker

        spawn.side_effect = spawn_worker
        shots = [{"id": "e1s1"}, {"id": "e1s2"}, {"id": "e1s3"}]
        drama_pipeline.run_video_workers(PROJECT_ID, shots)
        self.assertEqual(spawn.call_count, 3)

    def test_low_scores_retry_until_threshold(self):
        media = GeneratedMedia(Path("shot.mp4"), "", "test", {})
        generate = Mock(return_value=media)
        score = Mock(side_effect=[
            QualityResult(60, False, ["动作不符"], "vision"),
            QualityResult(70, True, [], "vision"),
        ])
        record = Mock()
        result_media, result_quality = drama_pipeline.run_quality_loop(
            prompt="原始提示词", generate=generate, score=score, threshold=70,
            should_cancel=lambda: False, record=record,
        )
        self.assertIs(result_media, media)
        self.assertEqual(result_quality.score, 70)
        self.assertEqual(generate.call_count, 2)
        self.assertIn("动作不符", generate.call_args_list[1].args[0])

    def test_quality_loop_has_no_maximum(self):
        media = GeneratedMedia(Path("shot.mp4"), "", "test", {})
        generate = Mock(return_value=media)
        score = Mock(side_effect=[
            *[QualityResult(10, False, ["继续改进"], "vision") for _ in range(12)],
            QualityResult(80, True, [], "vision"),
        ])
        _, quality = drama_pipeline.run_quality_loop(
            prompt="提示词", generate=generate, score=score, threshold=70,
            should_cancel=lambda: False, record=Mock(),
        )
        self.assertEqual(quality.score, 80)
        self.assertEqual(generate.call_count, 13)

    def test_cancelled_loop_does_not_start_another_attempt(self):
        generate = Mock()
        with self.assertRaises(GenerationCancelled):
            drama_pipeline.run_quality_loop(
                prompt="提示词", generate=generate, score=Mock(), threshold=70,
                should_cancel=lambda: True, record=Mock(),
            )
        generate.assert_not_called()

    def test_cancel_after_low_score_does_not_retry(self):
        media = GeneratedMedia(Path("shot.mp4"), "", "test", {})
        generate = Mock(return_value=media)
        should_cancel = Mock(side_effect=[False, False, False, True])
        with self.assertRaises(GenerationCancelled):
            drama_pipeline.run_quality_loop(
                prompt="提示词", generate=generate,
                score=Mock(return_value=QualityResult(60, False, ["重拍"], "vision")),
                threshold=70, should_cancel=should_cancel, record=Mock(),
            )
        generate.assert_called_once()

    def test_quality_unavailable_is_not_a_pass(self):
        media = GeneratedMedia(Path("shot.mp4"), "", "test", {})
        with self.assertRaises(QualityUnavailable):
            drama_pipeline.run_quality_loop(
                prompt="提示词", generate=Mock(return_value=media),
                score=Mock(side_effect=QualityUnavailable("评分服务不可用")), threshold=70,
                should_cancel=lambda: False, record=Mock(),
            )

    def test_only_current_qualified_video_can_compose(self):
        shot = {
            "revision": 2,
            "video": {"status": "ready", "revision": 2, "quality": {"passed": True}},
        }
        self.assertTrue(drama_pipeline.shot_video_eligible(shot, 2))
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 3))
        shot["video"]["quality"]["passed"] = False
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 2))
        shot["video"]["quality"]["passed"] = True
        shot["video"]["status"] = "stale"
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 2))

    def test_only_explicit_legacy_artifact_can_skip_quality(self):
        shot = {
            "revision": 1,
            "video": {
                "status": "ready", "revision": 1, "quality": None,
                "legacy_accepted": True,
            },
        }
        self.assertTrue(drama_pipeline.shot_video_eligible(shot, 1))
        shot["video"]["legacy_accepted"] = False
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 1))
        shot["video"]["legacy_accepted"] = True
        shot["video"]["quality"] = {"passed": False}
        self.assertFalse(drama_pipeline.shot_video_eligible(shot, 1))

    def test_project_cancel_cancels_current_shot(self):
        runtime = SimpleNamespace(cancel=threading.Event())
        with patch.dict(drama_pipeline._SHOT_RUNTIMES, {(PROJECT_ID, "e1s1"): runtime}, clear=True), \
             patch("app.services.drama_pipeline.drama_store.mark_project_cancelled"):
            drama_pipeline.cancel_project(PROJECT_ID)
        self.assertTrue(runtime.cancel.is_set())

    def test_shot_cancel_marks_episode_waiting_without_quality_retry(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            generate = Mock()
            with patch("app.services.drama_pipeline._run_video_loop") as loop:
                loop.side_effect = lambda *_args: (_ for _ in ()).throw(GenerationCancelled())
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video")
            project = drama_store.load_project(PROJECT_ID)
            self.assertEqual(project["episodes"][0]["status"], "waiting_review")
            self.assertEqual(project["episodes"][0]["shots"][0]["status"], "cancelled")
            generate.assert_not_called()

    def test_retry_shot_reuses_internal_worker_without_global_task(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="waiting_review")
            project["episodes"][0]["status"] = "waiting_review"
            project["episodes"][0]["shots"][0]["status"] = "cancelled"
            drama_store.save_project(project)
            with patch("app.services.drama_pipeline._spawn_shot") as spawn, \
                    patch("app.services.ai_drama.task_manager.create") as create:
                drama_pipeline.retry_shot(PROJECT_ID, "e1s1", "video")
            spawn.assert_called_once_with(PROJECT_ID, "e1s1", "video")
            create.assert_not_called()
            saved = drama_store.load_project(PROJECT_ID)
            self.assertEqual(saved["episodes"][0]["shots"][0]["status"], "pending")
            self.assertEqual(saved["task_id"], "drama-abc123def456")

    def test_video_worker_accepts_only_scored_current_artifact(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture()
            shot = project["episodes"][0]["shots"][0]
            shot["image"] = {
                "name": "e1s1.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True, "reasons": [], "checker": "vision"},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "e1s1.jpg").write_bytes(b"image")
            source = Path(temp) / "generated.mp4"
            source.write_bytes(b"video")
            media = GeneratedMedia(source, "", "test", {})
            with patch(
                "app.services.drama_pipeline.media_generation.generate_video_asset",
                return_value=media,
            ) as generate, patch(
                "app.services.drama_pipeline.media_quality.score_video",
                return_value=QualityResult(85, True, [], "vision"),
            ), patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"):
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video")
            saved = drama_store.load_project(PROJECT_ID)
            video = saved["episodes"][0]["shots"][0]["video"]
            self.assertEqual(video["status"], "ready")
            self.assertEqual(video["quality"]["score"], 85)
            self.assertEqual(video["attempts"], 1)
            self.assertTrue((assets / video["name"]).is_file())
            self.assertFalse(source.exists())
            generate.assert_called_once()

    def test_revision_change_during_generation_discards_late_result(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture()
            shot = project["episodes"][0]["shots"][0]
            shot["image"] = {
                "name": "e1s1.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True, "reasons": [], "checker": "vision"},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "e1s1.jpg").write_bytes(b"image")
            source = Path(temp) / "late.mp4"
            source.write_bytes(b"video")

            def generate(*_args, **_kwargs):
                def edit(current):
                    current["episodes"][0]["shots"][0]["revision"] = 2
                drama_store.update_project(PROJECT_ID, edit)
                return GeneratedMedia(source, "", "test", {})

            with patch(
                "app.services.drama_pipeline.media_generation.generate_video_asset",
                side_effect=generate,
            ), patch(
                "app.services.drama_pipeline.media_quality.score_video",
                return_value=QualityResult(90, True, [], "vision"),
            ), patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"):
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video")
            saved = drama_store.load_project(PROJECT_ID)
            self.assertEqual(saved["episodes"][0]["shots"][0]["revision"], 2)
            self.assertFalse(saved["episodes"][0]["shots"][0]["video"]["name"])
            self.assertFalse(source.exists())
            self.assertFalse(list(assets.glob("*_video.mp4")))

    def test_accept_then_cancel_cannot_compose_or_accept_again(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            accepted = Path(temp) / "accepted.mp4"
            accepted.write_bytes(b"video")
            self.assertTrue(drama_store.accept_artifact(PROJECT_ID, "e1s1", "video", 1, {
                "name": "e1s1.mp4", "path": str(accepted), "status": "ready",
                "quality": {"score": 80, "passed": True, "reasons": [], "checker": "vision"},
            }))
            drama_pipeline.cancel_shot(PROJECT_ID, "e1s1")
            saved = drama_store.load_project(PROJECT_ID)
            shot = saved["episodes"][0]["shots"][0]
            self.assertEqual(shot["status"], "cancelled")
            self.assertFalse(drama_pipeline.shot_video_eligible(shot, 1))
            with self.assertRaises(drama_pipeline.WaitingReview):
                drama_pipeline._check_stage_result(PROJECT_ID, "video")
            late = Path(temp) / "late.mp4"
            late.write_bytes(b"late")
            self.assertFalse(drama_store.accept_artifact(PROJECT_ID, "e1s1", "video", 1, {
                "name": "late.mp4", "path": str(late), "status": "ready",
                "quality": {"score": 90, "passed": True},
            }))
            self.assertFalse(late.exists())

    def test_permanent_generation_error_marks_failed_without_retry(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture()
            shot = project["episodes"][0]["shots"][0]
            shot["image"] = {
                "name": "e1s1.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True, "reasons": [], "checker": "vision"},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "e1s1.jpg").write_bytes(b"image")
            with patch(
                "app.services.drama_pipeline.media_generation.generate_video_asset",
                side_effect=drama_pipeline.MediaAIError("HTTP 422：提示词无效"),
            ) as generate, patch(
                "app.services.drama_pipeline._wait_before_retry",
                side_effect=AssertionError("永久错误不得进入重试等待"),
            ) as wait, patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"):
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video")
            saved = drama_store.load_project(PROJECT_ID)
            shot = saved["episodes"][0]["shots"][0]
            self.assertEqual(shot["status"], "failed")
            self.assertTrue(shot["recoverable"])
            self.assertIn("422", shot["error"])
            generate.assert_called_once()
            wait.assert_not_called()

    def test_transient_generation_error_uses_cancellable_retry(self):
        media = GeneratedMedia(Path("shot.mp4"), "", "test", {})
        generate = Mock(side_effect=[
            drama_pipeline.media_generation.TransientMediaAIError("HTTP 429"),
            media,
        ])
        with patch("app.services.drama_pipeline._wait_before_retry") as wait:
            result, quality = drama_pipeline.run_quality_loop(
                prompt="提示词", generate=generate,
                score=Mock(return_value=QualityResult(80, True, [], "vision")),
                threshold=70, should_cancel=lambda: False, record=Mock(),
            )
        self.assertIs(result, media)
        self.assertEqual(quality.score, 80)
        self.assertEqual(generate.call_count, 2)
        wait.assert_called_once()

    def test_scorer_outage_marks_recoverable_failure(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            with patch(
                "app.services.drama_pipeline._run_video_loop",
                side_effect=QualityUnavailable("评分服务不可用"),
            ):
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video")
            project = drama_store.load_project(PROJECT_ID)
            shot = project["episodes"][0]["shots"][0]
            self.assertEqual(shot["status"], "failed")
            self.assertTrue(shot["recoverable"])
            self.assertIn("评分服务不可用", shot["error"])

    def test_revision_safe_acceptance_deletes_late_file(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            late = Path(temp) / "late.mp4"
            late.write_bytes(b"late")
            accepted = drama_store.accept_artifact(PROJECT_ID, "e1s1", "video", 2, {
                "name": "late.mp4", "path": str(late), "status": "ready",
                "quality": {"passed": True},
            })
            self.assertFalse(accepted)
            self.assertFalse(late.exists())
            self.assertFalse(drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]["video"]["name"])

    def test_current_qualified_artifact_is_accepted_atomically(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            current = Path(temp) / "current.mp4"
            current.write_bytes(b"current")
            accepted = drama_store.accept_artifact(PROJECT_ID, "e1s1", "video", 1, {
                "name": "e1s1.mp4", "path": str(current), "status": "ready",
                "quality": {"score": 80, "passed": True, "reasons": [], "checker": "vision"},
                "attempts": 2,
            })
            project = drama_store.load_project(PROJECT_ID)
            video = project["episodes"][0]["shots"][0]["video"]
            self.assertTrue(accepted)
            self.assertEqual(video["name"], "e1s1.mp4")
            self.assertEqual(video["revision"], 1)
            self.assertEqual(video["attempts"], 2)
            self.assertFalse(video["legacy_accepted"])
            self.assertNotIn("path", video)

    def test_video_stage_waits_for_all_current_qualified_images(self):
        project = project_fixture()
        project["episodes"][0]["shots"][0]["image"] = {
            "name": "e1s1.jpg", "status": "ready", "revision": 1,
            "quality": {"passed": False},
        }
        with patch("app.services.drama_pipeline.drama_store.load_project", return_value=project), \
                patch("app.services.drama_pipeline.run_video_workers") as workers:
            with self.assertRaises(drama_pipeline.DramaPipelineError):
                drama_pipeline._run_video_stage(PROJECT_ID)
            workers.assert_not_called()

    def test_cancelled_shot_is_not_restarted_by_video_stage_scan(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="waiting_review")
            shot = project["episodes"][0]["shots"][0]
            shot["status"] = "cancelled"
            shot["image"] = {
                "name": "e1s1.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True, "reasons": [], "checker": "vision"},
            }
            drama_store.save_project(project)
            with patch("app.services.drama_pipeline.run_video_workers") as workers:
                with self.assertRaises(drama_pipeline.WaitingReview):
                    drama_pipeline._run_video_stage(PROJECT_ID)
            workers.assert_called_once_with(PROJECT_ID, [])

    def test_character_anchor_uses_quality_loop(self):
        character = {
            "id": "c1", "name": "角色", "age": 20, "gender": "女",
            "appearance": "短发", "clothing": "外套", "personality": "冷静",
            "profile_id": "profile-c1", "anchor": "", "status": "pending",
        }
        project = project_fixture(characters=[character], options={
            "quality_threshold": 70, "aspect_ratio": "9:16", "style": "realistic",
        })
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project)
            source = Path(temp) / "anchor.png"
            source.write_bytes(b"image")
            media = GeneratedMedia(source, "", "test", {})
            quality = QualityResult(80, True, [], "vision")
            with patch("app.services.drama_pipeline.run_quality_loop", return_value=(media, quality)) as loop, \
                    patch("app.services.ai_drama._to_jpeg", return_value=source), \
                    patch("app.services.ai_drama.profiles.save_reference", return_value={"name": "ref.jpg"}), \
                    patch("app.services.ai_drama.profiles.save_profile"), \
                    patch("app.services.ai_drama.ai_media.generate_image") as ordinary_generate:
                ai_drama._build_characters(project)
            loop.assert_called_once()
            ordinary_generate.assert_not_called()
            self.assertEqual(project["characters"][0]["quality"]["score"], 80)

    def test_compose_rejects_unqualified_or_missing_quality_video(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = drama_store.save_project(project_fixture())
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "e1s1.mp4").write_bytes(b"video")
            shot = project["episodes"][0]["shots"][0]
            shot["video"] = {
                "name": "e1s1.mp4", "status": "ready", "revision": 1, "quality": None,
                "legacy_accepted": False,
            }
            with patch("app.services.ai_drama.subprocess.run") as run:
                with self.assertRaisesRegex(ai_drama.DramaError, "质量"):
                    ai_drama._compose(project)
            run.assert_not_called()

    def test_resume_reuses_parent_and_does_not_create_shot_tasks(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture(status="cancelled", cancel_requested=True))
            with patch.object(ai_drama.threading, "Thread") as thread, \
                    patch.object(ai_drama.task_manager, "create") as create, \
                    patch.object(ai_drama.ai_media, "start_video_generation") as ordinary_start:
                thread.return_value.is_alive.return_value = False
                ai_drama.resume_project(PROJECT_ID)
            project = drama_store.load_project(PROJECT_ID)
            self.assertEqual(project["task_id"], "drama-abc123def456")
            self.assertFalse(project["cancel_requested"])
            create.assert_not_called()
            ordinary_start.assert_not_called()

    def test_project_cancel_is_persistent(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            drama_pipeline.cancel_project(PROJECT_ID)
            self.assertTrue(drama_store.project_cancelled(PROJECT_ID))
            self.assertEqual(drama_store.load_project(PROJECT_ID)["status"], "cancelled")

    def test_explicit_retry_invalidates_requested_artifacts(self):
        for stage in ("image", "video", "all"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as temp, \
                    patch.object(drama_store, "DRAMA_DIR", Path(temp)):
                project = project_fixture(status="success")
                shot = project["episodes"][0]["shots"][0]
                shot["status"] = "cancelled" if stage == "video" else "done"
                shot["image"] = {
                    "name": "old.jpg", "status": "ready", "revision": 1,
                    "quality": {"score": 80, "passed": True},
                }
                shot["video"] = {
                    "name": "old.mp4", "status": "ready", "revision": 1,
                    "quality": {"score": 80, "passed": True},
                }
                drama_store.save_project(project)
                with patch("app.services.drama_pipeline._spawn_shot"):
                    drama_pipeline.retry_shot(PROJECT_ID, "e1s1", stage)
                saved = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
                if stage in {"image", "all"}:
                    self.assertEqual(saved["image"]["status"], "stale")
                    self.assertIsNone(saved["image"]["quality"])
                else:
                    self.assertEqual(saved["image"]["status"], "ready")
                self.assertEqual(saved["video"]["status"], "stale")
                self.assertIsNone(saved["video"]["quality"])

    def test_cancelled_shot_with_qualified_video_is_regenerated(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="waiting_review")
            shot = project["episodes"][0]["shots"][0]
            shot["status"] = "cancelled"
            shot["image"] = {
                "name": "frame.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True},
            }
            shot["video"] = {
                "name": "old.mp4", "status": "ready", "revision": 1,
                "quality": {"score": 90, "passed": True},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "frame.jpg").write_bytes(b"image")
            source = Path(temp) / "new.mp4"
            source.write_bytes(b"video")

            def run_inline(project_id, shot_id, stage):
                drama_pipeline.run_shot(project_id, shot_id, stage)
                return SimpleNamespace()

            with patch("app.services.drama_pipeline._spawn_shot", side_effect=run_inline), \
                    patch("app.services.drama_pipeline.media_generation.generate_video_asset", return_value=GeneratedMedia(source, "", "test", {})) as generate, \
                    patch("app.services.drama_pipeline.media_quality.score_video", return_value=QualityResult(90, True, [], "vision")), \
                    patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"):
                drama_pipeline.retry_shot(PROJECT_ID, "e1s1", "video")
            generate.assert_called_once()
            saved_shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
            self.assertEqual(saved_shot["image"]["revision"], 2)
            self.assertEqual(saved_shot["video"]["name"], "e1s1_r2_video.mp4")

    def test_edited_prompt_reaches_video_generation_and_scoring(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture()
            shot = project["episodes"][0]["shots"][0]
            shot["prompt"] = "用户编辑后的独特动作提示"
            shot["image"] = {
                "name": "frame.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "frame.jpg").write_bytes(b"image")
            source = Path(temp) / "video.mp4"
            source.write_bytes(b"video")
            with patch("app.services.drama_pipeline.media_generation.generate_video_asset", return_value=GeneratedMedia(source, "", "test", {})) as generate, \
                    patch("app.services.drama_pipeline.media_quality.score_video", return_value=QualityResult(90, True, [], "vision")) as score, \
                    patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"):
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video")
            self.assertIn("用户编辑后的独特动作提示", generate.call_args.args[0])
            self.assertIn("用户编辑后的独特动作提示", score.call_args.args[1])

    def test_late_cancellation_cleanup_does_not_overwrite_new_revision(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())

            def edit_then_cancel(*_args):
                ai_drama.update_shot(PROJECT_ID, "e1s1", prompt="新版本")
                raise GenerationCancelled("旧版本已取消")

            with patch("app.services.drama_pipeline._run_video_loop", side_effect=edit_then_cancel):
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video")
            shot = drama_store.load_project(PROJECT_ID)["episodes"][0]["shots"][0]
            self.assertEqual(shot["revision"], 2)
            self.assertEqual(shot["prompt"], "新版本")
            self.assertEqual(shot["status"], "pending")

    def test_composition_edit_race_discards_temp_output_and_preserves_edit(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="running")
            episode = project["episodes"][0]
            episode.update({"status": "stale", "video": "episode_1.mp4"})
            shot = episode["shots"][0]
            shot["video"] = {
                "name": "shot.mp4", "status": "ready", "revision": 1,
                "quality": {"score": 90, "passed": True},
            }
            project = drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "shot.mp4").write_bytes(b"video")
            output = assets / "episode_1.mp4"
            output.write_bytes(b"old episode")

            def ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"stale composed output")
                ai_drama.update_shot(PROJECT_ID, "e1s1", prompt="合成期间的新编辑")
                return SimpleNamespace(returncode=0, stderr=b"")

            with patch("app.services.ai_drama.subprocess.run", side_effect=ffmpeg), \
                    patch("app.services.ai_drama.probe_video", return_value={"duration": 5}):
                with self.assertRaises(drama_pipeline.WaitingReview):
                    ai_drama._compose(project)
            saved = drama_store.load_project(PROJECT_ID)
            self.assertEqual(saved["episodes"][0]["shots"][0]["prompt"], "合成期间的新编辑")
            self.assertEqual(saved["episodes"][0]["shots"][0]["revision"], 2)
            self.assertEqual(saved["episodes"][0]["status"], "stale")
            self.assertEqual(output.read_bytes(), b"old episode")

    def test_edit_regenerate_recomposes_episode_and_finishes_same_parent(self):
        import time

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="success", characters=[{
                "id": "c1", "name": "角色", "profile_id": "profile-c1", "anchor": "anchor.jpg",
            }])
            episode = project["episodes"][0]
            episode.update({"status": "done", "video": "episode_1.mp4"})
            shot = episode["shots"][0]
            shot["status"] = "done"
            shot["image"] = {
                "name": "frame.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True},
            }
            shot["video"] = {
                "name": "old.mp4", "status": "ready", "revision": 1,
                "quality": {"score": 90, "passed": True},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            for name in ("frame.jpg", "old.mp4", "episode_1.mp4"):
                (assets / name).write_bytes(name.encode())
            ai_drama.update_shot(PROJECT_ID, "e1s1", prompt="编辑后重新生成")
            generated_count = 0

            def generate(*_args, **_kwargs):
                nonlocal generated_count
                generated_count += 1
                suffix = ".jpg" if generated_count == 1 else ".mp4"
                path = Path(temp) / f"generated_{generated_count}{suffix}"
                path.write_bytes(b"generated")
                return GeneratedMedia(path, "", "test", {})

            def ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"new episode")
                return SimpleNamespace(returncode=0, stderr=b"")

            with patch("app.services.drama_pipeline.media_generation.generate_image_asset", side_effect=generate), \
                    patch("app.services.drama_pipeline.media_generation.generate_video_asset", side_effect=generate), \
                    patch("app.services.drama_pipeline.media_quality.score_image", return_value=QualityResult(85, True, [], "vision")), \
                    patch("app.services.drama_pipeline.media_quality.score_video", return_value=QualityResult(90, True, [], "vision")), \
                    patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"), \
                    patch("app.services.ai_drama.subprocess.run", side_effect=ffmpeg) as run, \
                    patch("app.services.ai_drama.probe_video", return_value={"duration": 5}):
                worker = drama_pipeline.retry_shot(PROJECT_ID, "e1s1", "all")
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive())
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    saved = drama_store.load_project(PROJECT_ID)
                    if saved["status"] != "running":
                        break
                    time.sleep(0.02)
            saved = drama_store.load_project(PROJECT_ID)
            shot = saved["episodes"][0]["shots"][0]
            self.assertEqual(generated_count, 2)
            self.assertEqual(shot["revision"], 3)
            self.assertEqual(shot["image"]["revision"], 3)
            self.assertEqual(shot["video"]["revision"], 3)
            self.assertEqual(saved["episodes"][0]["status"], "done")
            self.assertEqual(saved["status"], "success")
            self.assertEqual(saved["task_id"], "drama-abc123def456")
            self.assertEqual((assets / "episode_1.mp4").read_bytes(), b"new episode")
            run.assert_called()

    def test_compose_reuses_current_episode_and_recomposes_only_stale_episode(self):
        import copy

        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="running")
            first = project["episodes"][0]
            first.update({"status": "done", "video": "episode_1.mp4"})
            first["shots"][0]["video"] = {
                "name": "e1s1.mp4", "status": "ready", "revision": 1,
                "quality": {"score": 90, "passed": True},
            }
            second = copy.deepcopy(first)
            second["index"] = 2
            second["status"] = "stale"
            second["video"] = "episode_2.mp4"
            second["shots"][0]["id"] = "e2s1"
            second["shots"][0]["video"]["name"] = "e2s1.mp4"
            project["episodes"].append(second)
            project = drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            for name in ("e1s1.mp4", "e2s1.mp4", "episode_1.mp4", "episode_2.mp4"):
                (assets / name).write_bytes(b"old")

            def ffmpeg(command, **_kwargs):
                Path(command[-1]).write_bytes(b"new")
                return SimpleNamespace(returncode=0, stderr=b"")

            with patch("app.services.ai_drama.subprocess.run", side_effect=ffmpeg) as run, \
                    patch("app.services.ai_drama.probe_video", return_value={"duration": 5}):
                ai_drama._compose(project)
            self.assertEqual(run.call_count, 1)
            self.assertIn(".episode_2_", run.call_args.args[0][-1])
            saved = drama_store.load_project(PROJECT_ID)
            self.assertEqual(saved["episodes"][0]["status"], "done")
            self.assertNotIn("composition_revisions", saved["episodes"][0])
            self.assertEqual(saved["episodes"][1]["status"], "done")
            self.assertEqual(saved["episodes"][1]["composition_revisions"], {"e2s1": 1})

    def test_parent_handoff_error_is_recoverable_worker_failure(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            drama_store.save_project(project_fixture())
            with patch("app.services.drama_pipeline._run_video_loop"), \
                    patch("app.services.ai_drama.request_orchestration", side_effect=RuntimeError("父任务启动失败")):
                drama_pipeline.run_shot(PROJECT_ID, "e1s1", "video", resume_parent=True)
            project = drama_store.load_project(PROJECT_ID)
            self.assertEqual(project["status"], "failed")
            self.assertTrue(project["recoverable"])
            self.assertIn("父任务启动失败", project["error"])

    def test_retry_during_composition_cannot_publish_pre_retry_clip(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="running")
            episode = project["episodes"][0]
            episode.update({"status": "stale", "video": "episode_1.mp4"})
            shot = episode["shots"][0]
            shot["status"] = "done"
            shot["image"] = {
                "name": "frame.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True},
            }
            shot["video"] = {
                "name": "old.mp4", "status": "ready", "revision": 1,
                "quality": {"score": 90, "passed": True},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            for name in ("frame.jpg", "old.mp4"):
                (assets / name).write_bytes(b"old clip")
            output = assets / "episode_1.mp4"
            output.write_bytes(b"old episode")
            ffmpeg_started = threading.Event()
            release_ffmpeg = threading.Event()
            compose_errors = []

            def ffmpeg(command, **_kwargs):
                ffmpeg_started.set()
                self.assertTrue(release_ffmpeg.wait(2))
                Path(command[-1]).write_bytes(b"pre-retry composition")
                return SimpleNamespace(returncode=0, stderr=b"")

            def compose():
                try:
                    ai_drama._compose(drama_store.load_project(PROJECT_ID))
                except Exception as exc:  # noqa: BLE001 - 跨线程断言收集
                    compose_errors.append(exc)

            source = Path(temp) / "replacement.mp4"
            source.write_bytes(b"new clip")

            def run_inline(project_id, shot_id, stage):
                drama_pipeline.run_shot(project_id, shot_id, stage, resume_parent=False)
                return SimpleNamespace()

            with patch("app.services.ai_drama.subprocess.run", side_effect=ffmpeg), \
                    patch("app.services.ai_drama.probe_video", return_value={"duration": 5}), \
                    patch("app.services.drama_pipeline.media_generation.generate_video_asset", return_value=GeneratedMedia(source, "", "test", {})), \
                    patch("app.services.drama_pipeline.media_quality.score_video", return_value=QualityResult(90, True, [], "vision")), \
                    patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"):
                composer = threading.Thread(target=compose)
                composer.start()
                self.assertTrue(ffmpeg_started.wait(2))
                with patch("app.services.drama_pipeline._spawn_shot", side_effect=run_inline):
                    drama_pipeline.retry_shot(PROJECT_ID, "e1s1", "video")
                release_ffmpeg.set()
                composer.join(timeout=2)
            self.assertFalse(composer.is_alive())
            self.assertEqual(len(compose_errors), 1)
            self.assertIsInstance(compose_errors[0], drama_pipeline.WaitingReview)
            saved = drama_store.load_project(PROJECT_ID)
            shot = saved["episodes"][0]["shots"][0]
            self.assertEqual(shot["revision"], 2)
            self.assertEqual(shot["video"]["revision"], 2)
            self.assertEqual(output.read_bytes(), b"old episode")

    def test_immediate_retry_replaces_live_cancelled_worker_without_silent_death(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp)):
            project = project_fixture(status="running")
            shot = project["episodes"][0]["shots"][0]
            shot["image"] = {
                "name": "frame.jpg", "status": "ready", "revision": 1,
                "quality": {"score": 80, "passed": True},
            }
            drama_store.save_project(project)
            assets = Path(temp) / PROJECT_ID / "assets"
            assets.mkdir(parents=True)
            (assets / "frame.jpg").write_bytes(b"image")
            old_started = threading.Event()
            release_old = threading.Event()
            generated = []

            def generate(*_args, **_kwargs):
                path = Path(temp) / f"worker_{len(generated) + 1}.mp4"
                path.write_bytes(b"video")
                generated.append(path)
                if len(generated) == 1:
                    old_started.set()
                    release_old.wait(2)
                return GeneratedMedia(path, "", "test", {})

            with patch("app.services.drama_pipeline.media_generation.generate_video_asset", side_effect=generate) as generate_mock, \
                    patch("app.services.drama_pipeline.media_quality.score_video", return_value=QualityResult(90, True, [], "vision")), \
                    patch("app.services.ai_drama._data_uri", return_value="data:image/jpeg;base64,abc"), \
                    patch("app.services.ai_drama.request_orchestration"):
                old_worker = drama_pipeline._spawn_shot(
                    PROJECT_ID, "e1s1", "video", resume_parent=False
                )
                self.assertTrue(old_started.wait(2))
                replacement = drama_pipeline.retry_shot(PROJECT_ID, "e1s1", "video")
                replacement.join(timeout=2)
                release_old.set()
                old_worker.join(timeout=2)
            self.assertFalse(old_worker.is_alive())
            self.assertFalse(replacement.is_alive())
            self.assertEqual(generate_mock.call_count, 2)
            saved = drama_store.load_project(PROJECT_ID)
            shot = saved["episodes"][0]["shots"][0]
            self.assertEqual(shot["revision"], 2)
            self.assertEqual(shot["status"], "done")
            self.assertEqual(shot["video"]["revision"], 2)
            self.assertNotEqual(saved["status"], "failed")


if __name__ == "__main__":
    unittest.main()
