"""生成最长 10 秒的真实 TTS 试听，复用正式配音的情绪参数。"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path
from typing import Any

from app.services import voice


def create_preview(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text") or "").strip()[:500]
    if not text:
        raise ValueError("试听文本不能为空")
    engine = str(payload.get("tts_engine") or "edge_tts")
    voice_name = str(payload.get("voice_name") or "zh-CN-XiaoxiaoNeural")
    rate = max(0.5, min(2.0, float(payload.get("voice_rate") or 1.0)))
    pitch = max(0.5, min(2.0, float(payload.get("voice_pitch") or 1.0)))
    emotion = str(payload.get("emotion") or "平静")
    rate, pitch = voice.emotion_settings(emotion, rate, pitch)
    directory = root / "storage" / "previews"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"voice-{uuid.uuid4().hex[:12]}.mp3"
    target = directory / name
    result = voice.tts(text, voice_name, rate, pitch, str(target), engine)
    if result is None or not target.is_file() or target.stat().st_size <= 0:
        raise RuntimeError("TTS 试听生成失败，请检查配音引擎和网络")
    trimmed = target.with_name(target.stem + "-10s.mp3")
    process = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(target), "-t", "10",
         "-af", "afftdn,deesser,loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "libmp3lame", str(trimmed)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if process.returncode == 0 and trimmed.is_file() and trimmed.stat().st_size > 0:
        target.unlink(missing_ok=True)
        target = trimmed
        name = trimmed.name
    return {
        "name": name,
        "text": text,
        "voice_name": voice_name,
        "voice_rate": rate,
        "voice_pitch": pitch,
        "emotion": emotion,
        "engine": engine,
        "native_emotion": engine != "edge_tts",
        "size": target.stat().st_size,
    }
