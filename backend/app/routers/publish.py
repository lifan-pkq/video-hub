"""发布：创建发布任务（多账号串行）+ 发布历史。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.config import PLATFORMS, PUBLISH_ACCOUNT, VIDEOS_DIR
from app.db import get_video, list_publish_records
from app.services.cookies import valid_account_name
from app.services.publisher import run_publish_guarded, sau_available
from app.services.tasks import task_manager

router = APIRouter(prefix="/api/publish", tags=["publish"])


class PublishTarget(BaseModel):
    platform: str
    account: str = PUBLISH_ACCOUNT


class PublishRequest(BaseModel):
    video_id: int
    targets: list[PublishTarget] = []
    title: str
    desc: str = ""
    tags: list[str] = []
    schedule: str | None = None  # "%Y-%m-%d %H:%M"，空 = 立即发布

    @field_validator("targets")
    @classmethod
    def _check_targets(cls, v):
        if not v:
            raise ValueError("至少选择一个发布账号")
        if len(v) > 16:
            raise ValueError("最多选择 16 个发布账号")
        seen = set()
        for t in v:
            if t.platform not in PLATFORMS:
                raise ValueError(f"不支持的平台: {t.platform}")
            if not valid_account_name(t.account):
                raise ValueError(f"非法账号名: {t.account}")
            key = (t.platform, t.account)
            if key in seen:
                raise ValueError(f"重复的发布账号: {t.platform}:{t.account}")
            seen.add(key)
        return v

    @field_validator("title")
    @classmethod
    def _check_title(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("标题不能为空")
        if len(v) > 55:
            raise ValueError("标题过长（小红书限制 20 字、抖音 55 字）")
        return v


@router.post("")
async def publish(req: PublishRequest):
    if not sau_available():
        raise HTTPException(500, f"发布引擎未就绪（未找到 sau 命令），请检查后端配置")
    video = get_video(req.video_id)
    if not video:
        raise HTTPException(404, "视频不存在")
    video_path = VIDEOS_DIR / video["filename"]
    if not video_path.exists():
        raise HTTPException(404, "视频文件已丢失")

    targets = [t.model_dump() for t in req.targets]
    brief = ",".join(f"{t['platform']}:{t['account']}" for t in targets)
    task = task_manager.create("publish", f"{video['title']} → {brief}")
    asyncio.create_task(run_publish_guarded(
        task, video, video_path, targets,
        req.title, req.desc, req.tags, req.schedule,
    ))
    return {"task_id": task.id}


@router.get("/records")
def publish_records():
    return list_publish_records()
