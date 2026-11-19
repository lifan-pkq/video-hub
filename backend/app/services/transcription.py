"""本机或远端转写。和选题、废片分析分开，避免把 whisper 生命周期揉进剪辑提示词。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from app.config import ASR_URL, TMP_DIR, WHISPER_MODEL
from app.services.ai_edit import AIServiceError, TranscriptionCancelled
from app.services.media import probe_video, render_semaphore

_model = None
_model_lock = threading.Lock()
# 同一个 WhisperModel 不能两路同时解码。缓存的检查、计算和写入也要放在这把锁里，
# 否则后完成的一路会把先完成的正确结果盖掉。
_inference_lock = threading.Lock()

def _get_model():
    global _model
    with _model_lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise AIServiceError(
                    "本机没有 faster-whisper，无法转写。"
                    "安装 faster-whisper，或设置 VIDEO_HUB_ASR_URL 改走远端转写。"
                ) from exc
            try:
                _model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            except Exception as exc:
                raise AIServiceError(
                    f"加载转写模型「{WHISPER_MODEL}」失败：{exc}。"
                    "没有改用别的模型，这次不能当成转写成功。"
                    "确认模型名是 tiny、base 或 small，磁盘还有空间，并且首次下载能访问模型仓库。"
                ) from exc
        return _model


_FAST_MODELS = {"tiny", "tiny.en", "base", "base.en"}
_STANDARD_MODELS = {"small", "small.en"}
_run_local = threading.local()


def decode_settings(model_name: str) -> dict:
    """锁定中文。提示词只说明用简体，不放入任何待转写的句子。

    同一条口播上，不写这个提示时 small 会输出繁体或丢掉个别字。
    温度回退仍用库默认，不把温度钉死。精度差别主要来自模型大小。
    请求的模型加载失败时不会改用更小的模型。
    """
    del model_name
    return {
        "language": "zh",
        "task": "transcribe",
        "vad_filter": True,
        "beam_size": 5,
        "condition_on_previous_text": True,
        "initial_prompt": "以下是简体中文。",
    }


def asr_preset(model_name: str) -> str:
    if model_name in _FAST_MODELS:
        return "fast"
    if model_name in _STANDARD_MODELS:
        return "standard"
    if model_name == "remote":
        return "remote"
    return "custom"


def public_asr_status(run: dict | None) -> dict:
    run = run or {}
    model = str(run.get("model") or "")
    preset = asr_preset(model) if model else ""
    ok = bool(run.get("ok")) and bool(model)
    if not ok:
        note = "转写没有成功完成，不能当成一次可用的草稿。"
    elif preset == "fast":
        note = (
            f"这次用的是较快模型 {model}。"
            "解码分数不能用来判断字对不对。没经人工核对的文字都是未核对草稿，不是标准精度结果。"
        )
    elif preset == "standard":
        note = (
            f"这次用的是标准模型 {model}，语言锁定为中文。"
            "解码分数不能用来判断字对不对。没经人工核对的文字都是未核对草稿，不是无错引用。"
        )
    elif preset == "remote":
        note = "这次走的是远端转写服务，不是本机标准模型。草稿需要核对。"
    elif model.startswith("sensevoice"):
        note = (
            "这次用的是 SenseVoice int8，不是 Whisper。"
            "解码分数不能用来判断字对不对。没经人工核对的文字都是未核对草稿。"
        )
    else:
        note = f"这次用的是模型 {model}。没有把它记成标准精度。"
    return {
        "model": model,
        "preset": preset if ok else "",
        "language": "zh",
        "ok": ok,
        "cache_hit": bool(run.get("cache_hit")),
        "note": note,
    }


def last_asr_run() -> dict:
    return dict(getattr(_run_local, "run", {}) or {})


def _remember_run(run: dict) -> None:
    _run_local.run = dict(run)


def _fingerprint(model_name: str, settings: dict) -> str:
    raw = json.dumps({"model": model_name, "decode": settings}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _cache_path(video_path: Path, model_name: str | None = None, settings: dict | None = None) -> Path:
    if not model_name:
        model_name = "remote" if ASR_URL else WHISPER_MODEL
    if settings is None:
        settings = {"language": "zh"} if model_name == "remote" else decode_settings(model_name)
    size = video_path.stat().st_size if video_path.exists() else 0
    return TMP_DIR / "transcripts" / f"{video_path.stem}-{size}-{_fingerprint(model_name, settings)}.json"


# 共享 ASR 分段转写：每段 300s。不要把原视频直接传给 transcribe_remote——
# requests 的 multipart 编码会先把整个文件读进内存，7GB 视频在 3.4GB 内存的
# 服务器上会直接 MemoryError。先抽成 64k mp3 再按段切分，单段文件只有约 2.4MB。
_CHUNK_SECONDS = 300


def _stop_if_cancelled(should_cancel) -> None:
    if callable(should_cancel) and should_cancel():
        raise TranscriptionCancelled("任务已取消")


def _ffmpeg_audio(args: list[str], *, timeout: int, job_id: str | None, should_cancel, fail: str) -> None:
    """走 engine.media.ffmpeg，取消时能杀掉进程组，而不是干等 subprocess。"""
    from engine.errors import EngineError
    from engine.media import ffmpeg

    try:
        ffmpeg(args, timeout=timeout, job_id=job_id, should_cancel=should_cancel)
    except EngineError as exc:
        if exc.code == "cancelled":
            raise TranscriptionCancelled("任务已取消") from exc
        if exc.code == "timeout":
            raise AIServiceError(f"{fail}超时（{timeout}s）") from exc
        raise AIServiceError(f"{fail}：{exc.message}") from exc


def _extract_audio(video_path: Path, audio_path: Path, *, job_id: str | None = None, should_cancel=None) -> None:
    """用 ffmpeg 把视频抽成 16k 单声道 64k mp3（不要 wav：98 分钟 wav 约 189MB）。"""
    _stop_if_cancelled(should_cancel)
    _ffmpeg_audio(
        ["-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio_path)],
        timeout=1800,
        job_id=job_id,
        should_cancel=should_cancel,
        fail="抽取音频失败",
    )
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise AIServiceError("抽取音频失败：没有写出音频")


def _cut_audio_segment(
    audio_path: Path,
    start: float,
    length: float,
    out_path: Path,
    *,
    job_id: str | None = None,
    should_cancel=None,
) -> None:
    """从已抽出的 mp3 里切出一段（重编码，保证切点可用）。"""
    _stop_if_cancelled(should_cancel)
    _ffmpeg_audio(
        ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(audio_path),
         "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out_path)],
        timeout=600,
        job_id=job_id,
        should_cancel=should_cancel,
        fail=f"切分音频失败（{start:.0f}s 段）",
    )
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise AIServiceError(f"切分音频失败（{start:.0f}s 段）：没有写出音频")


def _transcribe_shared(video_path: Path, log=print, *, job_id: str | None = None, should_cancel=None) -> list[dict]:
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
        _extract_audio(video_path, audio_path, job_id=job_id, should_cancel=should_cancel)
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
            _stop_if_cancelled(should_cancel)
            start = index * _CHUNK_SECONDS
            length = min(_CHUNK_SECONDS, duration - start)
            seg_path = tmpdir / f"seg{index + 1}.mp3"
            _cut_audio_segment(audio_path, start, length, seg_path, job_id=job_id, should_cancel=should_cancel)
            segments = None
            last_exc = None
            for attempt in range(2):
                _stop_if_cancelled(should_cancel)
                try:
                    segments = transcribe_remote(seg_path, ASR_URL, log=log)
                    break
                except TranscriptionCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 - 单段重试后统一包装上报
                    last_exc = exc
                    if attempt == 0:
                        log(
                            f"第 {index + 1}/{total} 段转写失败，2 秒后重试："
                            f"{str(exc) or type(exc).__name__}"
                        )
                        for _ in range(4):
                            _stop_if_cancelled(should_cancel)
                            time.sleep(0.5)
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


def _review_reason(segment) -> str:
    """低置信标记只提示核对，不改写、不补说话人、不补否定词。"""
    reasons = []
    avg = getattr(segment, "avg_logprob", None)
    no_speech = getattr(segment, "no_speech_prob", None)
    compression = getattr(segment, "compression_ratio", None)
    try:
        if avg is not None and float(avg) < -0.8:
            reasons.append("平均对数概率偏低")
    except (TypeError, ValueError):
        pass
    try:
        if no_speech is not None and float(no_speech) > 0.6:
            reasons.append("非语音概率偏高")
    except (TypeError, ValueError):
        pass
    try:
        if compression is not None and float(compression) > 2.4:
            reasons.append("压缩比偏高")
    except (TypeError, ValueError):
        pass
    return "、".join(reasons)


def _collect_whisper_segments(segments, offset: float) -> list[dict]:
    out = []
    for seg in segments:
        text = str(getattr(seg, "text", "") or "").strip()
        if not text:
            continue
        reason = _review_reason(seg)
        item = {
            "start": round(float(seg.start) + offset, 2),
            "end": round(float(seg.end) + offset, 2),
            "text": text,
            "origin": "asr",
            "confirmed": False,
            "review_state": "unverified",
            "low_confidence": bool(reason),
        }
        if reason:
            item["review_reason"] = reason
        out.append(item)
    return out


def _run_whisper(model, audio, offset: float, settings: dict):
    """在渲染锁内把生成器消费完。faster-whisper 的计算发生在迭代时，不能出了锁再读。"""
    with render_semaphore:
        segments, info = model.transcribe(audio, **settings)
        return _collect_whisper_segments(segments, offset), info


def _transcribe_local_chunked(
    video_path: Path, model, duration: float, log, *, job_id: str | None = None, should_cancel=None,
    settings: dict | None = None,
) -> list[dict]:
    """长音频按 300s 分段解码再转写，避免整段 float32 波形和模型同时占满内存。"""
    import tempfile

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"whisper-{video_path.stem}-", dir=TMP_DIR) as tmp:
        tmpdir = Path(tmp)
        audio_path = tmpdir / "audio.mp3"
        _extract_audio(video_path, audio_path, job_id=job_id, should_cancel=should_cancel)
        heard = float(probe_video(audio_path).get("duration") or duration or 0)
        if heard <= 0:
            heard = duration
        total = max(1, int(math.ceil(heard / _CHUNK_SECONDS)))
        log(f"本地转写按 {_CHUNK_SECONDS}s 分段，共 {total} 段，避免整段音频进内存")
        out: list[dict] = []
        info = None
        for index in range(total):
            _stop_if_cancelled(should_cancel)
            start = index * _CHUNK_SECONDS
            length = min(_CHUNK_SECONDS, max(0.1, heard - start))
            seg_path = tmpdir / f"seg{index + 1}.mp3"
            _cut_audio_segment(audio_path, start, length, seg_path, job_id=job_id, should_cancel=should_cancel)
            audio = _decode_audio_array(seg_path)
            try:
                part, info = _run_whisper(model, audio, start, settings or decode_settings(WHISPER_MODEL))
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
            log(f"转写完成：{len(out)} 句（模型 {WHISPER_MODEL}，检测语言 {info.language}）")
        return out


def _transcribe_local(
    video_path: Path, log=print, *, job_id: str | None = None, should_cancel=None,
    settings: dict | None = None,
) -> list[dict]:
    """本地 whisper 路径：优先传解码后的 numpy 数组，避开 av bug。长文件走分段。"""
    settings = settings or decode_settings(WHISPER_MODEL)
    model = _get_model()
    if not shutil.which("ffmpeg"):
        log("未找到 ffmpeg，回落到文件路径直接转写（可能撞 av TypeError）")
        try:
            out, info = _run_whisper(model, str(video_path), 0.0, settings)
        except Exception as exc:  # noqa: BLE001 - 回落失败必须报出真实错误
            raise AIServiceError(
                "本地 whisper 转写失败（ffmpeg 不可用，已回落文件路径）："
                f"{str(exc) or type(exc).__name__}"
            ) from exc
        log(f"转写完成：{len(out)} 句（模型 {WHISPER_MODEL}，检测语言 {info.language}，时长 {info.duration:.0f}s）")
        return out
    duration = float(probe_video(video_path).get("duration") or 0)
    if duration > _CHUNK_SECONDS:
        return _transcribe_local_chunked(
            video_path, model, duration, log, job_id=job_id, should_cancel=should_cancel, settings=settings,
        )
    audio = _decode_audio_array(video_path)
    try:
        out, info = _run_whisper(model, audio, 0.0, settings)
    finally:
        del audio
    log(f"转写完成：{len(out)} 句（模型 {WHISPER_MODEL}，检测语言 {info.language}，时长 {info.duration:.0f}s）")
    return out


def _cacheable(segments: list[dict], duration: float) -> bool:
    """时间明显超出音频的结果不进缓存，避免把一次坏解码留给后面的任务。"""
    if not duration or duration <= 0:
        return True
    limit = float(duration) + 1.0
    for item in segments:
        if not isinstance(item, dict):
            return False
        try:
            end = float(item.get("end"))
        except (TypeError, ValueError):
            return False
        if end > limit:
            return False
    return True


def asr_engine() -> str:
    """默认 SenseVoice。设 VIDEO_HUB_ASR_ENGINE=whisper 才走原来的 faster-whisper。"""
    return os.environ.get("VIDEO_HUB_ASR_ENGINE", "sensevoice").strip().lower() or "sensevoice"


def _read_cache(cache: Path, size: int, model_name: str, settings: dict):
    if not cache.exists():
        return None
    try:
        cached = json.loads(cache.read_text())
    except Exception:
        return None
    if cached.get("size") != size or cached.get("model") != model_name or cached.get("decode") != settings:
        return None
    segments = cached.get("segments")
    if not isinstance(segments, list):
        return None
    return segments


def transcribe(video_path: Path, log=print, *, job_id: str | None = None, should_cancel=None) -> list[dict]:
    """语音转写，返回 [{start, end, text}]（秒）。缓存按文件大小、模型和编码参数分开。"""
    _stop_if_cancelled(should_cancel)
    with _inference_lock:
        return _transcribe_unlocked(video_path, log, job_id=job_id, should_cancel=should_cancel)


def _transcribe_sensevoice(video_path: Path, log=print, *, job_id: str | None = None, should_cancel=None) -> list[dict]:
    from app.services.sensevoice import _recognizer, transcribe_array

    _recognizer()
    duration = float(probe_video(video_path).get("duration") or 0)
    if duration > _CHUNK_SECONDS and shutil.which("ffmpeg"):
        import tempfile

        TMP_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="sensevoice-", dir=TMP_DIR) as tmp:
            tmpdir = Path(tmp)
            audio_path = tmpdir / "audio.mp3"
            _extract_audio(video_path, audio_path, job_id=job_id, should_cancel=should_cancel)
            heard = float(probe_video(audio_path).get("duration") or duration or 0)
            total = max(1, int(math.ceil(heard / _CHUNK_SECONDS)))
            out: list[dict] = []
            for index in range(total):
                _stop_if_cancelled(should_cancel)
                start = index * _CHUNK_SECONDS
                length = min(_CHUNK_SECONDS, max(0.1, heard - start))
                seg_path = tmpdir / f"seg{index + 1}.mp3"
                _cut_audio_segment(
                    audio_path, start, length, seg_path, job_id=job_id, should_cancel=should_cancel,
                )
                audio = _decode_audio_array(seg_path)
                try:
                    out.extend(transcribe_array(audio, length, start))
                finally:
                    del audio
                    seg_path.unlink(missing_ok=True)
            out.sort(key=lambda item: item["start"])
            log(f"SenseVoice 转写完成：{len(out)} 句")
            return out
    audio = _decode_audio_array(video_path)
    try:
        out = transcribe_array(audio, duration, 0.0)
    finally:
        del audio
    log(f"SenseVoice 转写完成：{len(out)} 句")
    return out


def _transcribe_unlocked(video_path: Path, log=print, *, job_id: str | None = None, should_cancel=None) -> list[dict]:
    engine = asr_engine()
    if ASR_URL:
        model_name = "remote"
        settings = {"language": "zh"}
    elif engine == "whisper":
        model_name = WHISPER_MODEL
        settings = decode_settings(model_name)
    elif engine == "sensevoice":
        model_name = "sensevoice-int8"
        settings = {"engine": "sensevoice", "model": "sense-voice-zh-en-ja-ko-yue-2024-07-17-int8", "language": "zh"}
    else:
        raise AIServiceError(
            f"未知转写引擎「{engine}」。没有改用别的引擎，这次不能当成转写成功。"
        )
    size = video_path.stat().st_size
    cache = _cache_path(video_path, model_name, settings)
    cached = _read_cache(cache, size, model_name, settings)
    if cached is not None:
        _remember_run({"model": model_name, "ok": True, "cache_hit": True})
        log(f"命中转写缓存（模型 {model_name}，{len(cached)} 句）")
        return cached
    try:
        if ASR_URL:
            out = _transcribe_shared(video_path, log=log, job_id=job_id, should_cancel=should_cancel)
            for item in out:
                if isinstance(item, dict):
                    item.setdefault("origin", "asr")
                    item.setdefault("confirmed", False)
        elif engine == "whisper":
            out = _transcribe_local(
                video_path, log=log, job_id=job_id, should_cancel=should_cancel, settings=settings,
            )
        else:
            out = _transcribe_sensevoice(
                video_path, log=log, job_id=job_id, should_cancel=should_cancel,
            )
    except Exception:
        _remember_run({"model": model_name, "ok": False, "cache_hit": False})
        raise
    _remember_run({"model": model_name, "ok": True, "cache_hit": False})
    heard = float(probe_video(video_path).get("duration") or 0)
    if not _cacheable(out, heard):
        log(f"转写时间超出音频 {heard:.1f}s，这次不写入缓存")
    else:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({
                "size": size,
                "model": model_name,
                "decode": settings,
                "segments": out,
            }, ensure_ascii=False))
        except Exception:
            pass
    return out
