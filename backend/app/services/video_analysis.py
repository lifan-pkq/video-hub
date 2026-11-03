"""视频结构辅助：镜头切换检测与选段对齐（不依赖 OpenCV）。"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


def detect_scene_cuts(path: Path, threshold: float = 0.32, timeout: int = 300) -> list[float]:
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-filter:v", f"select='gt(scene,{max(0.05, min(threshold, 0.9))})',showinfo",
        "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    cuts: set[float] = set()
    for line in (result.stderr or "").splitlines():
        match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
        if match:
            try:
                value = round(float(match.group(1)), 2)
            except ValueError:
                continue
            if value > 0:
                cuts.add(value)
    return sorted(cuts)


def snap_plan_to_cuts(clips: list[dict], cuts: list[float], duration: float, tolerance: float = 2.0) -> list[dict]:
    """把选段边界吸附到附近镜头切换点，避免从镜头中间开始/结束。"""
    boundaries = [0.0, float(duration), *sorted(c for c in cuts if 0 < c < duration)]
    snapped: list[dict] = []
    previous_end = 0.0
    for clip in sorted(clips, key=lambda item: item["start"]):
        start = float(clip["start"])
        end = float(clip["end"])
        start_candidates = [b for b in boundaries if abs(b - start) <= tolerance and b >= previous_end]
        end_candidates = [b for b in boundaries if abs(b - end) <= tolerance and b > start]
        if start_candidates:
            start = min(start_candidates, key=lambda b: abs(b - start))
        if end_candidates:
            end = min(end_candidates, key=lambda b: abs(b - end))
        if end - start < 0.8:
            start, end = float(clip["start"]), float(clip["end"])
        if start < previous_end:
            start = previous_end
        if end <= start:
            continue
        item = dict(clip)
        item["start"] = round(start, 2)
        item["end"] = round(end, 2)
        if abs(start - float(clip["start"])) > 0.01 or abs(end - float(clip["end"])) > 0.01:
            item["reason"] = (str(item.get("reason") or "") + "；对齐镜头边界").strip("；")
        snapped.append(item)
        previous_end = end
    return snapped
