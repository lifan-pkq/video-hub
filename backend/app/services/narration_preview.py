"""审片阶段的低清选段预览：只按规划时间轴拼接原片，不生成 TTS/字幕。"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.config import NARRATO_TASKS_DIR

TIMESTAMP = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
RANGE = re.compile(
    r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})"
)


def _seconds(value: str) -> float:
    match = TIMESTAMP.search(value)
    if not match:
        raise ValueError(f"非法时间戳：{value}")
    return (
        int(match.group(1)) * 3600
        + int(match.group(2)) * 60
        + int(match.group(3))
        + int(match.group(4)) / 1000
    )


def _ranges(items: list[dict[str, Any]]) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    for item in items:
        match = RANGE.search(str(item.get("timestamp") or ""))
        if not match:
            continue
        start = _seconds(match.group(1))
        end = _seconds(match.group(2))
        if end > start:
            ranges.append((start, end))
    return ranges


def build_preview(task_id: str) -> dict[str, Any]:
    task_dir = Path(NARRATO_TASKS_DIR) / task_id
    spec_path = task_dir / "spec.json"
    plan_path = task_dir / "analysis" / "plan.json"
    if not spec_path.is_file() or not plan_path.is_file():
        raise ValueError("审片规划尚未生成")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    video = Path(str(spec.get("video_path") or ""))
    if not video.is_file():
        raise ValueError("原视频不存在")
    ranges = _ranges(plan.get("items") or [])
    if not ranges:
        raise ValueError("规划里没有可用片段")
    output = task_dir / "preview.mp4"
    with tempfile.TemporaryDirectory(prefix="preview-", dir=str(task_dir)) as tmp:
        clips: list[Path] = []
        for index, (start, end) in enumerate(ranges):
            clip = Path(tmp) / f"clip_{index:03d}.mp4"
            command = [
                "ffmpeg", "-nostdin", "-y", "-v", "error",
                "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{end - start:.3f}",
                "-vf", "scale=-2:480", "-r", "24",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                "-c:a", "aac", "-b:a", "64k",
                str(clip),
            ]
            result = subprocess.run(command, capture_output=True, timeout=180, check=False)
            if not clip.is_file() or clip.stat().st_size <= 0:
                command = [
                    "ffmpeg", "-nostdin", "-y", "-v", "error",
                    "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{end - start:.3f}",
                    "-vf", "scale=-2:480", "-r", "24",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30", "-an",
                    str(clip),
                ]
                subprocess.run(command, capture_output=True, timeout=180, check=False)
            if clip.is_file() and clip.stat().st_size > 0:
                clips.append(clip)
        if not clips:
            raise ValueError("预览片段生成失败")
        listing = Path(tmp) / "list.txt"
        listing.write_text(
            "\n".join(f"file '{clip.as_posix()}'" for clip in clips) + "\n",
            encoding="utf-8",
        )
        command = [
            "ffmpeg", "-nostdin", "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", "-movflags", "+faststart", str(output),
        ]
        result = subprocess.run(command, capture_output=True, timeout=180, check=False)
        if not output.is_file() or output.stat().st_size <= 0:
            detail = result.stderr.decode("utf-8", "replace") if result.stderr else "预览合并失败"
            raise ValueError(detail[-500:])
    return {
        "task_id": task_id,
        "url": f"/api/narrate/tasks/{task_id}/preview.mp4",
        "segments": len(ranges),
        "size": output.stat().st_size,
        "note": "低清选段预览：无配音、无字幕，只检查选段和原声节奏",
    }
