"""陈泽剪辑方式：默认关闭、自动路径、时长质检、身份和未启用规则。"""

import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("VIDEO_HUB_AUTH_MODE", "off")

from app.services.chenze_fit import evaluate, fit_label
from app.services.chenze_pack import auto_enabled, load_identity, load_rules, pack_dir
from app.services.chenze_pipeline import (
    choose_span,
    detect_event_units,
    plan_intelligent_edit,
)


def verified_account() -> dict:
    anchor = load_identity()
    return {
        "display_name": anchor["display_name"],
        "douyin_id": anchor["douyin_id"],
        "homepage_url": anchor["homepage_url"],
    }


def speech(duration: float = 40) -> list[dict]:
    segments = []
    cursor = 0.0
    while cursor < duration - 1:
        segments.append({
            "start": cursor,
            "end": min(duration, cursor + 8),
            "text": f"这句口播说完整了{int(cursor)}",
        })
        cursor += 8
    return segments


class FitQCTests(unittest.TestCase):
    def test_pack_cases_reject_over_limit_labels(self):
        seventy = evaluate(1155, 1225, 60)
        self.assertEqual(seventy["duration_seconds"], "70")
        self.assertEqual(seventy["status"], "REJECT_OVER_LIMIT")
        self.assertFalse(seventy["automatic_trim"])
        self.assertIsNone(fit_label(1155, 1225, 60))

        forty_one = evaluate(1237, 1278, 30)
        self.assertEqual(forty_one["duration_seconds"], "41")
        self.assertEqual(forty_one["status"], "REJECT_OVER_LIMIT")
        self.assertIsNone(fit_label(1237, 1278, 30))

        self.assertEqual(evaluate(0, 30, 30)["status"], "NEEDS_HUMAN_REVIEW")
        self.assertIsNone(fit_label(0, 30, 30))
        self.assertEqual(evaluate("0", "30.000001", "30")["status"], "REJECT_OVER_LIMIT")
        exact = evaluate(
            0, 30, 30,
            precision="human_verified_original_timeline",
            context_verified=True,
            sentence_tail_verified=True,
            asr_human_verified=True,
        )
        self.assertEqual(exact["status"], "FIT")
        self.assertEqual(fit_label(
            0, 30, 30,
            precision="human_verified_original_timeline",
            context_verified=True,
            sentence_tail_verified=True,
            asr_human_verified=True,
        ), "FIT30")
        self.assertFalse(exact["publication_approved"])

    def test_over_limit_event_is_no_fit_and_not_hard_cut(self):
        segments = [{"start": 1155, "end": 1225, "text": "这句要说到完整句尾"}]
        unit = detect_event_units(segments)[0]
        chosen = choose_span(unit, 60)
        self.assertEqual(chosen["status"], "NO_FIT")
        self.assertEqual(chosen["end"], 1225)
        self.assertNotEqual(chosen["end"], 1155 + 60)
        self.assertFalse(chosen["automatic_trim"])
        self.assertIsNone(chosen["fit_label"])

        plan = plan_intelligent_edit(
            segments,
            1300,
            chenze_style=True,
            chenze_options={**verified_account(), "max_seconds": 60, "column": "通用保真"},
            max_clips=4,
            request_ai=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应再走原来的模型选段")),
        )
        self.assertEqual(plan["source"], "chenze")
        self.assertEqual(plan["clips"], [])
        review = plan["chenze"]["review_candidates"]
        self.assertEqual(review[0]["status"], "NO_FIT")
        self.assertEqual(review[0]["end"], 1225)
        self.assertIsNone(review[0]["fit_label"])
        self.assertFalse(review[0]["automatic_trim"])
        self.assertNotIn("FIT60", json.dumps(review, ensure_ascii=False))

    def test_dependent_sentences_stay_together_instead_of_cutting_at_the_cap(self):
        segments = [
            {"start": 0, "end": 30, "speaker": "甲", "text": "如果被叫到名字就要停"},
            {"start": 30, "end": 70, "speaker": "乙", "text": "所以结果是他停了"},
        ]
        plan = plan_intelligent_edit(
            segments,
            80,
            chenze_style=True,
            chenze_options={**verified_account(), "max_seconds": 60, "column": "综艺"},
            request_ai=lambda *_args, **_kwargs: [],
        )
        self.assertEqual(plan["clips"], [])
        item = plan["chenze"]["review_candidates"][0]
        self.assertEqual(item["status"], "NO_FIT")
        self.assertEqual(item["start"], 0)
        self.assertEqual(item["end"], 70)
        self.assertIn("如果被叫到名字", item["text"])
        self.assertIn("所以结果是他停了", item["text"])
        self.assertFalse(item["automatic_trim"])
        self.assertIsNone(item["fit_label"])


class ChenzePipelineTests(unittest.TestCase):
    def test_default_off_keeps_today_planner(self):
        expected = {"source": "llm", "budget": 1, "warnings": [], "clips": [{"start": 0, "end": 12}]}
        with patch("app.services.chenze_pipeline.plan_douyin_shorts", return_value=expected) as planner:
            plan = plan_intelligent_edit(speech(), 40, instruction="留金句", chenze_style=False)
        planner.assert_called_once()
        self.assertIs(plan, expected)
        self.assertNotIn("chenze", plan)

    def test_option_on_runs_event_pipeline_for_verified_account(self):
        segments = [{"start": 0, "end": 18, "text": "今天把这件事说完。"}]
        with patch("app.services.chenze_pipeline.plan_douyin_shorts") as planner:
            plan = plan_intelligent_edit(
                segments,
                40,
                chenze_style=True,
                chenze_options={**verified_account(), "max_seconds": 60},
                request_ai=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("身份核上后不应走原选段")),
            )
        planner.assert_not_called()
        self.assertEqual(plan["source"], "chenze")
        self.assertTrue(plan["chenze"]["applied"])
        self.assertEqual(plan["chenze"]["automation"], "event_unit")
        self.assertEqual(plan["chenze"]["applied_rule_ids"], [])
        self.assertFalse(plan["chenze"]["publication_approved"])
        self.assertFalse(plan["chenze"]["validated_person_style"])
        self.assertEqual(len(plan["clips"]), 1)
        clip = plan["clips"][0]
        self.assertEqual(clip["end"], 18)
        self.assertEqual(clip["review_status"], "pending")
        self.assertFalse(clip["publication_approved"])
        self.assertIsNone(clip["fit_label"])
        self.assertLessEqual(Decimal(str(clip["end"])) - Decimal(str(clip["start"])), Decimal("60"))
        pending = [item for item in plan["chenze"]["review_candidates"] if item["status"] == "pending"]
        self.assertTrue(pending)

    def test_identity_mismatch_does_not_apply_self_style(self):
        anchor = load_identity()
        other = anchor["not_self"][0]["homepage_url"]
        fallback = {"source": "llm", "budget": 1, "warnings": [], "clips": [{"start": 1, "end": 12, "title": "原选段"}]}
        cases = [
            {**verified_account(), "douyin_id": "9896719"},
            {**verified_account(), "homepage_url": other},
            {**verified_account(), "display_name": "陈泽"},
            {"display_name": "陈泽-", "douyin_id": "", "homepage_url": ""},
        ]
        for options in cases:
            with self.subTest(options=options), patch(
                "app.services.chenze_pipeline.plan_douyin_shorts", return_value=dict(fallback),
            ) as planner:
                plan = plan_intelligent_edit(
                    speech(),
                    40,
                    chenze_style=True,
                    chenze_options=options,
                )
            planner.assert_called_once()
            self.assertFalse(plan["chenze"]["applied"])
            self.assertEqual(plan["chenze"]["applied_rule_ids"], [])
            self.assertEqual(plan["source"], "llm")
            self.assertIn("未按陈泽本人剪辑", plan["warnings"][-1])

    def test_disabled_rules_are_not_auto_enabled(self):
        self.assertEqual(auto_enabled(), [])
        raw = json.loads((pack_dir() / "rules-draft.json").read_text(encoding="utf-8"))
        self.assertTrue(all(rule["enabled_for_automatic_editing"] is False for rule in raw["rules"]))

        base = plan_intelligent_edit(
            [{"start": 0, "end": 16, "text": "这是谐音梗，不是综艺密度"}],
            40,
            chenze_style=True,
            chenze_options={**verified_account(), "column": "游戏", "max_seconds": 60},
        )
        self.assertEqual(base["chenze"]["applied_rule_ids"], [])
        self.assertIn("CZ-C14", base["chenze"]["skipped_disabled_rule_ids"])
        self.assertIn("CZ-C04", base["chenze"]["blocked_cross_column_rule_ids"])

        rules = load_rules()
        for rule in rules:
            if rule["rule_id"] in {"CZ-C14", "CZ-C04"}:
                rule["enabled_for_automatic_editing"] = True
        enabled = plan_intelligent_edit(
            [{"start": 0, "end": 16, "text": "这是谐音梗，不是综艺密度"}],
            40,
            chenze_style=True,
            chenze_options={**verified_account(), "column": "游戏", "max_seconds": 60},
            rules=rules,
        )
        self.assertEqual(enabled["chenze"]["applied_rule_ids"], ["CZ-C14"])
        self.assertIn("CZ-C04", enabled["chenze"]["blocked_cross_column_rule_ids"])
        self.assertNotIn("CZ-C04", enabled["chenze"]["applied_rule_ids"])
        self.assertNotIn("CZ-C14", enabled["chenze"]["skipped_disabled_rule_ids"])
        again = json.loads((pack_dir() / "rules-draft.json").read_text(encoding="utf-8"))
        self.assertTrue(all(rule["enabled_for_automatic_editing"] is False for rule in again["rules"]))

    def test_low_res_and_programme_fiction_are_not_cleared(self):
        plan = plan_intelligent_edit(
            [{"start": 0, "end": 12, "text": "这一段是老年假设，不是诊断"}],
            30,
            chenze_style=True,
            chenze_options={**verified_account(), "max_seconds": 60, "low_res": True},
            width=320,
            height=240,
        )
        self.assertFalse(plan["chenze"]["authorization_cleared"])
        self.assertFalse(plan["chenze"]["publication_approved"])
        item = plan["chenze"]["review_candidates"][0]
        self.assertEqual(item["status"], "context_required")
        self.assertTrue(any("低清" in note or "假设" in note or "诊断" in note for note in item["rights_notes"]))


class ChenzeRouteTests(unittest.TestCase):
    def setUp(self):
        self._inline = os.environ.get("VIDEO_HUB_JOBS_INLINE")
        self._data = os.environ.get("VIDEO_HUB_DATA_DIR")
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["VIDEO_HUB_JOBS_INLINE"] = "1"
        os.environ["VIDEO_HUB_DATA_DIR"] = self.tmp.name
        from fastapi.testclient import TestClient

        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        if self._inline is None:
            os.environ.pop("VIDEO_HUB_JOBS_INLINE", None)
        else:
            os.environ["VIDEO_HUB_JOBS_INLINE"] = self._inline
        if self._data is None:
            os.environ.pop("VIDEO_HUB_DATA_DIR", None)
        else:
            os.environ["VIDEO_HUB_DATA_DIR"] = self._data
        self.tmp.cleanup()

    def test_api_defaults_off_and_on_reaches_the_pipeline(self):
        from app.jobs import update

        seen = []

        def run(job_id, video_id, instruction, max_clips, layout="interview", chenze_style=False, chenze_options=None):
            seen.append({"chenze_style": chenze_style, "chenze_options": chenze_options})
            update(job_id, status="success", progress=100, stage="done", label="完成", result={"shorts": []})

        with patch("app.routers.edit.fetch", return_value={"id": 1, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=run):
            off = self.client.post("/api/edit/douyin", json={"video_id": 1, "instruction": "留金句"})
            on = self.client.post("/api/edit/douyin", json={
                "video_id": 1,
                "chenze_style": True,
                "chenze": {
                    "display_name": "陈泽-",
                    "douyin_id": "9896719.",
                    "homepage_url": verified_account()["homepage_url"],
                    "column": "游戏",
                    "max_seconds": 30,
                },
            })
        self.assertEqual(off.status_code, 200, off.text)
        self.assertFalse(off.json()["input"]["chenze_style"])
        self.assertFalse(seen[0]["chenze_style"])
        self.assertEqual(on.status_code, 200, on.text)
        self.assertTrue(on.json()["input"]["chenze_style"])
        self.assertEqual(on.json()["input"]["chenze"]["douyin_id"], "9896719.")
        self.assertTrue(seen[1]["chenze_style"])
        self.assertEqual(seen[1]["chenze_options"]["column"], "游戏")

        failed = on.json()
        update(failed["id"], status="failed", stage="failed", label="失败", error={"message": "失败"})
        with patch("app.routers.edit.fetch", return_value={"id": 1, "title": "原片"}), \
                patch("app.routers.edit.produce_douyin", side_effect=run):
            retried = self.client.post(f"/api/edit/douyin/{failed['id']}/retry")
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertTrue(retried.json()["input"]["chenze_style"])
        self.assertEqual(retried.json()["input"]["chenze"]["max_seconds"], 30)
        self.assertTrue(seen[2]["chenze_style"])


class ChenzeProduceTests(unittest.TestCase):
    def test_review_only_result_is_not_rendered_as_a_final_cut(self):
        from app.jobs import create, get
        from app.services.douyin_flow import produce_douyin

        previous = os.environ.get("VIDEO_HUB_DATA_DIR")
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["VIDEO_HUB_DATA_DIR"] = tmp
            try:
                job = create("edit.douyin", "抖音成片", {"video_id": 7})
                plan = {
                    "source": "chenze",
                    "budget": 1,
                    "warnings": ["有一条 NO_FIT"],
                    "clips": [],
                    "chenze": {
                        "requested": True,
                        "applied": True,
                        "review_candidates": [{"status": "NO_FIT", "start": 1155, "end": 1225}],
                        "publication_approved": False,
                    },
                }
                with patch("app.services.douyin_flow.fetch", return_value={"id": 7, "title": "原片", "source_url": ""}), \
                        patch("app.services.douyin_flow.resolve_media", return_value=Path(tmp) / "source.mp4"), \
                        patch("app.services.douyin_flow.probe_source", return_value={"duration": 1300, "width": 1280, "height": 720, "has_audio": True}), \
                        patch("app.services.douyin_flow.transcribe", return_value=[{"start": 1155, "end": 1225, "text": "完整句"}]), \
                        patch("app.services.douyin_flow.plan_intelligent_edit", return_value=plan), \
                        patch("app.services.douyin_flow.render_short") as render:
                    produce_douyin(job["id"], 7, "", 0, chenze_style=True, chenze_options=verified_account())
                saved = get(job["id"])
            finally:
                if previous is None:
                    os.environ.pop("VIDEO_HUB_DATA_DIR", None)
                else:
                    os.environ["VIDEO_HUB_DATA_DIR"] = previous
        render.assert_not_called()
        self.assertEqual(saved["status"], "success")
        self.assertEqual(saved["result"]["shorts"], [])
        self.assertEqual(saved["result"]["chenze"]["review_candidates"][0]["status"], "NO_FIT")
        self.assertEqual(saved["label"], "陈泽候选需人工复核")


if __name__ == "__main__":
    unittest.main()
