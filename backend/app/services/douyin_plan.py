"""抖音竖屏短视频的选段、文案和画幅计算。

这里只做纯函数：不读模型、不调 ffmpeg。任务编排在 douyin_flow，渲染在 douyin_render。
"""

from __future__ import annotations

import math
import re

from app.config import douyin_settings
from app.services.ai_edit import (
    AIResponseError,
    AIServiceError,
    _format_segment,
    _request_ai,
    chunk_segments,
)


def clip_budget(duration: float, cap: int | None = None) -> int:
    """原片越长，候选短视频越多，最多 10 条。大约每 70 秒素材一条。"""
    settings = douyin_settings()
    limit = settings["max_clips"] if cap is None else int(cap)
    limit = max(1, min(settings["max_clips"], limit or settings["max_clips"]))
    if not math.isfinite(duration) or duration < 20:
        return 1
    return max(1, min(limit, int(duration // 70) or 1))


def effective_bounds(duration: float, min_seconds: float | None = None, max_seconds: float | None = None) -> tuple[float, float]:
    settings = douyin_settings()
    min_len = float(settings["min_seconds"] if min_seconds is None else min_seconds)
    max_len = float(settings["max_seconds"] if max_seconds is None else max_seconds)
    if duration < min_len:
        min_len = max(5.0, float(duration))
    if max_len < min_len:
        max_len = min_len
    return min_len, max_len


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _nearest(values: list[float], target: float, window: float) -> float | None:
    best = None
    best_dist = window
    for value in values:
        dist = abs(value - target)
        if dist <= best_dist:
            best = value
            best_dist = dist
    return best


def text_in_range(segments: list[dict], start: float, end: float) -> str:
    parts = []
    for segment in segments:
        seg_start = _finite(segment.get("start"))
        seg_end = _finite(segment.get("end"))
        if seg_start is None or seg_end is None or seg_end <= start or seg_start >= end:
            continue
        text = str(segment.get("text") or "").strip()
        if text:
            parts.append(text)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def fit_clip(
    start: float,
    end: float,
    segments: list[dict],
    duration: float,
    min_len: float,
    max_len: float,
) -> tuple[float, float]:
    """把模型给的区间吸到邻近句首句尾，并收进目标时长。"""
    starts = [float(item["start"]) for item in segments if _finite(item.get("start")) is not None]
    ends = [float(item["end"]) for item in segments if _finite(item.get("end")) is not None]
    snapped_start = _nearest(starts, start, 1.4)
    snapped_end = _nearest(ends, end, 1.4)
    if snapped_start is not None and snapped_start < end - 3:
        start = snapped_start
    if snapped_end is not None and snapped_end > start + 3:
        end = snapped_end
    start = max(0.0, min(duration, start))
    end = max(0.0, min(duration, end))
    if end - start > max_len:
        candidates = [item for item in ends if start + min_len - 0.2 <= item <= start + max_len + 0.05]
        end = max(candidates) if candidates else min(duration, start + max_len)
    if end - start < min_len:
        for item in ends:
            if end < item <= start + max_len + 0.05:
                end = item
                if end - start >= min_len:
                    break
        if end - start < min_len:
            end = min(duration, start + min_len)
    if end - start > max_len + 0.2:
        end = min(duration, start + max_len)
    if end <= start:
        end = min(duration, start + min_len)
    return round(start, 2), round(min(duration, end), 2)


def _hashtags(value) -> list[str]:
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,，]+", value) if part.strip()]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def normalize_hashtag(tag: str) -> str:
    cleaned = re.sub(r"\s+", "", str(tag or "")).strip().lstrip("#")
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", cleaned)
    if not cleaned:
        return ""
    return "#" + cleaned[:18]


def normalize_copy(raw: dict, fallback_text: str) -> dict:
    title = re.sub(r"\s+", " ", str(raw.get("title") or "")).strip()
    pulled = re.findall(r"#([^\s#]{1,18})", title)
    title = re.sub(r"#[^\s#]+", "", title).strip(" -|，,")
    if not title:
        title = re.sub(r"\s+", "", fallback_text)[:22] or "精彩片段"
    title = title[:30]
    tags: list[str] = []
    seen: set[str] = set()
    for tag in [*_hashtags(raw.get("hashtags")), *["#" + item for item in pulled]]:
        norm = normalize_hashtag(tag)
        key = norm.lower()
        if not norm or key in seen:
            continue
        seen.add(key)
        tags.append(norm)
        if len(tags) >= 5:
            break
    if len(tags) < 2:
        for extra in ("#口播", "#干货"):
            if extra.lower() not in seen:
                tags.append(extra)
                seen.add(extra.lower())
            if len(tags) >= 2:
                break
    description = re.sub(r"\s+", " ", str(raw.get("description") or "")).strip()
    if not description:
        description = fallback_text[:120]
    return {"title": title, "hashtags": tags[:5], "description": description[:200]}


def _mostly_cjk(text: str) -> bool:
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    return cjk >= max(1, len(text) // 3)


def wrap_caption(text: str, limit: int = 14) -> list[str]:
    """一条字幕最多两行；更长的句子拆成多条。"""
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if _mostly_cjk(cleaned):
        cleaned = cleaned.replace(" ", "")
    if not cleaned:
        return []
    chunk = limit * 2
    parts = [cleaned[index:index + chunk] for index in range(0, len(cleaned), chunk)]
    wrapped = []
    for part in parts:
        lines = [part[index:index + limit] for index in range(0, len(part), limit)]
        wrapped.append("\n".join(lines[:2]))
    return wrapped


def build_captions(segments: list[dict], start: float, end: float) -> list[dict]:
    cues = []
    for segment in segments:
        seg_start = _finite(segment.get("start"))
        seg_end = _finite(segment.get("end"))
        if seg_start is None or seg_end is None or seg_end <= start or seg_start >= end:
            continue
        text = str(segment.get("text") or "").strip()
        pieces = wrap_caption(text)
        if not pieces:
            continue
        left = max(start, seg_start)
        right = min(end, seg_end)
        if right - left < 0.2:
            continue
        span = (right - left) / len(pieces)
        for index, piece in enumerate(pieces):
            cue_start = left + span * index
            cue_end = left + span * (index + 1)
            cues.append({
                "start": round(cue_start - start, 2),
                "end": round(max(cue_start - start + 0.2, cue_end - start), 2),
                "text": piece,
            })
    cues.sort(key=lambda item: (item["start"], item["end"]))
    return cues


def _overlap(left: dict, right: dict) -> float:
    return max(0.0, min(left["end"], right["end"]) - max(left["start"], right["start"]))


def clean_douyin_clips(
    items: list,
    duration: float,
    segments: list[dict],
    *,
    max_clips: int,
    min_seconds: float | None = None,
    max_seconds: float | None = None,
) -> list[dict]:
    """校验模型选段：截断、吸句、去重叠，并补上字幕和发布文案。"""
    duration = float(duration)
    if not math.isfinite(duration) or duration <= 0:
        return []
    min_len, max_len = effective_bounds(duration, min_seconds, max_seconds)
    prepared = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = _finite(item.get("start"))
        end = _finite(item.get("end"))
        if start is None or end is None:
            continue
        start = max(0.0, min(duration, start))
        end = max(0.0, min(duration, end))
        if end - start < 0.8:
            continue
        start, end = fit_clip(start, end, segments, duration, min_len, max_len)
        length = end - start
        if length < max(5.0, min_len) - 0.35 or length > max_len + 0.75:
            continue
        spoken = text_in_range(segments, start, end)
        if len(re.sub(r"\s+", "", spoken)) < 4:
            continue
        try:
            score = int(float(item.get("score") or item.get("importance") or 3))
        except (TypeError, ValueError):
            score = 3
        score = max(1, min(5, score))
        copy = normalize_copy(item, spoken)
        prepared.append({
            "start": start,
            "end": end,
            "score": score,
            "hook": str(item.get("hook") or "")[:80],
            "reason": str(item.get("reason") or "")[:200],
            **copy,
            "captions": build_captions(segments, start, end),
        })
    prepared.sort(key=lambda item: (-item["score"], item["start"], item["end"]))
    chosen: list[dict] = []
    limit = max(1, int(max_clips))
    for item in prepared:
        if any(_overlap(item, other) > 0.35 for other in chosen):
            continue
        chosen.append(item)
        if len(chosen) >= limit:
            break
    chosen.sort(key=lambda item: item["start"])
    for index, item in enumerate(chosen, start=1):
        item["index"] = index
        item["duration"] = round(item["end"] - item["start"], 2)
    return chosen


def portrait_crop_size(src_w: int, src_h: int) -> tuple[int, int]:
    """在原画面里取一块 9:16，边长取偶数，方便 yuv420。"""
    src_w = max(2, int(src_w))
    src_h = max(2, int(src_h))
    ratio = 9 / 16
    if src_w / src_h > ratio:
        crop_h = src_h - src_h % 2
        crop_w = int(src_h * ratio) // 2 * 2
    else:
        crop_w = src_w - src_w % 2
        crop_h = int(src_w / ratio) // 2 * 2
    crop_w = max(2, min(src_w - src_w % 2, crop_w))
    crop_h = max(2, min(src_h - src_h % 2, crop_h))
    return crop_w, crop_h


def crop_origin(src_w: int, src_h: int, crop_w: int, crop_h: int, box: dict | None) -> tuple[int, int]:
    """subject_box 用 0–1000 归一化坐标。没有主体时居中。"""
    if not box:
        return (src_w - crop_w) // 2, (src_h - crop_h) // 2
    try:
        center_x = (float(box.get("x", 500)) + float(box.get("width", 0)) / 2) / 1000 * src_w
        center_y = (float(box.get("y", 500)) + float(box.get("height", 0)) / 2) / 1000 * src_h
    except (TypeError, ValueError):
        return (src_w - crop_w) // 2, (src_h - crop_h) // 2
    x = int(round(center_x - crop_w / 2))
    y = int(round(center_y - crop_h / 2))
    return max(0, min(src_w - crop_w, x)), max(0, min(src_h - crop_h, y))


def _piecewise(steps: list[tuple[float, str]], fallback: str) -> str:
    expression = steps[-1][1]
    for index in range(len(steps) - 2, -1, -1):
        expression = f"if(lt(t,{steps[index + 1][0]:.3f}),{steps[index][1]},{expression})"
    return f"if(lt(t,{steps[0][0]:.3f}),{fallback},{expression})"


def vertical_filter(
    src_w: int,
    src_h: int,
    track: list[dict] | None,
    out_w: int,
    out_h: int,
) -> tuple[str, str]:
    """返回 ffmpeg 滤镜和 framing：subject（跟着说话人/主体）或 center。"""
    crop_w, crop_h = portrait_crop_size(src_w, src_h)
    points = [item for item in (track or []) if isinstance(item, dict) and isinstance(item.get("subject_box"), dict)]
    if not points:
        x, y = crop_origin(src_w, src_h, crop_w, crop_h, None)
        crop = f"crop={crop_w}:{crop_h}:{x}:{y}"
        mode = "center"
    elif len(points) == 1:
        x, y = crop_origin(src_w, src_h, crop_w, crop_h, points[0].get("subject_box"))
        crop = f"crop={crop_w}:{crop_h}:{x}:{y}"
        mode = "subject"
    else:
        xs: list[tuple[float, str]] = []
        ys: list[tuple[float, str]] = []
        for point in sorted(points, key=lambda item: float(item.get("timestamp") or 0)):
            x, y = crop_origin(src_w, src_h, crop_w, crop_h, point.get("subject_box"))
            stamp = max(0.0, float(point.get("timestamp") or 0))
            xs.append((stamp, str(x)))
            ys.append((stamp, str(y)))
        fallback_x, fallback_y = crop_origin(src_w, src_h, crop_w, crop_h, None)
        crop = (
            f"crop={crop_w}:{crop_h}:x='{_piecewise(xs, str(fallback_x))}':"
            f"y='{_piecewise(ys, str(fallback_y))}'"
        )
        mode = "subject"
    even_w = max(2, int(out_w) // 2 * 2)
    even_h = max(2, int(out_h) // 2 * 2)
    return f"{crop},scale={even_w}:{even_h}:flags=bilinear,setsar=1", mode


def skin_subject_box(rgb: bytes, width: int, height: int) -> dict | None:
    """在缩小的 RGB 帧上找肤色块，返回 0–1000 的主体框。找不到就返回 None，调用方居中。"""
    width = int(width)
    height = int(height)
    needed = width * height * 3
    if width < 8 or height < 8 or len(rgb) < needed:
        return None
    limit_y = max(1, int(height * 0.82))
    min_x, min_y = width, limit_y
    max_x, max_y = 0, 0
    count = 0
    for y in range(limit_y):
        row = y * width * 3
        for x in range(width):
            index = row + x * 3
            red, green, blue = rgb[index], rgb[index + 1], rgb[index + 2]
            if red > 95 and green > 40 and blue > 20 and red > green and red > blue and (red - green) > 15 and (red - blue) > 15:
                count += 1
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    area = width * limit_y
    if count < area * 0.015 or count > area * 0.55 or max_x <= min_x or max_y <= min_y:
        return None
    return {
        "x": round(min_x / width * 1000, 1),
        "y": round(min_y / height * 1000, 1),
        "width": round((max_x - min_x + 1) / width * 1000, 1),
        "height": round((max_y - min_y + 1) / height * 1000, 1),
    }


def speaker_turns(segments: list[dict], start: float, end: float) -> list[dict]:
    turns = []
    current = None
    for segment in segments:
        seg_start = _finite(segment.get("start"))
        seg_end = _finite(segment.get("end"))
        if seg_start is None or seg_end is None or seg_end <= start or seg_start >= end:
            continue
        speaker = str(segment.get("speaker") or "speaker_unknown")
        if current and current["speaker"] == speaker and seg_start - current["end"] < 1.2:
            current["end"] = max(current["end"], min(end, seg_end))
            continue
        current = {
            "speaker": speaker,
            "start": max(start, seg_start),
            "end": min(end, seg_end),
        }
        turns.append(current)
    return turns


def sample_times(start: float, end: float, segments: list[dict]) -> list[float]:
    """多说话人时每个回合取一帧；否则取前、中、后三帧。"""
    turns = speaker_turns(segments, start, end)
    named = [turn for turn in turns if turn["speaker"] != "speaker_unknown"]
    if len({turn["speaker"] for turn in named}) >= 2:
        times = []
        for turn in named:
            times.append(round((turn["start"] + turn["end"]) / 2, 2))
            if len(times) >= 6:
                break
        return times
    span = max(0.1, end - start)
    return [round(start + span * part, 2) for part in (0.2, 0.5, 0.8)]


def _nearest_box(samples: list[dict], moment: float, window: float = 4.0) -> dict | None:
    best = None
    best_dist = window
    for item in samples:
        stamp = _finite(item.get("time"))
        box = item.get("box")
        if stamp is None or not isinstance(box, dict):
            continue
        dist = abs(stamp - moment)
        if dist <= best_dist:
            best = box
            best_dist = dist
    return best


def build_subject_track(
    start: float,
    end: float,
    segments: list[dict],
    boxes: list[dict],
    skin_samples: list[dict],
) -> list[dict]:
    """成片内的主体轨迹。时间从成片 0 秒起算，方便 ffmpeg 的 t。"""
    turns = speaker_turns(segments, start, end)
    named = [turn for turn in turns if turn["speaker"] != "speaker_unknown"]
    anchors: list[tuple[float, dict]] = []
    if len({turn["speaker"] for turn in named}) >= 2:
        for turn in named:
            moment = (turn["start"] + turn["end"]) / 2
            box = _nearest_box(boxes, moment) or _nearest_box(skin_samples, moment)
            if box:
                anchors.append((moment, box))
    if not anchors:
        pool = boxes or skin_samples
        for item in pool:
            stamp = _finite(item.get("time"))
            box = item.get("box")
            if stamp is None or not isinstance(box, dict) or stamp < start - 1 or stamp > end + 1:
                continue
            anchors.append((stamp, box))
            if len(anchors) >= 6:
                break
    track = []
    seen: set[float] = set()
    for moment, box in anchors:
        relative = round(max(0.0, moment - start), 2)
        if relative in seen:
            continue
        seen.add(relative)
        track.append({"timestamp": relative, "subject_box": box})
    track.sort(key=lambda item: item["timestamp"])
    return track


def _line(segment: dict) -> str:
    speaker = str(segment.get("speaker") or "")
    if speaker and speaker != "speaker_unknown":
        return f"[{float(segment['start']):.1f}-{float(segment['end']):.1f} {speaker}] {segment.get('text') or ''}"
    return _format_segment(segment)


def douyin_prompt(duration: float, minimum: float, maximum: float, count: int, transcript: str, instruction: str, range_start: float, range_end: float) -> str:
    extra = f"\n额外要求（优先）：{instruction.strip()}\n" if instruction.strip() else ""
    return (
        "你是抖音短视频剪辑。下面是一条原片的语音转写，总时长 "
        f"{duration:.1f} 秒。请从中挑选最多 {count} 条可以单独发布的竖屏短视频（9:16）。\n"
        f"每条时长 {minimum:.0f}–{maximum:.0f} 秒，时间必须落在 {range_start:.1f}–{range_end:.1f} 内。"
        "范围外的句子只供理解，不能拿来做选段。\n\n"
        "要求：\n"
        "- 每条单独能看懂：开头有钩子，中间有信息，结尾有收束，不要半句话\n"
        "- 优先金句、冲突、反转、具体故事和可转发的观点；跳过寒暄、广告、重复和长时间沉默\n"
        "- 条目之间不要重叠，起止尽量落在句子边界\n"
        "- 素材不够就少选，不要为了凑数硬切，也不要编造时间\n"
        "- 标题 30 字以内，3 到 5 个带 # 的话题，一句简介\n"
        '- 只输出 JSON 数组：[{"start":12.0,"end":46.0,"title":"标题","hashtags":["#话题"],'
        '"description":"简介","hook":"开头为什么能留人","score":5}]\n'
        "- 没有合适片段就输出 []\n"
        f"{extra}\n字幕：\n{transcript}"
    )


def _in_core(item: dict, start: float, end: float) -> bool:
    left = _finite(item.get("start")) if isinstance(item, dict) else None
    right = _finite(item.get("end")) if isinstance(item, dict) else None
    if left is None or right is None:
        return False
    mid = (left + right) / 2
    return start - 0.5 <= mid <= end + 0.5


def fallback_highlights(
    segments: list[dict],
    duration: float,
    count: int,
    *,
    min_seconds: float | None = None,
    max_seconds: float | None = None,
) -> list[dict]:
    """模型没有给出可用选段时，按口播密度铺一组不重叠的窗口。"""
    min_len, max_len = effective_bounds(duration, min_seconds, max_seconds)
    if not segments or duration < 5:
        return []
    target = min(max_len, max(min_len, 36.0))
    step = max(10.0, target * 0.62)
    drafts = []
    cursor = 0.0
    while cursor < duration - 4:
        end = min(duration, cursor + target)
        spoken = text_in_range(segments, cursor, end)
        chars = len(re.sub(r"\s+", "", spoken))
        if chars >= 8 and end - cursor >= min(min_len, duration) * 0.8:
            density = chars / max(1.0, end - cursor)
            bonus = 1 if ("？" in spoken or "?" in spoken or "！" in spoken) else 0
            drafts.append({
                "start": cursor,
                "end": end,
                "title": re.sub(r"\s+", "", spoken)[:22],
                "description": spoken[:120],
                "hashtags": [],
                "score": max(1, min(5, int(round(density * 2)) + bonus)),
                "hook": "",
                "reason": "按口播密度挑选",
            })
        cursor += step
        if end >= duration:
            break
    if not drafts:
        spoken = text_in_range(segments, 0, duration)
        if len(re.sub(r"\s+", "", spoken)) >= 4:
            drafts.append({
                "start": float(segments[0].get("start") or 0),
                "end": min(duration, max(float(segments[-1].get("end") or duration), min_len)),
                "title": re.sub(r"\s+", "", spoken)[:22],
                "description": spoken[:120],
                "hashtags": [],
                "score": 3,
                "hook": "",
                "reason": "整段口播",
            })
    return clean_douyin_clips(
        drafts,
        duration,
        segments,
        max_clips=count,
        min_seconds=min_len,
        max_seconds=max_len,
    )


def plan_douyin_shorts(
    segments: list[dict],
    duration: float,
    *,
    instruction: str = "",
    max_clips: int = 0,
    min_seconds: float | None = None,
    max_seconds: float | None = None,
    request_ai=None,
    log=print,
) -> dict:
    """让模型按块挑选抖音高光。接口失败要抛出去；返回空数组时才用口播密度兜底。"""
    duration = float(duration)
    if not math.isfinite(duration) or duration < 5:
        raise AIResponseError("原片短于 5 秒，不够做成一条抖音短视频")
    if not segments:
        raise AIResponseError("没有识别到口播，无法挑选抖音高光")
    minimum, maximum = effective_bounds(duration, min_seconds, max_seconds)
    budget = clip_budget(duration, max_clips or None)
    ask = request_ai or _request_ai
    chunks = chunk_segments(segments, char_budget=4500, context_chars=180) or [{
        "segments": segments,
        "start": float(segments[0]["start"]),
        "end": float(segments[-1]["end"]),
    }]
    raw: list[dict] = []
    service_errors: list[Exception] = []
    response_errors: list[Exception] = []
    for index, chunk in enumerate(chunks, start=1):
        core_start = float(chunk["start"])
        core_end = float(chunk["end"])
        transcript = "\n".join(_line(segment) for segment in chunk["segments"])
        # 每块只求这一段素材能支撑的条数，最后再按分数全局截断。
        local_count = max(1, min(budget, 4))
        prompt = douyin_prompt(
            duration, minimum, maximum, local_count, transcript,
            instruction, core_start, core_end,
        )
        log(f"挑选抖音片段 {index}/{len(chunks)}：{core_start:.1f}s-{core_end:.1f}s")
        try:
            items = ask(prompt, log)
        except AIServiceError as exc:
            service_errors.append(exc)
            log(f"模型调用失败：{exc}")
            continue
        except AIResponseError as exc:
            response_errors.append(exc)
            log(f"模型没有返回可用 JSON：{exc}")
            continue
        raw.extend(item for item in items if _in_core(item, core_start, core_end))
    if service_errors and not raw and not response_errors:
        raise service_errors[0]
    cleaned = clean_douyin_clips(
        raw, duration, segments,
        max_clips=budget, min_seconds=minimum, max_seconds=maximum,
    )
    source = "llm"
    warnings: list[str] = []
    if service_errors and cleaned:
        warnings.append(f"有 {len(service_errors)} 段模型调用失败，已保留成功的选段。")
    if not cleaned:
        cleaned = fallback_highlights(
            segments, duration, budget, min_seconds=minimum, max_seconds=maximum,
        )
        source = "heuristic"
        if response_errors or not raw:
            warnings.append("模型没有给出可用选段，已按口播密度挑选。")
        else:
            warnings.append("模型选段都落在句子外面或互相重叠，已按口播密度重选。")
    if not cleaned:
        raise AIResponseError("没有找到适合抖音的口播片段")
    return {
        "source": source,
        "budget": budget,
        "min_seconds": round(minimum, 1),
        "max_seconds": round(maximum, 1),
        "warnings": warnings,
        "clips": cleaned,
    }


def boxes_from_understanding(data: dict | None) -> list[dict]:
    """理解结果里的 subject_box 转成 {time, box}。没有就返回空列表。"""
    if not isinstance(data, dict):
        return []
    visual = ((data.get("tracks") or {}).get("visual") or [])
    boxes = []
    for item in visual:
        if not isinstance(item, dict):
            continue
        box = item.get("subject_box") or item.get("box")
        if not isinstance(box, dict):
            continue
        stamp = _finite(item.get("timestamp", item.get("time")))
        if stamp is None:
            continue
        boxes.append({"time": stamp, "box": box})
    return boxes


def ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    rest = seconds % 60
    return f"{hours}:{minutes:02d}:{rest:05.2f}"


def ass_text(text: str) -> str:
    cleaned = str(text or "").replace("\\", "").replace("{", "｛").replace("}", "｝")
    return cleaned.replace("\n", "\\N")


def build_ass(cues: list[dict], width: int, height: int, font_name: str) -> str:
    """底部白字黑边，安全区大概在画面下方 16%。"""
    font_size = max(18, int(height * 0.034))
    margin_v = max(24, int(height * 0.16))
    events = []
    for cue in cues:
        text = ass_text(cue.get("text") or "")
        if not text:
            continue
        events.append(
            f"Dialogue: 0,{ass_timestamp(float(cue['start']))},{ass_timestamp(float(cue['end']))},"
            f"Caption,,0,0,0,,{text}"
        )
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {int(width)}\n"
        f"PlayResY: {int(height)}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,{font_name},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,"
        f"-1,0,0,0,100,100,0,0,1,4,0,2,70,70,{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(events) + ("\n" if events else "")
