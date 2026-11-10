"""用词级时间生成可读、逐词高亮的 ASS 字幕，保留原始文案。"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any


def _seconds_range(value: str) -> tuple[float, float]:
    match = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)-(\d+):(\d+):(\d+(?:\.\d+)?)", value or "")
    if not match:
        return 0.0, 0.0
    hours, minutes, seconds, end_hours, end_minutes, end_seconds = match.groups()
    return (
        int(hours) * 3600 + int(minutes) * 60 + float(seconds),
        int(end_hours) * 3600 + int(end_minutes) * 60 + float(end_seconds),
    )


def _ass_time(seconds: float) -> str:
    centiseconds = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, cents = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cents:02d}"


def _wrap(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= limit:
        return text
    lines: list[str] = []
    current = ""
    for piece in re.split(r"(?<=[，。！？；,.!?;])", text):
        if current and len(current) + len(piece) > limit:
            lines.append(current)
            current = ""
        current += piece
    if current:
        lines.append(current)
    if len(lines) <= 2:
        return r"\N".join(lines)
    return "\n".join(lines)


def _start_seconds(value: str) -> float:
    match = re.match(r"(\d+):(\d+):(\d+(?:[.,]\d+)?)", str(value or ""))
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))


def _alignment_at(subject_track: list[dict[str, Any]] | None, moment: float) -> int:
    """主体在下半部时把字幕移到上方；没有轨迹时保持贴底。"""
    points = [
        point for point in (subject_track or [])
        if isinstance(point, dict) and (point.get("subject_box") or point.get("box"))
    ]
    if not points:
        return 2
    nearest = min(points, key=lambda point: abs(float(point.get("timestamp") or 0) - moment))
    box = nearest.get("subject_box") or nearest.get("box") or {}
    center_y = float(box.get("y", 0)) + float(box.get("height", 0)) / 2
    return 8 if center_y > 500 else 2


def build_events(
    script: list[dict[str, Any]],
    tts_results: list[dict[str, Any]],
    max_chars: int = 16,
    subject_track: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    by_id = {int(item["_id"]): item for item in tts_results}
    events: list[dict[str, Any]] = []
    for item in script:
        start, _ = _seconds_range(str(item.get("editedTimeRange") or ""))
        tts = by_id.get(int(item.get("_id") or 0), {})
        original = str(item.get("narration") or "").strip()
        if not original:
            continue
        words = tts.get("words") or []
        spoken = str(tts.get("text") or "")
        alignment = _alignment_at(
            subject_track,
            _start_seconds(str(item.get("timestamp") or "")) or start,
        )
        if words and spoken == original:
            cursor = start
            for word in words:
                text = html.unescape(str(word.get("text") or ""))
                word_start = start + float(word.get("start") or 0)
                word_end = start + float(word.get("end") or 0)
                if not text or word_end <= cursor:
                    continue
                events.append({
                    "start": word_start,
                    "end": word_end,
                    "text": text,
                    "karaoke": True,
                    "alignment": alignment,
                })
                cursor = word_end
        else:
            duration = max(float(tts.get("duration") or 1.0), len(original) / 10)
            events.append({
                "start": start,
                "end": start + duration,
                "text": original,
                "karaoke": False,
                "alignment": alignment,
            })
    events.sort(key=lambda event: event["start"])
    return events


def write_ass(
    output: Path,
    script: list[dict[str, Any]],
    tts_results: list[dict[str, Any]],
    font_name: str = "Noto Sans CJK SC",
    font_size: int = 54,
    max_chars: int = 16,
    keywords: list[str] | None = None,
    play_res_x: int = 1080,
    play_res_y: int = 1920,
    subject_track: list[dict[str, Any]] | None = None,
    start_offset: float = 0.0,
) -> Path:
    keywords = keywords or []
    header = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {int(play_res_x)}
PlayResY: {int(play_res_y)}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Bottom,{font_name},{font_size},&H00FFFFFF,&H0000D7FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,2,36,36,72,1
Style: Top,{font_name},{font_size},&H00FFFFFF,&H0000D7FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1.5,0,8,36,36,72,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, Effect, Text
"""
    lines = [header]
    events = build_events(
        script, tts_results, max_chars=max_chars, subject_track=subject_track
    )
    for event in events:
        start = event["start"] + start_offset
        end = event["end"] + start_offset
        style = "Top" if event["alignment"] == 8 else "Bottom"
        if event["karaoke"]:
            duration = max(1, int(round((event["end"] - event["start"]) * 100)))
            text = str(event["text"])
            color = r"{\1c&H00D7FF&}" if any(keyword and keyword in text for keyword in keywords) else ""
            text = rf"{{\k{duration}}}{color}{text}"
        else:
            text = _wrap(str(event["text"]), max_chars).replace("\n", r"\N")
        text = text.replace("{", "（").replace("}", "）") if not event["karaoke"] else text
        lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(lines), encoding="utf-8-sig")
    return output
