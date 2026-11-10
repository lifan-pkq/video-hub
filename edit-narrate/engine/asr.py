"""Transcription: shared ASR, optional faster-whisper, or a labeled demo sidecar."""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import httpx

from engine.errors import EngineError
from engine.media import ffmpeg, probe
from engine.settings import asr_sidecar_enabled, asr_url

CHUNK_SECONDS = 300


def _segments_from_payload(payload: dict, duration: float) -> tuple[list[dict], str]:
    segments = payload.get("segments")
    if isinstance(segments, list) and segments:
        cleaned = []
        for index, item in enumerate(segments, start=1):
            if not isinstance(item, dict):
                continue
            try:
                start = float(item.get("start") or 0)
                end = float(item.get("end") or 0)
            except (TypeError, ValueError):
                continue
            text = str(item.get("text") or "").strip()
            if not text or end <= start:
                continue
            cleaned.append({
                "id": str(item.get("id") or f"t{index}"),
                "start": round(start, 2),
                "end": round(end, 2),
                "text": text,
            })
        if cleaned:
            return cleaned, ""
    text = str(payload.get("text") or "").strip()
    if text:
        return [{
            "id": "t1",
            "start": 0.0,
            "end": round(max(duration, 0.1), 2),
            "text": text,
        }], "识别结果没有时间戳，整段被放在 0 到片长。句间停顿建议不会准确。"
    return [], ""


def _load_sidecar(path: Path, duration: float) -> list[dict] | None:
    sidecar = path.with_suffix(".transcript.json")
    if not sidecar.is_file():
        return None
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineError(
            f"转写旁路文件损坏：{sidecar.name}（{exc}）",
            code="asr_sidecar",
            action="删掉该 json 或改成 {\"segments\":[{\"start\",\"end\",\"text\"}]}。",
            status=422,
        ) from exc
    segments, _warning = _segments_from_payload(payload if isinstance(payload, dict) else {"segments": payload}, duration)
    return segments


def _transcribe_remote(audio: Path, url: str, timeout: float = 900) -> dict:
    try:
        with audio.open("rb") as handle:
            response = httpx.post(
                url,
                files={"file": (audio.name, handle, "audio/mpeg")},
                data={"model": "whisper-1", "language": "zh", "response_format": "verbose_json"},
                timeout=httpx.Timeout(10.0, read=timeout),
            )
    except httpx.HTTPError as exc:
        raise EngineError(
            f"共享 ASR 无响应：{exc}",
            code="asr_http",
            action=f"检查 VIDEO_HUB_ASR_URL={url} 是否就是转写接口，服务是否在 8600。",
            status=502,
        ) from exc
    if response.status_code >= 400:
        raise EngineError(
            f"共享 ASR 返回 HTTP {response.status_code}：{response.text[:240]}",
            code="asr_http",
            action="确认接口接受 multipart 字段 file，并返回 segments 或 text。",
            status=502,
        )
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise EngineError(
            "共享 ASR 返回的不是 JSON",
            code="asr_parse",
            action="接口需要返回 {\"segments\":[{\"start\",\"end\",\"text\"}]} 或 {\"text\"}。",
            status=502,
        ) from exc
    if not isinstance(data, dict):
        raise EngineError("共享 ASR 的 JSON 不是对象", code="asr_parse", status=502)
    return data


def _whisper(audio_wav: Path) -> list[dict]:
    if importlib.util.find_spec("faster_whisper") is None:
        raise EngineError(
            "本机没有 faster-whisper",
            code="asr_unconfigured",
            action="配置 VIDEO_HUB_ASR_URL，或 pip install faster-whisper。演示素材会使用旁路稿。",
            status=409,
        )
    from faster_whisper import WhisperModel

    model = WhisperModel("small", device="cpu", compute_type="int8")
    decoded = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(audio_wav), "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True,
        check=False,
    )
    if decoded.returncode != 0:
        raise EngineError("ffmpeg 解码音频失败", code="ffmpeg", status=500)
    import numpy as np

    samples = np.frombuffer(decoded.stdout, np.int16).astype("float32") / 32768.0
    rows, _info = model.transcribe(samples, language="zh", vad_filter=True)
    segments = []
    for index, row in enumerate(rows, start=1):
        text = (row.text or "").strip()
        if text:
            segments.append({"id": f"t{index}", "start": round(row.start, 2), "end": round(row.end, 2), "text": text})
    return segments


def transcribe(path: Path, *, source_platform: str = "", job_id: str | None = None, should_cancel=None) -> dict:
    meta = probe(path)
    duration = float(meta["duration"])
    url = asr_url()
    if url:
        with tempfile.TemporaryDirectory(prefix="vh-asr-") as tmp:
            audio = Path(tmp) / "audio.mp3"
            ffmpeg(
                ["-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(audio)],
                timeout=1800,
                job_id=job_id,
                should_cancel=should_cancel,
            )
            total = max(1, int(math.ceil(duration / CHUNK_SECONDS)))
            merged: list[dict] = []
            warning = ""
            for index in range(total):
                start = index * CHUNK_SECONDS
                length = min(CHUNK_SECONDS, max(0.1, duration - start))
                piece = Path(tmp) / f"seg{index}.mp3"
                ffmpeg(
                    ["-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(audio), "-ac", "1", "-ar", "16000", "-b:a", "64k", str(piece)],
                    timeout=600,
                    job_id=job_id,
                    should_cancel=should_cancel,
                )
                payload = _transcribe_remote(piece, url)
                segments, warn = _segments_from_payload(payload, length)
                warning = warning or warn
                for seg in segments:
                    merged.append({
                        **seg,
                        "id": f"t{len(merged) + 1}",
                        "start": round(seg["start"] + start, 2),
                        "end": round(seg["end"] + start, 2),
                    })
            if not merged:
                raise EngineError(
                    "共享 ASR 返回空文本",
                    code="asr_empty",
                    action="确认音轨里有人声。也可以在解说页手动粘贴转写。",
                    status=422,
                )
            return {"source": "asr", "warning": warning, "segments": merged}

    if importlib.util.find_spec("faster_whisper") is not None and shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory(prefix="vh-whisper-") as tmp:
            wav = Path(tmp) / "audio.wav"
            ffmpeg(["-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)], timeout=1800, job_id=job_id, should_cancel=should_cancel)
            segments = _whisper(wav)
        if not segments:
            raise EngineError("本地转写结果是空的", code="asr_empty", action="检查音轨，或改用共享 ASR。", status=422)
        return {"source": "whisper", "warning": "", "segments": segments}

    allow_sidecar = source_platform == "demo" or asr_sidecar_enabled()
    if allow_sidecar:
        sidecar = _load_sidecar(path, duration)
        if sidecar:
            return {
                "source": "sidecar",
                "warning": "这条转写来自素材旁边的稿子，不是实时识别。配置 VIDEO_HUB_ASR_URL 或安装 faster-whisper 后会改为真实转写。",
                "segments": sidecar,
            }

    raise EngineError(
        "没有可用的转写",
        code="asr_unconfigured",
        action="设置 VIDEO_HUB_ASR_URL（指向已有的 /v1/audio/transcriptions 兼容接口），或安装 faster-whisper。剪辑页的静音、黑场和镜头建议不依赖转写。",
        status=409,
    )
