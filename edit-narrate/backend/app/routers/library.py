"""Minimal library API so this workbench runs alone. Do not mount it if the host already serves /api/videos."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from engine.demo_media import write_demo_set
from engine.errors import EngineError
from engine.media import probe
from app.db import add_video, get_video, list_videos
from app.services.videos import covers_dir, make_cover, public_video, resolve_media, videos_dir

router = APIRouter(prefix="/api/videos", tags=["videos"])


def _load(video_id: int) -> dict:
    video = get_video(video_id)
    if not video:
        raise HTTPException(404, {"code": "not_found", "message": f"视频 {video_id} 不存在", "action": "刷新素材库。"})
    return video


@router.get("")
def videos():
    return [public_video(video) for video in list_videos()]


@router.post("/seed-demo")
def seed_demo():
    if os.environ.get("VIDEO_HUB_SEED_DEMO", "1") == "0":
        raise HTTPException(403, {"code": "seed_disabled", "message": "演示素材已关闭", "action": "这是生产库。不要打开 VIDEO_HUB_SEED_DEMO。"})
    created = []
    for item in write_demo_set(videos_dir()):
        path = videos_dir() / item["filename"]
        meta = probe(path)
        cover = f"{path.stem}.jpg"
        make_cover(path, covers_dir() / cover)
        existing = [video for video in list_videos() if video.get("filename") == path.name]
        if existing:
            created.append(public_video(existing[0]))
            continue
        video = add_video(
            title=item["title"],
            filename=path.name,
            size=meta["size"],
            duration=meta["duration"],
            width=meta["width"],
            height=meta["height"],
            cover=cover,
            source_platform="demo",
            source_url=None,
        )
        created.append(public_video(video))
    return {"videos": created}


@router.get("/{video_id}/file")
def video_file(video_id: int):
    video = _load(video_id)
    try:
        path = resolve_media(video)
    except EngineError as exc:
        raise HTTPException(exc.status, exc.as_dict()) from exc
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/{video_id}/poster")
def video_poster(video_id: int):
    video = _load(video_id)
    cover = str(video.get("cover") or "")
    safe = Path(cover).name
    if cover and safe == cover and safe not in {"", ".", ".."}:
        path = covers_dir() / safe
        if path.is_file():
            return FileResponse(path, media_type="image/jpeg")
    try:
        media = resolve_media(video)
    except EngineError as exc:
        raise HTTPException(exc.status, exc.as_dict()) from exc
    dest = covers_dir() / f"{media.stem}.jpg"
    if not dest.is_file():
        make_cover(media, dest)
    if not dest.is_file():
        raise HTTPException(404, {"code": "missing_file", "message": "封面生成失败", "action": "直接播放视频。"})
    return FileResponse(dest, media_type="image/jpeg")
