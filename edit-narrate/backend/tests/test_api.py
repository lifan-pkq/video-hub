from shutil import copyfile

from app.db import add_video
from app.services.videos import videos_dir
from engine.media import probe


def test_analyze_plan_export(client):
    seeded = client.post("/api/videos/seed-demo")
    assert seeded.status_code == 200, seeded.text
    video = seeded.json()["videos"][0]
    job = client.post("/api/edit/analyze", json={"video_ids": [video["id"]]})
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
        "video_id": video["id"],
        "suggestions": clip["suggestions"],
        "use_llm": False,
    })
    assert plan.status_code == 200, plan.text
    assert plan.json()["source"] == "signal"

    denied = client.post("/api/edit/plan", json={
        "video_id": video["id"],
        "suggestions": clip["suggestions"],
        "use_llm": True,
    })
    assert denied.status_code == 409
    detail = denied.json()["detail"]
    assert detail["code"] == "llm_unconfigured"
    assert detail["action"]

    exported = client.post("/api/edit/export", json={
        "title": "测试成片",
        "clips": [{"video_id": video["id"], "start": 0.2, "end": 3.2}],
    })
    assert exported.status_code == 200, exported.text
    done = exported.json()
    assert done["status"] == "success", done
    assert done["result"]["video"]["id"]
    download = client.get(done["result"]["download_url"])
    assert download.status_code == 200
    assert len(download.content) > 500


def test_narrate_steps_fail_loud_without_model(client):
    videos = client.post("/api/videos/seed-demo").json()["videos"]
    created = client.post("/api/narrate/tasks", json={"video_id": videos[0]["id"], "max_frames": 4})
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["job"]["status"] == "success", payload["job"]
    task = payload["task"]
    assert task["transcript"]["segments"]
    assert task["frames"]
    assert task["frames"][0]["caption_error"]
    frame = client.get(f"/api/narrate/tasks/{task['id']}/frames/{task['frames'][0]['image']}")
    assert frame.status_code == 200

    segments = task["transcript"]["segments"]
    segments[0]["text"] = "改过的第一句，仍然对齐时间。"
    saved = client.put(f"/api/narrate/tasks/{task['id']}/transcript", json={"segments": segments})
    assert saved.status_code == 200
    assert saved.json()["transcript"]["segments"][0]["text"].startswith("改过的第一句")

    denied = client.post(f"/api/narrate/tasks/{task['id']}/script", json={"mode": "llm", "target_duration": 12})
    assert denied.status_code == 409
    assert denied.json()["detail"]["action"]

    drafted = client.post(f"/api/narrate/tasks/{task['id']}/script", json={"mode": "skeleton", "target_duration": 12})
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["job"]["status"] == "success"
    task = drafted.json()["task"]
    beats = []
    for beat in task["script"]["beats"]:
        picture = max(0.8, beat["end"] - beat["start"])
        beats.append({**beat, "narration": "画面里就这些"})
        assert picture >= 0.4
    voice = {
        "engine": "tone",
        "voice": "tone",
        "rate": 1,
        "original_volume": 0.12,
        "subtitles": True,
        "font_size": 36,
        "margin_v": 40,
    }
    reviewed = client.put(f"/api/narrate/tasks/{task['id']}/script", json={"beats": beats, "voice": voice})
    assert reviewed.status_code == 200, reviewed.text

    rendered = client.post(f"/api/narrate/tasks/{task['id']}/render")
    assert rendered.status_code == 200, rendered.text
    done = rendered.json()
    assert done["job"]["status"] == "success", done["job"]
    assert done["task"]["output"]["tts_engine"] == "tone"
    assert done["task"]["output"]["warnings"]
    media = client.get(f"/api/narrate/tasks/{task['id']}/output")
    assert media.status_code == 200
    assert len(media.content) > 1000
    imported = client.post(f"/api/narrate/tasks/{task['id']}/import")
    assert imported.status_code == 200, imported.text
    assert imported.json()["video"]["source_platform"] == "narration"


def test_real_video_without_asr_is_partial(client):
    seeded = client.post("/api/videos/seed-demo").json()["videos"][0]
    source = videos_dir() / seeded["filename"]
    target = videos_dir() / "plain.mp4"
    copyfile(source, target)
    meta = probe(target)
    video = add_video(
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
    assert job["error"]["action"]
    task = created.json()["task"]
    assert task["frames"]
    assert task["transcript"]["segments"] == []

    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["ffmpeg"] is True
    assert health.json()["llm"] is False
