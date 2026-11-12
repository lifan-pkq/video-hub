"""抖音一键成片：转写 → 选多条高光 → 竖屏渲染 → 预览包。发布仍由用户手动完成。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from engine.errors import EngineError
from app.config import douyin_settings
from app.jobs import cancelled, report, update
from app.services.ai_edit import AIResponseError, AIServiceError, TranscriptionCancelled, transcribe
from app.services.douyin_plan import (
    boxes_from_understanding,
    build_subject_track,
    plan_douyin_shorts,
)
from app.services.douyin_render import (
    build_clip_zip,
    build_pack_zip,
    probe_source,
    render_short,
    sample_skin_boxes,
    write_copy_files,
    write_cover,
)
from app.services.videos import data_dir, fetch, ingest, resolve_media


def _check(job_id: str):
    return lambda: cancelled(job_id)


def _log(job_id: str):
    def log(message: str) -> None:
        if cancelled(job_id):
            raise EngineError("任务已取消", code="cancelled", status=409)
        update(job_id, log=str(message)[:500])

    return log


def load_subject_boxes(video_id: int) -> list[dict]:
    path = data_dir() / "understanding" / f"{int(video_id)}.json"
    data = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
    if data is None:
        try:
            from app.services.understanding import get_analysis

            data = get_analysis(int(video_id))
        except Exception:
            data = None
    return boxes_from_understanding(data if isinstance(data, dict) else None)


def produce_douyin(
    job_id: str,
    video_id: int,
    instruction: str = "",
    max_clips: int = 0,
    *,
    layout: str = "interview",
) -> None:
    video = fetch(int(video_id))
    source = resolve_media(video)
    meta = probe_source(source)
    settings = douyin_settings()
    title = str(video.get("title") or source.name)
    layout = "podcast" if layout == "podcast" else "interview"
    report(job_id, 4, "asr", f"转写「{title}」")
    try:
        segments = transcribe(source, log=_log(job_id), job_id=job_id, should_cancel=_check(job_id))
    except TranscriptionCancelled as exc:
        raise EngineError("任务已取消", code="cancelled", status=409) from exc
    except (AIServiceError, AIResponseError) as exc:
        raise EngineError(
            str(exc),
            code="asr",
            action="确认本机 ffmpeg 和 faster-whisper 可用。长视频会先抽音频再按 300 秒分段，避免整段进内存。",
            status=502,
        ) from exc
    if not segments:
        raise EngineError(
            "没有识别到口播",
            code="empty_transcript",
            action="确认原片有人声。纯音乐或静音没法抽成抖音口播。",
            status=422,
        )
    report(job_id, 42, "plan", "挑选适合抖音的高光")
    try:
        plan = plan_douyin_shorts(
            segments,
            float(meta["duration"]),
            instruction=instruction.strip()[:400],
            max_clips=max_clips,
            log=_log(job_id),
        )
    except (AIServiceError, AIResponseError) as exc:
        raise EngineError(
            str(exc),
            code="ai",
            action="检查 AI_BASE_URL、API Key 和模型名，或把提示写得更具体后再试。",
            status=502,
        ) from exc
    for note in plan.get("warnings") or []:
        update(job_id, log=note)
    clips = plan["clips"]
    root = data_dir() / "douyin" / job_id
    root.mkdir(parents=True, exist_ok=True)
    from app.services.media import free_mb

    if free_mb(root) < 512:
        raise EngineError(
            "磁盘剩余不足 512MB，停止渲染。",
            code="disk",
            action="到系统页清理临时文件，或把 VIDEO_HUB_DOUYIN_WIDTH 改成 720。",
            status=507,
        )
    boxes = [] if layout == "podcast" else load_subject_boxes(video_id)
    shorts = []
    failures: list[str] = []

    def publish_live(pack_url: str = "") -> None:
        update(
            job_id,
            result={
                "video_id": int(video_id),
                "source_title": title,
                "source": plan["source"],
                "budget": plan["budget"],
                "duration": round(float(meta["duration"]), 2),
                "sentences": len(segments),
                "warnings": [*(plan.get("warnings") or []), *failures],
                "layout": layout,
                "pack_url": pack_url,
                "shorts": list(shorts),
            },
        )

    for offset, clip in enumerate(clips):
        if cancelled(job_id):
            raise EngineError("任务已取消", code="cancelled", status=409)
        index = offset + 1
        label = str(clip.get("title") or f"片段 {index}")
        report(job_id, 48 + (index / max(1, len(clips))) * 42, "render", f"渲染 {index}/{len(clips)} · {label}")
        folder = root / f"{index:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        write_copy_files(folder, clip)
        if layout == "podcast":
            track = []
        else:
            in_boxes = [
                item for item in boxes
                if float(clip["start"]) - 1 <= float(item["time"]) <= float(clip["end"]) + 1
            ]
            skin = [] if in_boxes else sample_skin_boxes(source, float(clip["start"]), float(clip["end"]), segments)
            track = build_subject_track(float(clip["start"]), float(clip["end"]), segments, in_boxes, skin)
        try:
            rendered = render_short(
                source,
                folder / "video.mp4",
                start=float(clip["start"]),
                end=float(clip["end"]),
                src_w=int(meta["width"]),
                src_h=int(meta["height"]),
                has_audio=bool(meta["has_audio"]),
                track=track,
                captions=list(clip.get("captions") or []),
                out_w=int(settings["width"]),
                out_h=int(settings["height"]),
                threads=int(settings["threads"]),
                layout=layout,
                job_id=job_id,
                should_cancel=_check(job_id),
            )
        except EngineError as exc:
            if exc.code == "cancelled":
                raise
            failures.append(f"「{label}」{exc.message}")
            update(job_id, log=failures[-1])
            continue
        cover_ok = write_cover(folder / "video.mp4", folder / "cover.jpg", job_id=job_id, should_cancel=_check(job_id))
        if not rendered["captions_burned"] and clip.get("captions"):
            update(job_id, log=f"「{label}」字幕没烧进去，文案和字幕稿仍可下载。")
        kit_ok = False
        try:
            kit_ok = build_clip_zip(folder)
        except Exception as exc:  # noqa: BLE001 - 成片已写出，单条打包失败仍可预览
            update(job_id, log=f"「{label}」单条发布包没打成：{exc}")
        shorts.append(_public_short(job_id, index, clip, rendered, cover_ok, layout=layout, kit_ok=kit_ok))
        publish_live()
    if not shorts:
        detail = failures[0] if failures else "没有可渲染的片段。"
        raise EngineError(
            "没有生成抖音成片",
            code="render",
            action=detail,
            status=500,
        )
    warnings = [*(plan.get("warnings") or []), *failures]
    pack_url = ""
    try:
        report(job_id, 94, "pack", "打包视频、封面和文案")
        build_pack_zip(root, shorts)
        pack_url = f"/api/edit/douyin/{job_id}/pack"
    except Exception as exc:  # noqa: BLE001 - 成片已经写出，打包失败不应把任务打成全失败
        warnings.append(f"整包压缩失败：{exc}")
        update(job_id, log=warnings[-1])
    partial = bool(failures) or not pack_url
    error = None
    if failures:
        label = "已生成，有片段失败"
        error = {
            "code": "partial_render",
            "message": failures[0],
            "action": "其余成片可以预览和下载。失败原因在任务日志里。",
        }
    elif not pack_url:
        label = "成片已生成，整包失败"
        error = {
            "code": "pack",
            "message": warnings[-1] if warnings else "整包没有生成",
            "action": "可以分别下载每条成片。",
        }
    else:
        label = "抖音成片已生成"
    update(
        job_id,
        status="partial" if partial else "success",
        progress=100,
        stage="done",
        label=label,
        error=error,
        result={
            "video_id": int(video_id),
            "source_title": title,
            "source": plan["source"],
            "budget": plan["budget"],
            "duration": round(float(meta["duration"]), 2),
            "sentences": len(segments),
            "warnings": warnings,
            "layout": layout,
            "pack_url": pack_url,
            "shorts": shorts,
        },
    )


def _public_short(
    job_id: str,
    index: int,
    clip: dict,
    rendered: dict,
    cover_ok: bool,
    *,
    layout: str,
    kit_ok: bool,
) -> dict:
    return {
        "index": index,
        "title": clip.get("title") or f"片段 {index}",
        "hashtags": list(clip.get("hashtags") or []),
        "description": clip.get("description") or "",
        "hook": clip.get("hook") or "",
        "reason": clip.get("reason") or "",
        "start": clip.get("start"),
        "end": clip.get("end"),
        "duration": clip.get("duration"),
        "score": clip.get("score"),
        "layout": layout,
        "framing": rendered.get("framing") or "center",
        "captions_burned": bool(rendered.get("captions_burned")),
        "width": rendered.get("width"),
        "height": rendered.get("height"),
        "file_url": f"/api/edit/douyin/{job_id}/shorts/{index}/file",
        "cover_url": f"/api/edit/douyin/{job_id}/shorts/{index}/cover" if cover_ok else "",
        "kit_url": f"/api/edit/douyin/{job_id}/shorts/{index}/pack" if kit_ok else "",
        "video_id": None,
    }


def _job_or_raise(job_id: str) -> dict:
    from app.jobs import get

    job = get(job_id)
    if str(job.get("kind") or "") != "edit.douyin":
        raise EngineError("这不是抖音成片任务", code="job", status=404)
    return job


def short_file(job_id: str, index: int, *, cover: bool = False, kit: bool = False) -> Path:
    job = _job_or_raise(job_id)
    if job.get("status") not in {"success", "partial", "running"}:
        raise EngineError("成片还没完成", code="missing_file", action="等任务结束，或看失败原因。", status=404)
    shorts = ((job.get("result") or {}).get("shorts") or [])
    if not any(int(item.get("index") or 0) == int(index) for item in shorts):
        raise EngineError("没有这条成片", code="missing_file", status=404)
    root = (data_dir() / "douyin" / job["id"]).resolve()
    folder = (root / f"{int(index):02d}").resolve()
    folder.relative_to(root)
    name = "kit.zip" if kit else "cover.jpg" if cover else "video.mp4"
    path = folder / name
    if not path.is_file():
        raise EngineError("文件不在磁盘上", code="missing_file", action="重新跑一次一键生成。", status=404)
    return path


def download_name(job_id: str, index: int, *, cover: bool = False, kit: bool = False) -> str:
    job = _job_or_raise(job_id)
    short = next(
        (item for item in ((job.get("result") or {}).get("shorts") or []) if int(item.get("index") or 0) == int(index)),
        {},
    )
    stem = re.sub(r'[\\/:*?"<>|\n\r]+', "_", str(short.get("title") or f"短视频{index}")).strip(" ._")[:40]
    stem = stem or f"短视频{index}"
    suffix = ".zip" if kit else ".jpg" if cover else ".mp4"
    return f"{int(index):02d}-{stem}{suffix}"


def pack_file(job_id: str) -> Path:
    job = _job_or_raise(job_id)
    if job.get("status") not in {"success", "partial"}:
        raise EngineError("成片还没完成", code="missing_file", status=404)
    path = (data_dir() / "douyin" / job["id"] / "pack.zip").resolve()
    root = path.parent
    path.relative_to(root)
    if not path.is_file():
        raise EngineError("整包还没有生成", code="missing_file", action="可以分别下载每条成片。", status=404)
    return path


def send_to_library(job_id: str, indexes: list[int] | None = None) -> dict:
    """把选中的成片入库。之后可以走现有的封面与导出，不会自动发布。"""
    job = _job_or_raise(job_id)
    if job.get("status") not in {"success", "partial"}:
        raise EngineError(
            "成片还没完成，不能入库",
            code="not_ready",
            action="等生成结束。失败的话先看任务日志。",
            status=409,
        )
    result = dict(job.get("result") or {})
    shorts = [dict(item) for item in (result.get("shorts") or [])]
    wanted = {int(item) for item in (indexes or []) if int(item) > 0}
    chosen = [item for item in shorts if not wanted or int(item.get("index") or 0) in wanted]
    if not chosen:
        raise EngineError("没有选中的成片", code="empty", action="至少选一条已经生成的成片。", status=422)
    videos = []
    warnings = []
    for item in chosen:
        if item.get("video_id"):
            try:
                videos.append(fetch(int(item["video_id"])))
                continue
            except EngineError:
                item["video_id"] = None
        try:
            path = short_file(job_id, int(item["index"]))
            video = ingest(path, str(item.get("title") or "抖音成片"), "douyin_short")
        except EngineError as exc:
            warnings.append(f"「{item.get('title') or item.get('index')}」{exc.message}")
            continue
        item["video_id"] = video.get("id")
        videos.append(video)
    by_index = {int(item["index"]): item for item in chosen}
    merged = []
    for item in shorts:
        merged.append(by_index.get(int(item.get("index") or 0), item))
    result["shorts"] = merged
    update(job_id, result=result)
    if not videos:
        raise EngineError(
            "没有成片入库",
            code="library_ingest",
            action=warnings[0] if warnings else "确认成片文件还在。",
            status=500,
        )
    return {"videos": videos, "shorts": merged, "warnings": warnings}
