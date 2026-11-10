"""FFmpeg 画面精修与按主体轨迹重构画幅，所有处理保留参数清单。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

DIMENSIONS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


def _run(command: list[str], timeout: int = 3600) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError("画面精修失败：" + (result.stderr or "")[-500:])


def polish_filter(style: dict[str, Any] | None = None) -> str:
    style = style or {}
    saturation = float(style.get("saturation") or 1.04)
    brightness = float(style.get("brightness") or 0.0)
    contrast = float(style.get("contrast") or 1.02)
    filters = [f"eq=saturation={saturation:.3f}:brightness={brightness:.3f}:contrast={contrast:.3f}"]
    if style.get("deshake", True):
        filters.append("deshake")
    filters.extend(["hqdn3d=1.2:1.2:3:3", "unsharp=5:5:0.45:5:5:0.0"])
    return ",".join(filters)


def polish_master(source: Path, target: Path, style: dict[str, Any] | None = None) -> dict[str, Any]:
    filter_graph = polish_filter(style)
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source),
        "-vf", filter_graph, "-c:v", "libx264", "-crf", "19", "-preset", "fast",
        "-c:a", "copy", "-movflags", "+faststart", str(target),
    ])
    return {"filter": filter_graph, "output": str(target)}


def enhance_key_clip(source: Path, target: Path) -> None:
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source),
        "-vf", "scale=iw*2:ih*2:flags=lanczos,unsharp=5:5:0.55:5:5:0.0",
        "-c:v", "libx264", "-crf", "18", "-preset", "slow", "-c:a", "copy", str(target),
    ])


def _piecewise_crop(steps: list[tuple[float, str]], fallback: str) -> str:
    """把按时间排序的轨迹点折成分段表达式：每点生效到下一个采样时刻为止。

    首个采样时刻之前保持 fallback（居中），避免在没有任何观测时外推主体位置。
    """
    expression = steps[-1][1]
    for index in range(len(steps) - 2, -1, -1):
        expression = f"if(lt(t,{steps[index + 1][0]:.3f}),{steps[index][1]},{expression})"
    return f"if(lt(t,{steps[0][0]:.3f}),{fallback},{expression})"


def crop_filter(
    source_width: int,
    source_height: int,
    aspect: str,
    subject_track: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    target_width, target_height = DIMENSIONS[aspect]
    ratio = target_width / target_height
    if source_width / source_height > ratio:
        crop_height = source_height
        crop_width = int(source_height * ratio) // 2 * 2
    else:
        crop_width = source_width
        crop_height = int(source_width / ratio) // 2 * 2
    crop_width = min(source_width // 2 * 2, crop_width)
    crop_height = min(source_height // 2 * 2, crop_height)
    points = sorted(subject_track or [], key=lambda item: float(item.get("timestamp") or 0))
    if not points:
        return f"crop={crop_width}:{crop_height}:(iw-{crop_width})/2:(ih-{crop_height})/2", "safe_center"
    x_values: list[tuple[float, str]] = []
    y_values: list[tuple[float, str]] = []
    for point in points:
        box = point.get("subject_box") or point.get("box") or {}
        center_x = float(box.get("x", 500)) + float(box.get("width", 0)) / 2
        center_y = float(box.get("y", 500)) + float(box.get("height", 0)) / 2
        source_x = center_x / 1000 * source_width
        source_y = center_y / 1000 * source_height
        x = f"min(max({source_x:.3f}-{crop_width}/2,0),iw-{crop_width})"
        y = f"min(max({source_y:.3f}-{crop_height}/2,0),ih-{crop_height})"
        timestamp = float(point.get("timestamp") or 0)
        x_values.append((timestamp, x))
        y_values.append((timestamp, y))
    x_expression = _piecewise_crop(x_values, "iw/2")
    y_expression = _piecewise_crop(y_values, "ih/2")
    return f"crop={crop_width}:{crop_height}:x='{x_expression}':y='{y_expression}'", "subject_track"


def reframe(
    source: Path,
    target: Path,
    aspect: str,
    subject_track: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if aspect not in DIMENSIONS:
        raise ValueError("不支持的输出画幅")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(source)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    payload = json.loads(probe.stdout or "{}")
    stream = (payload.get("streams") or [{}])[0]
    width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if not width or not height:
        raise RuntimeError("无法读取原片尺寸")
    crop, tracking = crop_filter(width, height, aspect, subject_track)
    target_width, target_height = DIMENSIONS[aspect]
    graph = f"{crop},scale={target_width}:{target_height},setsar=1"
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source), "-vf", graph,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(target),
    ])
    return {"aspect": aspect, "tracking": tracking, "filter": graph, "output": str(target)}
