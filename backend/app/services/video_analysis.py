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


def snap_plan_to_cuts(
    clips: list[dict],
    cuts: list[float],
    duration: float,
    tolerance: float = 2.0,
    beats: list[float] | None = None,
    sentence_ends: list[float] | None = None,
) -> list[dict]:
    """保持故事顺序，边界按句末、镜头、节拍的优先级吸附。"""
    scene_boundaries = [0.0, float(duration), *sorted(c for c in cuts if 0 < c < duration)]
    sentences = sentence_ends or []
    beat_points = beats or []

    def nearest(value: float, points: list[float], limit: float) -> float | None:
        candidates = [point for point in points if abs(point - value) <= limit]
        return min(candidates, key=lambda point: abs(point - value)) if candidates else None

    snapped: list[dict] = []
    for clip in clips:
        original_start, original_end = float(clip["start"]), float(clip["end"])
        start = nearest(original_start, sentences, 0.8)
        if start is None:
            start = nearest(original_start, scene_boundaries, tolerance)
        if start is None:
            start = nearest(original_start, beat_points, 0.3)
        end = nearest(original_end, sentences, 0.8)
        if end is None:
            end = nearest(original_end, scene_boundaries, tolerance)
        if end is None:
            end = nearest(original_end, beat_points, 0.3)
        start = original_start if start is None else start
        end = original_end if end is None else end
        if end - start < 0.8:
            start, end = original_start, original_end
        if any(start < item["end"] and end > item["start"] for item in snapped):
            continue
        item = dict(clip)
        item["start"] = round(start, 2)
        item["end"] = round(end, 2)
        if abs(start - original_start) > 0.01 or abs(end - original_end) > 0.01:
            item["reason"] = (str(item.get("reason") or "") + "；对齐句末/镜头/节拍").strip("；")
        snapped.append(item)
    return snapped
