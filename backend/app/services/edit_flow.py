"""Analyze signals, optionally rank them, propose highlight keeps, then concat."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from engine.asr import transcribe
from engine.compose import output_seconds, render_image_segment, render_video_segment
from engine.errors import EngineError
from engine.highlight import plan_highlight
from engine.media import even, ffmpeg, probe
from engine.rank import rank
from engine.signals import build_suggestions, detect
from app.jobs import cancelled, report, update
from app.services.media_refs import resolve_broll, resolve_image
from app.services.videos import fetch, ingest, resolve_media, unique_path, videos_dir


def _check(job_id: str):
    return lambda: cancelled(job_id)


def analyze_videos(job_id: str, video_ids: list[int], *, silence_db: float, min_silence: float, min_black: float, scene_threshold: float) -> None:
    if not video_ids:
        raise EngineError("没有选择视频", code="empty", action="先从素材库把成片加到时间线。", status=400)
    if len(video_ids) > 12:
        raise EngineError("一次最多分析 12 条", code="too_many", action="分成几次分析。", status=400)
    clips = []
    transcript_errors = []
    for index, video_id in enumerate(video_ids):
        video = fetch(video_id)
        path = resolve_media(video)
        title = str(video.get("title") or path.name)
        report(job_id, 5 + index / len(video_ids) * 80, "signals", f"检测「{title}」的静音、黑场和镜头")
        signals = detect(
            path,
            silence_db=silence_db,
            min_silence=min_silence,
            min_black=min_black,
            scene_threshold=scene_threshold,
            job_id=job_id,
            should_cancel=_check(job_id),
        )
        report(job_id, 10 + index / len(video_ids) * 85, "asr", f"转写「{title}」")
        try:
            transcript = transcribe(path, source_platform=str(video.get("source_platform") or ""), job_id=job_id, should_cancel=_check(job_id))
        except EngineError as exc:
            transcript = {"source": "none", "warning": exc.message, "segments": [], "error": exc.as_dict()}
            transcript_errors.append(exc.as_dict())
            update(job_id, log=f"转写失败，信号建议仍然保留：{exc.message}")
        suggestions = build_suggestions(video_id, signals, transcript.get("segments") or [])
        clips.append({
            "video_id": video_id,
            "title": title,
            "duration": signals["duration"],
            "width": signals["width"],
            "height": signals["height"],
            "transcript": transcript,
            "signals": {
                "silence": signals["silence"],
                "black": signals["black"],
                "scenes": signals["scenes"],
            },
            "suggestions": suggestions,
        })
        update(job_id, log=f"「{title}」{len(suggestions)} 条切点建议")
    status = "partial" if transcript_errors else "success"
    update(
        job_id,
        status=status,
        progress=100,
        stage="done",
        label="分析完成" if status == "success" else "信号已完成，转写有问题",
        error=transcript_errors[0] if transcript_errors else None,
        result={"clips": clips},
    )


def plan_suggestions(video_id: int, suggestions: list[dict], *, instruction: str, use_llm: bool) -> dict:
    video = fetch(video_id)
    path = resolve_media(video)
    duration = float(video.get("duration") or 0) or float(probe(path)["duration"])
    owned = [item for item in suggestions if int(item.get("video_id") or video_id) == int(video_id)]
    if not owned:
        raise EngineError(
            "没有可排序的建议",
            code="empty",
            action="先对这条素材点「分析切点」。",
            status=422,
        )
    return rank(owned, instruction=instruction, duration=duration, use_llm=use_llm)


@dataclass
class _Piece:
    kind: str
    path: Path
    start: float = 0.0
    end: float = 0.0
    speed: float = 1.0
    freeze: float = 0.0
    has_audio: bool = True
    width: int = 1280
    height: int = 720
    title: str = ""
    overlay: Path | None = None
    overlay_seconds: float = 0.0
    hold: float = 0.0


def _clip_title(clip: dict, fallback: str) -> str:
    return str(clip.get("title") or fallback or "片段")[:40]


def _prepare_pieces(clips: list[dict]) -> tuple[list[_Piece], list[str]]:
    prepared: list[_Piece] = []
    warnings: list[str] = []
    for clip in clips:
        kind = str(clip.get("kind") or "video")
        title = _clip_title(clip, "")
        if kind == "image":
            image = resolve_image(int(clip.get("image_id") or 0), str(clip.get("image_name") or ""))
            label = title or str(clip.get("image_name") or clip.get("image_id") or "插图")
            if image is None:
                warnings.append(f"插图「{label}」找不到文件，已跳过。")
                continue
            hold = float(clip.get("end") or 0) - float(clip.get("start") or 0)
            if hold < 0.3:
                hold = float(clip.get("freeze") or 0) or 2.5
            hold = max(0.3, min(30.0, hold))
            piece = _Piece(kind="image", path=image, hold=hold, title=label, width=1280, height=720)
            if clip.get("overlay"):
                host = next((item for item in reversed(prepared) if item.kind == "video" and item.overlay is None), None)
                if host is None:
                    warnings.append(f"叠图「{label}」前面没有画面，已改成全屏静帧。")
                    prepared.append(piece)
                else:
                    host.overlay = image
                    host.overlay_seconds = hold
            else:
                prepared.append(piece)
            continue
        found = resolve_broll(int(clip.get("video_id") or 0), str(clip.get("video_name") or ""))
        if found is None:
            label = title or f"视频 {clip.get('video_id') or clip.get('video_name') or ''}"
            warnings.append(f"插入视频「{label}」找不到文件，已跳过。")
            continue
        path, video = found
        meta = probe(path)
        start = round(float(clip.get("start") or 0), 3)
        end = round(float(clip.get("end") or 0), 3)
        speed = float(clip.get("speed") or 1)
        freeze = float(clip.get("freeze") or 0)
        label = _clip_title(clip, str(video.get("title") or path.name))
        duration = float(meta["duration"])
        if start < 0 or start >= duration:
            warnings.append(f"「{label}」的入点超出片长（{duration:.2f}s），已跳过。")
            continue
        if end > duration:
            end = duration
        if end <= start:
            warnings.append(f"「{label}」出点不晚于入点，已跳过。")
            continue
        if output_seconds(start, end, speed, freeze) < 0.25:
            warnings.append(f"「{label}」短于 0.3 秒，已跳过。")
            continue
        prepared.append(_Piece(
            kind="video",
            path=path,
            start=start,
            end=end,
            speed=speed,
            freeze=freeze,
            has_audio=bool(meta["has_audio"]),
            width=int(meta["width"] or 1280),
            height=int(meta["height"] or 720),
            title=label,
        ))
    if not prepared:
        detail = warnings[0] if warnings else "至少保留一段超过 0.3 秒的画面。"
        raise EngineError(
            "没有可导出的片段",
            code="empty" if not warnings else "media_missing",
            action=detail,
            status=422 if warnings else 400,
        )
    return prepared, warnings


def build_highlight(videos: list[dict], *, instruction: str, use_llm: bool) -> dict:
    prepared = []
    notes: list[str] = []
    for item in videos:
        video_id = int(item.get("video_id") or 0)
        try:
            video = fetch(video_id)
            path = resolve_media(video)
        except EngineError as exc:
            notes.append(exc.message)
            continue
        duration = float(item.get("duration") or 0)
        if duration <= 0:
            duration = float(video.get("duration") or 0) or float(probe(path)["duration"])
        frames = [frame for frame in (item.get("frames") or []) if isinstance(frame, dict)]
        has_caption = any(str(frame.get("caption") or "").strip() for frame in frames)
        if use_llm and not has_caption and duration <= 180:
            try:
                from engine.settings import vision_configured

                if vision_configured():
                    frames = _sample_captions(path, item.get("scenes") or [], duration)
            except EngineError as exc:
                notes.append(f"「{video.get('title') or path.name}」画面说明没有生成：{exc.message}")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"「{video.get('title') or path.name}」画面说明没有生成：{exc}")
        prepared.append({
            **item,
            "video_id": video_id,
            "duration": duration,
            "title": item.get("title") or video.get("title") or path.name,
            "frames": frames,
        })
    if not prepared:
        raise EngineError(
            "没有可分析的素材",
            code="empty",
            action="先把成片加到时间线并完成分析。",
            status=422,
        )
    result = plan_highlight(prepared, instruction=instruction.strip()[:400], use_llm=use_llm)
    result["warnings"] = [*notes, *(result.get("warnings") or [])]
    return result


def _sample_captions(path: Path, scenes, duration: float) -> list[dict]:
    import tempfile

    from engine.frames import extract_frames, pick_times
    from app.services.videos import data_dir

    times = pick_times(duration, [float(item) for item in scenes if isinstance(item, (int, float))], max_frames=4, frames_per_second=0.5)
    root = data_dir() / "tmp"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="highlight-frames-", dir=root) as tmp:
        frames = extract_frames(path, Path(tmp), times, caption=True)
    return [
        {"time": frame.get("time"), "caption": frame.get("caption") or "", "caption_error": frame.get("caption_error") or ""}
        for frame in frames
    ]


def export_clips(job_id: str, title: str, clips: list[dict]) -> None:
    if not clips:
        raise EngineError("没有可导出的片段", code="empty", action="至少保留一段超过 0.3 秒的画面。", status=400)
    if len(clips) > 40:
        raise EngineError("片段超过 40 段", code="too_many", action="合并相邻保留段后再导出。", status=400)
    prepared, media_warnings = _prepare_pieces(clips)
    for note in media_warnings:
        update(job_id, log=note)
    width = even(next((item.width for item in prepared if item.kind == "video"), 1280))
    height = even(next((item.height for item in prepared if item.kind == "video"), 720))
    work = unique_path(videos_dir() / "_edit_tmp", "job", "")
    work.mkdir(parents=True, exist_ok=True)
    pieces: list[Path] = []
    try:
        for index, item in enumerate(prepared, start=1):
            report(job_id, index / len(prepared) * 75, "export", f"裁切 {index}/{len(prepared)} · {item.title or item.kind}")
            piece = work / f"{index:02d}.mp4"
            if item.kind == "image":
                render_image_segment(
                    item.path, piece, duration=item.hold, width=width, height=height,
                    job_id=job_id, should_cancel=_check(job_id),
                )
            else:
                render_video_segment(
                    item.path, piece,
                    start=item.start, end=item.end, speed=item.speed, freeze=item.freeze,
                    width=width, height=height, has_audio=item.has_audio,
                    overlay=item.overlay, overlay_seconds=item.overlay_seconds,
                    job_id=job_id, should_cancel=_check(job_id),
                )
            pieces.append(piece)
        report(job_id, 85, "concat", "拼接成片")
        listing = work / "concat.txt"
        listing.write_text("".join(f"file '{piece.resolve().as_posix()}'\n" for piece in pieces), encoding="utf-8")
        output = unique_path(videos_dir(), title or "剪辑成片")
        ffmpeg(
            ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(output)],
            timeout=300,
            job_id=job_id,
            should_cancel=_check(job_id),
        )
        meta = probe(output)
        try:
            video = ingest(output, title or "剪辑成片", "edited")
            ingest_error = None
        except EngineError as exc:
            video = None
            ingest_error = exc.as_dict()
            update(job_id, log=exc.message)
        partial = bool(ingest_error or media_warnings)
        if media_warnings and not ingest_error:
            label = "已导出，有素材被跳过"
            error = {
                "code": "media_missing",
                "message": media_warnings[0],
                "action": "缺失的图片或视频已跳过，其余片段已导出。重新生成或从素材库再选一次即可补上。",
            }
        elif ingest_error:
            label = "已导出，但入库失败"
            error = ingest_error
        else:
            label = "已导出"
            error = None
        update(
            job_id,
            status="partial" if partial else "success",
            progress=100,
            stage="done",
            label=label,
            error=error,
            result={
                "output_path": str(output),
                "download_url": f"/api/jobs/{job_id}/file",
                "duration": meta["duration"],
                "video": video,
                "ingest_error": ingest_error,
                "warnings": media_warnings,
            },
        )
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
