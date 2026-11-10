"""正式渲染前的低成本分镜：代表帧、文案、故事角色和预估时长。"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from engine.spec import parse_timestamp_range


def _safe_id(value: Any, index: int) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(value or f"segment-{index + 1}"))[:80]


def _unique_id(value: Any, index: int, used: set[str]) -> str:
    base = _safe_id(value, index)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}-{suffix}"
        suffix += 1
    used.add(candidate)
    return candidate


def _previous_frames(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {}
    frames: dict[str, float] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("frame") or "")
        stamp = item.get("frame_timestamp")
        if name and isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
            frames[name] = float(stamp)
    return frames


def build_storyboard(task_dir: Path, items: list[dict[str, Any]], video_path: str, extract: bool = True) -> dict[str, Any]:
    directory = task_dir / "storyboard"
    directory.mkdir(parents=True, exist_ok=True)
    analysis_dir = task_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    previous = _previous_frames(analysis_dir / "storyboard.json")
    frames: list[dict[str, Any]] = []
    used: set[str] = set()
    for index, item in enumerate(items):
        segment_id = _unique_id(item.get("segment_id") or item.get("_id"), index, used)
        filename = f"{segment_id}.jpg"
        target = directory / filename
        try:
            start, end = parse_timestamp_range(str(item.get("timestamp") or ""))
            midpoint = start + (end - start) / 2
        except (TypeError, ValueError):
            start, end, midpoint = 0.0, 0.0, 0.0
        frame_timestamp = previous.get(filename)
        if extract and (frame_timestamp != midpoint or not target.is_file()):
            target.unlink(missing_ok=True)
            completed = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{midpoint:.3f}", "-i", video_path,
                 "-frames:v", "1", "-vf", "scale='min(640,iw)':-2", "-q:v", "4", str(target)],
                capture_output=True,
                timeout=120,
                check=False,
            )
            if completed.returncode == 0 and target.is_file() and target.stat().st_size > 0:
                frame_timestamp = midpoint
        if not target.is_file():
            frame_timestamp = None
        frames.append({
            "segment_id": segment_id,
            "timestamp": item.get("timestamp"),
            "frame": filename if target.is_file() else "",
            "frame_timestamp": frame_timestamp,
            "narration": item.get("narration") or "",
            "picture": item.get("picture") or "",
            "story_role": item.get("story_role") or "setup",
            "estimated_duration": item.get("estimated_duration") or 0,
            "quality_flags": item.get("quality_flags") or [],
            "transition": item.get("transition") or "cut",
            "morph_preview_required": item.get("transition") == "morph",
            "morph_preview_status": item.get("morph_preview_status") or ("pending" if item.get("transition") == "morph" else "not_required"),
        })
    result = {
        "schema_version": 1,
        "items": frames,
        "total_estimated_duration": round(sum(float(item["estimated_duration"] or 0) for item in frames), 2),
    }
    (analysis_dir / "storyboard.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
