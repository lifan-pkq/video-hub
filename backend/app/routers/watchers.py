"""主播监控（自动录制）：watcher 的增删改查。轮询循环在 watcher 服务里。"""
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import add_watcher, delete_watcher, get_watcher, list_watchers, update_watcher
from app.services import recorder

router = APIRouter(prefix="/api/watchers", tags=["watchers"])

# 监控只支持有稳定开播检测的平台
WATCHABLE = {"douyin", "bilibili"}


class WatcherRequest(BaseModel):
    url: str
    name: str = ""
    quality: str = "best"
    fps: int = 0


def _validate(req: WatcherRequest) -> tuple[str, str]:
    url = recorder.extract_url(req.url)
    platform = recorder.detect_platform(url)
    if platform not in WATCHABLE:
        raise HTTPException(400, "自动监控目前只支持抖音 / B站直播间链接")
    if req.quality not in recorder.QUALITY_LEVELS:
        raise HTTPException(400, "画质档位不合法")
    if req.fps not in (0, 24, 30, 60):
        raise HTTPException(400, "帧率仅支持 0（原始）/24/30/60")
    return platform, url


@router.post("")
def create(req: WatcherRequest):
    platform, url = _validate(req)
    for w in list_watchers():
        if w["url"] == url:
            raise HTTPException(400, "该直播间已在监控列表中")
    wid = add_watcher(platform, url, req.name.strip(), req.quality, req.fps)
    return get_watcher(wid)


@router.get("")
def list_all():
    return list_watchers()


class WatcherPatch(BaseModel):
    enabled: bool | None = None
    name: str | None = None
    quality: str | None = None
    fps: int | None = None


@router.patch("/{watcher_id}")
def patch(watcher_id: int, req: WatcherPatch):
    w = get_watcher(watcher_id)
    if not w:
        raise HTTPException(404, "监控不存在")
    fields: dict = {}
    if req.enabled is not None:
        fields["enabled"] = 1 if req.enabled else 0
    if req.name is not None:
        fields["name"] = req.name.strip()
    if req.quality is not None:
        if req.quality not in recorder.QUALITY_LEVELS:
            raise HTTPException(400, "画质档位不合法")
        fields["quality"] = req.quality
    if req.fps is not None:
        if req.fps not in (0, 24, 30, 60):
            raise HTTPException(400, "帧率仅支持 0（原始）/24/30/60")
        fields["fps"] = req.fps
    update_watcher(watcher_id, **fields)
    return get_watcher(watcher_id)


@router.delete("/{watcher_id}")
def remove(watcher_id: int):
    if not get_watcher(watcher_id):
        raise HTTPException(404, "监控不存在")
    delete_watcher(watcher_id)
    return {"ok": True}
