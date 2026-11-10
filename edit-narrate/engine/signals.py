"""Cut candidates from measurable signals. The model may rank these later; it does not invent them."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from engine.media import ffmpeg, probe

SILENCE_RE_START = re.compile(r"silence_start:\s*([0-9.]+)")
SILENCE_RE_END = re.compile(r"silence_end:\s*([0-9.]+)")
BLACK_RE = re.compile(r"black_start:\s*([0-9.]+)\s+black_end:\s*([0-9.]+)")
SCENE_RE = re.compile(r"pts_time:\s*([0-9.]+)")

FILLERS = ("嗯", "啊", "呃", "额", "哦", "那个", "就是", "然后", "这个", "对吧", "好吧", "嗯嗯", "啊啊")
DROP_THRESHOLD = 0.62


def parse_silence(log: str, duration: float) -> list[dict]:
    regions: list[dict] = []
    start: float | None = None
    for line in log.splitlines():
        found_start = SILENCE_RE_START.search(line)
        found_end = SILENCE_RE_END.search(line)
        if found_start:
            start = float(found_start.group(1))
        if found_end and start is not None:
            end = float(found_end.group(1))
            if end > start:
                regions.append({"start": round(start, 3), "end": round(end, 3)})
            start = None
    if start is not None and duration > start + 0.2:
        regions.append({"start": round(start, 3), "end": round(duration, 3)})
    return regions


def parse_black(log: str) -> list[dict]:
    regions = []
    for start, end in BLACK_RE.findall(log):
        a, b = float(start), float(end)
        if b > a:
            regions.append({"start": round(a, 3), "end": round(b, 3)})
    return regions


def parse_scenes(log: str) -> list[float]:
    times = sorted({round(float(value), 3) for value in SCENE_RE.findall(log)})
    kept: list[float] = []
    for time in times:
        if not kept or time - kept[-1] >= 0.2:
            kept.append(time)
    return kept


def detect(
    path: Path,
    *,
    silence_db: float = -32,
    min_silence: float = 0.45,
    min_black: float = 0.3,
    scene_threshold: float = 0.3,
    kinds: tuple[str, ...] = ("silence", "black", "scene"),
    job_id: str | None = None,
    should_cancel=None,
) -> dict:
    meta = probe(path)
    duration = float(meta["duration"])
    noise = max(-60.0, min(-15.0, float(silence_db)))
    silence: list[dict] = []
    black: list[dict] = []
    scenes: list[float] = []
    if "silence" in kinds:
        silence_log = ffmpeg(
            ["-i", str(path), "-af", f"silencedetect=noise={noise:.0f}dB:d={max(0.2, min_silence):.2f}", "-vn", "-f", "null", "-"],
            loglevel="info",
            timeout=1800,
            job_id=job_id,
            should_cancel=should_cancel,
        )
        silence = parse_silence(silence_log, duration)
    if "black" in kinds:
        black_log = ffmpeg(
            ["-i", str(path), "-vf", f"blackdetect=d={max(0.15, min_black):.2f}:pix_th=0.12:pic_th=0.98", "-an", "-f", "null", "-"],
            loglevel="info",
            timeout=1800,
            job_id=job_id,
            should_cancel=should_cancel,
        )
        black = parse_black(black_log)
    if "scene" in kinds:
        scene_log = ffmpeg(
            ["-i", str(path), "-filter:v", f"select='gt(scene,{max(0.05, min(0.8, scene_threshold)):.2f})',showinfo", "-an", "-f", "null", "-"],
            loglevel="info",
            timeout=1800,
            job_id=job_id,
            should_cancel=should_cancel,
        )
        scenes = [t for t in parse_scenes(scene_log) if 0.05 < t < duration - 0.05]
    return {
        "duration": round(duration, 3),
        "width": meta["width"],
        "height": meta["height"],
        "silence": silence,
        "black": black,
        "scenes": scenes,
        "silence_db": noise,
    }


def _sid(video_id: int, kind: str, start: float, end: float) -> str:
    raw = f"{video_id}:{kind}:{start:.2f}:{end:.2f}".encode()
    return "s" + hashlib.sha1(raw).hexdigest()[:12]


def _nearby_text(segments: list[dict], start: float, end: float) -> str:
    bits = []
    for seg in segments:
        if float(seg["end"]) < start - 1.5 or float(seg["start"]) > end + 1.5:
            continue
        text = str(seg.get("text") or "").strip()
        if text:
            bits.append(text)
    return " ".join(bits)[:80]


def _snap(value: float, scenes: list[float], tol: float = 0.28) -> tuple[float, float | None]:
    if not scenes:
        return value, None
    nearest = min(scenes, key=lambda item: abs(item - value))
    if abs(nearest - value) <= tol:
        return nearest, nearest
    return value, None


def _merge(drops: list[dict]) -> list[dict]:
    if not drops:
        return []
    ordered = sorted(drops, key=lambda item: (item["start"], item["end"]))
    merged = [dict(ordered[0])]
    rank = {"black": 4, "silence": 3, "pause": 2, "filler": 1}
    for item in ordered[1:]:
        prev = merged[-1]
        if item["kind"] == prev["kind"] and item["start"] <= prev["end"] + 0.12:
            prev["end"] = round(max(prev["end"], item["end"]), 3)
            if rank.get(item["kind"], 0) > rank.get(prev["kind"], 0):
                prev["kind"] = item["kind"]
            prev["score"] = round(max(prev["score"], item["score"]), 3)
            if item["evidence"] not in prev["evidence"]:
                prev["evidence"] = (prev["evidence"] + "；" + item["evidence"])[:180]
            if item["reason"] not in prev["reason"]:
                prev["reason"] = (prev["reason"] + "；" + item["reason"])[:120]
        else:
            merged.append(dict(item))
    return merged


def build_suggestions(video_id: int, signals: dict, segments: list[dict] | None = None) -> list[dict]:
    duration = float(signals.get("duration") or 0)
    scenes = [float(item) for item in signals.get("scenes") or []]
    segments = segments or []
    drops: list[dict] = []

    for region in signals.get("silence") or []:
        start, end = float(region["start"]), float(region["end"])
        span = end - start
        if span < 0.3 or start >= duration:
            continue
        end = min(end, duration)
        score = round(min(0.98, 0.55 + span * 0.08), 3)
        drops.append({
            "kind": "silence",
            "start": start,
            "end": end,
            "score": score,
            "reason": f"静音 {span:.1f}s",
            "evidence": f"silencedetect {signals.get('silence_db', -32):.0f}dB，{span:.2f}s",
        })

    for region in signals.get("black") or []:
        start, end = float(region["start"]), float(region["end"])
        span = end - start
        if span < 0.2:
            continue
        drops.append({
            "kind": "black",
            "start": start,
            "end": min(end, duration),
            "score": round(min(0.99, 0.82 + span * 0.03), 3),
            "reason": f"黑场 {span:.1f}s",
            "evidence": f"blackdetect {span:.2f}s",
        })

    ordered = sorted(segments, key=lambda item: float(item["start"]))
    for prev, nxt in zip(ordered, ordered[1:]):
        gap_start = float(prev["end"])
        gap_end = float(nxt["start"])
        if gap_end - gap_start < 0.7:
            continue
        start = gap_start + 0.06
        end = gap_end - 0.06
        if end - start < 0.35:
            continue
        span = end - start
        drops.append({
            "kind": "pause",
            "start": round(start, 3),
            "end": round(end, 3),
            "score": round(min(0.9, 0.58 + span * 0.05), 3),
            "reason": f"句间停顿 {span:.1f}s",
            "evidence": f"ASR 间隙 {gap_start:.2f}-{gap_end:.2f}",
        })

    for seg in ordered:
        text = re.sub(r"[\s，。！？、,.!?\-—…]+", "", str(seg.get("text") or ""))
        span = float(seg["end"]) - float(seg["start"])
        if text in FILLERS and 0 < span <= 1.2:
            drops.append({
                "kind": "filler",
                "start": round(float(seg["start"]), 3),
                "end": round(float(seg["end"]), 3),
                "score": 0.58,
                "reason": f"口头禅「{text}」",
                "evidence": f"转写短句 {span:.2f}s",
            })

    snapped: list[dict] = []
    for item in _merge(drops):
        start, snap_a = _snap(item["start"], scenes)
        end, snap_b = _snap(item["end"], scenes)
        if end - start < 0.25:
            continue
        evidence = item["evidence"]
        if snap_a is not None or snap_b is not None:
            evidence += "；边缘吸附到镜头切点"
        snapped.append({**item, "start": round(start, 3), "end": round(min(end, duration), 3), "evidence": evidence[:180]})

    suggestions = []
    for item in snapped:
        suggestions.append({
            "id": _sid(video_id, item["kind"], item["start"], item["end"]),
            "video_id": video_id,
            "start": item["start"],
            "end": item["end"],
            "kind": item["kind"],
            "action": "drop",
            "score": item["score"],
            "reason": item["reason"],
            "evidence": item["evidence"],
            "recommended": item["score"] >= DROP_THRESHOLD,
            "llm_action": None,
            "text": _nearby_text(segments, item["start"], item["end"]),
        })

    for time in scenes:
        suggestions.append({
            "id": _sid(video_id, "scene", time, time),
            "video_id": video_id,
            "start": round(time, 3),
            "end": round(time, 3),
            "kind": "scene",
            "action": "split",
            "score": 0.45,
            "reason": "镜头切换",
            "evidence": f"scene score 超过阈值 @ {time:.2f}s",
            "recommended": False,
            "llm_action": None,
            "text": _nearby_text(segments, time, time),
        })
    suggestions.sort(key=lambda item: (item["start"], 0 if item["action"] == "drop" else 1))
    return suggestions


def apply_signal_plan(suggestions: list[dict]) -> list[dict]:
    planned = []
    for item in suggestions:
        copy = dict(item)
        if copy.get("action") == "drop":
            copy["recommended"] = float(copy.get("score") or 0) >= DROP_THRESHOLD
        else:
            copy["recommended"] = False
        planned.append(copy)
    return planned
