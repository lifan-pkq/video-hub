"""系统维护：一键清理视频/日志、临时文件，以及定时清理调度。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import (
    COVERS_DIR,
    DATA_DIR,
    NARRATO_TASKS_DIR,
    TMP_DIR,
    VIDEOS_DIR,
)
from app.services.tasks import task_manager
from app.db import (
    clear_video_references,
    delete_video,
    get_maintenance_settings,
    list_narration_tasks,
    list_recordings,
    list_videos,
    update_maintenance_settings,
)

NARRATO_ROOT = NARRATO_TASKS_DIR.parent.parent
LOG_RETENTION_DEFAULT = 7
TEMP_RETENTION_HOURS = 24


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError:
        return 0


def _tree_stats(path: Path) -> dict[str, int]:
    files = 0
    size = 0
    if not path.exists():
        return {"files": 0, "size": 0}
    try:
        for item in path.rglob("*"):
            if item.is_file():
                files += 1
                size += item.stat().st_size
    except OSError:
        pass
    return {"files": files, "size": size}


def _remove_file(path: Path, result: dict[str, int]) -> None:
    try:
        if path.is_file():
            size = path.stat().st_size
            path.unlink()
            result["files"] += 1
            result["bytes"] += size
    except OSError:
        pass


def _remove_old_entries(root: Path, cutoff: float, result: dict[str, int]) -> None:
    if not root.exists():
        return
    for item in list(root.iterdir()):
        try:
            if item.stat().st_mtime >= cutoff:
                continue
            if item.is_dir() and not item.is_symlink():
                size = _size(item)
                shutil.rmtree(item, ignore_errors=True)
                result["files"] += 1
                result["bytes"] += size
            elif item.is_file():
                _remove_file(item, result)
        except OSError:
            continue


def storage_status() -> dict[str, Any]:
    videos = list_videos()
    tasks = list_narration_tasks(200)
    task_stats = _tree_stats(NARRATO_TASKS_DIR)
    temp_stats = _tree_stats(TMP_DIR)
    cache_stats = _tree_stats(NARRATO_ROOT / "storage" / "analysis_cache")
    logs = 0
    log_bytes = 0
    for root in (NARRATO_TASKS_DIR,):
        if not root.exists():
            continue
        for item in root.rglob("*.log"):
            try:
                logs += 1
                log_bytes += item.stat().st_size
            except OSError:
                pass
    return {
        "videos": {
            "count": len(videos),
            "size": sum(int(v.get("size") or 0) for v in videos),
            "files_size": _size(VIDEOS_DIR),
            "covers_size": _size(COVERS_DIR),
        },
        "tasks": {"count": len(tasks), **task_stats},
        "temp": temp_stats,
        "cache": cache_stats,
        "logs": {"files": logs, "size": log_bytes},
        "checked_at": _now(),
    }


def _active_work() -> tuple[int, int, int]:
    running_tasks = sum(
        1 for task in list_narration_tasks(200) if task.get("status") in {"queued", "running", "awaiting_review"}
    )
    recording = sum(1 for item in list_recordings() if item.get("status") == "recording")
    jobs = sum(1 for task in task_manager.list(limit=200) if task.status in {"pending", "running"})
    return running_tasks, recording, jobs


def clear_all_videos(force: bool = False) -> dict[str, Any]:
    active_tasks, recording, jobs = _active_work()
    if not force and (active_tasks or recording or jobs):
        raise ValueError(
            f"还有 {active_tasks} 个解说任务、{recording} 个录制、{jobs} 个下载任务在进行，请先停止"
        )
    videos = list_videos()
    total = 0
    removed = 0
    sources: dict[str, int] = {}
    for video in videos:
        total += int(video.get("size") or 0)
        removed += 1
        platform = str(video.get("source_platform") or "unknown")
        sources[platform] = sources.get(platform, 0) + 1
        delete_video(video["id"])
    for directory in (VIDEOS_DIR, COVERS_DIR, DATA_DIR / "generated" / "videos"):
        if directory.exists():
            for item in directory.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                except OSError:
                    pass
        directory.mkdir(parents=True, exist_ok=True)
    clear_video_references()
    return {"ok": True, "videos": removed, "bytes": total, "sources": sources, "at": _now()}


def _clean_task_logs(cutoff: float, result: dict[str, int]) -> None:
    if not NARRATO_TASKS_DIR.exists():
        return
    for directory in NARRATO_TASKS_DIR.iterdir():
        if not directory.is_dir():
            continue
        state: dict[str, Any] = {}
        try:
            state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        if state.get("status") in {"queued", "running"}:
            continue
        for name in ("worker.log",):
            path = directory / name
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    _remove_file(path, result)
            except OSError:
                pass


def clean_logs(retention_days: int = LOG_RETENTION_DEFAULT) -> dict[str, Any]:
    days = max(1, min(int(retention_days or LOG_RETENTION_DEFAULT), 365))
    cutoff = time.time() - days * 86400
    result = {"files": 0, "bytes": 0}
    _clean_task_logs(cutoff, result)
    temp_cutoff = time.time() - TEMP_RETENTION_HOURS * 3600
    for root in (NARRATO_ROOT / "storage" / "temp", TMP_DIR):
        _remove_old_entries(root, temp_cutoff, result)

    return {"ok": True, "retention_days": days, "files": result["files"], "bytes": result["bytes"], "at": _now()}


def get_settings() -> dict[str, Any]:
    row = get_maintenance_settings()
    return {
        "enabled": bool(row.get("enabled")),
        "interval_hours": int(row.get("interval_hours") or 24),
        "retention_days": int(row.get("retention_days") or LOG_RETENTION_DEFAULT),
        "clean_logs": bool(row.get("clean_logs", 1)),
        "clear_videos": bool(row.get("clear_videos", 0)),
        "last_run_at": row.get("last_run_at") or "",
        "next_run_at": row.get("next_run_at") or "",
        "last_result": _parse_json(row.get("last_result")),
    }


def _parse_json(value: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(payload.get("enabled"))
    interval = max(1, min(int(payload.get("interval_hours") or 24), 24 * 30))
    retention = max(1, min(int(payload.get("retention_days") or 7), 365))
    next_run = (datetime.now() + timedelta(hours=interval)).isoformat(timespec="seconds") if enabled else None
    update_maintenance_settings(
        enabled=1 if enabled else 0,
        interval_hours=interval,
        retention_days=retention,
        clean_logs=1 if payload.get("clean_logs", True) else 0,
        clear_videos=1 if payload.get("clear_videos", False) else 0,
        next_run_at=next_run,
    )
    return get_settings()


def run_cleanup(trigger: str = "manual", force: bool = False) -> dict[str, Any]:
    settings = get_settings()
    result: dict[str, Any] = {"trigger": trigger, "at": _now()}
    if settings["clean_logs"] or force:
        result["logs"] = clean_logs(settings["retention_days"])
    if settings["clear_videos"]:
        try:
            result["videos"] = clear_all_videos(force=force)
        except ValueError as exc:
            result["videos_skipped"] = str(exc)
    interval = int(settings["interval_hours"] or 24)
    next_run = (datetime.now() + timedelta(hours=interval)).isoformat(timespec="seconds") if settings["enabled"] else None
    update_maintenance_settings(
        last_run_at=_now(),
        next_run_at=next_run,
        last_result=result,
    )
    return result


async def maintenance_loop() -> None:
    """每分钟检查一次；到期后在线程池里执行，避免阻塞 API。"""
    while True:
        try:
            settings = get_settings()
            next_run = settings.get("next_run_at") or ""
            if settings.get("enabled") and next_run:
                try:
                    due = datetime.fromisoformat(next_run) <= datetime.now()
                except ValueError:
                    due = False
                if due:
                    await asyncio.to_thread(run_cleanup, "scheduled")
        except Exception:
            pass
        await asyncio.sleep(60)
