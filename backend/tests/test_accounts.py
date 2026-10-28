import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.routers import accounts


class CookieUploadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.path = Path(self.temp_dir.name) / "account.json"
        for target in ("app.routers.accounts.cookie_path", "app.services.cookies.cookie_path"):
            item = patch(target, return_value=self.path)
            item.start()
            self.addCleanup(item.stop)

    @staticmethod
    def upload(content):
        return UploadFile(io.BytesIO(content), filename="cookies.json")

    async def test_invalid_json_does_not_replace_existing_cookie(self):
        self.path.write_bytes(b"original")

        with self.assertRaises(HTTPException) as caught:
            await accounts.upload_cookie("douyin", "default", self.upload(b"not-json"))

        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self.path.read_bytes(), b"original")

    async def test_valid_cookie_atomically_replaces_file_with_owner_only_permissions(self):
        cases = (
            ("douyin", {"cookies": []}),
            ("bilibili", {"cookie_info": {"cookies": []}, "token_info": {}}),
        )
        for platform, data in cases:
            with self.subTest(platform=platform):
                self.path.write_bytes(b"original")
                self.path.chmod(0o644)
                content = json.dumps(data).encode()
                with patch("os.replace", wraps=os.replace) as replace:
                    result = await accounts.upload_cookie(
                        platform, "default", self.upload(content))

                self.assertTrue(result["ok"])
                self.assertEqual(self.path.read_bytes(), content)
                self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
                replace.assert_called_once()


if __name__ == "__main__":
    unittest.main()
