"""AI 智能剪辑分析：共享 ASR 或本地 whisper 转写 → LLM 标出低价值/广告区间。

LLM 走 OpenAI 兼容协议（base_url + api_key + model 环境变量），
可无缝切换 Agnes / DeepSeek / 通义千问 / 小米 MiMo 等任何兼容服务。
"""
import json
import math
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import requests

from app.config import AI_BASE_URL, AI_MODEL, ASR_URL, TMP_DIR, WHISPER_MODEL
from app.services.ai_pool import AI_POOL, AIPoolError
from app.services.media import probe_video, render_semaphore

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


# 共享 ASR 分段转写：每段 300s。不要把原视频直接传给 transcribe_remote——
# requests 的 multipart 编码会先把整个文件读进内存，7GB 视频在 3.4GB 内存的
# 服务器上会直接 MemoryError。先抽成 64k mp3 再按段切分，单段文件只有约 2.4MB。
_CHUNK_SECONDS = 300


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    """用 ffmpeg 把视频抽成 16k 单声道 64k mp3（不要 wav：98 分钟 wav 约 189MB）。"""
    if not shutil.which("ffmpeg"):
        raise AIServiceError("未找到 ffmpeg，无法抽取音频进行转写")
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(video_path),
             "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio_path)],
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired as exc:
        raise AIServiceError("抽取音频超时（1800s）") from exc
    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        raise AIServiceError(
            "抽取音频失败："
            + (result.stderr or "").strip()[-300:]
        )


def _cut_audio_segment(audio_path: Path, start: float, length: float, out_path: Path) -> None:
    """从已抽出的 mp3 里切出一段（重编码，保证切点可用）。"""
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(audio_path), "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out_path)],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise AIServiceError(f"切分音频超时（{start:.0f}s 段）") from exc
    if result.returncode != 0 or not out_path.exists() or out_path.stat().st_size == 0:
        raise AIServiceError(
            f"切分音频失败（{start:.0f}s 段）："
            + (result.stderr or "").strip()[-300:]
        )


def _transcribe_shared(video_path: Path, log=print) -> list[dict]:
    """共享 ASR 路径：抽音频 → 按 300s 分段 → 逐段转写 → 按段偏移拼接。

    临时文件全部放在 TMP_DIR 下的临时目录里，成功或失败都会由
    TemporaryDirectory 清理，不留 7GB 原视频的任何大文件副本。
    """
    import tempfile

    from app.services.asr_client import transcribe_remote

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"transcribe-{video_path.stem}-", dir=TMP_DIR) as tmp:
        tmpdir = Path(tmp)
        audio_path = tmpdir / "audio.mp3"
        _extract_audio(video_path, audio_path)
        duration = (
            probe_video(audio_path).get("duration")
            or probe_video(video_path).get("duration")
            or 0
        )
        if not duration or duration <= 0:
            # 读不到时长时无法分段：此时传的已是抽出的小 mp3，不再是原视频，
            # 不会复发大文件读内存的问题。
            log("无法读取音频时长，改用整段转写（传的是已抽出的小音频）")
            return transcribe_remote(audio_path, ASR_URL, log=log)
        total = int(math.ceil(duration / _CHUNK_SECONDS))
        log(f"音频已抽取（{duration:.0f}s），分 {total} 段转写，每段 {_CHUNK_SECONDS}s")
        out: list[dict] = []
        for index in range(total):
            start = index * _CHUNK_SECONDS
            length = min(_CHUNK_SECONDS, duration - start)
            seg_path = tmpdir / f"seg{index + 1}.mp3"
            _cut_audio_segment(audio_path, start, length, seg_path)
            segments = None
            last_exc = None
            for attempt in range(2):
                try:
                    segments = transcribe_remote(seg_path, ASR_URL, log=log)
                    break
                except Exception as exc:  # noqa: BLE001 - 单段重试后统一包装上报
                    last_exc = exc
                    if attempt == 0:
                        log(
                            f"第 {index + 1}/{total} 段转写失败，2 秒后重试："
                            f"{str(exc) or type(exc).__name__}"
                        )
                        time.sleep(2)
            if segments is None:
                raise AIServiceError(
                    f"第 {index + 1}/{total} 段转写失败"
                    f"（{start:.0f}s-{start + length:.0f}s）："
                    f"{str(last_exc) or type(last_exc).__name__}"
                ) from last_exc
            for seg in segments:
                out.append({
                    "start": round(float(seg["start"]) + start, 2),
                    "end": round(float(seg["end"]) + start, 2),
                    "text": seg["text"],
                })
            log(
                f"转写进度：第 {index + 1}/{total} 段完成（{len(segments)} 句），"
                f"已转写 {min(duration, start + length):.0f}s/{duration:.0f}s"
            )
            seg_path.unlink(missing_ok=True)
        out.sort(key=lambda item: item["start"])
        return out


def _decode_audio_array(video_path: Path):
    """照 ASR 服务 _decode_audio 的做法：ffmpeg 解码成 16k 单声道 float32 数组。

    不要把文件路径直接交给 faster-whisper 的 model.transcribe：
    faster-whisper 1.2.1 + av 19.0.2 会在 av.open(metadata_errors=...) 处报 TypeError。
    """
    import numpy as np

    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video_path),
         "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", "16000", "-"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise AIServiceError(
            "ffmpeg 解码音频失败：" + result.stderr.decode(errors="ignore")[:300]
        )
    return np.frombuffer(result.stdout, np.int16).astype(np.float32) / 32768.0


def _collect_whisper_segments(segments, offset: float) -> list[dict]:
    out = []
    for seg in segments:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        out.append({
            "start": round(float(seg.start) + offset, 2),
            "end": round(float(seg.end) + offset, 2),
            "text": text,
        })
    return out


def _run_whisper(model, audio, offset: float):
    """在渲染锁内把生成器消费完。faster-whisper 的计算发生在迭代时，不能出了锁再读。"""
    with render_semaphore:
        segments, info = model.transcribe(audio, language="zh", vad_filter=True)
        return _collect_whisper_segments(segments, offset), info


def _transcribe_local_chunked(video_path: Path, model, duration: float, log) -> list[dict]:
    """长音频按 300s 分段解码再转写，避免整段 float32 波形和模型同时占满内存。"""
    import tempfile

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"whisper-{video_path.stem}-", dir=TMP_DIR) as tmp:
        tmpdir = Path(tmp)
        audio_path = tmpdir / "audio.mp3"
        _extract_audio(video_path, audio_path)
        heard = float(probe_video(audio_path).get("duration") or duration or 0)
        if heard <= 0:
            heard = duration
        total = max(1, int(math.ceil(heard / _CHUNK_SECONDS)))
        log(f"本地转写按 {_CHUNK_SECONDS}s 分段，共 {total} 段，避免整段音频进内存")
        out: list[dict] = []
        info = None
        for index in range(total):
            start = index * _CHUNK_SECONDS
            length = min(_CHUNK_SECONDS, max(0.1, heard - start))
            seg_path = tmpdir / f"seg{index + 1}.mp3"
            _cut_audio_segment(audio_path, start, length, seg_path)
            audio = _decode_audio_array(seg_path)
            try:
                part, info = _run_whisper(model, audio, start)
            finally:
                del audio
                seg_path.unlink(missing_ok=True)
            out.extend(part)
            log(
                f"本地转写进度：第 {index + 1}/{total} 段完成（{len(part)} 句），"
                f"已转写 {min(heard, start + length):.0f}s/{heard:.0f}s"
            )
        out.sort(key=lambda item: item["start"])
        if info is not None:
            log(f"转写完成：{len(out)} 句（检测语言 {info.language}）")
        return out


def _transcribe_local(video_path: Path, log=print) -> list[dict]:
    """本地 whisper 路径：优先传解码后的 numpy 数组，避开 av bug。长文件走分段。"""
    model = _get_model()
    if not shutil.which("ffmpeg"):
        log("未找到 ffmpeg，回落到文件路径直接转写（可能撞 av TypeError）")
        try:
            out, info = _run_whisper(model, str(video_path), 0.0)
        except Exception as exc:  # noqa: BLE001 - 回落失败必须报出真实错误
            raise AIServiceError(
                "本地 whisper 转写失败（ffmpeg 不可用，已回落文件路径）："
                f"{str(exc) or type(exc).__name__}"
            ) from exc
        log(f"转写完成：{len(out)} 句（检测语言 {info.language}，时长 {info.duration:.0f}s）")
        return out
    duration = float(probe_video(video_path).get("duration") or 0)
    if duration > _CHUNK_SECONDS:
        return _transcribe_local_chunked(video_path, model, duration, log)
    audio = _decode_audio_array(video_path)
    try:
        out, info = _run_whisper(model, audio, 0.0)
    finally:
        del audio
    log(f"转写完成：{len(out)} 句（检测语言 {info.language}，时长 {info.duration:.0f}s）")
    return out


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
        out = _transcribe_shared(video_path, log=log)
    else:
        out = _transcribe_local(video_path, log=log)
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
        "你是短视频导演。输入是同一条原片的融合理解时间线，包含口播、画面和声音。\n"
        f"原片总时长 {duration:.1f} 秒，目标成片约 {target:.0f} 秒。\n\n"
        "先设计钩子→铺垫→转折→高潮→收尾，再按故事选段：\n"
        f"- 总时长控制在 {target * 0.9:.0f}–{target * 1.1:.0f} 秒；\n"
        "- 只选 4–12 个片段，每段 2–15 秒，优先完整句子；\n"
        "- 优先口播讲得好且画面有信息的片段，跳过寒暄、广告、重复和无信息过程；\n"
        "- 开头必须是最有钩子的片段，结尾必须回扣主题；允许把后面的高潮前置，不要重叠；\n"
        "- 每段说明故事角色、重要度（1-5）和为什么选择它；\n"
        '- 只输出 JSON 数组：[{"start":1.5,"end":9.0,"story_role":"hook","importance":5,"reason":"为什么保留"}]\n'
        "- 不确定就少选，不要编造时间或画面。\n"
    ) + (f"\n额外要求（优先）：{custom_prompt.strip()}\n" if custom_prompt.strip() else "") + "\n融合时间线：\n" + transcript


def _clean_plan(items: list[dict], duration: float, target: float) -> list[dict]:
    del target
    valid: list[dict] = []
    occupied: list[tuple[float, float]] = []
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
        if end - start < 0.8 or any(start < other_end and end > other_start for other_start, other_end in occupied):
            continue
        role = str(item.get("story_role") or "setup")
        if role not in {"hook", "setup", "turn", "climax", "ending"}:
            role = "setup"
        try:
            importance = max(1, min(5, int(item.get("importance") or 3)))
        except (TypeError, ValueError):
            importance = 3
        valid.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "story_role": role,
            "importance": importance,
            "reason": str(item.get("reason") or "")[:300],
        })
        occupied.append((start, end))
    return valid


def conserve_duration(clips: list[dict], duration: float, target: float) -> tuple[list[dict], list[str]]:
    """按故事重要度补/删，目标偏差 10% 以外时记录每次调整。"""
    result = [dict(item) for item in clips]
    adjustments: list[str] = []
    total = sum(item["end"] - item["start"] for item in result)
    if total > target * 1.1:
        overflow = total - target * 1.1
        candidates = sorted(
            [item for item in result if item.get("story_role") not in {"hook", "ending"}],
            key=lambda item: (item.get("importance", 3), item["end"] - item["start"]),
        )
        for item in candidates:
            if overflow <= 0:
                break
            length = item["end"] - item["start"]
            cut = min(overflow, max(0.0, length - 0.8))
            if cut:
                item["end"] = round(item["end"] - cut, 2)
                adjustments.append(f"裁短 {item['story_role']} 段 {cut:.2f} 秒")
                overflow -= cut
        while overflow > 0.05 and candidates:
            victim = candidates.pop(0)
            if victim not in result:
                continue
            result.remove(victim)
            length = victim["end"] - victim["start"]
            overflow -= length
            adjustments.append(f"删除低重要度 {victim['story_role']} 段（{length:.2f} 秒）")
    total = sum(item["end"] - item["start"] for item in result)
    if total < target * 0.9:
        need = target - total
        for item in result:
            if need <= 0:
                break
            next_starts = [other["start"] for other in result if other["start"] >= item["end"] and other is not item]
            boundary = min([duration, *next_starts])
            room = max(0.0, boundary - item["end"] - 0.1)
            add = min(room, need)
            if add:
                item["end"] = round(item["end"] + add, 2)
                item["reason"] = (item["reason"] + "；为时长守恒扩展").strip("；")
                adjustments.append(f"扩展 {item['story_role']} 段 {add:.2f} 秒")
                need -= add
    return result, adjustments


def plan_edit(segments: list[dict], duration: float, target_duration: float,
              custom_prompt: str = "", log=print, cuts: list[float] | None = None,
              beats: list[float] | None = None, understanding: dict | None = None) -> dict:
    if not segments and not understanding:
        raise AIResponseError("没有可用的融合理解，无法选段")
    target = max(5.0, min(float(target_duration or 60), duration))
    transcript = "\n".join(_format_segment(segment) for segment in segments)
    if understanding:
        transcript += "\n画面与声音：\n" + json.dumps({
            "visual": (understanding.get("tracks") or {}).get("visual") or [],
            "audio_summary": understanding.get("audio_summary") or {},
            "structure": understanding.get("structure") or [],
        }, ensure_ascii=False)
    if len(transcript) > 36000:
        head, tail = transcript[:18000], transcript[-18000:]
        transcript = head + "\n...[中间材料省略]...\n" + tail
    items = _request_ai(_plan_prompt(duration, target, transcript, custom_prompt), log)
    clips = _clean_plan(items, duration, target)
    if cuts:
        from app.services.video_analysis import snap_plan_to_cuts
        clips = snap_plan_to_cuts(
            clips, cuts, duration, beats=beats,
            sentence_ends=[float(item["end"]) for item in segments],
        )
    clips, adjustments = conserve_duration(clips, duration, target)
    if not clips:
        raise AIResponseError("AI 没有返回可用的保留片段")
    return {
        "duration": round(float(duration), 2),
        "target_duration": round(target, 1),
        "clips": clips,
        "total": round(sum(item["end"] - item["start"] for item in clips), 2),
        "adjustments": adjustments,
        "story_arc": [item["story_role"] for item in clips],
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
