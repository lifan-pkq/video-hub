"""Script beats: editable skeleton, or one strict LLM draft."""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from engine.errors import EngineError
from engine.llm import complete_json
from engine.schemas import Beat, ScriptResponse

PURPOSES = {"hook", "explain", "evidence", "turn", "close"}
SYSTEM = """你是解说编剧。只根据给定转写和画面说明写口播，禁止编造没有出现的人物、数字、品牌和情节。
输出严格 JSON：
{"beats":[{"id":"b1","start":0.0,"end":4.5,"purpose":"hook","narration":"不超过40个汉字"}]}
purpose 只能是 hook、explain、evidence、turn、close。
规则：
- 时间戳落在源视频时长内，按时间排序，互不重叠
- 每段画面时长要装得下这句口播，中文大约每秒 4 个字
- 3 到 8 段
- 第一段要有具体信息，禁止「大家好」「今天给大家」这类空开头
- 不要输出 JSON 以外的文字"""


def _purpose(value: str, index: int, total: int) -> str:
    if value in PURPOSES:
        return value
    if index == 0:
        return "hook"
    if index == total - 1:
        return "close"
    return "explain"


def normalize_beats(raw_beats: list[dict], duration: float) -> tuple[list[dict], list[str]]:
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
        if not narration:
            warnings.append(f"{beat_id} 口播为空，已丢弃")
            continue
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
        fixed.append({
            "id": beat_id,
            "start": round(start, 2),
            "end": round(end, 2),
            "narration": narration,
            "purpose": purpose,
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
        budget = (beat["end"] - beat["start"]) * 4.3
        if len(beat["narration"]) > budget * 1.35:
            warnings.append(
                f"{beat['id']} 大约 {len(beat['narration'])} 字，画面只有 {beat['end'] - beat['start']:.1f}s，渲染时可能被拒绝"
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
    beats, warnings = normalize_beats(raw, duration)
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
    return normalize_beats(raw, duration)
