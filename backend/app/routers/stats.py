"""仪表盘统计：视频库 / 账号 / 录制 / 发布 / 任务的聚合数据。"""
from datetime import datetime

from fastapi import APIRouter

from app.db import list_publish_records, list_recordings, list_videos
from app.services.cookies import list_accounts
from app.services.tasks import task_manager

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
def stats():
    videos = list_videos()
    accounts = list_accounts()
    recordings = list_recordings()
    records = list_publish_records(limit=500)
    tasks = task_manager.list(limit=200)

    today = datetime.now().strftime("%Y-%m-%d")
    today_records = [r for r in records if (r.get("created_at") or "").startswith(today)]

    return {
        "videos": {
            "count": len(videos),
            "size": sum(v.get("size") or 0 for v in videos),
        },
        "accounts": {
            "total": len(accounts),
            "with_cookie": sum(1 for a in accounts if a.get("cookie_exists")),
        },
        "recordings": {
            "total": len(recordings),
            "active": sum(1 for r in recordings if r.get("status") == "recording"),
        },
        "publish": {
            "total": len(records),
            "today": len(today_records),
            "success": sum(1 for r in records if r.get("status") == "success"),
        },
        "tasks": {
            "running": sum(1 for t in tasks if t.status in ("pending", "running")),
            "needs_verify": sum(1 for t in tasks if t.needs_verify and t.status == "running"),
        },
    }
