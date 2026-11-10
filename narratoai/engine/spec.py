"""解说任务的输入校验与归一化（纯标准库，可独立测试）。

引擎接收的是"一条视频 + 一段创作意图"，而不是 Streamlit 的 session_state。
把校验收在这里，HTTP 层与 worker 都只消费归一化后的 spec。
"""

from __future__ import annotations

import math
import re
import uuid
from pathlib import Path
from typing import Any

from engine.personas import resolve_persona, resolve_persona_strength
from engine.profiles import (
    ASPECTS,
    DEFAULT_TTS_ENGINE,
    DEFAULT_VOICE,
    SCRIPT_MODES,
    resolve_content_type,
)

TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
VOICE_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,80}$")
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
POSITIONS = {"bottom", "top", "center", "custom"}
LANGUAGES = {"中文", "英文", "中英双语", "zh-CN", "en-US"}
CLIP_MODES = {"timeline", "narration"}
ANALYSIS_MODES = {"fast", "deep"}
OUTPUT_MODES = {"narration", "edit"}
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_FONT_RE = re.compile(r"^[A-Za-z0-9._ -]{0,120}$")
POSITION_Y = {"top": 8.0, "center": 50.0, "bottom": 88.0}


class SpecError(ValueError):
    """输入不合法，HTTP 层应返回 400。"""


def new_task_id() -> str:
    return "narrate-" + uuid.uuid4().hex[:12]


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))


def _as_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(low, min(high, number))


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _color(value: Any, default: str) -> str:
    text = str(value or "").strip()
    if _COLOR_RE.match(text):
        return text.upper()
    return default


def _font_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not _FONT_RE.match(text) or "/" in text or chr(92) in text:
        raise SpecError("字幕字体名不合法")
    return text


def aspect_from_dimensions(width: Any, height: Any) -> str:
    try:
        w, h = float(width), float(height)
    except (TypeError, ValueError):
        return "9:16"
    if w <= 0 or h <= 0:
        return "9:16"
    ratio = w / h
    if ratio > 1.2:
        return "16:9"
    if ratio > 0.9:
        return "1:1"
    if ratio > 0.65:
        return "3:4"
    return "9:16"


def _parse_clock(value: str) -> float:
    value = value.strip().replace(",", ".")
    if not value:
        raise SpecError("时间戳为空")
    parts = value.split(":")
    if len(parts) > 3:
        raise SpecError(f"时间戳格式错误：{value}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise SpecError(f"时间戳格式错误：{value}") from exc
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    if seconds < 0 or not math.isfinite(seconds):
        raise SpecError(f"时间戳格式错误：{value}")
    return seconds


def format_clock(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole = int(secs)
    millis = int(round((secs - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def parse_timestamp_range(timestamp: str) -> tuple[float, float]:
    text = str(timestamp or "").strip()
    if not text:
        raise SpecError("缺少时间戳")
    # 兼容 00:00:00,000-00:00:05,000 / 00:00-00:05 / 00:00:00~00:00:05
    parts = re.split(r"\s*[-~—至]\s*", text, maxsplit=1)
    if len(parts) != 2:
        raise SpecError(f"时间戳必须是“开始-结束”：{text}")
    start = _parse_clock(parts[0])
    end = _parse_clock(parts[1])
    if end <= start:
        raise SpecError(f"时间戳结束必须大于开始：{text}")
    return start, end


def normalize_timestamp(timestamp: str) -> str:
    start, end = parse_timestamp_range(timestamp)
    return f"{format_clock(start)}-{format_clock(end)}"


def _allocate_duration(duration: float, weights: list[float]) -> list[tuple[float, float]]:
    total = sum(weights) or float(len(weights) or 1)
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    for index, weight in enumerate(weights):
        length = duration * (weight / total)
        start = cursor
        end = duration if index == len(weights) - 1 else min(duration, cursor + length)
        if end <= start:
            end = min(duration, start + max(0.5, duration / max(1, len(weights)) / 4))
        spans.append((start, end))
        cursor = end
    return spans


def _split_sentences(text: str) -> list[str]:
    cleaned = re.sub(r"[ \t]+", " ", text.replace("\r\n", "\n")).strip()
    if not cleaned:
        return []
    chunks: list[str] = []
    for line in cleaned.split("\n"):
        line = line.strip(" -·•\t")
        if not line:
            continue
        # 先按句末标点切，再把过短句合并，避免 TTS 一句只有两三个字。
        pieces = re.split(r"(?<=[。！？!?；;])", line)
        buffer = ""
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if buffer and len(buffer) + len(piece) > 48:
                chunks.append(buffer)
                buffer = ""
            buffer += piece
        if buffer:
            # 一行至少是一段，避免不同行的语义被合并。
            chunks.append(buffer)
    # 长句硬切，保证每段不超过 80 字。
    result: list[str] = []
    for chunk in chunks:
        while len(chunk) > 80:
            cut = max(chunk.rfind("，", 0, 80), chunk.rfind(",", 0, 80), chunk.rfind(" ", 0, 80))
            if cut < 20:
                cut = 80
            result.append(chunk[: cut + 1].strip())
            chunk = chunk[cut + 1 :].strip()
        if chunk:
            result.append(chunk)
    return [item for item in result if re.search(r"[\w\u4e00-\u9fff]", item)]


def script_from_text(
    text: str,
    duration: float = 60.0,
    coverage: str = "full",
    target_duration: float | None = None,
) -> list[dict[str, Any]]:
    """把用户粘贴的解说稿分配到时间轴。

    coverage=timeline 全片时按整条视频分配；highlights 时把片段均匀铺在全片，
    但每段只占目标时长内的一小段，避免只取开头、也避免整条长视频都配音。
    """
    sentences = _split_sentences(text)
    if not sentences:
        raise SpecError("文稿为空，无法生成解说脚本")
    if len(sentences) > 200:
        raise SpecError("文稿段落过多，请控制在 200 段以内")
    source_duration = max(5.0, float(duration or 60.0))
    target = max(5.0, float(target_duration or 60.0))
    if str(coverage or "").lower() == "highlights" and target < source_duration:
        segment = max(1.0, target / len(sentences))
        segment = min(segment, source_duration)
        max_start = max(0.0, source_duration - segment)
        count = len(sentences)
        starts = [0.0] if count == 1 else [max_start * index / (count - 1) for index in range(count)]
        spans = [(start, min(source_duration, start + segment)) for start in starts]
        weights = [1.0] * len(sentences)
    else:
        weights = [max(1.0, len(sentence)) for sentence in sentences]
        spans = _allocate_duration(source_duration, weights)
    return [
        {
            "_id": index + 1,
            "narration": sentence,
            "picture": sentence,
            "timestamp": f"{format_clock(start)}-{format_clock(end)}",
            "OST": 2,
        }
        for index, (sentence, (start, end)) in enumerate(zip(sentences, spans))
    ]


def normalize_script_items(items: Any, duration: float = 60.0) -> list[dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise SpecError("脚本必须是非空数组")
    if len(items) > 200:
        raise SpecError("脚本片段过多，请控制在 200 段以内")
    normalized: list[dict[str, Any]] = []
    default_spans = _allocate_duration(
        max(5.0, float(duration or 60.0)),
        [max(1.0, len(str((item or {}).get("narration") or ""))) if isinstance(item, dict) else 1.0 for item in items],
    )
    used_timestamps: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SpecError(f"脚本第 {index + 1} 段不是对象")
        narration = _text(item.get("narration"), 500)
        try:
            ost = int(item.get("OST", 2))
        except (TypeError, ValueError):
            ost = 2
        if not narration and ost != 1:
            raise SpecError(f"脚本第 {index + 1} 段缺少 narration")
        timestamp = item.get("timestamp")
        if timestamp:
            normalized_timestamp = normalize_timestamp(str(timestamp))
        else:
            start, end = default_spans[index]
            normalized_timestamp = f"{format_clock(start)}-{format_clock(end)}"
        if ost not in (0, 1, 2):
            raise SpecError(f"脚本第 {index + 1} 段的 OST 只能是 0/1/2")
        if normalized_timestamp in used_timestamps:
            raise SpecError(f"脚本第 {index + 1} 段时间戳重复：{normalized_timestamp}")
        used_timestamps.add(normalized_timestamp)
        normalized_item = {
            "_id": index + 1,
            "narration": narration,
            "picture": _text(item.get("picture"), 500) or narration,
            "timestamp": normalized_timestamp,
            "OST": ost,
        }
        for key in (
            "segment_id", "story_role", "reason", "evidence", "estimated_duration",
            "emotion", "voice_name", "voice_rate", "voice_pitch", "quality_flags",
            "score", "review_reason", "clip_duration", "transition", "morph_preview_status",
        ):
            if key in item:
                normalized_item[key] = item[key]
        normalized.append(normalized_item)
    return normalized


def normalize_spec(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SpecError("请求体必须是 JSON 对象")

    raw_path = _text(payload.get("video_path"), 2000)
    if not raw_path:
        raise SpecError("缺少 video_path")
    try:
        video_path = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SpecError(f"视频文件不存在：{raw_path}") from exc
    if not video_path.is_file():
        raise SpecError(f"视频文件不存在：{raw_path}")
    if video_path.suffix.lower() not in VIDEO_SUFFIXES:
        raise SpecError(f"不支持的视频格式：{video_path.suffix}")

    task_id = _text(payload.get("task_id"), 80) or new_task_id()
    if not TASK_ID_RE.match(task_id):
        raise SpecError("task_id 只能包含字母、数字、下划线和连字符")

    content_type = resolve_content_type(payload.get("content_type"))
    persona = resolve_persona(payload.get("persona"))
    persona_strength = resolve_persona_strength(payload.get("persona_strength"))
    mode = str(payload.get("mode") or "auto").strip().lower()
    if mode not in {item["value"] for item in SCRIPT_MODES}:
        mode = "auto"

    aspect = str(payload.get("aspect") or "auto").strip()
    if aspect not in {item["value"] for item in ASPECTS}:
        aspect = "auto"
    if aspect == "auto":
        aspect = aspect_from_dimensions(payload.get("video_width"), payload.get("video_height"))

    voice_name = str(payload.get("voice_name") or DEFAULT_VOICE).strip()
    if not VOICE_RE.match(voice_name):
        raise SpecError("voice_name 含有非法字符")

    tts_engine = str(payload.get("tts_engine") or DEFAULT_TTS_ENGINE).strip()
    if not re.match(r"^[a-z0-9_]{1,40}$", tts_engine):
        raise SpecError("tts_engine 含有非法字符")

    language = str(payload.get("language") or "中文").strip()
    if language not in LANGUAGES:
        language = "中文"
    clip_mode = str(payload.get("clip_mode") or "timeline").strip().lower()
    if clip_mode not in CLIP_MODES:
        clip_mode = "timeline"
    coverage = str(payload.get("coverage") or "highlights").strip().lower()
    if coverage not in {"highlights", "full"}:
        coverage = "highlights"
    analysis_mode = str(payload.get("analysis_mode") or "deep").strip().lower()
    if analysis_mode not in ANALYSIS_MODES:
        analysis_mode = "deep"
    output_mode = str(payload.get("output_mode") or "narration").strip().lower()
    if output_mode not in OUTPUT_MODES:
        output_mode = "narration"

    duration = _as_float(payload.get("video_duration"), 0.0, 0.0, 86400.0)
    target_duration = _as_int(payload.get("target_duration"), 60, 5, 7200)
    if coverage == "full" and duration > 0:
        target_duration = max(5, min(int(round(duration)), 7200))
    subtitle_position = str(payload.get("subtitle_position") or "bottom").strip()
    if subtitle_position not in POSITIONS:
        subtitle_position = "bottom"
    custom_position = _as_float(payload.get("custom_position"), 70.0, 0.0, 100.0)
    default_y = POSITION_Y.get(subtitle_position, custom_position)
    subtitle_y_percent = _as_float(payload.get("subtitle_y_percent"), default_y, 0.0, 99.0)
    font_name = _font_name(payload.get("font_name", "Noto Sans CJK SC"))
    font_size = _as_int(payload.get("font_size"), 40, 20, 160)
    text_fore_color = _color(payload.get("text_fore_color"), "#FFFFFF")
    stroke_color = _color(payload.get("stroke_color"), "#000000")
    stroke_width = _as_float(payload.get("stroke_width"), 0.8, 0.0, 10.0)
    subtitle_mask_enabled = bool(payload.get("subtitle_mask_enabled", False))
    subtitle_mask_y = _as_float(payload.get("subtitle_mask_y"), 78.0, 0.0, 99.0)
    subtitle_mask_height = _as_float(payload.get("subtitle_mask_height"), 14.0, 2.0, 60.0)
    subtitle_mask_blur = _as_int(payload.get("subtitle_mask_blur"), 18, 0, 60)
    subtitle_mask_opacity = _as_int(payload.get("subtitle_mask_opacity"), 82, 0, 100)

    script_items = payload.get("script_items")
    script_text = _text(payload.get("script_text"), 20000)
    transcript_text = _text(payload.get("transcript_text"), 50000)
    if script_items is not None:
        mode = "script"
    elif script_text:
        mode = "script"
    elif transcript_text and mode in {"auto", "transcript"}:
        mode = "transcript"

    if mode == "script":
        if script_items is not None:
            items = normalize_script_items(script_items, duration or target_duration)
        elif script_text:
            items = script_from_text(
                script_text,
                duration or target_duration,
                coverage=coverage,
                target_duration=target_duration,
            )
        else:
            raise SpecError("使用自己的文稿时，需要提供 script_text 或 script_items")
    elif mode == "transcript":
        if not transcript_text:
            raise SpecError("按口播文稿生成时，需要提供 transcript_text")
        items = []
    elif mode == "asr":
        # 由 worker 调用共享 ASR 识别视频口播；也允许预置 transcript_text 以跳过识别。
        items = []
    else:
        items = []

    if not duration:
        duration = float(target_duration)

    spec = {
        "task_id": task_id,
        "video_path": str(video_path),
        "video_name": video_path.name,
        "title": _text(payload.get("title"), 80) or video_path.stem,
        "video_duration": round(duration, 3),
        "video_width": _as_int(payload.get("video_width"), 0, 0, 100000),
        "video_height": _as_int(payload.get("video_height"), 0, 0, 100000),
        "content_type": content_type,
        "persona": persona,
        "persona_strength": persona_strength,
        "topic": _text(payload.get("topic"), 500),
        "instruction": _text(payload.get("instruction"), 1500),
        "tone": _text(payload.get("tone"), 200),
        "target_duration": target_duration,
        "language": language,
        "mode": mode,
        "clip_mode": clip_mode,
        "coverage": coverage,
        "analysis_mode": analysis_mode,
        "output_mode": output_mode,
        "pause_between": bool(payload.get("pause_between", True)),
        "pause_seconds": _as_float(payload.get("pause_seconds"), 0.4, 0.0, 6.0),
        "review_mode": bool(payload.get("review_mode", True)),
        "loudness_normalize": bool(payload.get("loudness_normalize", True)),
        "visual_polish": bool(payload.get("visual_polish", True)),
        "tts_pronunciation": bool(payload.get("tts_pronunciation", True)),
        "deep_segment_seconds": _as_float(payload.get("deep_segment_seconds"), 30.0, 10.0, 120.0),
        "deep_frame_interval": _as_float(payload.get("deep_frame_interval"), 1.5, 0.5, 5.0),
        "deep_scene_detection": bool(payload.get("deep_scene_detection", True)),
        "deep_vision_batch_size": _as_int(payload.get("deep_vision_batch_size"), 8, 1, 20),
        "aspect": aspect,
        "voice_name": voice_name,
        "tts_engine": tts_engine,
        "voice_rate": _as_float(payload.get("voice_rate"), 1.05, 0.5, 2.0),
        "voice_emotion": _text(payload.get("voice_emotion"), 20) if _text(payload.get("voice_emotion"), 20) in {"平静", "激动", "哽咽", "幽默"} else "平静",
        "voice_pitch": _as_float(payload.get("voice_pitch"), 1.0, 0.5, 2.0),
        "original_volume": _as_float(payload.get("original_volume"), 0.15, 0.0, 1.5),
        "tts_volume": _as_float(payload.get("tts_volume"), 1.0, 0.0, 2.0),
        "subtitles": bool(payload.get("subtitles", True)),
        "font_name": font_name,
        "font_size": font_size,
        "text_fore_color": text_fore_color,
        "stroke_color": stroke_color,
        "stroke_width": stroke_width,
        "subtitle_position": subtitle_position,
        "subtitle_max_chars": _as_int(payload.get("subtitle_max_chars"), 16, 6, 30),
        "custom_position": custom_position,
        "subtitle_y_percent": subtitle_y_percent,
        "subtitle_mask_enabled": subtitle_mask_enabled,
        "subtitle_mask_y": subtitle_mask_y,
        "subtitle_mask_height": subtitle_mask_height,
        "subtitle_mask_blur": subtitle_mask_blur,
        "subtitle_mask_opacity": subtitle_mask_opacity,
        "frame_interval": _as_float(payload.get("frame_interval"), 3.0, 1.0, 10.0),
        "vision_batch_size": _as_int(payload.get("vision_batch_size"), 10, 1, 20),
        "vision_max_concurrency": _as_int(payload.get("vision_max_concurrency"), 2, 1, 6),
        "script_items": items,
        "script_text": script_text if not items else "",
        "transcript_text": transcript_text if mode == "transcript" else "",
        "profile_snapshot": payload.get("profile_snapshot") if isinstance(payload.get("profile_snapshot"), dict) else {},
        "pronunciation": {
            str(key)[:80]: str(value)[:80]
            for key, value in (payload.get("pronunciation") or {}).items()
        } if isinstance(payload.get("pronunciation"), dict) else {},
        "variant_group_id": _text(payload.get("variant_group_id"), 80),
        "variant_key": _text(payload.get("variant_key"), 20),
    }
    return spec
