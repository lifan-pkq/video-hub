"""Sparse frames plus optional vision captions."""

from __future__ import annotations

from pathlib import Path

from engine.errors import EngineError
from engine.llm import complete_json
from engine.media import ffmpeg
from engine.schemas import CaptionResponse
from engine.settings import vision_base_url, vision_configured, vision_model

# 单片安全上限，避免 2fps 对长视频打爆视觉接口
MAX_FRAME_CAP = 60


def pick_times(
    duration: float,
    scenes: list[float],
    max_frames: int | None = None,
    *,
    frames_per_second: float = 2.0,
) -> list[float]:
    """按每秒帧数均匀抽帧；镜头切点只作参考，不再压到个位数。"""
    fps = max(0.5, min(4.0, float(frames_per_second) or 2.0))
    if duration <= 0.4:
        return [0.0]
    target = int(max_frames) if max_frames is not None else int(round(duration * fps))
    target = max(1, min(MAX_FRAME_CAP, target))
    times = [round(duration * (index + 0.5) / target, 2) for index in range(target)]
    cuts = [t for t in scenes if 0.15 < t < duration - 0.15]
    if cuts and len(cuts) <= target:
        snapped: list[float] = []
        used: set[int] = set()
        for cut in cuts:
            index = min(
                range(len(times)),
                key=lambda i: abs(times[i] - cut) if i not in used else 1e9,
            )
            used.add(index)
            snapped.append(round(cut, 2))
        for index, value in enumerate(times):
            if index not in used:
                snapped.append(value)
        times = sorted({round(item, 2) for item in snapped})[:target]
    return times


def _brightness(image: Path) -> float | None:
    import subprocess

    proc = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(image),
            "-vf",
            "scale=1:1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
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
            system=(
                "你只描述画面里看得见的内容。"
                '输出 JSON {"caption":"不超过80个汉字的画面说明"}。'
                "不要猜测身份，不要补剧情。若说明偏长，自行压缩到 80 字以内。"
            ),
            user="描述这一帧。",
            model=CaptionResponse,
            images=[("image/jpeg", blob)],
            base_url=vision_base_url(),
            # 不传 api_key：走 VISION_POOL / AI_POOL 多 Key 轮换
            model_name=vision_model(),
            timeout=60,
        )
    except EngineError as exc:
        message = exc.message or ""
        if "at most" in message or "max_length" in message or "String should" in message:
            message = "画面说明超长或字段不合约定，请重试这一帧。"
        return "", message
    caption = (parsed.caption or "").strip()
    if not caption:
        return "", "视觉模型返回了空说明。"
    return caption[:200], ""


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
        frames.append(
            {
                "time": round(time, 2),
                "image": name,
                "caption": text,
                "caption_error": error,
                "brightness": _brightness(target),
            }
        )
    return frames
