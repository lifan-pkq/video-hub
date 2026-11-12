"""本机或远端转写。和选题、废片分析分开，避免把 whisper 生命周期揉进剪辑提示词。"""
from __future__ import annotations

import json
import math
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
                    "确认模型名是 tiny、base 或 small，磁盘还有空间，并且首次下载能访问模型仓库。"
                ) from exc
        return _model


def _cache_path(video_path: Path) -> Path:
    return TMP_DIR / "transcripts" / f"{video_path.stem}.json"


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


def _transcribe_local_chunked(
    video_path: Path, model, duration: float, log, *, job_id: str | None = None, should_cancel=None,
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


def _transcribe_local(video_path: Path, log=print, *, job_id: str | None = None, should_cancel=None) -> list[dict]:
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
        return _transcribe_local_chunked(
            video_path, model, duration, log, job_id=job_id, should_cancel=should_cancel,
        )
    audio = _decode_audio_array(video_path)
    try:
        out, info = _run_whisper(model, audio, 0.0)
    finally:
        del audio
    log(f"转写完成：{len(out)} 句（检测语言 {info.language}，时长 {info.duration:.0f}s）")
    return out


def transcribe(video_path: Path, log=print, *, job_id: str | None = None, should_cancel=None) -> list[dict]:
    """语音转写，返回 [{start, end, text}]（秒）。结果按 文件名+大小 缓存。"""
    _stop_if_cancelled(should_cancel)
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
        out = _transcribe_shared(video_path, log=log, job_id=job_id, should_cancel=should_cancel)
    else:
        out = _transcribe_local(video_path, log=log, job_id=job_id, should_cancel=should_cancel)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"size": video_path.stat().st_size, "segments": out}, ensure_ascii=False))
    except Exception:
        pass
    return out
