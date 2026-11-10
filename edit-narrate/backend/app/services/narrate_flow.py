"""Four visible narration artifacts: transcript, frames, script, render."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from engine.asr import transcribe
from engine.errors import EngineError
from engine.frames import extract_frames, pick_times
from engine.media import probe
from engine.render import render_task
from engine.scripting import draft_beats, normalize_beats, skeleton_beats
from engine.signals import detect
from engine.schemas import VoiceSettings
from app.jobs import cancelled, report, update
from app.services.videos import data_dir, fetch, ingest, resolve_media


def tasks_root() -> Path:
    path = data_dir() / "narrate"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _check(job_id: str):
    return lambda: cancelled(job_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _valid_id(task_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{12}", task_id or ""):
        raise EngineError("解说任务编号不合法", code="not_found", status=404)
    return task_id


def task_dir(task_id: str) -> Path:
    return tasks_root() / _valid_id(task_id)


def load_task(task_id: str) -> dict:
    path = task_dir(task_id) / "task.json"
    if not path.is_file():
        raise EngineError("解说任务不存在", code="not_found", action="从素材重新开始理解。", status=404)
    return json.loads(path.read_text(encoding="utf-8"))


def save_task(task: dict) -> dict:
    directory = task_dir(task["id"])
    directory.mkdir(parents=True, exist_ok=True)
    task["updated_at"] = _now()
    target = directory / "task.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)
    return task


def list_tasks(limit: int = 20) -> list[dict]:
    rows = []
    for directory in tasks_root().iterdir():
        path = directory / "task.json"
        if path.is_file():
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return rows[: max(1, min(limit, 50))]


def understand(job_id: str, video_id: int, max_frames: int) -> None:
    video = fetch(video_id)
    path = resolve_media(video)
    meta = probe(path)
    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "video_id": video_id,
        "title": video.get("title") or path.name,
        "video_path": str(path),
        "duration": meta["duration"],
        "width": meta["width"],
        "height": meta["height"],
        "transcript": {"source": "none", "warning": "", "segments": []},
        "frames": [],
        "script": None,
        "voice": VoiceSettings().model_dump(),
        "output": None,
        "step": "understand",
        "created_at": _now(),
    }
    save_task(task)
    report(job_id, 8, "asr", "转写口播")
    asr_error = None
    try:
        task["transcript"] = transcribe(
            path,
            source_platform=str(video.get("source_platform") or ""),
            job_id=job_id,
            should_cancel=_check(job_id),
        )
    except EngineError as exc:
        asr_error = exc
        task["transcript"] = {"source": "none", "warning": exc.message, "segments": [], "error": exc.as_dict()}
        update(job_id, log=exc.message)
    report(job_id, 45, "frames", "按镜头抽帧")
    try:
        signals = detect(path, kinds=("scene",), job_id=job_id, should_cancel=_check(job_id))
        scenes = signals["scenes"]
    except EngineError as exc:
        scenes = []
        update(job_id, log=f"镜头检测失败，改为均匀抽帧：{exc.message}")
    times = pick_times(float(meta["duration"]), scenes, max_frames)
    frames = extract_frames(
        path,
        task_dir(task_id) / "frames",
        times,
        job_id=job_id,
        should_cancel=_check(job_id),
        caption=True,
    )
    task["frames"] = frames
    task["step"] = "understand"
    save_task(task)
    caption_errors = [frame["caption_error"] for frame in frames if frame.get("caption_error")]
    warnings = []
    if task["transcript"].get("warning"):
        warnings.append(task["transcript"]["warning"])
    if caption_errors:
        warnings.append(caption_errors[0] if len(set(caption_errors)) == 1 else f"{len(caption_errors)} 帧没有画面说明")
    task["warnings"] = warnings
    save_task(task)
    if asr_error and not task["transcript"]["segments"]:
        update(
            job_id,
            status="partial" if frames else "failed",
            progress=100,
            stage="understand",
            label="理解未完成",
            error=asr_error.as_dict(),
            result={"task_id": task_id},
        )
        return
    update(job_id, status="success", progress=100, stage="done", label="理解完成", result={"task_id": task_id, "warnings": warnings})


def write_transcript(task_id: str, segments: list[dict]) -> dict:
    task = load_task(task_id)
    duration = float(task.get("duration") or 0)
    cleaned = []
    for index, segment in enumerate(segments, start=1):
        text = str(segment.get("text") or "").strip()
        try:
            start = float(segment.get("start"))
            end = float(segment.get("end"))
        except (TypeError, ValueError) as exc:
            raise EngineError(f"第 {index} 句时间不是数字", code="transcript", action="开始和结束都填秒数。", status=422) from exc
        if not text:
            raise EngineError(f"第 {index} 句是空的", code="transcript", action="删掉空行，或写上这句说了什么。", status=422)
        if end <= start:
            raise EngineError(f"第 {index} 句结束时间必须晚于开始", code="transcript", status=422)
        if duration and end > duration + 0.4:
            raise EngineError(
                f"第 {index} 句超出片长 {duration:.1f}s",
                code="transcript",
                action="把结束时间改回视频时长以内。",
                status=422,
            )
        cleaned.append({"id": str(segment.get("id") or f"t{index}"), "start": round(start, 2), "end": round(end, 2), "text": text[:400]})
    task["transcript"] = {
        "source": "edited",
        "warning": task.get("transcript", {}).get("warning") or "",
        "segments": cleaned,
    }
    task["step"] = "understand"
    return save_task(task)


def write_script(job_id: str, task_id: str, *, mode: str, instruction: str, tone: str, target_duration: float, language: str) -> None:
    task = load_task(task_id)
    segments = task.get("transcript", {}).get("segments") or []
    duration = float(task.get("duration") or 0)
    report(job_id, 30, "script", "整理解说段" if mode == "skeleton" else "撰写解说稿")
    if mode == "skeleton":
        beats, warnings = skeleton_beats(segments, duration, target_duration)
    elif mode == "llm":
        beats, warnings = draft_beats(
            segments=segments,
            frames=task.get("frames") or [],
            duration=duration,
            instruction=instruction,
            tone=tone,
            target=target_duration,
            language=language,
        )
    else:
        raise EngineError("未知文稿模式", code="script", action="使用 llm 或 skeleton。", status=400)
    task["script"] = {"beats": beats, "warnings": warnings, "mode": mode, "instruction": instruction, "tone": tone, "target_duration": target_duration}
    task["step"] = "script"
    save_task(task)
    update(job_id, status="success", progress=100, stage="done", label="文稿已生成", result={"task_id": task_id, "warnings": warnings})


def save_script(task_id: str, beats: list[dict], voice: dict) -> dict:
    task = load_task(task_id)
    normalized, warnings = normalize_beats(beats, float(task.get("duration") or 0))
    settings = VoiceSettings.model_validate(voice or task.get("voice") or {})
    previous = (task.get("script") or {}).get("warnings") or []
    task["script"] = {
        **(task.get("script") or {}),
        "beats": normalized,
        "warnings": [*previous, *warnings],
    }
    task["voice"] = settings.model_dump()
    task["step"] = "review"
    return save_task(task)


def render(job_id: str, task_id: str) -> None:
    task = load_task(task_id)
    beats = (task.get("script") or {}).get("beats") or []
    if not beats:
        raise EngineError("还没有解说稿", code="script_empty", action="先生成文稿，或在审阅里写至少一段。", status=422)
    video = Path(task["video_path"])
    if not video.is_file():
        video = resolve_media(fetch(int(task["video_id"])))
    voice = VoiceSettings.model_validate(task.get("voice") or {}).model_dump()

    def progress(pct: float, stage: str, label: str) -> None:
        report(job_id, pct, stage, label)

    result = render_task(
        task_dir(task_id),
        video=video,
        beats=beats,
        voice=voice,
        duration=float(task.get("duration") or 0),
        width=int(task.get("width") or 0),
        height=int(task.get("height") or 0),
        progress=progress,
        job_id=job_id,
        should_cancel=_check(job_id),
    )
    task["output"] = result
    task["step"] = "render"
    save_task(task)
    update(job_id, status="success", progress=100, stage="done", label="成片已渲染", result={"task_id": task_id, "output": result})


def import_output(task_id: str) -> dict:
    task = load_task(task_id)
    output = task.get("output") or {}
    filename = output.get("file")
    if not filename:
        raise EngineError("还没有成片", code="missing_file", action="先完成渲染。", status=422)
    path = task_dir(task_id) / filename
    if not path.is_file():
        raise EngineError("成片文件丢失", code="missing_file", action="重新渲染。", status=404)
    video = ingest(path, f"解说 · {task.get('title') or '未命名'}", "narration")
    task["output"] = {**output, "video_id": video["id"]}
    save_task(task)
    return {"video": video, "task": task}
