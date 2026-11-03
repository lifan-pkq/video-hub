"""旧版 NarratoAI 历史任务：扫描旧任务目录，支持一键导入视频库。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import NARRATO_TASKS_DIR
from app.db import get_video_by_source
from app.services import narrato_import
from app.services.media import probe_video

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")


def _title(task_dir: Path) -> str:
    for name in ("spec.json", "task.json"):
        try:
            data = json.loads((task_dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            value = str(data.get("title") or data.get("name") or "").strip()
            if value:
                return value[:80]
    return f"历史任务 · {task_dir.name[:18]}"


def list_legacy(limit: int = 50) -> list[dict[str, Any]]:
    root = Path(NARRATO_TASKS_DIR)
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for directory in root.iterdir():
        if not directory.is_dir() or not SAFE_ID.match(directory.name):
            continue
        # 新版引擎任务由 narration_tasks 管理，不在这里重复展示
        if (directory / "spec.json").is_file() or directory.name.startswith("narrate-"):
            continue
        source = directory / "combined.mp4"
        if not source.is_file() or source.stat().st_size <= 0:
            continue
        ledger_id = f"legacy-{directory.name}"
        imported = get_video_by_source("legacy", f"narrato://task/{ledger_id}")
        meta = probe_video(source)
        try:
            created = datetime.fromtimestamp(source.stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            created = ""
        items.append(
            {
                "task_id": directory.name,
                "title": _title(directory),
                "duration": meta.get("duration") or 0,
                "size": source.stat().st_size,
                "created_at": created,
                "imported_video_id": imported.get("id") if imported else None,
            }
        )
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return items[: max(1, min(int(limit or 50), 200))]


def import_legacy(task_id: str) -> dict[str, Any]:
    if not SAFE_ID.match(task_id or ""):
        raise ValueError("非法任务 ID")
    directory = Path(NARRATO_TASKS_DIR) / task_id
    source = directory / "combined.mp4"
    if not source.is_file() or source.stat().st_size <= 0:
        raise ValueError("历史任务没有可用成片")
    ledger_id = f"legacy-{task_id}"
    return narrato_import.import_task(
        ledger_id,
        source,
        title=_title(directory),
        source_platform="legacy",
    )
