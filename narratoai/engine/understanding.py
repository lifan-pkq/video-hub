"""把口播、画面和声音整理成供剧本、剪辑与字幕共用的理解时间线。"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
from array import array
from pathlib import Path
from typing import Any

from engine.spec import format_clock

TIME_RE = re.compile(
    r"(\d{2}:\d{2}:\d{2},\d{3})\s*(?:-->|[-~—])\s*(\d{2}:\d{2}:\d{2},\d{3})"
)


def _seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest.replace(",", "."))


def parse_markdown(markdown: str, kind: str) -> list[dict[str, Any]]:
    """从现有 ASR/画面 Markdown 中提取带绝对时间的记录。"""
    records: list[dict[str, Any]] = []
    current_section = ""
    for raw_line in str(markdown or "").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            current_section = line.lstrip("#").strip()
            continue
        match = TIME_RE.search(line)
        if not match:
            continue
        try:
            start = _seconds(match.group(1))
            end = _seconds(match.group(2))
        except (ValueError, TypeError):
            continue
        if end <= start:
            continue
        text = line[match.end():].lstrip(" ：:>-").strip()
        if not text:
            continue
        records.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "kind": kind,
            "text": text[:1000],
            "section": current_section[:200],
        })
    return records


def _silence_and_loudness(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "silencedetect=noise=-35dB:d=0.6,ebur128=peak=true",
        "-vn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError):
        return [], {"status": "unknown"}
    silence: list[dict[str, Any]] = []
    pending: float | None = None
    loudness: dict[str, Any] = {"status": "ok"}
    for line in (result.stderr or "").splitlines():
        start = re.search(r"silence_start:\s*([-0-9.]+)", line)
        if start:
            pending = max(0.0, float(start.group(1)))
        end = re.search(r"silence_end:\s*([-0-9.]+)", line)
        if end and pending is not None:
            value = float(end.group(1))
            if value > pending:
                silence.append({"start": round(pending, 3), "end": round(value, 3), "kind": "silence"})
            pending = None
        integrated = re.search(r"I:\s*([-0-9.]+)\s*LUFS", line)
        if integrated:
            loudness["integrated_lufs"] = float(integrated.group(1))
        true_peak = re.search(r"Peak:\s*([-0-9.]+)\s*dBFS", line)
        if true_peak:
            loudness["true_peak_dbfs"] = float(true_peak.group(1))
    if result.returncode != 0:
        loudness = {"status": "unknown"}
    return silence, loudness


def energy_beats(samples: array, sample_rate: int = 8000) -> list[float]:
    """用低频能量突变估计节拍候选；结果只作吸附参考，不冒充精确 BPM。"""
    if not samples or sample_rate <= 0:
        return []
    window = max(1, int(sample_rate * 0.12))
    energies: list[float] = []
    for offset in range(0, len(samples), window):
        chunk = samples[offset:offset + window]
        if not chunk:
            continue
        energies.append(sum(abs(value) for value in chunk) / len(chunk))
    if len(energies) < 3:
        return []
    baseline = max(1.0, statistics.median(energies))
    if max(energies) < 100:
        return []
    beats: list[float] = []
    previous = -1.0
    for index in range(1, len(energies) - 1):
        value = energies[index]
        if value < max(baseline * 1.7, energies[index - 1] * 1.35):
            continue
        if value < energies[index + 1]:
            continue
        timestamp = index * window / sample_rate
        if timestamp - previous >= 0.28:
            beats.append(round(timestamp, 3))
            previous = timestamp
    return beats


def _beats(path: Path) -> list[float]:
    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
        "-af", "highpass=f=60,lowpass=f=180", "-ac", "1", "-ar", "8000",
        "-f", "s16le", "-acodec", "pcm_s16le", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    samples = array("h")
    samples.frombytes(result.stdout)
    return energy_beats(samples)


def build_understanding(
    video_path: str | Path,
    transcript_markdown: str = "",
    visual_markdown: str = "",
    asr_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    speech = parse_markdown(transcript_markdown, "speech")
    visual = parse_markdown(visual_markdown, "visual")
    raw_segments = (asr_payload or {}).get("segments") or []
    for index, segment in enumerate(raw_segments):
        if index >= len(speech) or not isinstance(segment, dict):
            break
        speaker = str(segment.get("speaker") or segment.get("speaker_id") or "").strip()
        speech[index]["speaker"] = speaker or "speaker_unknown"
        if isinstance(segment.get("words"), list):
            speech[index]["words"] = segment["words"]
    silence, loudness = _silence_and_loudness(Path(video_path))
    beats = _beats(Path(video_path))
    audio: list[dict[str, Any]] = list(silence)
    audio.extend({"start": value, "end": round(value + 0.12, 3), "kind": "beat"} for value in beats)
    duration = max(
        [item["end"] for item in speech + visual + audio] or [0.0]
    )
    return {
        "schema_version": 1,
        "duration": round(duration, 3),
        "tracks": {
            "speech": speech,
            "visual": visual,
            "audio": sorted(audio, key=lambda item: (item["start"], item["kind"])),
        },
        "audio_summary": {
            **loudness,
            "beat_detection": "energy_onset_candidates",
            "music_segments": [],
            "events": [],
            "event_status": "unknown",
        },
        "structure": [],
        "emotion_curve": [],
        "warnings": [
            "说话人分离依赖 ASR 返回 speaker；speaker_unknown 不代表单人口播。"
        ] if any(item.get("speaker") == "speaker_unknown" for item in speech) else [],
    }


def timeline_markdown(understanding: dict[str, Any]) -> str:
    lines = ["# 融合理解时间线", "", "口播、画面与声音使用同一条原片绝对时间轴。"]
    labels = {"speech": "口播", "visual": "画面", "audio": "声音"}
    for kind in ("speech", "visual", "audio"):
        records = understanding.get("tracks", {}).get(kind) or []
        lines.extend(["", f"## {labels[kind]}（{len(records)} 条）"])
        for item in records:
            start = format_clock(float(item["start"]))
            end = format_clock(float(item["end"]))
            speaker = f" [{item['speaker']}]" if item.get("speaker") else ""
            text = item.get("text") or item["kind"]
            lines.append(f"- {start}-{end}{speaker}：{text}")
    summary = understanding.get("audio_summary") or {}
    if summary.get("integrated_lufs") is not None:
        lines.extend(["", f"- 全片响度：{summary['integrated_lufs']} LUFS"])
    return "\n".join(lines) + "\n"


def write_understanding(task_dir: Path, understanding: dict[str, Any]) -> str:
    analysis = task_dir / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "understanding.json").write_text(
        json.dumps(understanding, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = timeline_markdown(understanding)
    (analysis / "timeline.md").write_text(markdown, encoding="utf-8")
    return markdown
