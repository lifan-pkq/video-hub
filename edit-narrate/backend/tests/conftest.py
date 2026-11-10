import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("VIDEO_HUB_DATA_DIR", str(data))
    monkeypatch.setenv("VIDEO_HUB_DB", str(data / "library.sqlite"))
    monkeypatch.setenv("VIDEO_HUB_JOBS_INLINE", "1")
    monkeypatch.setenv("VIDEO_HUB_AI_API_KEY", "")
    monkeypatch.setenv("VIDEO_HUB_AI_BASE_URL", "")
    monkeypatch.setenv("VIDEO_HUB_ASR_URL", "")
    monkeypatch.setenv("VIDEO_HUB_VISION_MODEL", "")
    monkeypatch.delenv("VIDEO_HUB_EXTERNAL_DB", raising=False)
    from app.main import app

    return TestClient(app)
