"""抖音一键成片：转写 → 选多条高光 → 竖屏渲染 → 预览包。发布仍由用户手动完成。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from engine.errors import EngineError
from app.config import douyin_settings
from app.jobs import cancelled, report, update
from app.services.ai_edit import AIResponseError, AIServiceError, TranscriptionCancelled, transcribe
from app.services.transcription import last_asr_run, public_asr_status
from app.services.chenze_pipeline import plan_intelligent_edit
from app.services.douyin_plan import (
    boxes_from_understanding,
    build_subject_track,
    requested_limits,
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
from app.services.text_enhance import (
    TextEnhanceError,
    apply_confirmed_cues,
    burn_new_captions,
    caption_policy_status,
    merge_confirmed_cues,
    normalize_settings,
    normalize_source_captions,
    prepare_cues,
    public_status,
    write_srt,
)
from app.services.acoustic_tail import apply_acoustic_tails
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


def _clip_limit_view(plan: dict) -> dict:
    """给预览页的上限说明。cap 是用户这次给出的上限；window 是旧请求的系统窗口。"""
    mode = str(plan.get("limit_mode") or "window")
    if mode not in {"cap", "window"}:
        mode = "window"
    try:
        seconds = round(float(plan.get("max_seconds")), 1)
    except (TypeError, ValueError):
        seconds = None
    if mode == "cap":
        note = "这是单条上限，不是精确时长。成片收到完整句尾，通常更短。放不进时会失败，不会静默保留整段。"
    else:
        note = "这次没有单独设置上限，仍按原来的系统窗口挑选。新的剪辑页默认会带上 30 秒上限。"
    return {"mode": mode, "seconds": seconds, "note": note}


def produce_douyin(
    job_id: str,
    video_id: int,
    instruction: str = "",
    max_clips: int = 0,
    *,
    layout: str = "interview",
    chenze_style: bool = False,
    chenze_options: dict | None = None,
    text_enhance: dict | None = None,
    source_captions=None,
    confirmed_cues: list | None = None,
    max_seconds: float | None = None,
) -> None:
    video = fetch(int(video_id))
    source = resolve_media(video)
    meta = probe_source(source)
    settings = douyin_settings()
    title = str(video.get("title") or source.name)
    layout = "podcast" if layout == "podcast" else "interview"
    try:
        enhance = normalize_settings(text_enhance)
        caption_mode = normalize_source_captions(source_captions)
    except TextEnhanceError as exc:
        raise EngineError(exc.message, code=exc.code, action="检查旁注和样式后再生成。", status=400) from exc
    policy = caption_policy_status(caption_mode, enhance)
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
    asr_status = public_asr_status(last_asr_run())
    if asr_status.get("note"):
        update(job_id, log=str(asr_status["note"]))
    update(job_id, log=str(policy["note"]))
    if not segments:
        raise EngineError(
            "没有识别到口播",
            code="empty_transcript",
            action="确认原片有人声。纯音乐或静音没法抽成抖音口播。",
            status=422,
        )
    asked_cap, _asked_count = requested_limits(instruction)
    if max_seconds is not None or asked_cap is not None:
        before = [round(float(item.get("end") or 0), 2) for item in segments]
        try:
            segments = apply_acoustic_tails(source, segments, float(meta["duration"] or 0))
        except AIServiceError as exc:
            raise EngineError(
                str(exc),
                code="audio_tail",
                action="确认 ffmpeg 能读出原片音轨。有时长上限时不能把没说完的句尾交出去。",
                status=502,
            ) from exc
        after = [round(float(item.get("end") or 0), 2) for item in segments]
        if after != before:
            update(job_id, log="句尾已收到声音落下的位置。文件末尾的空白不算还在说话，也不会因此带进下一句。")
    report(job_id, 42, "plan", "按陈泽剪辑方式整理候选" if chenze_style else "挑选适合抖音的高光")
    try:
        plan = plan_intelligent_edit(
            segments,
            float(meta["duration"]),
            instruction=instruction.strip()[:400],
            max_clips=max_clips,
            max_seconds=max_seconds,
            chenze_style=bool(chenze_style),
            chenze_options=chenze_options or {},
            video=video,
            width=int(meta["width"]) if meta.get("width") else None,
            height=int(meta["height"]) if meta.get("height") else None,
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
    chenze = plan.get("chenze") if isinstance(plan.get("chenze"), dict) else None
    if not clips and chenze and chenze.get("applied") and chenze.get("review_candidates"):
        update(
            job_id,
            status="success",
            progress=100,
            stage="done",
            label="陈泽候选需人工复核",
            result={
                "video_id": int(video_id),
                "source_title": title,
                "source": plan["source"],
                "budget": plan["budget"],
                "duration": round(float(meta["duration"]), 2),
                "sentences": len(segments),
                "clip_limit": _clip_limit_view(plan),
                "warnings": list(plan.get("warnings") or []),
                "layout": layout,
                "pack_url": "",
                "shorts": [],
                "chenze": chenze,
                "text_enhance": public_status(enhance),
                "caption_policy": policy,
                "asr": asr_status,
            },
        )
        return
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
                "clip_limit": _clip_limit_view(plan),
                "warnings": [*(plan.get("warnings") or []), *failures],
                "layout": layout,
                "pack_url": pack_url,
                "shorts": list(shorts),
                "text_enhance": public_status(enhance),
                "caption_policy": policy,
                "asr": asr_status,
                **({"chenze": chenze} if chenze else {}),
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
        clip = apply_confirmed_cues(dict(clip), confirmed_cues)
        burn = burn_new_captions(caption_mode, enhance)
        render_enhance = dict(enhance) if enhance.get("enabled") else None
        if render_enhance is not None and not burn:
            render_enhance["captions"] = False
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
                captions=list(clip.get("captions") or []) if burn else [],
                out_w=int(settings["width"]),
                out_h=int(settings["height"]),
                threads=int(settings["threads"]),
                layout=layout,
                job_id=job_id,
                should_cancel=_check(job_id),
                enhance=render_enhance,
            )
        except EngineError as exc:
            if exc.code == "cancelled":
                raise
            failures.append(f"「{label}」{exc.message}")
            update(job_id, log=failures[-1])
            continue
        cover_ok = write_cover(folder / "video.mp4", folder / "cover.jpg", job_id=job_id, should_cancel=_check(job_id))
        review = _review_cues(clip, rendered)
        if not burn and clip.get("captions"):
            update(job_id, log=f"「{label}」{policy['note']}")
        elif not rendered["captions_burned"] and (clip.get("captions") or (enhance.get("enabled") and enhance.get("title_text"))):
            update(job_id, log=f"「{label}」字幕没烧进去，文案和字幕稿仍可下载。")
        if review:
            write_srt(review, folder / "captions.srt")
        kit_ok = False
        try:
            kit_ok = build_clip_zip(folder)
        except Exception as exc:  # noqa: BLE001 - 成片已写出，单条打包失败仍可预览
            update(job_id, log=f"「{label}」单条发布包没打成：{exc}")
        short = _public_short(job_id, index, clip, rendered, cover_ok, layout=layout, kit_ok=kit_ok)
        short["quote_cues"] = review
        short["asr_cues"] = _cue_rows(clip.get("asr_cues") or [])
        short["srt_url"] = f"/api/edit/douyin/{job_id}/shorts/{index}/srt" if review else ""
        shorts.append(short)
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
            "clip_limit": _clip_limit_view(plan),
            "warnings": warnings,
            "layout": layout,
            "pack_url": pack_url,
            "shorts": shorts,
            "text_enhance": public_status(enhance, applied=_collected_effects(shorts)),
            "caption_policy": policy,
            "asr": asr_status,
            **({"chenze": chenze} if chenze else {}),
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
        "review_status": clip.get("review_status") or "",
        "publication_approved": bool(clip.get("publication_approved")),
        "fit_label": clip.get("fit_label"),
        "end_basis": str(clip.get("end_basis") or ""),
        "quote_cues": list(rendered.get("quote_cues") or []),
        "text_effects": list(rendered.get("text_effects") or []),
        "title_text": str(rendered.get("title_text") or ""),
        "srt_url": f"/api/edit/douyin/{job_id}/shorts/{index}/srt" if rendered.get("quote_cues") else "",
    }


def _cue_rows(cues: list) -> list[dict]:
    rows = []
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        text = str(cue.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(cue.get("start"))
            end = float(cue.get("end"))
        except (TypeError, ValueError):
            continue
        row = {
            "start": start,
            "end": end,
            "text": text,
            "role": "quote",
            "origin": "user" if cue.get("origin") == "user" or cue.get("confirmed") else "asr",
            "confirmed": bool(cue.get("confirmed")),
            "review_state": "reviewed" if cue.get("confirmed") or cue.get("origin") == "user" else "unverified",
            "low_confidence": bool(cue.get("low_confidence")) and not cue.get("confirmed"),
        }
        if row["low_confidence"] and cue.get("review_reason"):
            row["review_reason"] = str(cue.get("review_reason"))
        rows.append(row)
    return rows


def _review_cues(clip: dict, rendered: dict) -> list[dict]:
    burned = bool(rendered.get("captions_burned"))
    if burned and rendered.get("quote_cues"):
        source = rendered.get("quote_cues") or []
        flags = list(clip.get("captions") or [])
    else:
        source = clip.get("captions") or []
        flags = source
    rows = _cue_rows(source)
    for index, row in enumerate(rows):
        flag = flags[index] if index < len(flags) and isinstance(flags[index], dict) else {}
        if flag.get("origin") == "user" or flag.get("confirmed"):
            row["origin"] = "user"
            row["confirmed"] = True
            row["review_state"] = "reviewed"
            row["low_confidence"] = False
            row.pop("review_reason", None)
        elif flag.get("low_confidence"):
            row["low_confidence"] = True
            if flag.get("review_reason"):
                row["review_reason"] = str(flag.get("review_reason"))
    return rows


def _collected_effects(shorts: list[dict]) -> list[str]:
    effects: list[str] = []
    for short in shorts:
        for name in short.get("text_effects") or []:
            if name not in effects:
                effects.append(name)
    return effects


def _job_or_raise(job_id: str) -> dict:
    from app.jobs import get

    job = get(job_id)
    if str(job.get("kind") or "") != "edit.douyin":
        raise EngineError("这不是抖音成片任务", code="job", status=404)
    return job


def short_file(job_id: str, index: int, *, cover: bool = False, kit: bool = False, srt: bool = False) -> Path:
    job = _job_or_raise(job_id)
    if job.get("status") not in {"success", "partial", "running"}:
        raise EngineError("成片还没完成", code="missing_file", action="等任务结束，或看失败原因。", status=404)
    shorts = ((job.get("result") or {}).get("shorts") or [])
    if not any(int(item.get("index") or 0) == int(index) for item in shorts):
        raise EngineError("没有这条成片", code="missing_file", status=404)
    root = (data_dir() / "douyin" / job["id"]).resolve()
    folder = (root / f"{int(index):02d}").resolve()
    folder.relative_to(root)
    name = "kit.zip" if kit else "captions.srt" if srt else "cover.jpg" if cover else "video.mp4"
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


def reburn_short(
    job_id: str,
    index: int,
    cues: list,
    title_text: str | None = None,
    source_captions=None,
) -> dict:
    """保存核对后的口播字幕。只有选择烧录时才写进画面，不改切点，也不加新音轨。"""
    from app.config import douyin_settings
    from app.jobs import update

    job = _job_or_raise(job_id)
    if job.get("status") not in {"success", "partial"}:
        raise EngineError("成片还没完成，不能改字幕", code="not_ready", action="等生成结束。", status=409)
    payload = dict(job.get("input") or {})
    try:
        enhance = normalize_settings(payload.get("text_enhance"))
        caption_mode = normalize_source_captions(
            source_captions if source_captions is not None else payload.get("source_captions")
        )
    except TextEnhanceError as exc:
        raise EngineError(exc.message, code=exc.code, status=400) from exc
    burn = burn_new_captions(caption_mode, enhance)
    result = dict(job.get("result") or {})
    shorts = [dict(item) for item in (result.get("shorts") or [])]
    short = next((item for item in shorts if int(item.get("index") or 0) == int(index)), None)
    if short is None:
        raise EngineError("没有这条成片", code="missing_file", status=404)
    if title_text is not None and enhance.get("enabled"):
        cleaned = str(title_text).replace("\x00", "").strip()
        if len(cleaned) > 40:
            raise EngineError("旁注请控制在 40 字以内", code="title", status=400)
        enhance["title_text"] = cleaned
        stored = dict(payload.get("text_enhance") or {})
        stored["title_text"] = cleaned
        payload["text_enhance"] = stored
    render_enhance = dict(enhance) if enhance.get("enabled") else None
    if render_enhance is not None:
        render_enhance["captions"] = bool(cues) and burn
    try:
        video_id = int(result.get("video_id") or payload.get("video_id") or 0)
    except (TypeError, ValueError):
        video_id = 0
    video = fetch(video_id)
    source = resolve_media(video)
    meta = probe_source(source)
    start = float(short.get("start") or 0)
    end = float(short.get("end") or 0)
    if end <= start:
        raise EngineError("这条成片没有有效切点", code="INVALID_INTERVAL", status=400)
    try:
        prepared = prepare_cues(list(cues or []), max(0.3, end - start))
    except TextEnhanceError as exc:
        raise EngineError(exc.message, code=exc.code, action="改字幕时间或文字后再保存。", status=400) from exc
    layout = short.get("layout") if short.get("layout") in {"interview", "podcast"} else result.get("layout")
    layout = "podcast" if layout == "podcast" else "interview"
    settings = douyin_settings()
    boxes = [] if layout == "podcast" else load_subject_boxes(video_id)
    if layout == "podcast":
        track = []
    else:
        in_boxes = [item for item in boxes if start - 1 <= float(item["time"]) <= end + 1]
        track = build_subject_track(start, end, [], in_boxes, [])
    folder = data_dir() / "douyin" / job["id"] / f"{int(index):02d}"
    folder.mkdir(parents=True, exist_ok=True)
    rendered = render_short(
        source,
        folder / "video.mp4",
        start=start,
        end=end,
        src_w=int(meta["width"]),
        src_h=int(meta["height"]),
        has_audio=bool(meta["has_audio"]),
        track=track,
        captions=[{
            "start": float(cue["start"]),
            "end": float(cue["end"]),
            "text": cue["text"],
        } for cue in prepared] if burn else [],
        out_w=int(settings["width"]),
        out_h=int(settings["height"]),
        threads=int(settings["threads"]),
        layout=layout,
        job_id=job_id,
        enhance=render_enhance,
    )
    review = [{
        "start": float(cue["start"]),
        "end": float(cue["end"]),
        "text": cue["text"],
        "role": "quote",
        "origin": "user",
        "confirmed": True,
        "review_state": "reviewed",
        "low_confidence": False,
    } for cue in prepared]
    if review:
        write_srt(review, folder / "captions.srt")
    elif (folder / "captions.srt").is_file():
        (folder / "captions.srt").unlink()
    write_cover(folder / "video.mp4", folder / "cover.jpg", job_id=job_id)
    short["quote_cues"] = review
    short["title_text"] = str(rendered.get("title_text") or short.get("title_text") or "")
    short["text_effects"] = list(rendered.get("text_effects") or [])
    short["captions_burned"] = bool(rendered.get("captions_burned"))
    short["srt_url"] = f"/api/edit/douyin/{job_id}/shorts/{int(index)}/srt" if review else ""
    result["shorts"] = shorts
    result["text_enhance"] = public_status(enhance, applied=_collected_effects(shorts))
    policy = caption_policy_status(caption_mode, enhance)
    result["caption_policy"] = policy
    if caption_mode != "legacy":
        payload["source_captions"] = caption_mode
    replacement = [{
        "start": round(start + float(cue["start"]), 2),
        "end": round(start + float(cue["end"]), 2),
        "text": cue["text"],
        "origin": "user",
        "confirmed": True,
    } for cue in review]
    payload["confirmed_cues"] = merge_confirmed_cues(payload.get("confirmed_cues"), start, end, replacement)
    update(job_id, input=payload, result=result)
    return {
        "short": short,
        "text_enhance": result["text_enhance"],
        "caption_policy": policy,
        "source_captions": caption_mode,
    }


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
