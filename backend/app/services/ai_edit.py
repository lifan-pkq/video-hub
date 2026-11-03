"""AI 智能剪辑分析：共享 ASR 或本地 whisper 转写 → LLM 标出低价值/广告区间。

LLM 走 OpenAI 兼容协议（base_url + api_key + model 环境变量），
可无缝切换 Agnes / DeepSeek / 通义千问 / 小米 MiMo 等任何兼容服务。
"""
import json
import math
import re
import threading
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests

from app.config import AI_BASE_URL, AI_MODEL, ASR_URL, TMP_DIR, WHISPER_MODEL
from app.services.ai_pool import AI_POOL, AIPoolError
from app.services.media import render_semaphore

# 兼容旧调用/测试：生产环境优先使用 AI_POOL；单 Key 回退仅在池为空时生效。
AI_API_KEY = AI_POOL.keys[0] if AI_POOL.keys else ""

# whisper 模型全局单例（首次加载需下载模型文件，之后常驻内存）
_model = None
_model_lock = threading.Lock()

# 各类废片段的判定说明（写进 LLM prompt）
KIND_DESCS = {
    "intro": "开头的寒暄、自我介绍、与主题无关的铺垫",
    "outro": "结尾的求关注/求点赞/预告下期/客套收尾",
    "ad": "中间的广告口播、带货推广、品牌植入",
    "repeat": "无信息价值的重复句、口头禅；只能从给出的候选句中选择",
    "silence": "本地音频检测到的静音区间",
}
DEFAULT_LLM_KINDS = ["intro", "outro", "ad"]


class AIServiceError(RuntimeError):
    """AI 接口不可用或返回非成功状态。"""


class AIResponseError(RuntimeError):
    """AI 响应结构或 JSON 内容无法解析。"""


def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        return _model


def _cache_path(video_path: Path) -> Path:
    return TMP_DIR / "transcripts" / f"{video_path.stem}.json"


def transcribe(video_path: Path, log=print) -> list[dict]:
    """语音转写，返回 [{start, end, text}]（秒）。结果按 文件名+大小 缓存。"""
    cache = _cache_path(video_path)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if cached.get("size") == video_path.stat().st_size:
                log(f"命中转写缓存（{len(cached['segments'])} 句）")
                return cached["segments"]
        except Exception:
            pass
    if ASR_URL:
        from app.services.asr_client import transcribe_remote

        out = transcribe_remote(video_path, ASR_URL, log=log)
    else:
        model = _get_model()
        with render_semaphore:
            segments, info = model.transcribe(str(video_path), language="zh", vad_filter=True)
        out = []
        for seg in segments:
            text = seg.text.strip()
            if text:
                out.append({"start": round(seg.start, 2), "end": round(seg.end, 2), "text": text})
        log(f"转写完成：{len(out)} 句（检测语言 {info.language}，时长 {info.duration:.0f}s）")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"size": video_path.stat().st_size, "segments": out}, ensure_ascii=False))
    except Exception:
        pass
    return out


def _normalize_repetition_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(char for char in normalized if char.isalnum())


def repetition_candidates(segments: list[dict]) -> list[dict]:
    """本地找出完全或高相似重复句，只返回后出现的候选区间。"""
    candidates = []
    representatives: list[str] = []
    exact_seen = set()
    for segment in segments:
        normalized = _normalize_repetition_text(str(segment.get("text") or ""))
        if not normalized:
            continue
        repeated = normalized in exact_seen
        if not repeated and len(normalized) >= 6:
            for previous in representatives:
                if min(len(normalized), len(previous)) < 6:
                    continue
                if min(len(normalized), len(previous)) / max(len(normalized), len(previous)) < 0.9:
                    continue
                if SequenceMatcher(None, normalized, previous).ratio() >= 0.9:
                    repeated = True
                    break
        if repeated:
            candidates.append(dict(segment))
        else:
            exact_seen.add(normalized)
            representatives.append(normalized)
    return candidates


def _format_segment(segment: dict) -> str:
    return f"[{float(segment['start']):.1f}-{float(segment['end']):.1f}] {segment['text']}"


def chunk_segments(segments: list[dict], char_budget: int = 6000,
                   context_chars: int = 240) -> list[dict]:
    """按字幕字符预算分块，并为每块附带前后少量仅供理解的上下文。"""
    if not segments:
        return []
    char_budget = max(1, int(char_budget))
    context_chars = max(0, int(context_chars))
    cores = []
    current = []
    used = 0
    for segment in segments:
        line_chars = len(_format_segment(segment))
        added = line_chars + (1 if current else 0)
        if current and used + added > char_budget:
            cores.append(current)
            current = []
            used = 0
            added = line_chars
        current.append(segment)
        used += added
    if current:
        cores.append(current)

    chunks = []
    for index, core in enumerate(cores):
        before = []
        before_chars = 0
        if index:
            for segment in reversed(cores[index - 1]):
                added = len(_format_segment(segment)) + (1 if before else 0)
                if before_chars + added > context_chars:
                    break
                before.insert(0, segment)
                before_chars += added
        after = []
        after_chars = 0
        if index + 1 < len(cores):
            for segment in cores[index + 1]:
                added = len(_format_segment(segment)) + (1 if after else 0)
                if after_chars + added > context_chars:
                    break
                after.append(segment)
                after_chars += added
        chunks.append({
            "segments": before + core + after,
            "start": float(core[0]["start"]),
            "end": float(core[-1]["end"]),
        })
    return chunks


def clean_intervals(items: list[dict], duration: float, kinds: list[str] | None = None,
                    range_start: float = 0.0, range_end: float | None = None) -> list[dict]:
    """校验、截断并合并建议区间；任何非有限数或无效时长都不会进入结果。"""
    duration = float(duration)
    lower = float(range_start)
    upper = duration if range_end is None else float(range_end)
    if not all(math.isfinite(value) for value in (duration, lower, upper)) or duration <= 0:
        return []
    lower = max(0.0, min(duration, lower))
    upper = max(0.0, min(duration, upper))
    allowed = [kind for kind in (kinds or list(KIND_DESCS)) if kind in KIND_DESCS]
    if not allowed or upper <= lower:
        return []

    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = max(lower, min(upper, start))
        end = max(lower, min(upper, end))
        if end - start < 0.3:
            continue
        kind = item.get("kind")
        if kind not in allowed:
            if "ad" in allowed:
                kind = "ad"
            elif len(allowed) == 1:
                kind = allowed[0]
            else:
                continue
        valid.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "kind": kind,
            "reason": str(item.get("reason") or ""),
            "source": str(item.get("source") or "llm"),
        })
    valid.sort(key=lambda item: (item["start"], item["end"]))
    merged = []
    for item in valid:
        if merged and item["start"] < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
        else:
            merged.append(item)
    return merged


def _build_prompt(kinds: list[str], custom_prompt: str, duration: float, transcript: str,
                  range_start: float = 0.0, range_end: float | None = None) -> str:
    range_end = duration if range_end is None else range_end
    lines = ["你是视频剪辑助手。下面是视频的语音识别字幕（每行格式：[开始秒-结束秒] 文本），视频总时长 %.1f 秒。" % duration,
             "", "请找出以下内容低价值、可以直接剪掉的时间区间："]
    for i, k in enumerate(kinds, 1):
        lines.append(f"{i}. {k}：{KIND_DESCS[k]}")
    lines += [
        "",
        "要求：",
        f"- 宁缺毋滥，不确定的不要标；区间必须落在 {range_start:.1f}~{range_end:.1f} 内",
        "- 本块范围外的字幕仅用于理解上下文，不得返回对应区间",
        "- 同一段区间不要重叠",
        '- 只输出 JSON 数组，不要输出任何其他内容，格式：',
        '[{"start": 1.5, "end": 8.0, "kind": "%s", "reason": "简述"}]' % kinds[0],
        "- 没有可剪的内容就输出 []",
    ]
    if kinds == ["repeat"]:
        lines.append("- 只判断候选重复句是否值得剪除，不得创造候选之外的区间")
    if custom_prompt.strip():
        lines += ["", f"额外要求（用户自定义，优先级最高）：{custom_prompt.strip()}"]
    lines += ["", "字幕：", transcript]
    return "\n".join(lines)


def _request_ai(prompt: str, log) -> list[dict]:
    if AI_POOL.has_keys():
        try:
            r = AI_POOL.request(
                "POST",
                f"{AI_BASE_URL.rstrip('/')}/chat/completions",
                json_body={
                    "model": AI_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.1,
                },
                timeout=120,
            )
        except AIPoolError as exc:
            raise AIServiceError(f"AI 接口错误：{exc}") from exc
    else:
        if not AI_API_KEY:
            raise AIServiceError("没有配置 AI API Key")
        r = requests.post(
            f"{AI_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": AI_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1,
            },
            timeout=120,
        )
    if r.status_code != 200:
        raise AIServiceError(f"AI 接口错误 {r.status_code}：{r.text[:200]}")
    try:
        content = r.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("content is not a string")
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise AIResponseError("AI 返回结构不完整，缺少文本内容") from exc
    log(f"AI 返回：{content[:200]}")
    match = re.search(r"\[[\s\S]*\]", content)
    if not match:
        raise AIResponseError("AI 返回中未找到 JSON 数组")
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AIResponseError("AI 返回内容不是合法 JSON") from exc
    if not isinstance(items, list):
        raise AIResponseError("AI 返回内容不是区间数组")
    return items


def analyze_transcript(segments: list[dict], duration: float,
                       kinds: list[str] | None = None, custom_prompt: str = "",
                       log=print, char_budget: int = 6000, context_chars: int = 240) -> list[dict]:
    """分块发送字幕和重复候选给 LLM，返回来源为 llm 的清洗后区间。"""
    selected = [kind for kind in (kinds or DEFAULT_LLM_KINDS) if kind in KIND_DESCS] or DEFAULT_LLM_KINDS
    if not segments:
        return []
    base_kinds = [kind for kind in selected if kind in DEFAULT_LLM_KINDS]
    candidates = repetition_candidates(segments) if "repeat" in selected else []
    if not base_kinds and not candidates:
        return []
    if not AI_POOL.has_keys() and not AI_API_KEY:
        raise RuntimeError("未配置 AI API Key")

    jobs = []
    if base_kinds:
        jobs.append((base_kinds, segments))
    if candidates:
        jobs.append((["repeat"], candidates))
    results = []
    for job_kinds, job_segments in jobs:
        chunks = chunk_segments(job_segments, char_budget=char_budget, context_chars=context_chars)
        for index, chunk in enumerate(chunks, 1):
            log(f"AI 分析分块 {index}/{len(chunks)}：{chunk['start']:.1f}s-{chunk['end']:.1f}s")
            transcript = "\n".join(_format_segment(segment) for segment in chunk["segments"])
            prompt = _build_prompt(
                job_kinds, custom_prompt, duration, transcript, chunk["start"], chunk["end"],
            )
            raw_items = _request_ai(prompt, log)
            results.extend(clean_intervals(
                raw_items, duration, kinds=job_kinds,
                range_start=chunk["start"], range_end=chunk["end"],
            ))
    return clean_intervals(results, duration, kinds=[kind for kind in selected if kind != "silence"])




def _plan_prompt(duration: float, target: float, transcript: str, custom_prompt: str) -> str:
    return (
        "你是短视频导演。下面是一条视频的语音字幕，格式：[开始秒-结束秒] 文本。\n"
        f"原片总时长 {duration:.1f} 秒，目标成片约 {target:.0f} 秒。\n\n"
        "请选择要保留的片段，要求：\n"
        f"- 总时长控制在 {target * 0.9:.0f}–{target * 1.1:.0f} 秒，不能明显超时；\n"
        "- 只选 4–12 个片段，每段 2–15 秒，优先完整句子；\n"
        "- 片段要分布在整条视频，不要只取开头；\n"
        "- 开头选最有钩子的内容，结尾选能回扣主题的内容；\n"
        "- 跳过寒暄、广告、重复、无信息过程；\n"
        "- 片段按原片时间顺序输出，不要重叠；\n"
        '- 只输出 JSON 数组：[{"start": 1.5, "end": 9.0, "reason": "为什么保留"}]\n'
        "- 不确定就少选，不要编造时间。\n"
    ) + (f"\n额外要求（优先）：{custom_prompt.strip()}\n" if custom_prompt.strip() else "") + "\n字幕：\n" + transcript


def _clean_plan(items: list[dict], duration: float, target: float) -> list[dict]:
    valid = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        start = max(0.0, min(float(duration), start))
        end = max(0.0, min(float(duration), end))
        if end - start < 0.8:
            continue
        valid.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "reason": str(item.get("reason") or "")[:160],
        })
    valid.sort(key=lambda item: item["start"])
    merged: list[dict] = []
    for item in valid:
        if merged and item["start"] < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            continue
        merged.append(item)
    total = sum(item["end"] - item["start"] for item in merged)
    if total > target * 1.12 and merged:
        # 从尾部逐步裁短，优先保住开头钩子
        overflow = total - target * 1.12
        for item in reversed(merged):
            length = item["end"] - item["start"]
            cut = min(overflow, max(0.0, length - 0.8))
            item["end"] = round(item["end"] - cut, 2)
            overflow -= cut
            if overflow <= 0:
                break
    merged = [item for item in merged if item["end"] - item["start"] >= 0.8]
    # LLM 常会少选；优先向后扩展已选片段，保证接近目标时长
    total = sum(item["end"] - item["start"] for item in merged)
    need = target * 0.95 - total
    if need > 0:
        for index, item in enumerate(merged):
            if need <= 0:
                break
            next_start = merged[index + 1]["start"] if index + 1 < len(merged) else duration
            room = max(0.0, next_start - item["end"] - 0.3)
            add = min(room, need)
            if add > 0:
                item["end"] = round(item["end"] + add, 2)
                item["reason"] = (item["reason"] + "；扩展以接近目标时长").strip("；")
                need -= add
        if need > 0 and merged:
            tail = merged[-1]
            add = min(max(0.0, duration - tail["end"]), need)
            tail["end"] = round(tail["end"] + add, 2)
    return merged


def plan_edit(segments: list[dict], duration: float, target_duration: float,
              custom_prompt: str = "", log=print, cuts: list[float] | None = None) -> dict:
    if not segments:
        raise AIResponseError("没有可用的语音字幕，无法选段")
    target = max(5.0, min(float(target_duration or 60), duration))
    transcript = "\n".join(_format_segment(segment) for segment in segments)
    # 控制上下文，优先保留完整时间轴；超长时截断中段，但保留开头和结尾
    if len(transcript) > 36000:
        head, tail = transcript[:18000], transcript[-18000:]
        transcript = head + "\n...[中间字幕省略]...\n" + tail
    items = _request_ai(_plan_prompt(duration, target, transcript, custom_prompt), log)
    clips = _clean_plan(items, duration, target)
    if cuts:
        from app.services.video_analysis import snap_plan_to_cuts
        clips = snap_plan_to_cuts(clips, cuts, duration)
    if not clips:
        raise AIResponseError("AI 没有返回可用的保留片段")
    return {
        "duration": round(float(duration), 2),
        "target_duration": round(target, 1),
        "clips": clips,
        "total": round(sum(item["end"] - item["start"] for item in clips), 2),
    }


def keep_segments(waste: list[dict], duration: float) -> list[dict]:
    """废片区间求补集 → 保留区间 [{start, end}]（供拼接渲染）。"""
    keep = []
    pos = 0.0
    for w in waste:
        if w["start"] - pos >= 0.3:
            keep.append({"start": round(pos, 2), "end": round(w["start"], 2)})
        pos = max(pos, w["end"])
    if duration - pos >= 0.3:
        keep.append({"start": round(pos, 2), "end": round(duration, 2)})
    return keep
