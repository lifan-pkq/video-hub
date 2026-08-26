"""AI 智能剪辑分析：faster-whisper 本地转写字幕 → LLM 标出低价值/广告区间。

LLM 走 OpenAI 兼容协议（base_url + api_key + model 环境变量），
可无缝切换 Agnes / DeepSeek / 通义千问 / 小米 MiMo 等任何兼容服务。
"""
import json
import re
import threading
from pathlib import Path

import requests

from app.config import AI_API_KEY, AI_BASE_URL, AI_MODEL, TMP_DIR, WHISPER_MODEL
from app.services.media import render_semaphore

# whisper 模型全局单例（首次加载需下载模型文件，之后常驻内存）
_model = None
_model_lock = threading.Lock()

# 各类废片段的判定说明（写进 LLM prompt）
KIND_DESCS = {
    "intro": "开头的寒暄、自我介绍、与主题无关的铺垫",
    "outro": "结尾的求关注/求点赞/预告下期/客套收尾",
    "ad": "中间的广告口播、带货推广、品牌植入",
}


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
    """本地语音转写，返回 [{start, end, text}]（秒）。结果按 文件名+大小 缓存。"""
    cache = _cache_path(video_path)
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            if cached.get("size") == video_path.stat().st_size:
                log(f"命中转写缓存（{len(cached['segments'])} 句）")
                return cached["segments"]
        except Exception:
            pass
    model = _get_model()
    # 限并发：whisper 推理内存开销大，多请求排队执行
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


def _build_prompt(kinds: list[str], custom_prompt: str, duration: float, transcript: str) -> str:
    lines = ["你是视频剪辑助手。下面是视频的语音识别字幕（每行格式：[开始秒-结束秒] 文本），视频总时长 %.1f 秒。" % duration,
             "", "请找出以下内容低价值、可以直接剪掉的时间区间："]
    for i, k in enumerate(kinds, 1):
        lines.append(f"{i}. {k}：{KIND_DESCS[k]}")
    lines += [
        "",
        "要求：",
        f"- 宁缺毋滥，不确定的不要标；区间必须落在 0~{duration:.1f} 内",
        "- 同一段区间不要重叠",
        '- 只输出 JSON 数组，不要输出任何其他内容，格式：',
        '[{"start": 1.5, "end": 8.0, "kind": "%s", "reason": "简述"}]' % kinds[0],
        "- 没有可剪的内容就输出 []",
    ]
    if custom_prompt.strip():
        lines += ["", f"额外要求（用户自定义，优先级最高）：{custom_prompt.strip()}"]
    lines += ["", "字幕：", transcript]
    return "\n".join(lines)


def analyze_transcript(segments: list[dict], duration: float,
                       kinds: list[str] | None = None, custom_prompt: str = "",
                       log=print) -> list[dict]:
    """把字幕发给 LLM，返回废片区间 [{start, end, kind, reason}]。失败抛 RuntimeError。

    kinds: 要识别的废片类型子集（intro/outro/ad），默认全部。
    custom_prompt: 用户自定义的额外剪辑要求，原样拼进 prompt。
    """
    if not AI_API_KEY:
        raise RuntimeError("未配置 AI API Key（环境变量 VIDEO_HUB_AI_API_KEY）")
    kinds = [k for k in (kinds or list(KIND_DESCS)) if k in KIND_DESCS] or list(KIND_DESCS)
    if not segments:
        return []
    transcript = "\n".join(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}" for s in segments)
    r = requests.post(
        f"{AI_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {AI_API_KEY}"},
        json={
            "model": AI_MODEL,
            "messages": [{"role": "user", "content": _build_prompt(kinds, custom_prompt, duration, transcript)}],
            "temperature": 0.1,
        },
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"AI 接口错误 {r.status_code}：{r.text[:200]}")
    content = r.json()["choices"][0]["message"]["content"]
    log(f"AI 返回：{content[:200]}")

    m = re.search(r"\[[\s\S]*\]", content)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError:
        raise RuntimeError("AI 返回内容不是合法 JSON")

    # 清洗：类型/范围校验 + 去重叠
    valid = []
    for it in items:
        try:
            start, end = float(it["start"]), float(it["end"])
        except (KeyError, TypeError, ValueError):
            continue
        start, end = max(0.0, start), min(float(duration), end)
        if end - start < 0.3:
            continue
        valid.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "kind": it.get("kind") if it.get("kind") in ("intro", "outro", "ad") else "ad",
            "reason": str(it.get("reason") or ""),
        })
    valid.sort(key=lambda x: x["start"])
    merged: list[dict] = []
    for it in valid:
        if merged and it["start"] < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], it["end"])
        else:
            merged.append(it)
    return merged


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
