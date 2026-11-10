"""Script beats: editable skeleton, or one strict LLM draft."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from engine.errors import EngineError
from engine.llm import complete_json
from engine.schemas import Beat, ScriptResponse

PURPOSES = {"hook", "explain", "evidence", "turn", "close"}
# Comfortable Mandarin pacing used when fitting AI/skeleton drafts.
# Review/render still allow up to 4.3 chars/s * 1.35 stretch; we aim well below that
# so TTS does not need to rush (atempo) and review will not warn.
SPEECH_CPS = 3.4
SPEECH_BREATH = 0.3
MAX_AUTO_FREEZE = 12.0
MIN_AUTO_SPEED = 0.85
PREFERRED_MAX_CHARS = 28
REVIEW_CPS = 4.3
REVIEW_STRETCH = 1.35

SYSTEM = """你是解说编剧。只根据给定转写和画面说明写口播，禁止编造没有出现的人物、数字、品牌和情节。
输出严格 JSON：
{"beats":[{"id":"b1","start":0.0,"end":6.0,"purpose":"hook","narration":"一句完整具体的口播","speed":1,"freeze":0,"pause":false}]}
purpose 只能是 hook、explain、evidence、turn、close。
规则：
- 时间戳落在源视频时长内，按时间排序，互不重叠
- 每段只写一句能一口气说完的口播，大约 12 到 28 个汉字；不要写长复合句，也不要把两句塞进一段
- 口播要具体、有信息量：保留关键事实、数字、动作和对象，不要注水，也不要写成提纲或关键词堆砌
- 从容配音：按每秒大约 3 个汉字估算。画面时长 = (end-start)/speed + freeze，必须大于口播字数/3。宁肯画面停住，也不要赶着把字塞完
- 5 到 12 段。镜头信息多就多切几段，保证每句都听得清
- 某一小段口播需要的时间比画面长时：先把 end 往后拉（不与下一段重叠）；还不够就加 freeze（段尾静帧，通常 0.8 到 4 秒），让画面停住把话说完。不要靠加快语速硬塞
- 第一段要有具体信息，禁止「大家好」「今天给大家」这类空开头
- 某一小段只让画面走、不配音时，设 "pause": true 且 "narration": ""。不要用在第一段，也不要连续超过两段
- speed 默认 1（0.5 到 2）。没有特殊需要不要加快；只有口播仍偏紧时才略降到 0.85–0.95
- 不要输出 JSON 以外的文字"""


def _clamp_speed(value) -> float:
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    if speed != speed:
        return 1.0
    return max(0.5, min(2.0, speed))


def _clamp_freeze(value) -> float:
    try:
        freeze = float(value)
    except (TypeError, ValueError):
        return 0.0
    if freeze != freeze or freeze < 0:
        return 0.0
    return min(30.0, freeze)


def _safe_name(value, prefix: str) -> str:
    name = str(value or "").strip()
    if not name or "/" in name or "\\" in name or not name.startswith(prefix):
        return ""
    return name


def _purpose(value: str, index: int, total: int) -> str:
    if value in PURPOSES:
        return value
    if index == 0:
        return "hook"
    if index == total - 1:
        return "close"
    return "explain"


def _chars(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "").strip()))


def _picture_seconds(start: float, end: float, speed: float, freeze: float) -> float:
    return max(0.1, (float(end) - float(start)) / max(float(speed) or 1.0, 0.5) + max(0.0, float(freeze)))


def _needed_seconds(text: str) -> float:
    count = _chars(text)
    if count <= 0:
        return 0.0
    return count / SPEECH_CPS + SPEECH_BREATH


def _speech_rate(value, default: float = 1.0) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return default
    if rate != rate:
        return default
    return max(0.5, min(2.0, rate))


def _pack_clauses(chunks: list[str], max_len: int = PREFERRED_MAX_CHARS) -> list[str]:
    packed: list[str] = []
    buf = ""
    for chunk in chunks:
        candidate = buf + chunk
        if buf and _chars(candidate) > max_len:
            packed.append(buf)
            buf = chunk
        else:
            buf = candidate
    if buf:
        packed.append(buf)
    return packed or chunks


def split_narration(text: str) -> list[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    parts = [item.strip() for item in re.split(r"(?<=[。！？!?])", cleaned) if item.strip()]
    if len(parts) >= 2:
        return parts
    if _chars(cleaned) > PREFERRED_MAX_CHARS:
        clauses = [item.strip() for item in re.split(r"(?<=[，、；;])", cleaned) if item.strip()]
        if len(clauses) >= 2:
            return _pack_clauses(clauses)
    # Last resort: a wall of text with no punctuation still must be speakable.
    count = _chars(cleaned)
    if count > 40:
        chars = re.sub(r"\s+", "", cleaned)
        chunks = []
        start = 0
        while start < len(chars):
            end = min(len(chars), start + PREFERRED_MAX_CHARS)
            if 0 < len(chars) - end < 8:
                end = len(chars)
            chunks.append(chars[start:end])
            start = end
        return chunks
    return [cleaned]


def split_long_beats(raw_beats: list[dict], max_beats: int = 18) -> tuple[list[dict], list[str]]:
    items = sorted(raw_beats, key=lambda item: float(item.get("start") or 0))
    out: list[dict] = []
    notes: list[str] = []
    remaining_items = len(items)
    for item in items:
        remaining_items -= 1
        narration = str(item.get("narration") or "").strip()
        pause = bool(item.get("pause")) or not narration
        parts = [] if pause else split_narration(narration)
        slots_left = max_beats - len(out) - remaining_items
        if pause or len(parts) <= 1 or slots_left < 2:
            out.append(item)
            continue
        if len(parts) > slots_left:
            keep = max(2, slots_left)
            parts = parts[: keep - 1] + ["".join(parts[keep - 1 :])]
        try:
            start = float(item.get("start") or 0)
            end = float(item.get("end") or start + 1)
        except (TypeError, ValueError):
            out.append(item)
            continue
        if end <= start:
            out.append(item)
            continue
        span = end - start
        weights = [max(1, _chars(part)) for part in parts]
        total_w = sum(weights) or len(parts)
        cursor = start
        base_id = str(item.get("id") or f"b{len(out) + 1}")
        for index, part in enumerate(parts):
            piece_end = end if index == len(parts) - 1 else min(end, cursor + span * (weights[index] / total_w))
            if piece_end <= cursor:
                piece_end = min(end, cursor + 0.4)
            piece = dict(item)
            piece["id"] = base_id if index == 0 else f"{base_id}s{index + 1}"
            piece["start"] = cursor
            piece["end"] = piece_end
            piece["narration"] = part
            if index > 0 and str(piece.get("purpose") or "") == "hook":
                piece["purpose"] = "explain"
            out.append(piece)
            cursor = piece_end
        notes.append(f"{base_id} 已拆成 {len(parts)} 句，方便从容配音")
    return out, notes


def fit_beats_pacing(raw_beats: list[dict], duration: float) -> tuple[list[dict], list[str]]:
    """Slow, safe pacing for AI/skeleton drafts: shorter lines, more time, freeze if needed."""
    duration = float(duration or 0)
    split, notes = split_long_beats(raw_beats)
    fitted: list[dict] = []
    for item in split:
        beat = dict(item)
        try:
            start = float(beat.get("start") or 0)
            end = float(beat.get("end") or 0)
        except (TypeError, ValueError):
            fitted.append(beat)
            continue
        narration = re.sub(r"\s+", "", str(beat.get("narration") or "").strip())
        beat["start"] = start
        beat["end"] = end
        beat["speed"] = _clamp_speed(beat.get("speed", 1))
        beat["freeze"] = _clamp_freeze(beat.get("freeze", 0))
        beat["pause"] = bool(beat.get("pause")) or not narration
        fitted.append(beat)

    total = len(fitted)
    for index, beat in enumerate(fitted):
        if beat.get("pause"):
            continue
        narration = str(beat.get("narration") or "")
        needed = _needed_seconds(narration)
        start = float(beat["start"])
        end = float(beat["end"])
        speed = float(beat["speed"])
        freeze = float(beat["freeze"])
        picture = _picture_seconds(start, end, speed, freeze)
        if picture >= needed:
            continue

        next_start = float(fitted[index + 1]["start"]) if index + 1 < total else (duration or end)
        if duration > 0:
            next_start = min(next_start, duration)
        gap = max(0.0, next_start - end)
        if gap > 0.05:
            take = min(gap, needed - picture)
            if take > 0.05:
                end = round(end + take, 2)
                beat["end"] = end
                notes.append(f"{beat.get('id')} 已拉长到 {end:.1f}s，给口播留时间")

        picture = _picture_seconds(start, end, speed, freeze)
        if picture < needed:
            add = min(needed - picture, MAX_AUTO_FREEZE - freeze)
            if add > 0.05:
                freeze = round(freeze + add, 3)
                beat["freeze"] = freeze
                notes.append(f"{beat.get('id')} 已加 {add:.1f}s 静止，让画面停住把话说完")

        picture = _picture_seconds(start, end, speed, freeze)
        if picture < needed and speed > MIN_AUTO_SPEED:
            span = max(0.4, end - start)
            target_speed = span / max(0.4, needed - freeze)
            new_speed = max(MIN_AUTO_SPEED, min(speed, target_speed))
            if new_speed < speed - 0.02:
                beat["speed"] = round(new_speed, 3)
                notes.append(f"{beat.get('id')} 已把画面速度调到 {new_speed:.2f}，口播更从容")

        picture = _picture_seconds(start, end, float(beat["speed"]), float(beat["freeze"]))
        # Last resort: more freeze (up to 20s) rather than truncating good narration.
        if _chars(narration) > picture * REVIEW_CPS:
            extra = min(_needed_seconds(narration) - picture, 20.0 - float(beat["freeze"]))
            if extra > 0.05:
                beat["freeze"] = round(float(beat["freeze"]) + extra, 3)
                notes.append(f"{beat.get('id')} 再补 {extra:.1f}s 静止，避免渲染被拒")
    return fitted, notes


def normalize_beats(raw_beats: list[dict], duration: float, speech_rate: float = 1.0) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    items = sorted(raw_beats, key=lambda item: float(item.get("start") or 0))
    fixed: list[dict] = []
    for index, item in enumerate(items):
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            warnings.append(f"第 {index + 1} 段时间不是数字，已丢弃")
            continue
        narration = re.sub(r"\s+", "", str(item.get("narration") or "").strip())
        beat_id = str(item.get("id") or f"b{index + 1}")
        pause = bool(item.get("pause")) or not narration
        if not narration and not item.get("pause"):
            warnings.append(f"{beat_id} 口播为空，按暂停解说保留画面")
        if len(narration) > 200:
            narration = narration[:200]
            warnings.append(f"{beat_id} 超过 200 字，已截断")
        if start >= duration:
            warnings.append(f"{beat_id} 起点超出片长，已丢弃")
            continue
        if end <= start:
            warnings.append(f"{beat_id} 结束时间不晚于开始，已丢弃")
            continue
        if end > duration + 0.05:
            end = duration
            warnings.append(f"{beat_id} 结束时间超出片长，已收到 {duration:.2f}s")
        if fixed and start < fixed[-1]["end"] - 0.01:
            start = fixed[-1]["end"]
            warnings.append(f"{beat_id} 与上一段重叠，已后移起点")
        if end - start < 0.4:
            warnings.append(f"{beat_id} 不足 0.4 秒，已丢弃")
            continue
        purpose = _purpose(str(item.get("purpose") or ""), len(fixed), max(len(items), 1))
        insert_kind = str(item.get("insert_kind") or "none")
        if insert_kind not in {"none", "image", "video"}:
            insert_kind = "none"
        insert_mode = str(item.get("insert_mode") or "fullscreen")
        if insert_mode not in {"overlay", "fullscreen"}:
            insert_mode = "fullscreen"
        try:
            image_id = int(item.get("image_id") or 0)
        except (TypeError, ValueError):
            image_id = 0
        try:
            video_id = int(item.get("video_id") or 0)
        except (TypeError, ValueError):
            video_id = 0
        fixed.append({
            "id": beat_id,
            "start": round(start, 2),
            "end": round(end, 2),
            "narration": narration,
            "purpose": purpose,
            "pause": pause,
            "speed": round(_clamp_speed(item.get("speed", 1)), 3),
            "freeze": round(_clamp_freeze(item.get("freeze", 0)), 3),
            "insert_kind": insert_kind,
            "insert_mode": insert_mode,
            "image_id": max(0, image_id),
            "image_name": _safe_name(item.get("image_name"), "ai_"),
            "video_id": max(0, video_id),
            "video_name": _safe_name(item.get("video_name"), "ai_"),
        })
    if not fixed:
        raise EngineError(
            "没有得到任何可用的解说段",
            code="script_empty",
            action="检查转写是否为空。可以改用逐句骨架，或把口播直接写进审阅表。",
            status=422,
        )
    if len(fixed) > 20:
        raise EngineError(
            f"解说段有 {len(fixed)} 段，超过 20 段",
            code="script_too_long",
            action="合并短句后再渲染。",
            status=422,
        )
    total = len(fixed)
    for index, beat in enumerate(fixed):
        beat["purpose"] = _purpose(beat["purpose"], index, total)
        if beat.get("pause"):
            continue
        picture = _picture_seconds(beat["start"], beat["end"], beat.get("speed") or 1, beat.get("freeze") or 0)
        budget = picture * REVIEW_CPS * _speech_rate(speech_rate)
        if len(beat["narration"]) > budget * REVIEW_STRETCH:
            warnings.append(
                f"{beat['id']} 大约 {len(beat['narration'])} 字，画面约 {picture:.1f}s，渲染时可能被拒绝"
            )
    try:
        validated = [Beat.model_validate(beat).model_dump() for beat in fixed]
    except ValidationError as exc:
        raise EngineError(
            "解说段校验失败：" + exc.errors()[0].get("msg", ""),
            code="script_schema",
            action="修改时间或口播后再保存。",
            status=422,
        ) from exc
    return validated, warnings


def skeleton_beats(segments: list[dict], duration: float, target: float) -> tuple[list[dict], list[str]]:
    usable = [seg for seg in segments if str(seg.get("text") or "").strip()]
    if not usable:
        raise EngineError(
            "没有转写，无法生成逐句骨架",
            code="script_empty",
            action="先完成理解，或在转写表里至少粘贴一句，再生成骨架。",
            status=422,
        )
    target = max(5.0, min(float(target or duration or 30), duration or 30))
    # Keep source timing. Merge tiny lines until each beat is about target/n, n around 4-6.
    desired = 4 if target >= 12 else 2
    chunk = max(1.2, target / desired)
    groups: list[list[dict]] = []
    current: list[dict] = []
    span = 0.0
    for seg in usable:
        current.append(seg)
        span = float(current[-1]["end"]) - float(current[0]["start"])
        if span >= chunk and len(groups) < 7:
            groups.append(current)
            current = []
    if current:
        if groups and span < 0.8:
            groups[-1].extend(current)
        else:
            groups.append(current)
    raw = []
    for index, group in enumerate(groups, start=1):
        text = "".join(str(item.get("text") or "").strip() for item in group)
        raw.append({
            "id": f"b{index}",
            "start": float(group[0]["start"]),
            "end": float(group[-1]["end"]),
            "narration": text[:200],
            "purpose": "explain",
        })
    fitted, pace_notes = fit_beats_pacing(raw, duration)
    beats, warnings = normalize_beats(fitted, duration)
    warnings = [*pace_notes, *warnings]
    warnings.insert(0, "未调用大模型。这是按转写切出的骨架，没有润色。")
    return beats, warnings


def draft_beats(
    *,
    segments: list[dict],
    frames: list[dict],
    duration: float,
    instruction: str,
    tone: str,
    target: float,
    language: str,
) -> tuple[list[dict], list[str]]:
    if not segments and not any(frame.get("caption") for frame in frames):
        raise EngineError(
            "没有转写，也没有画面说明，不能写解说",
            code="script_empty",
            action="先补转写，或配置视觉模型后再理解一次。",
            status=422,
        )
    transcript = [
        {"start": seg["start"], "end": seg["end"], "text": str(seg.get("text") or "")[:120]}
        for seg in segments
    ]
    notes = [
        {"time": frame.get("time"), "caption": frame.get("caption") or frame.get("caption_error") or "无说明"}
        for frame in frames[:12]
    ]
    payload = {
        "duration": round(duration, 2),
        "target_duration": target,
        "language": language or "中文",
        "tone": (tone or "清楚、具体")[:80],
        "instruction": (instruction or "")[:800],
        "transcript": transcript[:80],
        "frames": notes,
    }
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > 8000:
        text = text[:8000] + "…（上下文已截断）"
    parsed = complete_json(system=SYSTEM, user=text, model=ScriptResponse, timeout=90)
    raw = [beat.model_dump() for beat in parsed.beats]
    fitted, pace_notes = fit_beats_pacing(raw, duration)
    beats, warnings = normalize_beats(fitted, duration)
    return beats, [*pace_notes, *warnings]
