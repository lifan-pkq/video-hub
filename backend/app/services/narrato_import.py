"""NarratoAI 最终成片扫描与幂等入库。"""
import asyncio
import logging
import os
import re
import shutil
import uuid
from pathlib import Path

from app.config import COVERS_DIR, NARRATO_TASKS_DIR, VIDEOS_DIR
from app.db import add_narrato_video, get_video_by_source
from app.services.media import make_cover, probe_video

logger = logging.getLogger(__name__)
_observed_sizes: dict[str, int] = {}


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


def _import_final(source: Path, task_id: str) -> dict:
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
            title=f"AI 解说 {task_id[:8]}",
            filename=destination.name,
            size=destination.stat().st_size,
            duration=meta.get("duration"),
            width=meta.get("width"),
            height=meta.get("height"),
            cover=cover,
            source_platform="narrato",
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


def scan_once(tasks_dir: Path) -> list[dict]:
    """导入连续两次扫描大小不变的最终成片，成功任务永久去重。"""
    if not tasks_dir.is_dir():
        return []
    imported = []
    for source in sorted(tasks_dir.glob("*/combined.mp4")):
        task_id = source.parent.name
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
            imported.append(_import_final(source, task_id))
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
