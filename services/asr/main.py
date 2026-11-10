"""Local ASR service for NarratoAI, powered by faster-whisper.

Exposes an OpenAI-compatible /v1/audio/transcriptions endpoint so
NarratoAI's fun_asr "local" backend can use it directly.
"""

import gc
import os
import subprocess
import tempfile
import threading
import time

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from faster_whisper import WhisperModel
from starlette.concurrency import run_in_threadpool

MODEL_SIZE = os.environ.get("ASR_MODEL", "small")
MODEL_IDLE_SECONDS = max(60, int(os.environ.get("ASR_IDLE_UNLOAD_SECONDS", "300") or 300))
ALLOWED_FORMATS = {"json", "verbose_json", "srt", "vtt"}

app = FastAPI(title="NarratoAI Local ASR", version="1.0.0")
_model: WhisperModel | None = None
_model_error: str | None = None
_model_lock = threading.RLock()
_model_last_used = 0.0


def _load_model() -> WhisperModel:
    global _model, _model_error, _model_last_used
    with _model_lock:
        if _model is None:
            try:
                _model = WhisperModel(
                    MODEL_SIZE,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=int(os.environ.get("ASR_CPU_THREADS", "2")),
                )
                _model_error = None
            except Exception as exc:  # noqa: BLE001
                _model_error = str(exc)
                raise HTTPException(status_code=503, detail=f"ASR model unavailable: {exc}") from exc
        _model_last_used = time.time()
        return _model


def _idle_unload_loop() -> None:
    """空闲一段时间后释放 Whisper 模型，避免常驻占用 1GB+ 内存。"""
    global _model
    while True:
        time.sleep(30)
        with _model_lock:
            if _model is not None and time.time() - _model_last_used > MODEL_IDLE_SECONDS:
                _model = None
                gc.collect()


@app.on_event("startup")
def _start_idle_unload() -> None:
    threading.Thread(target=_idle_unload_loop, daemon=True).start()


def _to_srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _save_upload(upload: UploadFile) -> str:
    suffix = os.path.splitext(upload.filename or "audio.mp4")[1] or ".wav"
    fd, path = tempfile.mkstemp(prefix="narrato_asr_", suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(upload.file.read())
    return path


def _decode_audio(path: str) -> np.ndarray:
    """Decode any ffmpeg-readable input to 16kHz mono float32 via ffmpeg CLI."""
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", path,
        "-f", "s16le", "-ac", "1", "-acodec", "pcm_s16le", "-ar", "16000", "-",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=f"ffmpeg decode failed: {result.stderr.decode(errors='ignore')[:300]}")
    return np.frombuffer(result.stdout, np.int16).astype(np.float32) / 32768.0


def _transcribe(path: str, language: str | None):
    global _model_last_used
    with _model_lock:
        model = _load_model()
        audio = _decode_audio(path)
        segments_iter, info = model.transcribe(
            audio,
            language=language or None,
            vad_filter=True,
            beam_size=int(os.environ.get("ASR_BEAM_SIZE", "3")),
        )
        segments = [
            {
                "id": idx,
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": seg.text.strip(),
            }
            for idx, seg in enumerate(segments_iter)
        ]
        _model_last_used = time.time()
        return segments, info


@app.get("/health")
def health():
    if _model_error is not None:
        raise HTTPException(status_code=503, detail=_model_error)
    return {
        "status": "ok" if _model is not None else "model-not-loaded",
        "service": "narrato-asr",
        "model": MODEL_SIZE,
        "model_error": _model_error,
        "idle_unload_seconds": MODEL_IDLE_SECONDS,
    }


@app.get("/")
def index():
    return {
        "service": "NarratoAI Local ASR",
        "model": MODEL_SIZE,
        "endpoints": ["/health", "/v1/audio/transcriptions", "/asr"],
    }


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form(None),
    language: str = Form(None),
    response_format: str = Form("json"),
    prompt: str = Form(None),
    temperature: float = Form(None),
):
    fmt = (response_format or "json").strip().lower()
    if fmt not in ALLOWED_FORMATS:
        fmt = "json"
    path = _save_upload(file)
    try:
        # 同步转写放到线程池，避免占满 uvicorn 事件循环导致 /health 与后续请求全部超时
        segments, info = await run_in_threadpool(_transcribe, path, language)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    if fmt == "srt":
        body = "\n".join(
            f"{seg['id'] + 1}\n{_to_srt_time(seg['start'])} --> {_to_srt_time(seg['end'])}\n{seg['text']}"
            for seg in segments
        )
        return PlainTextResponse(body + "\n" if body else "")

    text = "".join(seg["text"] for seg in segments) if (info.language or "").startswith("zh") else " ".join(
        seg["text"] for seg in segments
    )
    if fmt == "verbose_json":
        return {
            "text": text,
            "language": info.language,
            "duration": round(float(info.duration), 3),
            "segments": segments,
        }
    return {"text": text}


@app.post("/asr")
async def asr_rest(
    file: UploadFile = File(...),
    language: str = Form(None),
    hotword: str = Form(None),
    enable_spk: str = Form(None),
):
    """FunASR-Pack style fallback endpoint."""
    path = _save_upload(file)
    try:
        segments, info = await run_in_threadpool(_transcribe, path, language)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    text = "".join(seg["text"] for seg in segments)
    return {
        "text": text,
        "language": info.language,
        "sentences": [
            {
                "text": seg["text"],
                "begin_time": int(seg["start"] * 1000),
                "end_time": int(seg["end"] * 1000),
            }
            for seg in segments
        ],
    }
