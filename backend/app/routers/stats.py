"""仪表盘统计：视频库 / 录制 / 任务 / AI 素材的聚合数据。"""

from datetime import datetime

from fastapi import APIRouter

from app.db import list_recordings, list_videos
from app.services.ai_images import list_images
from app.services.tasks import task_manager

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
def stats():
    videos = list_videos()
    images = list_images(limit=300)
    recordings = list_recordings()
    tasks = task_manager.list(limit=200)

    today = datetime.now().strftime("%Y-%m-%d")
    return {
        "videos": {
            "count": len(videos),
            "size": sum(v.get("size") or 0 for v in videos),
        },
        "images": {
            "count": len(images),
            "size": sum(v.get("size") or 0 for v in images),
        },
        "recordings": {
            "total": len(recordings),
            "active": sum(1 for r in recordings if r.get("status") == "recording"),
        },
        "tasks": {
            "running": sum(1 for t in tasks if t.status in ("pending", "running")),
            "today": sum(1 for t in tasks if str(t.created_at).startswith(today)),
        },
    }
