"""Cut the source to the script, lay narration, burn subtitles."""

from __future__ import annotations

from pathlib import Path

from engine.ass_sub import chinese_font, write_ass
from engine.errors import EngineError
from engine.media import even, ffmpeg, filter_path, probe
from engine.scripting import normalize_beats
from engine.tts import fit_to_picture, synthesize


def _mix_segment(
    video: Path,
    narration: Path,
    dest: Path,
    start: float,
    end: float,
    *,
    width: int,
    height: int,
    has_audio: bool,
    original_volume: float,
    job_id: str | None,
    should_cancel,
) -> None:
    pre = max(0.0, start - 0.8)
    local_start = start - pre
    local_end = end - pre
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
    )
    picture = local_end - local_start
    if has_audio and original_volume > 0:
        graph = (
            f"[0:v]trim=start={local_start:.3f}:end={local_end:.3f},setpts=PTS-STARTPTS,{vf}[v];"
            f"[0:a]atrim=start={local_start:.3f}:end={local_end:.3f},asetpts=PTS-STARTPTS,"
            f"aformat=sample_rates=44100:channel_layouts=stereo,volume={original_volume:.3f}[a0];"
            f"[1:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=1[a1];"
            f"[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[a]"
        )
        ffmpeg(
            ["-ss", f"{pre:.3f}", "-i", str(video), "-i", str(narration), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
             "-t", f"{picture:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest)],
            timeout=600,
            job_id=job_id,
            should_cancel=should_cancel,
        )
        return
    graph = f"[0:v]trim=start={local_start:.3f}:end={local_end:.3f},setpts=PTS-STARTPTS,{vf}[v]"
    ffmpeg(
        ["-ss", f"{pre:.3f}", "-i", str(video), "-i", str(narration), "-filter_complex", graph, "-map", "[v]", "-map", "1:a",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(dest)],
        timeout=600,
        job_id=job_id,
        should_cancel=should_cancel,
    )


def render_task(
    task_dir: Path,
    *,
    video: Path,
    beats: list[dict],
    voice: dict,
    duration: float,
    width: int,
    height: int,
    progress=None,
    job_id: str | None = None,
    should_cancel=None,
) -> dict:
    def report(pct: float, stage: str, label: str) -> None:
        if progress:
            progress(pct, stage, label)

    beats, warnings = normalize_beats(beats, duration)
    meta = probe(video)
    width = even(width or meta["width"] or 1280)
    height = even(height or meta["height"] or 720)
    work = task_dir / "render"
    if work.exists():
        for child in work.iterdir():
            child.unlink()
    work.mkdir(parents=True, exist_ok=True)
    segments = []
    cues = []
    cursor = 0.0
    used_engine = ""
    for index, beat in enumerate(beats, start=1):
        if should_cancel and should_cancel():
            raise EngineError("任务已取消", code="cancelled", status=409)
        report(5 + index / len(beats) * 70, "voice", f"配音 {index}/{len(beats)} · {beat['id']}")
        raw_wav = work / f"{index:02d}.wav"
        fitted = work / f"{index:02d}.fit.wav"
        piece = work / f"{index:02d}.mp4"
        try:
            used_engine = synthesize(
                beat["narration"],
                raw_wav,
                engine=str(voice.get("engine") or "auto"),
                voice=str(voice.get("voice") or "zh-CN-XiaoxiaoNeural"),
                rate=float(voice.get("rate") or 1),
            )
            picture = float(beat["end"]) - float(beat["start"])
            fit_to_picture(raw_wav, fitted, picture)
            _mix_segment(
                video,
                fitted,
                piece,
                float(beat["start"]),
                float(beat["end"]),
                width=width,
                height=height,
                has_audio=bool(meta["has_audio"]),
                original_volume=float(voice.get("original_volume") if voice.get("original_volume") is not None else 0.18),
                job_id=job_id,
                should_cancel=should_cancel,
            )
        except EngineError as exc:
            raise EngineError(
                f"第 {index} 段（{beat['id']}）失败：{exc.message}",
                code=exc.code,
                action=exc.action,
                status=exc.status,
            ) from exc
        span = float(probe(piece)["duration"])
        cues.append({"start": round(cursor, 3), "end": round(cursor + span, 3), "text": beat["narration"]})
        cursor += span
        segments.append(piece)

    report(82, "concat", "拼接各段")
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{segment.resolve().as_posix()}'\n" for segment in segments), encoding="utf-8")
    combined = work / "combined.mp4"
    ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(combined)], timeout=300, job_id=job_id, should_cancel=should_cancel)

    output = task_dir / "output.mp4"
    subtitles = bool(voice.get("subtitles", True))
    ass_path = task_dir / "output.ass"
    if subtitles:
        report(90, "subtitle", "烧录字幕")
        write_ass(
            ass_path,
            cues,
            width=width,
            height=height,
            font_size=int(voice.get("font_size") or 46),
            margin_v=int(voice.get("margin_v") or 48),
        )
        _family, font_file = chinese_font()
        fontsdir = font_file.parent if font_file else Path("/usr/share/fonts")
        try:
            ffmpeg(
                ["-i", str(combined), "-vf", f"subtitles={filter_path(ass_path)}:fontsdir={filter_path(fontsdir)}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "copy", "-movflags", "+faststart", str(output)],
                timeout=600,
                job_id=job_id,
                should_cancel=should_cancel,
            )
        except EngineError as exc:
            nosub = task_dir / "output.nosub.mp4"
            combined.replace(nosub)
            raise EngineError(
                f"字幕烧录失败：{exc.message}。无字幕成片已留在 {nosub.name}",
                code="subtitle",
                action="关掉字幕再渲染，或检查系统是否有中文字体（文泉驿或 Droid Sans Fallback）。",
                status=500,
            ) from exc
    else:
        combined.replace(output)

    if not output.is_file() or output.stat().st_size < 200:
        raise EngineError("成片文件是空的", code="render_empty", action="看任务日志里的 ffmpeg 报错。", status=500)
    final = probe(output)
    report(100, "done", "成片已写出")
    warning = ""
    if used_engine == "tone":
        warning = "这次用的是测试音，不是人声。安装 edge-tts 或配置 TTS 接口后再渲染一版。"
    return {
        "file": output.name,
        "ass": ass_path.name if ass_path.is_file() else "",
        "duration": final["duration"],
        "width": final["width"],
        "height": final["height"],
        "size": final["size"],
        "tts_engine": used_engine,
        "warnings": [item for item in [*warnings, warning] if item],
        "cues": cues,
    }
