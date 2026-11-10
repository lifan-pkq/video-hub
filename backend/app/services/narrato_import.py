"""NarratoAI 最终成片扫描与幂等入库。"""
import asyncio
import json
import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path

from app.config import COVERS_DIR, NARRATO_TASKS_DIR, VIDEOS_DIR
from app.db import add_narrato_video, get_video_by_source
from app.services.media import make_cover, probe_video

logger = logging.getLogger(__name__)
_observed_sizes: dict[str, int] = {}
# 扫描循环与解说任务状态轮询可能同时触发入库，统一串行化，避免重复复制文件。
_import_lock = threading.RLock()


def _unique_path(task_id: str) -> Path:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", task_id)[:80] or "task"
    candidate = VIDEOS_DIR / f"narrato_{safe_id}.mp4"
    index = 1
    while candidate.exists():
        candidate = VIDEOS_DIR / f"narrato_{safe_id}_{index}.mp4"
        index += 1
    return candidate


def _signature(source: Path) -> tuple[int, int]:
    stat = source.stat()
    return stat.st_size, stat.st_mtime_ns


def _import_final(
    source: Path,
    task_id: str,
    title: str | None = None,
    source_platform: str = "narrato",
) -> dict:
    with _import_lock:
        existing = get_video_by_source(source_platform, f"narrato://task/{task_id}")
        if existing:
            return existing
        return _import_locked(source, task_id, title, source_platform)


def _import_locked(
    source: Path,
    task_id: str,
    title: str | None = None,
    source_platform: str = "narrato",
) -> dict:
    before = _signature(source)
    if not probe_video(source).get("duration"):
        raise RuntimeError("最终成片尚未完成或无法读取")

    destination = _unique_path(task_id)
    temporary = VIDEOS_DIR / f".{destination.stem}.{uuid.uuid4().hex}.tmp"
    linked = False
    completed = False
    try:
        shutil.copy2(source, temporary)
        if before != _signature(source):
            raise RuntimeError("复制期间源文件发生变化")
        meta = probe_video(temporary)
        if not meta.get("duration") or not meta.get("width"):
            raise RuntimeError("复制后的成片无法读取")

        while True:
            try:
                os.link(temporary, destination)
                linked = True
                break
            except FileExistsError:
                destination = _unique_path(task_id)
        temporary.unlink()

        cover_name = f"{destination.stem}.jpg"
        cover = cover_name if make_cover(destination, COVERS_DIR / cover_name) else None
        video = add_narrato_video(
            task_id,
            title=title or f"AI 解说 {task_id[:8]}",
            filename=destination.name,
            size=destination.stat().st_size,
            duration=meta.get("duration"),
            width=meta.get("width"),
            height=meta.get("height"),
            cover=cover,
            source_platform=source_platform,
            source_url=f"narrato://task/{task_id}",
        )
        if video["filename"] != destination.name:
            destination.unlink(missing_ok=True)
            linked = False
        completed = True
        return video
    finally:
        temporary.unlink(missing_ok=True)
        if linked and not completed:
            destination.unlink(missing_ok=True)


def import_task(
    task_id: str,
    source: Path | str,
    title: str | None = None,
    source_platform: str = "narrato",
) -> dict:
    """按任务 ID 显式导入成片（解说任务状态轮询调用）。"""
    path = Path(source)
    if not path.is_file() or path.stat().st_size <= 0:
        raise FileNotFoundError(f"成片不存在或为空：{path}")
    return _import_final(path, task_id, title, source_platform)


def _quality_allows(source: Path) -> bool:
    try:
        report = json.loads((source.parent / "quality_report.json").read_text(encoding="utf-8"))
        return report.get("passed") is True
    except (OSError, ValueError):
        return False


def scan_once(tasks_dir: Path) -> list[dict]:
    """只自动导入有通过质量报告且文件稳定的成片。"""
    if not tasks_dir.is_dir():
        return []
    imported = []
    for source in sorted(tasks_dir.glob("*/combined.mp4")):
        task_id = source.parent.name
        if not _quality_allows(source):
            _observed_sizes.pop(task_id, None)
            continue
        source_key = f"narrato://task/{task_id}"
        if get_video_by_source("narrato", source_key):
            _observed_sizes.pop(task_id, None)
            continue
        try:
            size = source.stat().st_size
        except OSError:
            continue
        if size <= 0:
            continue
        if _observed_sizes.get(task_id) != size:
            _observed_sizes[task_id] = size
            continue
        try:
            title = None
            source_platform = "narrato"
            try:
                spec = json.loads((source.parent / "spec.json").read_text(encoding="utf-8"))
                title = str(spec.get("title") or "").strip() or None
                if spec.get("output_mode") == "edit":
                    source_platform = "ai_edit"
            except (OSError, ValueError):
                pass
            imported.append(_import_final(source, task_id, title, source_platform))
            logger.info("NarratoAI 成片已入库：%s", task_id)
        except Exception as exc:
            logger.warning("NarratoAI 成片导入失败，将重试：%s：%s", task_id, exc)
    return imported


async def importer_loop():
    while True:
        try:
            await asyncio.to_thread(scan_once, NARRATO_TASKS_DIR)
        except Exception:
            logger.exception("NarratoAI 成片扫描失败")
        await asyncio.sleep(15)
