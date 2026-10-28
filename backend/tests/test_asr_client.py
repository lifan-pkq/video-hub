import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from app.services.ai_edit import AIResponseError, AIServiceError, transcribe
from app.services.asr_client import transcribe_remote


class RemoteTranscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.video_path = Path(self.tempdir.name) / "a.mp4"
        self.video_path.write_bytes(b"video")

    def tearDown(self):
        self.tempdir.cleanup()

    @patch("app.services.asr_client.requests.post")
    def test_transcribe_remote_returns_normalized_segments(self, post):
        response = Mock()
        response.json.return_value = {
            "language": "zh",
            "duration": 2.5,
            "segments": [
                {"start": 0, "end": 1.234, "text": " 你好 "},
                {"start": 1.3, "end": 2.5, "text": " "},
            ],
        }
        response.raise_for_status.return_value = None
        post.return_value = response

        result = transcribe_remote(
            self.video_path,
            "http://127.0.0.1:8600/v1/audio/transcriptions",
            log=lambda _: None,
        )

        self.assertEqual(result, [{"start": 0.0, "end": 1.23, "text": "你好"}])
        self.assertEqual(post.call_args.kwargs["data"]["response_format"], "verbose_json")
        self.assertEqual(post.call_args.kwargs["data"]["language"], "zh")
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 1800))

    @patch("app.services.asr_client.requests.post", side_effect=requests.RequestException("offline"))
    def test_network_failure_raises_service_error(self, post):
        with self.assertRaises(AIServiceError):
            transcribe_remote(self.video_path, "http://127.0.0.1:8600/v1/audio/transcriptions")

    @patch("app.services.asr_client.requests.post")
    def test_invalid_payload_raises_response_error(self, post):
        response = Mock()
        response.json.return_value = {"segments": "invalid"}
        response.raise_for_status.return_value = None
        post.return_value = response

        with self.assertRaises(AIResponseError):
            transcribe_remote(self.video_path, "http://127.0.0.1:8600/v1/audio/transcriptions")

    @patch("app.services.asr_client.transcribe_remote")
    def test_configured_remote_service_does_not_load_local_model(self, remote):
        remote.return_value = [{"start": 0.0, "end": 1.0, "text": "远端转写"}]
        cache = Path(self.tempdir.name) / "cache.json"
        with patch("app.services.ai_edit.ASR_URL", "http://127.0.0.1:8600/v1/audio/transcriptions"), \
                patch("app.services.ai_edit._cache_path", return_value=cache), \
                patch("app.services.ai_edit._get_model") as get_model:
            result = transcribe(self.video_path, log=lambda _: None)

        self.assertEqual(result[0]["text"], "远端转写")
        get_model.assert_not_called()
        self.assertEqual(json.loads(cache.read_text())["segments"], result)


if __name__ == "__main__":
    unittest.main()
