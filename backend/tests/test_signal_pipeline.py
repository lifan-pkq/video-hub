"""手动时间线：没有模型时必须大声失败，不能交回空成功。

这个文件按 pytest 收集。unittest discover 只导入它，不跑这些函数。
"""

from shutil import copyfile

try:
    import pytest
except ImportError:  # unittest 收集时不能因为没装 pytest 而中断
    pytest = None
from fastapi.testclient import TestClient

import app.db as db
from app.main import app
from app.services.videos import videos_dir
from engine.demo_media import write_demo_set
from engine.media import probe


def _fixture(fn):
    return pytest.fixture()(fn) if pytest is not None else fn


@_fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    library = data / "videos"
    library.mkdir(parents=True)
    monkeypatch.setenv("VIDEO_HUB_DATA_DIR", str(data))
    monkeypatch.setenv("VIDEO_HUB_VIDEOS_DIR", str(library))
    monkeypatch.setenv("VIDEO_HUB_JOBS_INLINE", "1")
    monkeypatch.setenv("VIDEO_HUB_AUTH_MODE", "off")
    for name in (
        "VIDEO_HUB_AI_API_KEY",
        "VIDEO_HUB_AI_BASE_URL",
        "VIDEO_HUB_AI_MODEL",
        "VIDEO_HUB_ASR_URL",
        "VIDEO_HUB_VISION_API_KEY",
        "VIDEO_HUB_VISION_BASE_URL",
        "VIDEO_HUB_VISION_MODEL",
        "VIDEO_HUB_TTS_API_KEY",
        "VIDEO_HUB_TTS_BASE_URL",
    ):
        monkeypatch.setenv(name, "")
    monkeypatch.setattr("app.services.videos.covers_dir", lambda: data / "covers")

    rows: dict[int, dict] = {}
    seq = {"n": 1}

    def remember(**fields):
        video_id = seq["n"]
        seq["n"] += 1
        row = {"id": video_id, "cover": None, "source_url": None, **fields}
        rows[video_id] = row
        return row

    def lookup(video_id: int):
        return rows.get(int(video_id))

    monkeypatch.setattr("app.db.add_video", remember)
    monkeypatch.setattr("app.db.get_video", lookup)

    for item in write_demo_set(library):
        path = library / item["filename"]
        meta = probe(path)
        remember(
            title=item["title"],
            filename=item["filename"],
            size=meta["size"],
            duration=meta["duration"],
            width=meta["width"],
            height=meta["height"],
            source_platform="demo",
        )
    return TestClient(app)


def _demo_video():
    for candidate in range(1, 8):
        row = db.get_video(candidate)
        if row and row.get("source_platform") == "demo":
            return row
    raise AssertionError("演示片没有写入测试库")


def test_analyze_plan_export(client):
    video_id = _demo_video()["id"]

    job = client.post("/api/edit/analyze", json={"video_ids": [video_id]})
    assert job.status_code == 200, job.text
    body = job.json()
    assert body["status"] in {"success", "partial"}
    clip = body["result"]["clips"][0]
    assert clip["transcript"]["segments"]
    assert clip["transcript"]["source"] == "sidecar"
    kinds = {item["kind"] for item in clip["suggestions"]}
    assert "silence" in kinds
    assert "black" in kinds
    assert "scene" in kinds

    plan = client.post("/api/edit/plan", json={
        "video_id": video_id,
        "suggestions": clip["suggestions"],
        "use_llm": False,
    })
    assert plan.status_code == 200, plan.text
    assert plan.json()["source"] == "signal"

    denied = client.post("/api/edit/plan", json={
        "video_id": video_id,
        "suggestions": clip["suggestions"],
        "use_llm": True,
    })
    assert denied.status_code == 409
    detail = denied.json()["detail"]
    assert detail["code"] == "llm_unconfigured"
    assert detail["message"]
    assert detail["action"]

    exported = client.post("/api/edit/export", json={
        "title": "测试成片",
        "clips": [{"video_id": video_id, "start": 0.2, "end": 3.2}],
    })
    assert exported.status_code == 200, exported.text
    done = exported.json()
    assert done["status"] == "success", done
    assert done["result"]["video"]["id"]
    download = client.get(done["result"]["download_url"])
    assert download.status_code == 200
    assert len(download.content) > 500

    listed_tasks = client.get("/api/tasks")
    assert listed_tasks.status_code == 200
    kinds_in_center = {item.get("kind") for item in listed_tasks.json()}
    assert "edit.analyze" in kinds_in_center
    assert "edit.export" in kinds_in_center


def test_highlight_speed_freeze_and_missing_still(client):
    video = _demo_video()
    planned = client.post("/api/edit/highlight", json={
        "use_llm": True,
        "instruction": "留下有口播的段落",
        "videos": [{
            "video_id": video["id"],
            "duration": video["duration"],
            "title": video["title"],
            "segments": [
                {"start": 0.3, "end": 3.6, "text": "这是一段演示口播，用来说明切点怎么对齐句子。"},
                {"start": 9.3, "end": 13.5, "text": "黑场之后画面切到青绿，句子从这里重新开始。"},
            ],
            "scenes": [6.5, 9.0],
            "silence": [{"start": 4.0, "end": 6.5}],
            "black": [{"start": 6.5, "end": 9.0}],
            "suggestions": [],
            "frames": [{"time": 1.0, "caption": "橙色画面里有人在讲。"}],
        }],
    })
    assert planned.status_code == 200, planned.text
    body = planned.json()
    assert body["source"] == "signal"
    assert body["clips"]
    assert any("模型" in item or "没有配置" in item for item in body["warnings"])

    from app.services.videos import data_dir
    from engine.media import ffmpeg

    image_dir = data_dir() / "generated" / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    still = image_dir / "ai_teststill.png"
    ffmpeg(["-f", "lavfi", "-i", "color=c=0xc4492c:s=640x360", "-frames:v", "1", str(still)], timeout=30)
    exported = client.post("/api/edit/export", json={
        "title": "变速静帧",
        "clips": [
            {"video_id": video["id"], "start": 0.4, "end": 3.2, "speed": 2, "freeze": 0.6, "kind": "video"},
            {"kind": "image", "image_name": "ai_teststill.png", "start": 0, "end": 1.2, "overlay": False, "title": "静帧"},
            {"kind": "image", "image_name": "ai_missing.png", "start": 0, "end": 1, "overlay": True, "title": "缺失叠图"},
        ],
    })
    assert exported.status_code == 200, exported.text
    done = exported.json()
    assert done["status"] == "partial", done
    assert "找不到" in (done.get("error") or {}).get("message", "")
    duration = float(done["result"]["duration"])
    # 2.8s / 2 + 0.6s freeze + 1.2s still ≈ 3.2s
    assert 2.4 < duration < 4.2, duration


def test_real_video_without_asr_is_partial(client):
    seeded = _demo_video()
    source = videos_dir() / seeded["filename"]
    target = videos_dir() / "plain.mp4"
    copyfile(source, target)
    meta = probe(target)
    video = db.add_video(
        title="无转写原片",
        filename=target.name,
        size=meta["size"],
        duration=meta["duration"],
        width=meta["width"],
        height=meta["height"],
        source_platform="upload",
    )
    created = client.post("/api/narrate/tasks", json={"video_id": video["id"], "max_frames": 3})
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    assert job["status"] == "partial"
    assert job["error"]["code"] == "asr_unconfigured"
    assert job["error"]["message"]
    assert job["error"]["action"]
    task = created.json()["task"]
    assert task["frames"]
    assert task["transcript"]["segments"] == []

    health = client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["ffmpeg"] is True
    assert body["llm"] is False
