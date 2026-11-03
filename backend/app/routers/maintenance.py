"""系统维护 API：存储概览、清空视频、清理日志、定时清理设置。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import maintenance

router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


class ClearVideosBody(BaseModel):
    force: bool = False


class CleanLogsBody(BaseModel):
    retention_days: int = 7


class MaintenanceSettingsBody(BaseModel):
    enabled: bool = False
    interval_hours: int = 24
    retention_days: int = 7
    clean_logs: bool = True
    clear_videos: bool = False


@router.get("/status")
def status():
    return {
        "storage": maintenance.storage_status(),
        "settings": maintenance.get_settings(),
    }


@router.post("/videos/clear")
def clear_videos(body: ClearVideosBody | None = None):
    try:
        return maintenance.clear_all_videos(force=bool(body and body.force))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/logs/clean")
def clean_logs(body: CleanLogsBody | None = None):
    return maintenance.clean_logs((body or CleanLogsBody()).retention_days)


@router.get("/settings")
def get_settings():
    return maintenance.get_settings()


@router.put("/settings")
def save_settings(body: MaintenanceSettingsBody):
    return maintenance.save_settings(body.model_dump())


@router.post("/run")
def run_now():
    return maintenance.run_cleanup(trigger="manual", force=True)
