"""Analyze signals, optionally rank them, then concat the kept ranges."""

from __future__ import annotations

from pathlib import Path

from engine.asr import transcribe
from engine.errors import EngineError
from engine.media import ffmpeg, probe
from engine.rank import rank
from engine.signals import build_suggestions, detect
from app.jobs import cancelled, report, update
from app.services.videos import fetch, ingest, public_video, resolve_media, unique_path, videos_dir


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


def export_clips(job_id: str, title: str, clips: list[dict]) -> None:
    if not clips:
        raise EngineError("没有可导出的片段", code="empty", action="至少保留一段超过 0.3 秒的画面。", status=400)
    if len(clips) > 40:
        raise EngineError("片段超过 40 段", code="too_many", action="合并相邻保留段后再导出。", status=400)
    prepared: list[tuple[Path, float, float, int, int, bool]] = []
    for clip in clips:
        video = fetch(int(clip["video_id"]))
        path = resolve_media(video)
        meta = probe(path)
        start = round(float(clip["start"]), 3)
        end = round(float(clip["end"]), 3)
        if end - start < 0.25:
            raise EngineError(
                f"片段太短：{start:.2f}-{end:.2f}",
                code="clip",
                action="删掉短于 0.3 秒的残留，或把切点拉开。",
                status=422,
            )
        if start < 0 or end > float(meta["duration"]) + 0.15:
            raise EngineError(
                f"「{video.get('title') or path.name}」的出点超出片长（{meta['duration']:.2f}s）",
                code="clip",
                action="把出点拖回视频时长以内。",
                status=422,
            )
        prepared.append((path, start, end, int(meta["width"] or 1280), int(meta["height"] or 720), bool(meta["has_audio"])))

    from engine.media import even

    width = even(prepared[0][3])
    height = even(prepared[0][4])
    work = unique_path(videos_dir() / "_edit_tmp", "job", "")
    work.mkdir(parents=True, exist_ok=True)
    pieces: list[Path] = []
    try:
        for index, (path, start, end, _w, _h, has_audio) in enumerate(prepared, start=1):
            report(job_id, index / len(prepared) * 75, "export", f"裁切 {index}/{len(prepared)}")
            piece = work / f"{index:02d}.mp4"
            pre = max(0.0, start - 0.5)
            local_start = start - pre
            local_end = end - pre
            vf = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p"
            )
            if has_audio:
                graph = (
                    f"[0:v]trim=start={local_start:.3f}:end={local_end:.3f},setpts=PTS-STARTPTS,{vf}[v];"
                    f"[0:a]atrim=start={local_start:.3f}:end={local_end:.3f},asetpts=PTS-STARTPTS,"
                    f"aformat=sample_rates=44100:channel_layouts=stereo[a]"
                )
                ffmpeg(
                    ["-ss", f"{pre:.3f}", "-i", str(path), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "160k",
                     "-t", f"{(end - start):.3f}", "-movflags", "+faststart", str(piece)],
                    timeout=900,
                    job_id=job_id,
                    should_cancel=_check(job_id),
                )
            else:
                graph = f"[0:v]trim=start={local_start:.3f}:end={local_end:.3f},setpts=PTS-STARTPTS,{vf}[v]"
                ffmpeg(
                    ["-ss", f"{pre:.3f}", "-i", str(path), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                     "-filter_complex", graph, "-map", "[v]", "-map", "1:a", "-shortest",
                     "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k",
                     "-movflags", "+faststart", str(piece)],
                    timeout=900,
                    job_id=job_id,
                    should_cancel=_check(job_id),
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
        update(
            job_id,
            status="partial" if ingest_error else "success",
            progress=100,
            stage="done",
            label="已导出" if not ingest_error else "已导出，但入库失败",
            error=ingest_error,
            result={
                "output_path": str(output),
                "download_url": f"/api/jobs/{job_id}/file",
                "duration": meta["duration"],
                "video": video,
                "ingest_error": ingest_error,
            },
        )
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
