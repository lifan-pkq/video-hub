"""Keep-plan for 精彩片段. Signals always produce a plan; the model may replace it."""

from __future__ import annotations

import json

from engine.errors import EngineError
from engine.llm import complete_json, configured
from engine.schemas import HighlightPlan

SYSTEM = """你是短视频剪辑师。任务是选出要留下的精彩片段，不是只标出废片。
只根据给定的口播、能量峰、镜头切换、静音/黑场和画面说明判断。禁止编造素材里没有的时间点、人物和数字。
输出严格 JSON：
{"clips":[{"video_id":1,"start":1.2,"end":6.4,"speed":1.0,"freeze":0,"reason":"不超过30字"}]}
规则：
- clips 是保留段，同一条素材内按时间排序且互不重叠
- 每段 1.2 到 15 秒，总数 2 到 10 段；素材很短时可以只有 1 段
- 优先留下：口播信息密、能量高、镜头刚切开、画面说明里有动作或结果的部分
- 避开：静音、黑场、口头禅、寒暄、与主题无关的长停顿
- speed 取 0.5 到 2.0，默认 1。只在口播明显拖沓时略加快，只在需要看清动作时略放慢
- freeze 是段尾静帧的秒数，0 到 3，默认 0。只加在一句收束或关键画面上
- reason 必须能对上口播原文或画面说明，不要编故事
- 不要输出 JSON 以外的文字"""


def _num(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _intervals(items) -> list[tuple[float, float]]:
    spans: list[tuple[float, float]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        start = _num(item.get("start"), -1)
        end = _num(item.get("end"), -1)
        if end - start >= 0.2:
            spans.append((max(0.0, start), end))
    spans.sort()
    return spans


def _waste(video: dict) -> list[tuple[float, float]]:
    spans = _intervals(video.get("silence")) + _intervals(video.get("black"))
    for item in video.get("suggestions") or []:
        if not isinstance(item, dict):
            continue
        if item.get("action") == "drop" and (item.get("recommended") or item.get("llm_action") == "drop"):
            spans.extend(_intervals([item]))
    spans.sort()
    return spans


def _segments(video: dict, duration: float) -> list[dict]:
    rows = []
    for item in video.get("segments") or []:
        if not isinstance(item, dict):
            continue
        start = max(0.0, _num(item.get("start"), -1))
        end = min(duration, _num(item.get("end"), -1))
        text = " ".join(str(item.get("text") or "").split())
        if end - start >= 0.25 and text:
            rows.append({"start": start, "end": end, "text": text[:120]})
    rows.sort(key=lambda row: row["start"])
    merged: list[dict] = []
    for row in rows:
        if merged and row["start"] - merged[-1]["end"] <= 0.45:
            merged[-1]["end"] = max(merged[-1]["end"], row["end"])
            merged[-1]["text"] = (merged[-1]["text"] + row["text"])[:160]
        else:
            merged.append(dict(row))
    return merged


def _subtract(start: float, end: float, wastes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pieces = [(start, end)]
    for left, right in wastes:
        nxt: list[tuple[float, float]] = []
        for piece_start, piece_end in pieces:
            if right <= piece_start or left >= piece_end:
                nxt.append((piece_start, piece_end))
                continue
            if left > piece_start + 0.15:
                nxt.append((piece_start, min(piece_end, left)))
            if right < piece_end - 0.15:
                nxt.append((max(piece_start, right), piece_end))
        pieces = nxt
    return [(a, b) for a, b in pieces if b - a >= 1.0]


def _scenes(video: dict) -> list[float]:
    values = []
    for item in video.get("scenes") or []:
        if isinstance(item, dict):
            number = _num(item.get("time", item.get("start")), -1)
        else:
            number = _num(item, -1)
        if number >= 0:
            values.append(number)
    return values


def _score(start: float, end: float, text: str, scenes: list[float]) -> float:
    duration = max(0.2, end - start)
    density = len(text) / duration
    scene_hit = 1.0 if any(start - 0.45 <= scene <= end + 0.15 for scene in scenes) else 0.0
    penalty = 0.4 if duration > 12 else 0.0
    return density + scene_hit * 2.2 - penalty


def _chunk(start: float, end: float, size: float = 6.5) -> list[tuple[float, float]]:
    pieces = []
    cursor = start
    while end - cursor >= 1.2:
        piece_end = min(end, cursor + size)
        if piece_end - cursor < 1.2:
            break
        pieces.append((cursor, round(piece_end, 2)))
        cursor = piece_end
    return pieces


def _greedy(candidates: list[dict], target: float, limit: int) -> list[dict]:
    picked: list[dict] = []
    total = 0.0
    for cand in sorted(candidates, key=lambda item: item["score"], reverse=True):
        if len(picked) >= limit or (picked and total >= target):
            break
        blocked = False
        for chosen in picked:
            if chosen["video_id"] != cand["video_id"]:
                continue
            if cand["end"] <= chosen["start"] + 0.05 or cand["start"] >= chosen["end"] - 0.05:
                continue
            blocked = True
            break
        if blocked:
            continue
        picked.append(cand)
        total += cand["end"] - cand["start"]
    return picked


def _clip(video_id: int, start: float, end: float, reason: str, score: float) -> dict:
    end = min(end, start + 15.0)
    return {
        "video_id": int(video_id),
        "start": round(max(0.0, start), 2),
        "end": round(end, 2),
        "speed": 1.0,
        "freeze": 0.0,
        "reason": (reason or "精彩片段")[:40],
        "score": round(score, 3),
    }


def energy_peaks(video: dict) -> list[dict]:
    duration = _num(video.get("duration"))
    if duration <= 0:
        return []
    segments = _segments(video, duration)
    scenes = _scenes(video)
    wastes = _waste(video)
    step = 2.0
    bins = []
    cursor = 0.0
    while cursor < duration - 0.25:
        end = min(duration, cursor + step)
        chars = 0.0
        for seg in segments:
            overlap = min(end, seg["end"]) - max(cursor, seg["start"])
            if overlap > 0:
                chars += len(seg["text"]) * overlap / max(0.2, seg["end"] - seg["start"])
        waste = 0.0
        for left, right in wastes:
            waste += max(0.0, min(end, right) - max(cursor, left))
        scene_hit = 1 if any(cursor <= scene < end for scene in scenes) else 0
        score = chars * 1.15 + scene_hit * 2.0 - waste * 3.0
        bins.append({
            "start": round(cursor, 2),
            "end": round(end, 2),
            "score": round(score, 2),
            "speech_chars": round(chars, 1),
            "scene": bool(scene_hit),
        })
        cursor = end
    top = sorted(bins, key=lambda item: item["score"], reverse=True)[:8]
    top.sort(key=lambda item: item["start"])
    return top


def signal_plan(videos: list[dict]) -> dict:
    order = []
    candidates: list[dict] = []
    peaks: list[dict] = []
    for video in videos:
        video_id = int(video.get("video_id") or 0)
        duration = _num(video.get("duration"))
        if video_id <= 0 or duration <= 0.4:
            continue
        order.append(video_id)
        scenes = _scenes(video)
        wastes = _waste(video)
        peaks.extend({"video_id": video_id, **item} for item in energy_peaks(video))
        speech = _segments(video, duration)
        local: list[dict] = []
        if speech:
            for seg in speech:
                for start, end in _subtract(seg["start"], seg["end"], wastes):
                    start = max(0.0, start)
                    end = min(duration, end)
                    if end - start < 1.0:
                        continue
                    text = seg["text"]
                    local.append(_clip(video_id, start, end, f"口播：{text[:24]}", _score(start, end, text, scenes)))
        else:
            pieces = [(0.0, duration)]
            for left, right in wastes:
                nxt = []
                for start, end in pieces:
                    if right <= start or left >= end:
                        nxt.append((start, end))
                        continue
                    if left > start:
                        nxt.append((start, left))
                    if right < end:
                        nxt.append((right, end))
                pieces = nxt
            for start, end in pieces:
                for piece_start, piece_end in _chunk(start, min(duration, end)):
                    near = any(abs(scene - piece_start) < 1.2 or abs(scene - piece_end) < 1.2 for scene in scenes)
                    reason = "镜头切换附近" if near else "去掉静音和黑场后的画面"
                    local.append(_clip(video_id, piece_start, piece_end, reason, _score(piece_start, piece_end, "", scenes) + 0.2))
        if not local and duration >= 1.2:
            local.append(_clip(video_id, 0.0, min(duration, 8.0), "整段里没有更干净的切点，先留开头", 0.1))
        candidates.extend(local)
    if not candidates:
        raise EngineError(
            "没有可用的精彩片段",
            code="highlight_empty",
            action="先完成分析。若整段都是静音或黑场，换一条素材。",
            status=422,
        )
    target = 0.0
    for video in videos:
        target += max(4.0, _num(video.get("duration")) * 0.45)
    limit = 4 if len(order) == 1 else min(10, 4 * len(order))
    picked = _greedy(candidates, target=target, limit=limit)
    rank = {video_id: index for index, video_id in enumerate(order)}
    picked.sort(key=lambda item: (rank.get(item["video_id"], 99), item["start"]))
    public = [{key: value for key, value in item.items() if key != "score"} for item in picked]
    return {"clips": public, "peaks": peaks[:12]}


def _sanitize(parsed: HighlightPlan, videos: list[dict]) -> tuple[list[dict], list[str]]:
    by_id = {int(item.get("video_id") or 0): item for item in videos}
    warnings: list[str] = []
    cleaned: list[dict] = []
    for index, clip in enumerate(parsed.clips, start=1):
        meta = by_id.get(int(clip.video_id))
        if not meta:
            warnings.append(f"第 {index} 条不属于这次分析的素材，已忽略")
            continue
        duration = _num(meta.get("duration"))
        start = max(0.0, float(clip.start))
        end = min(duration, float(clip.end))
        if end - start < 0.8:
            warnings.append(f"第 {index} 条太短或超出片长（{duration:.1f}s），已忽略")
            continue
        if end - start > 15:
            end = start + 15
            warnings.append(f"第 {index} 条超过 15 秒，已截断")
        speed = max(0.5, min(2.0, float(clip.speed)))
        freeze = max(0.0, min(3.0, float(clip.freeze)))
        overlap = False
        for chosen in cleaned:
            if chosen["video_id"] != int(clip.video_id):
                continue
            if end <= chosen["start"] + 0.05 or start >= chosen["end"] - 0.05:
                continue
            overlap = True
            break
        if overlap:
            warnings.append(f"第 {index} 条与已选片段重叠，已忽略")
            continue
        cleaned.append({
            "video_id": int(clip.video_id),
            "start": round(start, 2),
            "end": round(end, 2),
            "speed": round(speed, 2),
            "freeze": round(freeze, 2),
            "reason": (clip.reason or "精彩片段")[:40],
        })
    if len(cleaned) > 12:
        warnings.append("模型片段超过 12 段，已保留前 12 段")
        cleaned = cleaned[:12]
    if not cleaned:
        raise EngineError(
            "模型返回的精彩片段都无法落在片长内",
            code="llm_schema",
            action="可重试。时间线会改用按口播和镜头选出的信号方案。",
            status=422,
        )
    order = [int(item.get("video_id") or 0) for item in videos]
    rank = {video_id: index for index, video_id in enumerate(order)}
    cleaned.sort(key=lambda item: (rank.get(item["video_id"], 99), item["start"]))
    return cleaned, warnings


def llm_plan(videos: list[dict], signal: dict, instruction: str) -> tuple[list[dict], list[str]]:
    payload = {
        "instruction": (instruction or "")[:400],
        "videos": [],
    }
    for video in videos:
        duration = round(_num(video.get("duration")), 2)
        segments = _segments(video, duration)[:40]
        frames = [
            {"time": item.get("time"), "caption": str(item.get("caption") or "")[:80]}
            for item in (video.get("frames") or [])
            if isinstance(item, dict) and str(item.get("caption") or "").strip()
        ][:8]
        payload["videos"].append({
            "video_id": int(video.get("video_id") or 0),
            "title": str(video.get("title") or "")[:80],
            "duration": duration,
            "speech": [{"start": item["start"], "end": item["end"], "text": item["text"]} for item in segments],
            "energy_peaks": [item for item in signal.get("peaks") or [] if item.get("video_id") == int(video.get("video_id") or 0)][:8],
            "scenes": [round(item, 2) for item in _scenes(video)[:24]],
            "silence": [{"start": round(a, 2), "end": round(b, 2)} for a, b in _waste(video)[:20]],
            "frames": frames,
        })
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > 9000:
        text = text[:9000] + "…（上下文已截断）"
    parsed = complete_json(system=SYSTEM, user=text, model=HighlightPlan, timeout=90)
    return _sanitize(parsed, videos)


def plan_highlight(videos: list[dict], *, instruction: str, use_llm: bool) -> dict:
    signal = signal_plan(videos)
    if not use_llm:
        return {
            "source": "signal",
            "clips": signal["clips"],
            "warnings": ["未调用模型。精彩段按口播密度、镜头切换、静音和黑场选出。"],
            "energy": signal["peaks"],
        }
    if not configured():
        return {
            "source": "signal",
            "clips": signal["clips"],
            "warnings": ["没有配置文本模型，已按口播密度、镜头切换、静音和黑场选出精彩段。"],
            "energy": signal["peaks"],
        }
    try:
        clips, warnings = llm_plan(videos, signal, instruction)
    except EngineError as exc:
        return {
            "source": "signal",
            "clips": signal["clips"],
            "warnings": [
                f"模型方案未采用：{exc.message}",
                exc.action or "可重试精彩剪辑。当前用的是信号方案。",
            ],
            "error": exc.as_dict(),
            "energy": signal["peaks"],
        }
    return {"source": "llm", "clips": clips, "warnings": warnings, "energy": signal["peaks"]}
