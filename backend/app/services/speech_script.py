"""独立口播解说：对已有素材出短句文稿，审核后再用原声渲染。

不合成新旁白，不走下载，也不走智能剪辑的成片流程。
"""
from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

from app.db import create_narration_task, get_narration_task, update_narration_task
from app.services.ai_edit import AIResponseError
from app.services.videos import data_dir, fetch, resolve_media

PAUSE_SECONDS = 0.45
MAX_CHARS = 18


def split_utterance(text: str) -> list[str]:
    """按标点收成短句。超长且没有标点时才按字数切开，并标成未核对。"""
    cleaned = re.sub(r"\s+", "", str(text or ""))
    if not cleaned:
        return []
    parts = [item for item in re.split(r"(?<=[。！？；，、])", cleaned) if item]
    short = []
    for part in parts or [cleaned]:
        if len(part) <= MAX_CHARS:
            short.append(part)
            continue
        for index in range(0, len(part), MAX_CHARS):
            short.append(part[index:index + MAX_CHARS])
    return short


def draft_script(segments: list[dict], target_seconds: float) -> dict:
    """用原片时间轴上的短句填满目标时长。放不进的下一句整句留下，不在句中截断。"""
    if target_seconds < 5 or target_seconds > 180:
        raise AIResponseError("解说目标时长要在 5 到 180 秒之间，这次没有生成。")
    beats = []
    used = 0.0
    for segment in segments:
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start)
        if end <= start:
            continue
        pieces = split_utterance(str(segment.get("text") or ""))
        if not pieces:
            continue
        weights = [max(1, len(piece)) for piece in pieces]
        total = sum(weights)
        cursor = start
        span = end - start
        for piece, weight in zip(pieces, weights):
            piece_len = span * weight / total
            piece_end = cursor + piece_len
            if used + piece_len > target_seconds + 0.05:
                if not beats:
                    raise AIResponseError(
                        f"第一句大约 {piece_len:.1f} 秒，放不进 {target_seconds:g} 秒。"
                        "没有截断这句，也没有出片。"
                    )
                return {
                    "ok": True,
                    "beats": beats,
                    "covered_seconds": round(used, 2),
                    "stopped": "limit",
                    "audio": "original",
                    "pause_seconds": PAUSE_SECONDS,
                }
            forced = len(piece) >= MAX_CHARS and piece[-1] not in "。！？；，、"
            beats.append({
                "id": f"b{len(beats) + 1}",
                "text": piece,
                "start": round(cursor, 2),
                "end": round(piece_end, 2),
                "pause_after": PAUSE_SECONDS,
                "audio": "original",
                "speed": 1.0,
                "review_state": "unverified",
                "split_note": "这句没有标点，只按字数分开，需要核对。" if forced else "",
            })
            used += piece_len
            cursor = piece_end
    if not beats:
        raise AIResponseError("没有可用口播，无法写解说文稿。")
    return {
        "ok": True,
        "beats": beats,
        "covered_seconds": round(used, 2),
        "stopped": "source",
        "audio": "original",
        "pause_seconds": PAUSE_SECONDS,
    }


def _task_dir(task_id: str) -> Path:
    path = data_dir() / "narration" / task_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_draft(video_id: int, target_seconds: float, *, segments: list[dict] | None = None) -> dict:
    video = fetch(int(video_id))
    if segments is None:
        from app.services.transcription import transcribe

        segments = transcribe(resolve_media(video), log=lambda _message: None)
    script = draft_script(segments, float(target_seconds))
    task_id = uuid.uuid4().hex[:12]
    row = create_narration_task(
        id=task_id,
        video_id=int(video_id),
        title=str(video.get("title") or "口播解说")[:80],
        content_type="speech",
        mode="original-audio",
        target_duration=int(target_seconds),
        status="awaiting_review",
        progress=40,
        message="文稿待审核。确认后才用原声渲染。",
        request_json={"video_id": int(video_id), "target_seconds": target_seconds, "audio": "original"},
        result_json=script,
    )
    return row


def save_review(task_id: str, beats: list[dict]) -> dict:
    row = get_narration_task(task_id)
    if not row:
        raise AIResponseError("找不到这篇解说。")
    if row.get("status") not in {"awaiting_review", "failed"}:
        raise AIResponseError("这篇解说已经不在审核状态。")
    cleaned = []
    for index, beat in enumerate(beats, start=1):
        text = re.sub(r"\s+", "", str(beat.get("text") or ""))
        if not text:
            continue
        start = float(beat.get("start") or 0)
        end = float(beat.get("end") or 0)
        if end <= start:
            raise AIResponseError(f"第 {index} 句的时间无效。")
        cleaned.append({
            "id": f"b{index}",
            "text": text[:80],
            "start": round(start, 2),
            "end": round(end, 2),
            "pause_after": PAUSE_SECONDS,
            "audio": "original",
            "speed": 1.0,
            "review_state": "reviewed",
            "split_note": str(beat.get("split_note") or ""),
        })
    if not cleaned:
        raise AIResponseError("审核后的文稿是空的，没有渲染。")
    covered = round(sum(item["end"] - item["start"] for item in cleaned), 2)
    return update_narration_task(
        task_id,
        status="awaiting_review",
        progress=70,
        message="文稿已审核，可以渲染原声。",
        result_json={
            "ok": True,
            "beats": cleaned,
            "covered_seconds": covered,
            "stopped": "reviewed",
            "audio": "original",
            "pause_seconds": PAUSE_SECONDS,
            "reviewed": True,
        },
    )


def render_original(task_id: str) -> dict:
    row = get_narration_task(task_id)
    if not row:
        raise AIResponseError("找不到这篇解说。")
    result = row.get("result") or {}
    if not result.get("reviewed"):
        raise AIResponseError("文稿还没审核，不能渲染。")
    beats = result.get("beats") or []
    if not beats:
        raise AIResponseError("没有可渲染的句子。")
    video = fetch(int(row["video_id"]))
    source = resolve_media(video)
    dest = _task_dir(task_id) / "narration.mp4"
    _render_beats(source, beats, dest)
    return update_narration_task(
        task_id,
        status="success",
        progress=100,
        message="已用原声渲染。句间留了短暂停顿。",
        result_json={**result, "output": str(dest), "rendered": True},
    )


def output_file(task_id: str) -> Path:
    row = get_narration_task(task_id)
    if not row or not (row.get("result") or {}).get("rendered"):
        raise AIResponseError("解说还没渲染。")
    path = Path(str((row.get("result") or {}).get("output") or ""))
    root = (data_dir() / "narration" / task_id).resolve()
    resolved = path.resolve()
    if root not in resolved.parents or resolved.name != "narration.mp4" or not resolved.is_file():
        raise AIResponseError("解说文件不在这次任务目录里。")
    return resolved


def _ffmpeg(args: list[str]) -> None:
    completed = subprocess.run(["ffmpeg", "-y", "-v", "error", *args], capture_output=True, timeout=120)
    if completed.returncode != 0:
        raise AIResponseError("原声渲染失败：" + completed.stderr.decode(errors="ignore")[-300:])


def _render_beats(source: Path, beats: list[dict], dest: Path) -> None:
    """按审核后的原声片段拼接，句间插入固定静音，不另录旁白。"""
    work = dest.parent
    silence = work / "pause.mp4"
    _ffmpeg([
        "-f", "lavfi", "-i", f"color=c=black:s=320x180:r=25:d={PAUSE_SECONDS}",
        "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={PAUSE_SECONDS}",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(silence),
    ])
    parts = []
    for index, beat in enumerate(beats):
        part = work / f"beat-{index}.mp4"
        length = max(0.2, float(beat["end"]) - float(beat["start"]))
        _ffmpeg([
            "-ss", f"{float(beat['start']):.3f}", "-t", f"{length:.3f}", "-i", str(source),
            "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2,fps=25",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
            str(part),
        ])
        parts.append(part)
    listing = work / "concat.txt"
    lines = []
    for index, part in enumerate(parts):
        lines.append("file " + part.name)
        if index != len(parts) - 1:
            lines.append("file " + silence.name)
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(dest)])
    if not dest.is_file() or dest.stat().st_size < 1000:
        raise AIResponseError("原声渲染没有写出成片。")
