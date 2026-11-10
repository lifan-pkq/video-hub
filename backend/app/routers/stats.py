"""创作仪表盘统计：素材、录制与任务。"""

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
            "size": sum(video.get("size") or 0 for video in videos),
            "analyzed": sum(1 for video in videos if video.get("analysis_status") in {"success", "partial"}),
            "high_quality": sum(1 for video in videos if (video.get("quality_score") or 0) >= 70),
        },
        "images": {
            "count": len(images),
            "size": sum(image.get("size") or 0 for image in images),
        },
        "recordings": {
            "total": len(recordings),
            "active": sum(1 for recording in recordings if recording.get("status") == "recording"),
        },
        "tasks": {
            "running": sum(1 for task in tasks if task.status in ("pending", "running")),
            "today": sum(1 for task in tasks if str(task.created_at).startswith(today)),
        },
    }
