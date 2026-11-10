import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services import profiles


class ProfileTests(unittest.TestCase):
    def test_character_snapshot_keeps_fixed_features_and_pronunciation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.services.profiles.PROFILES_DIR", root), patch("app.services.profiles.REFERENCES_DIR", root / "refs"):
                character = profiles.save_profile("characters", {
                    "name": "陈默", "fixed_features": "左眼下有痣", "age_rules": "只有陈默会变老"
                })
                profiles.save_profile("preferences", {"name": "默认", "pronunciation": {"陈默": "陈莫"}})
                bundle = profiles.resolve_bundle(character_id=character["id"])
        self.assertIn("左眼下有痣", bundle["prompt"])
        self.assertIn("只有陈默会变老", bundle["prompt"])
        self.assertEqual(bundle["pronunciation"]["陈默"], "陈莫")

    def test_invalid_kind_is_rejected(self):
        with self.assertRaises(profiles.ProfileError):
            profiles.list_profiles("unknown")


class MissingReferenceTests(unittest.TestCase):
    def test_missing_reference_is_kept_and_marked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            refs = root / "refs"
            refs.mkdir()
            (refs / "profile-aaaa.png").write_bytes(b"img")
            with patch("app.services.profiles.PROFILES_DIR", root),                     patch("app.services.profiles.REFERENCES_DIR", refs):
                character = profiles.save_profile("characters", {
                    "name": "陈默",
                    "references": [{"name": "profile-aaaa.png"}, {"name": "profile-gone.png"}],
                })
            present, missing = character["references"]
        self.assertEqual([item["name"] for item in (present, missing)],
                         ["profile-aaaa.png", "profile-gone.png"])
        self.assertEqual(present["size"], 3)
        self.assertNotIn("missing", present)
        self.assertTrue(missing["missing"])
        self.assertNotIn("size", missing)


class DefaultPreferencesTests(unittest.TestCase):
    def _save_preferences(self, root, profile_id, name, pronunciation, updated_at=None, is_default=False):
        payload = {"id": profile_id, "name": name, "pronunciation": pronunciation}
        if is_default:
            payload["is_default"] = True
        profiles.save_profile("preferences", payload, profile_id)
        if updated_at:
            path = root / f"preferences-{profile_id}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["updated_at"] = updated_at
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_pronunciation_prefers_default_marked_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.services.profiles.PROFILES_DIR", root),                     patch("app.services.profiles.REFERENCES_DIR", root / "refs"):
                self._save_preferences(root, "aaa", "alpha", {"陈默": "甲读"},
                                       updated_at="2030-01-01T00:00:00")
                self._save_preferences(root, "zzz", "zeta", {"陈默": "乙读"},
                                       is_default=True, updated_at="2020-01-01T00:00:00")
                bundle = profiles.resolve_bundle()
        self.assertEqual(bundle["pronunciation"]["陈默"], "乙读")

    def test_pronunciation_falls_back_to_most_recently_updated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.services.profiles.PROFILES_DIR", root),                     patch("app.services.profiles.REFERENCES_DIR", root / "refs"):
                self._save_preferences(root, "aaa", "alpha", {"陈默": "旧"},
                                       updated_at="2020-01-01T00:00:00")
                self._save_preferences(root, "zzz", "zeta", {"陈默": "新"},
                                       updated_at="2030-01-01T00:00:00")
                bundle = profiles.resolve_bundle()
        self.assertEqual(bundle["pronunciation"]["陈默"], "新")


class DeleteProfileTests(unittest.TestCase):
    def _setup(self, root):
        refs = root / "refs"
        refs.mkdir()
        for name in ("profile-shared.png", "profile-solo.png"):
            (refs / name).write_bytes(b"img")
        profiles.save_profile("characters", {
            "id": "keep", "name": "甲", "references": [{"name": "profile-shared.png"}],
        }, "keep")
        profiles.save_profile("characters", {
            "id": "gone", "name": "乙",
            "references": [{"name": "profile-shared.png"}, {"name": "profile-solo.png"}],
        }, "gone")
        return refs

    def test_delete_removes_image_only_when_unreferenced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.services.profiles.PROFILES_DIR", root),                     patch("app.services.profiles.REFERENCES_DIR", root / "refs"),                     patch("app.db.list_narration_tasks", return_value=[
                        {"id": "narrate-1", "request": {"character_profile_id": "gone"}},
                        {"id": "narrate-2", "request": {"character_profile_id": "keep"}},
                    ]):
                refs = self._setup(root)
                result = profiles.delete_profile("characters", "gone")
                shared_exists = (refs / "profile-shared.png").is_file()
                solo_exists = (refs / "profile-solo.png").is_file()
                gone_exists = (root / "characters-gone.json").exists()
        self.assertEqual(result["references_removed"], ["profile-solo.png"])
        self.assertFalse(solo_exists)
        self.assertTrue(shared_exists)
        self.assertFalse(gone_exists)
        self.assertEqual(result["task_snapshots"], ["narrate-1"])

    def test_delete_keeps_image_still_referenced_by_another_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with patch("app.services.profiles.PROFILES_DIR", root),                     patch("app.services.profiles.REFERENCES_DIR", root / "refs"),                     patch("app.db.list_narration_tasks", return_value=[]):
                refs = self._setup(root)
                profiles.save_profile("characters", {
                    "id": "other", "name": "丙", "references": [{"name": "profile-solo.png"}],
                }, "other")
                result = profiles.delete_profile("characters", "gone")
                solo_exists = (refs / "profile-solo.png").is_file()
        self.assertEqual(result["references_removed"], [])
        self.assertTrue(solo_exists)


if __name__ == "__main__":
    unittest.main()
