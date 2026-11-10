import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main
from app.routers.drama import DramaBody, DramaOptions
from app.services import ai_drama, drama_store


class DramaRouterTests(unittest.TestCase):
    def test_create_options_use_dynamic_planning_contract(self):
        options = DramaOptions().model_dump()

        self.assertEqual(options, {
            "episodes_count": 2,
            "quality_threshold": 70,
            "aspect_ratio": "9:16",
            "style": "realistic",
            "burn_subtitles": False,
        })
        body = DramaBody(
            novel="正文",
            shots_per_episode=6,
            seconds_per_shot=5,
            max_attempts=8,
        ).model_dump(exclude={"novel", "title"})
        self.assertNotIn("shots_per_episode", body)
        self.assertNotIn("seconds_per_shot", body)
        self.assertNotIn("max_attempts", body)

    def test_quality_threshold_accepts_only_one_through_one_hundred(self):
        for value in (1, 70, 100):
            with self.subTest(value=value):
                self.assertEqual(DramaOptions(quality_threshold=value).quality_threshold, value)
        for value in (0, 101):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                DramaOptions(quality_threshold=value)

    def test_create_route_persists_schema_v2_with_only_new_options(self):
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(drama_store, "DRAMA_DIR", Path(temp) / "drama"), \
                patch.object(ai_drama.AI_POOL, "has_keys", return_value=True), \
                patch.object(ai_drama, "_spawn") as spawn:
            response = TestClient(main.app).post("/api/drama/projects", json={
                "novel": "剧情" * 100,
                "title": "测试项目",
                "episodes_count": 3,
                "quality_threshold": 82,
                "aspect_ratio": "16:9",
                "style": "anime",
                "burn_subtitles": True,
                "shots_per_episode": 6,
                "seconds_per_shot": 5,
                "max_attempts": 8,
            })

            self.assertEqual(response.status_code, 200, response.text)
            project_id = response.json()["id"]
            project = drama_store.load_project(project_id)
            self.assertEqual(project["schema_version"], 2)
            self.assertEqual(project["task_id"], f"drama-{project_id}")
            self.assertEqual(project["options"], {
                "episodes_count": 3,
                "quality_threshold": 82,
                "aspect_ratio": "16:9",
                "style": "anime",
                "burn_subtitles": True,
            })
            spawn.assert_called_once()

    def test_create_route_rejects_invalid_quality_threshold(self):
        response = TestClient(main.app).post("/api/drama/projects", json={
            "novel": "剧情" * 100,
            "quality_threshold": 101,
        })
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
