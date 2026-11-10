"""Sparse frames plus optional vision captions."""

from __future__ import annotations

from pathlib import Path

from engine.errors import EngineError
from engine.llm import complete_json
from engine.media import ffmpeg
from engine.schemas import CaptionResponse
from engine.settings import vision_api_key, vision_base_url, vision_configured, vision_model


def pick_times(duration: float, scenes: list[float], max_frames: int) -> list[float]:
    max_frames = max(1, min(12, max_frames))
    if duration <= 0.4:
        return [0.0]
    candidates = [t for t in scenes if 0.15 < t < duration - 0.15]
    if len(candidates) > max_frames:
        step = len(candidates) / max_frames
        candidates = [candidates[min(len(candidates) - 1, int(i * step))] for i in range(max_frames)]
    if len(candidates) < min(4, max_frames):
        count = min(max_frames, max(3, int(duration // 3) or 1))
        extra = [round(duration * (index + 1) / (count + 1), 2) for index in range(count)]
        candidates = sorted({round(item, 2) for item in [*candidates, *extra]})
    return candidates[:max_frames]


def _brightness(image: Path) -> float | None:
    import subprocess

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(image), "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0 or len(proc.stdout) < 3:
        return None
    red, green, blue = proc.stdout[:3]
    return round((0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255, 3)


def caption_image(image: Path) -> tuple[str, str]:
    if not vision_configured():
        return "", "未配置视觉模型。设置 VIDEO_HUB_VISION_MODEL（可与文本模型不同）后才能生成画面说明。"
    blob = image.read_bytes()
    try:
        parsed = complete_json(
            system="你只描述画面里看得见的内容。输出 JSON {\"caption\":\"不超过24个汉字\"}。不要猜测身份，不要补剧情。",
            user="描述这一帧。",
            model=CaptionResponse,
            images=[("image/jpeg", blob)],
            base_url=vision_base_url(),
            api_key=vision_api_key(),
            model_name=vision_model(),
            timeout=60,
        )
    except EngineError as exc:
        return "", exc.message
    caption = parsed.caption.strip()
    if not caption:
        return "", "视觉模型返回了空说明。"
    return caption[:40], ""


def extract_frames(
    video: Path,
    dest_dir: Path,
    times: list[float],
    *,
    job_id: str | None = None,
    should_cancel=None,
    caption: bool = True,
) -> list[dict]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, time in enumerate(times, start=1):
        if should_cancel and should_cancel():
            raise EngineError("任务已取消", code="cancelled", status=409)
        name = f"frame_{index:02d}.jpg"
        target = dest_dir / name
        ffmpeg(
            ["-ss", f"{time:.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(target)],
            timeout=120,
            job_id=job_id,
            should_cancel=should_cancel,
        )
        if not target.is_file() or target.stat().st_size == 0:
            raise EngineError(
                f"抽帧失败：{time:.2f}s",
                code="frame",
                action="确认视频有画面轨。",
                status=500,
            )
        text, error = caption_image(target) if caption else ("", "")
        frames.append({
            "time": round(time, 2),
            "image": name,
            "caption": text,
            "caption_error": error,
            "brightness": _brightness(target),
        })
    return frames
