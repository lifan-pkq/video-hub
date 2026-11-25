"""把识别时间戳收到这句话真正说完的位置。

SenseVoice 的词时间通常落在字的中前部，渲染若停在这个时间，末字的衰减会被切掉。
文件或容器的结束只是参考，后面的空白不能当成还在说话。
"""
from __future__ import annotations

import array
import subprocess
from pathlib import Path

_RATE = 16000
_FRAME = 320  # 20ms
# 人声落下之后再留一段静音，避免把低于固定门限的弱尾音切掉。
# 这段静音不能跨过下一句的起点，也不能靠它把成片补到文件末尾。
_QUIET_CONTEXT = 0.25
_LOUD_CAP = 40.0


def apply_acoustic_tails(media: Path, segments: list[dict], duration: float) -> list[dict]:
    """只在有上限的选段前调用。失败要抛出，不能悄悄留着被切掉的句尾。"""
    duration = float(duration or 0)
    if duration <= 0 or not segments:
        return segments
    ordered = sorted(segments, key=lambda item: float(item.get("start") or 0))
    if duration <= 180:
        pcm, offset = _decode(media, 0.0, duration)
        windows = [(pcm, offset)]
    else:
        windows = None
    updated = []
    for index, segment in enumerate(ordered):
        start = float(segment.get("start") or 0)
        asr_end = float(segment.get("end") or start)
        next_start = duration
        if index + 1 < len(ordered):
            next_start = float(ordered[index + 1].get("start") or duration)
        hard_stop = min(duration, max(asr_end, next_start))
        if windows is None:
            left = max(0.0, asr_end - 0.2)
            pcm, offset = _decode(media, left, max(0.05, hard_stop - left))
        else:
            pcm, offset = windows[0]
        end = _acoustic_end(pcm, offset, asr_end, hard_stop)
        copied = dict(segment)
        copied["end"] = round(end, 2)
        updated.append(copied)
    return updated


def _acoustic_end(pcm: array.array, offset: float, asr_end: float, hard_stop: float) -> float:
    frames = _frames(pcm, offset)
    if not frames:
        return min(asr_end, hard_stop)
    levels = sorted(level for _, level in frames)
    floor = levels[max(0, len(levels) // 10)]
    # 门限跟着这段里的静音底走，并封顶，避免整段都是人声时把门限抬高。
    threshold = min(_LOUD_CAP, max(18.0, floor * 2.5))
    last = None
    silent_for = 0.0
    next_voice = None
    for stamp, level in frames:
        frame_end = stamp + 0.02
        if frame_end < asr_end - 0.08:
            continue
        if frame_end > hard_stop + 1e-6:
            break
        if level >= threshold:
            if last is not None and silent_for >= 0.08:
                next_voice = stamp
                break
            last = frame_end
            silent_for = 0.0
        elif last is not None:
            silent_for += 0.02
    if last is None:
        return min(asr_end, hard_stop)
    # 把切点放进后面的静音，而不是停在门限刚落到的那一帧。下一句一开始就停。
    end = last + _QUIET_CONTEXT
    if next_voice is not None:
        end = min(end, next_voice)
    return min(hard_stop, end)


def _frames(pcm: array.array, offset: float) -> list[tuple[float, float]]:
    frames = []
    step = _FRAME
    for index in range(0, max(0, len(pcm) - step + 1), step):
        chunk = pcm[index:index + step]
        energy = sum(sample * sample for sample in chunk) / len(chunk)
        frames.append((offset + index / _RATE, energy ** 0.5))
    return frames


def _decode(media: Path, start: float, length: float) -> tuple[array.array, float]:
    if length <= 0:
        return array.array("h"), start
    completed = subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-ss", f"{max(0.0, start):.3f}",
            "-t", f"{length:.3f}",
            "-i", str(media),
            "-ac", "1", "-ar", str(_RATE),
            "-f", "s16le", "-",
        ],
        capture_output=True,
        timeout=60,
    )
    if completed.returncode != 0:
        from app.services.ai_edit import AIServiceError

        detail = completed.stderr.decode(errors="ignore")[-200:]
        raise AIServiceError(f"无法测量句尾声音：{detail}")
    raw = completed.stdout[: len(completed.stdout) // 2 * 2]
    pcm = array.array("h")
    pcm.frombytes(raw)
    return pcm, max(0.0, start)
