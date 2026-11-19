"""本机 SenseVoice 转写。这不是 Whisper，加载失败时不会改用 Whisper。"""
from __future__ import annotations

import os
import tarfile
import urllib.request
from pathlib import Path

from app.config import DATA_DIR
from app.services.ai_edit import AIServiceError

MODEL_ID = "sense-voice-zh-en-ja-ko-yue-2024-07-17"
RELEASE_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    f"sherpa-onnx-{MODEL_ID}.tar.bz2"
)
_GAP_SECONDS = 0.45
_PUNCT = set("，。！？；、,.;!?")
_SENSE_RECOGNIZER = None


def model_dir() -> Path:
    custom = os.environ.get("VIDEO_HUB_SENSEVOICE_DIR", "").strip()
    if custom:
        return Path(custom).expanduser()
    return DATA_DIR / "models" / MODEL_ID


def segments_from_tokens(tokens, timestamps, duration: float, offset: float = 0.0) -> list[dict]:
    """按停顿和标点切开。不删字，也不另写一版句子。"""
    groups: list[tuple[list[str], float, float]] = []
    chars: list[str] = []
    start = None
    end = None
    previous = None
    for token, stamp in zip(tokens, timestamps):
        piece = str(token or "")
        if not piece or piece.startswith("<|"):
            continue
        try:
            at = float(stamp) + offset
        except (TypeError, ValueError):
            continue
        if chars and previous is not None and piece not in _PUNCT and at - previous > _GAP_SECONDS:
            groups.append((chars, float(start), float(end)))
            chars = []
            start = None
        chars.append(piece)
        start = at if start is None else start
        end = at
        previous = at
        if piece in _PUNCT:
            groups.append((chars, float(start), float(end)))
            chars = []
            start = None
            previous = None
    if chars and start is not None and end is not None:
        groups.append((chars, float(start), float(end)))

    cues = []
    limit = float(duration) + offset if duration and duration > 0 else None
    for raw, cue_start, cue_end in groups:
        text = "".join(raw).strip()
        if not text:
            continue
        if all(char in _PUNCT or char.isspace() for char in text):
            if cues:
                cues[-1]["text"] += text
                cues[-1]["end"] = round(max(cues[-1]["end"], cue_end), 2)
            continue
        cue_end = max(cue_end, cue_start + 0.2)
        if limit is not None:
            cue_end = min(cue_end, limit)
        if cue_end <= cue_start:
            continue
        cues.append({
            "start": round(cue_start, 2),
            "end": round(cue_end, 2),
            "text": text,
            "origin": "asr",
            "confirmed": False,
            "review_state": "unverified",
            "low_confidence": False,
        })
    if cues and cues[0]["start"] - offset <= 0.5:
        cues[0]["start"] = round(offset, 2)
    if cues and limit is not None and limit - cues[-1]["end"] <= 0.5:
        cues[-1]["end"] = round(limit, 2)
    return cues


def _ensure_files() -> tuple[Path, Path]:
    folder = model_dir()
    model = folder / "model.int8.onnx"
    tokens = folder / "tokens.txt"
    if model.is_file() and tokens.is_file():
        return model, tokens
    custom = os.environ.get("VIDEO_HUB_SENSEVOICE_DIR", "").strip()
    if custom:
        raise AIServiceError(
            f"SenseVoice 目录「{folder}」里没有 model.int8.onnx 和 tokens.txt。"
            "没有改用 Whisper，这次不能当成转写成功。"
        )
    folder.mkdir(parents=True, exist_ok=True)
    archive = folder / "model.tar.bz2"
    try:
        urllib.request.urlretrieve(RELEASE_URL, archive)
        with tarfile.open(archive) as tar:
            wanted = {"model.int8.onnx": model, "tokens.txt": tokens}
            for member in tar.getmembers():
                name = Path(member.name).name
                target = wanted.get(name)
                if target is None or not member.isfile():
                    continue
                source = tar.extractfile(member)
                if source is None:
                    continue
                target.write_bytes(source.read())
    except AIServiceError:
        raise
    except Exception as exc:
        raise AIServiceError(
            f"下载 SenseVoice int8 失败：{exc}。"
            "没有改用 Whisper，这次不能当成转写成功。"
        ) from exc
    finally:
        archive.unlink(missing_ok=True)
    if not model.is_file() or not tokens.is_file():
        raise AIServiceError(
            "SenseVoice 压缩包里没有 int8 模型。"
            "没有改用 Whisper，这次不能当成转写成功。"
        )
    return model, tokens


def _recognizer():
    global _SENSE_RECOGNIZER
    if _SENSE_RECOGNIZER is None:
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise AIServiceError(
                "本机没有 sherpa-onnx，无法使用 SenseVoice。"
                "没有改用 Whisper，这次不能当成转写成功。"
            ) from exc
        model, tokens = _ensure_files()
        try:
            _SENSE_RECOGNIZER = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=str(model),
                tokens=str(tokens),
                num_threads=2,
                sample_rate=16000,
                use_itn=True,
                language="zh",
            )
        except Exception as exc:
            _SENSE_RECOGNIZER = None
            raise AIServiceError(
                f"加载 SenseVoice int8 失败：{exc}。"
                "没有改用 Whisper，这次不能当成转写成功。"
            ) from exc
    return _SENSE_RECOGNIZER


def transcribe_array(audio, duration: float, offset: float = 0.0) -> list[dict]:
    recognizer = _recognizer()
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, audio)
    recognizer.decode_stream(stream)
    result = stream.result
    return segments_from_tokens(result.tokens, result.timestamps, duration, offset)
