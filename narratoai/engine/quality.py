"""成片质量门：严重问题需人工复核，未执行的检查明确标 unknown。"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from engine.spec import parse_timestamp_range

# 黑场只有持续超过 2 秒才是断片级事故；短黑场多为转场、淡入淡出或夜景。
BLACK_ERROR_SECONDS = 2.0
# 母带峰值常落在 -1.5 dBFS 附近，只有逼近满幅才可能是削波。
PEAK_ERROR_DBFS = -0.1
FREEZE_WARNING_SECONDS = 4.0
MIN_SUBTITLE_SECONDS = 0.7
SILENCE_MIN_SECONDS = 3.0
# 静态章节、幻灯片式成片本来就长时间静止，冻结告警对这类成片是噪音。
STATIC_CONTENT_TYPES = {"knowledge", "tutorial", "product"}
SUBTITLE_TIMELINE_NAME = "script_subtitles.srt"


def _issue(check: str, severity: str, message: str, **details: Any) -> dict[str, Any]:
    return {"check": check, "severity": severity, "message": message, "details": details}


def _seconds(match: re.Match[str] | None) -> float | None:
    return float(match.group(1)) if match else None


def _render_duration(path: Path) -> float:
    """探测成片实际时长；拿不到返回 0（此时不按时长判定）。"""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if result.returncode != 0:
        return 0.0
    try:
        return max(0.0, float((result.stdout or "").strip() or 0))
    except ValueError:
        return 0.0


def _audio_report(path: Path, duration: float) -> list[dict[str, Any]]:
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-af", "silencedetect=noise=-38dB:d=2,ebur128=peak=true", "-vn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return [_issue("audio", "unknown", f"声音检查未执行：{exc}")]
    if result.returncode != 0:
        return [_issue("audio", "unknown", "FFmpeg 无法完成声音检查")]
    issues: list[dict[str, Any]] = []
    longest = 0.0
    pending: float | None = None
    for line in (result.stderr or "").splitlines():
        start = re.search(r"silence_start:\s*([0-9.]+)", line)
        end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if start:
            pending = float(start.group(1))
        if end and pending is not None:
            longest = max(longest, float(end.group(1)) - pending)
            pending = None
        integrated = re.search(r"I:\s*([-0-9.]+)\s*LUFS", line)
        if integrated:
            loudness = float(integrated.group(1))
            if loudness < -24 or loudness > -10:
                issues.append(_issue("audio", "warning", f"成片响度偏离目标：{loudness} LUFS", integrated_lufs=loudness))
        peak = re.search(r"Peak:\s*([-0-9.]+)\s*dBFS", line)
        if peak and float(peak.group(1)) > PEAK_ERROR_DBFS:
            issues.append(_issue("audio", "error", f"音频峰值疑似削波：{peak.group(1)} dBFS", peak_dbfs=float(peak.group(1))))
    # 片尾静音没有 silence_end，用成片总时长把最后一段未闭合的静音补上。
    if pending is not None and duration and duration > pending:
        longest = max(longest, duration - pending)
    if duration and longest > max(SILENCE_MIN_SECONDS, duration * 0.25):
        issues.append(_issue("audio", "error", f"检测到过长静音：{longest:.1f} 秒", longest_silence=round(longest, 3)))
    return issues or [_issue("audio", "ok", "未发现长静音或明显响度异常")]


def _video_report(path: Path, duration: float = 0.0, content_type: str = "auto") -> list[dict[str, Any]]:
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
        "-vf", "blackdetect=d=1:pix_th=0.03,freezedetect=n=0.003:d=4",
        "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=900, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return [_issue("visual", "unknown", f"画面检查未执行：{exc}")]
    issues: list[dict[str, Any]] = []
    if result.returncode != 0:
        issues.append(_issue("visual", "error", "成片解码失败", detail=(result.stderr or "")[-300:]))
    longest_black = 0.0
    black_unknown = ""
    longest_freeze = 0.0
    freeze_start: float | None = None
    for line in (result.stderr or "").splitlines():
        black_start = re.search(r"black_start:\s*([0-9.]+)", line)
        black_end = re.search(r"black_end:\s*([0-9.]+)", line)
        black_duration = _seconds(re.search(r"black_duration:\s*([0-9.]+)", line))
        if black_start or black_duration is not None:
            length = black_duration
            if length is None and black_start and black_end:
                length = float(black_end.group(1)) - float(black_start.group(1))
            if length is None:
                black_unknown = line[-300:]
            else:
                longest_black = max(longest_black, length)
        start = re.search(r"freeze_start:\s*(-?[0-9.]+)", line)
        if start:
            freeze_start = float(start.group(1))
        freeze_duration = _seconds(re.search(r"freeze_duration:\s*([0-9.]+)", line))
        if freeze_duration is not None and freeze_start is not None:
            longest_freeze = max(longest_freeze, freeze_duration)
            freeze_start = None
        freeze_end = re.search(r"freeze_end:\s*(-?[0-9.]+)", line)
        if freeze_end and freeze_start is not None:
            longest_freeze = max(longest_freeze, float(freeze_end.group(1)) - freeze_start)
            freeze_start = None
    if freeze_start is not None and duration > freeze_start:
        # 冻结一直持续到片尾时 freezedetect 不会给出 freeze_end，用成片时长闭合。
        longest_freeze = max(longest_freeze, duration - freeze_start)
    if longest_black > BLACK_ERROR_SECONDS:
        issues.append(_issue("visual", "error", f"检测到持续黑帧：{longest_black:.1f} 秒", black_seconds=round(longest_black, 3)))
    elif longest_black > 0:
        issues.append(_issue("visual", "warning", f"检测到短黑场（转场/淡入淡出）：{longest_black:.1f} 秒", black_seconds=round(longest_black, 3)))
    if black_unknown:
        issues.append(_issue("visual", "unknown", "黑帧时长无法解析，未按时长判定", raw=black_unknown))
    if longest_freeze > FREEZE_WARNING_SECONDS:
        if content_type in STATIC_CONTENT_TYPES:
            issues.append(_issue("visual", "ok", f"静态成片（{content_type}）存在 {longest_freeze:.1f} 秒静止画面，按静态章节放宽"))
        else:
            issues.append(_issue("visual", "warning", f"检测到长时间冻结画面：{longest_freeze:.1f} 秒", freeze_seconds=round(longest_freeze, 3)))
    return issues or [_issue("visual", "ok", "未发现黑帧、冻结或解码错误")]


def _subtitle_cues(task_dir: Path) -> list[tuple[float, float, str]]:
    """读取真正被烧进成片的字幕时间轴，用于按真实显示时长判定。"""
    path = task_dir / SUBTITLE_TIMELINE_NAME
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 3:
            continue
        timing = re.match(
            r"(\d{1,2}:\d{2}:\d{2}[,.][0-9]{3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.][0-9]{3})",
            lines[1],
        )
        if not timing:
            continue
        try:
            start, end = parse_timestamp_range(f"{timing.group(1)}-{timing.group(2)}")
        except Exception:  # noqa: BLE001
            continue
        cues.append((start, end, " ".join(lines[2:])))
    return cues


def evaluate(
    task_dir: Path,
    spec: dict[str, Any],
    script: list[dict[str, Any]],
    output: Path,
    output_duration: float = 0.0,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    # 原片时长只用于判断剧本时间戳是否越界；成片时长用于静音阈值与字幕时长。
    source_duration = float(spec.get("video_duration") or 0)
    render_duration = float(output_duration or 0) or _render_duration(output)
    normalized_texts: set[str] = set()
    for item in script:
        segment = str(item.get("segment_id") or item.get("_id") or "")
        try:
            start, end = parse_timestamp_range(str(item.get("timestamp") or ""))
            if start < 0 or (source_duration and end > source_duration + 0.5):
                issues.append(_issue("alignment", "error", f"{segment} 的时间区间超出原片"))
            picture_duration = end - start
            estimated = float(item.get("estimated_duration") or 0)
            if estimated and estimated > picture_duration * 1.8 + 0.5:
                issues.append(_issue("alignment", "error", f"{segment} 的口播明显长于画面", estimated=estimated, picture=picture_duration))
            for evidence in item.get("evidence") or []:
                if not isinstance(evidence, dict):
                    continue
                evidence_start = float(evidence.get("start", start))
                evidence_end = float(evidence.get("end", end))
                if evidence_start < start - 0.5 or evidence_end > end + 0.5:
                    issues.append(_issue("alignment", "warning", f"{segment} 的事实依据不在所选镜头内"))
        except (TypeError, ValueError):
            issues.append(_issue("alignment", "error", f"{segment} 的时间戳无效"))
        flags = [str(value) for value in item.get("quality_flags") or []]
        if flags:
            issues.append(_issue("structure", "error", f"{segment} 的剧本批判仍未通过", flags=flags))
        narration = str(item.get("narration") or "").strip()
        normalized = re.sub(r"[\W_]+", "", narration)
        if normalized and normalized in normalized_texts:
            issues.append(_issue("structure", "error", f"{segment} 与前段文案重复"))
        normalized_texts.add(normalized)
        max_chars = int(spec.get("subtitle_max_chars") or 16)
        for line in narration.splitlines() or [narration]:
            if len(line) > max_chars * 3:
                issues.append(_issue("subtitle", "error", f"{segment} 字幕过长且未合理拆行", chars=len(line)))
    cues = _subtitle_cues(task_dir)
    if not cues:
        issues.append(_issue("subtitle", "unknown", f"字幕停留未检查：找不到真实字幕时间轴 {SUBTITLE_TIMELINE_NAME}"))
    else:
        shortest = min(cues, key=lambda cue: cue[1] - cue[0])
        shortest_seconds = shortest[1] - shortest[0]
        if shortest_seconds < MIN_SUBTITLE_SECONDS:
            issues.append(_issue(
                "subtitle", "warning",
                f"字幕停留可能过短：{shortest_seconds:.2f} 秒（{shortest[2][:20]}）",
                shortest_seconds=round(shortest_seconds, 3),
            ))
    if script and script[0].get("story_role") != "hook":
        issues.append(_issue("structure", "warning", "第一段未标记为钩子，请确认开头 3 秒的吸引力"))
    if script and script[-1].get("story_role") not in {"ending", "climax"}:
        issues.append(_issue("structure", "warning", "最后一段未标记为收尾，请检查是否突然结束"))
    issues.extend(_audio_report(output, render_duration))
    issues.extend(_video_report(output, render_duration, str(spec.get("content_type") or "auto")))
    errors = [item for item in issues if item["severity"] == "error"]
    warnings = [item for item in issues if item["severity"] == "warning"]
    unknown = [item for item in issues if item["severity"] == "unknown"]
    score = max(0, 100 - len(errors) * 25 - len(warnings) * 5)
    return {
        "schema_version": 1,
        "passed": not errors,
        "status": "review" if errors else ("warning" if warnings else "pass"),
        "score": score,
        "checks": issues,
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "unknown": len(unknown),
        },
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }


def write_report(task_dir: Path, report: dict[str, Any]) -> Path:
    path = task_dir / "quality_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
