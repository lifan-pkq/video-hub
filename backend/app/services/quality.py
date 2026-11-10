"""可解释的素材质量分；未执行的检测明确标 unknown。"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from app.db import update_video
from app.services.audio_analysis import analyze_loudness


def _probe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration,bit_rate:stream=codec_type,width,height,avg_frame_rate",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return json.loads(result.stdout or "{}") if result.returncode == 0 else {}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def _picture_metrics(path: Path) -> dict[str, float]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-t", "30",
             "-vf", "signalstats,metadata=mode=print", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    values: dict[str, list[float]] = {"YAVG": [], "YHIGH": [], "YLOW": [], "YDIF": []}
    for line in (result.stderr or "").splitlines():
        for key in values:
            marker = f"lavfi.signalstats.{key}="
            if marker in line:
                try:
                    values[key].append(float(line.split(marker, 1)[1].split()[0]))
                except (ValueError, IndexError):
                    pass
    return {
        key: sum(items) / len(items)
        for key, items in values.items() if items
    }


def assess_source(path: Path) -> dict[str, Any]:
    probe = _probe(path)
    video = next((item for item in probe.get("streams", []) if item.get("codec_type") == "video"), {})
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    bitrate = int(float((probe.get("format") or {}).get("bit_rate") or 0))
    pixels = width * height
    resolution_score = min(100, pixels / (1920 * 1080) * 100) if pixels else 0
    bitrate_score = min(100, bitrate / 8_000_000 * 100) if bitrate else 0
    picture = _picture_metrics(path)
    brightness = picture.get("YAVG")
    contrast = picture.get("YHIGH", 0) - picture.get("YLOW", 0) if picture else 0
    picture_score = 50.0
    flags: list[str] = []
    if brightness is not None:
        picture_score = max(0, 100 - abs(brightness - 128) / 1.28)
        if brightness < 35:
            flags.append("过暗")
        elif brightness > 220:
            flags.append("过亮")
    if picture and contrast < 15:
        flags.append("低对比")
    loudness = analyze_loudness(path)
    integrated = loudness.get("integrated_lufs")
    audio_score = max(0, 100 - abs(integrated + 16) * 4) if integrated is not None else 50
    score = round(
        resolution_score * 0.3 + bitrate_score * 0.2 + picture_score * 0.25 + audio_score * 0.25,
        1,
    )
    if pixels and pixels < 640 * 360:
        flags.append("分辨率偏低")
    if bitrate and bitrate < 500_000:
        flags.append("码率偏低")
    return {
        "schema_version": 1,
        "score": score,
        "level": "low" if score < 40 else ("medium" if score < 70 else "high"),
        "flags": flags,
        "metrics": {
            "width": width,
            "height": height,
            "bitrate": bitrate,
            "brightness": brightness,
            "contrast": contrast,
            **loudness,
        },
        "checks": {
            "resolution": "ok" if pixels else "unknown",
            "bitrate": "ok" if bitrate else "unknown",
            "sharpness": "estimated_from_signalstats" if picture else "unknown",
            "audio": "ok" if integrated is not None else "unknown",
            "watermark": "unknown",
        },
    }


def update_quality(video_id: int, path: Path) -> dict[str, Any]:
    quality = assess_source(path)
    update_video(
        video_id,
        quality_score=quality["score"],
        quality_json=json.dumps(quality, ensure_ascii=False),
    )
    return quality
