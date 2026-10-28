"""多视频剪辑拼接：时间线片段 → ffmpeg 拼接渲染（后台任务）→ 入库视频库。

另含 AI 智能剪辑分析（whisper 转写 + LLM 标废片段）与拆分导出。
"""
import hashlib
import re
import subprocess
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import COVERS_DIR, VIDEOS_DIR, ensure_dirs
from app.db import add_video, get_video
from app.services.ai_edit import (
    AIResponseError,
    AIServiceError,
    DEFAULT_LLM_KINDS,
    KIND_DESCS,
    analyze_transcript,
    clean_intervals,
    keep_segments,
    transcribe,
)
from app.services.audio_analysis import detect_silence
from app.services.media import concat_clips, make_cover, probe_video
from app.services.tasks import task_manager

router = APIRouter(prefix="/api/edit", tags=["edit"])


class Clip(BaseModel):
    """一个时间线片段：截取 video_id 视频的 [start, end) 秒。"""

    video_id: int
    start: float
    end: float


class ConcatRequest(BaseModel):
    title: str = ""
    clips: list[Clip]
    """导出后拆分成几段（1 = 不拆分，>1 时成片再均分入库，每段标题带序号）"""
    split_parts: int = 1


def _unique_path(directory: Path, stem: str, suffix: str = ".mp4") -> Path:
    p = directory / f"{stem}{suffix}"
    n = 1
    while p.exists():
        p = directory / f"{stem}_{n}{suffix}"
        n += 1
    return p


def _ingest(task, path: Path, title: str) -> dict:
    """渲染产物探测元数据 + 封面 + 入库。"""
    meta = probe_video(path)
    cover_name = path.stem + ".jpg"
    cover = cover_name if make_cover(path, COVERS_DIR / cover_name) else None
    video = add_video(
        title=title,
        filename=path.name,
        size=path.stat().st_size,
        duration=meta.get("duration"),
        width=meta.get("width"),
        height=meta.get("height"),
        cover=cover,
        source_platform="edited",
        source_url=None,
    )
    dur = meta.get("duration")
    task.log(f"已入库：「{title}」{f'（{dur:.1f}s）' if dur else ''}")
    return video


@router.post("/concat")
def concat_videos(req: ConcatRequest):
    """创建拼接渲染任务（异步），立即返回 task_id，进度在任务中心查看。"""
    if not req.clips:
        raise HTTPException(400, "时间线为空")
    if len(req.clips) > 50:
        raise HTTPException(400, "片段过多（最多 50 个）")
    if not 1 <= req.split_parts <= 20:
        raise HTTPException(400, "拆分段数需在 1-20 之间")
    sources = []
    for c in req.clips:
        v = get_video(c.video_id)
        if not v:
            raise HTTPException(404, f"视频 {c.video_id} 不存在")
        path = VIDEOS_DIR / v["filename"]
        if not path.exists():
            raise HTTPException(404, f"视频「{v['title']}」文件已丢失")
        start, end = round(c.start, 2), round(c.end, 2)
        dur = v.get("duration")
        if start < 0 or end <= start:
            raise HTTPException(400, f"「{v['title']}」时间区间不合法（需 0 ≤ start < end）")
        if dur and end > dur + 0.5:
            raise HTTPException(400, f"「{v['title']}」结束时间超出视频时长（{dur:.1f}s）")
        sources.append({"path": path, "start": start, "end": end})

    title = re.sub(r'[\\/:*?"<>|#\n\r]', "_", req.title.strip())[:80] or "拼接视频"
    total = sum(s["end"] - s["start"] for s in sources)
    if req.split_parts > 1 and total / req.split_parts < 0.5:
        raise HTTPException(400, f"拆成 {req.split_parts} 段后每段不足 0.5s，减少段数")
    task_title = f"拼接 {len(sources)} 段 → {title}"
    if req.split_parts > 1:
        task_title += f"（再拆 {req.split_parts} 段）"
    task = task_manager.create("edit", task_title)
    task.log(f"共 {len(sources)} 个片段，预计总时长 {total:.1f}s")
    threading.Thread(target=_render, args=(task, sources, title, req.split_parts), daemon=True).start()
    return {"task_id": task.id}


def _render(task, sources: list[dict], title: str, split_parts: int = 1):
    ensure_dirs()
    final = _unique_path(VIDEOS_DIR, title)
    task.status = "running"
    try:
        concat_clips(
            sources,
            final,
            on_progress=lambda p: setattr(task, "progress", p * (0.7 if split_parts > 1 else 1.0)),
        )
    except Exception as e:
        task.log(f"渲染失败：{e}")
        task.finish("failed")
        return

    if split_parts <= 1:
        task.result["video"] = _ingest(task, final, title)
        task.progress = 100.0
        task.finish("success")
        return

    # 拆分成 N 段入库（重编码保证切点精确）；母片不留库
    meta = probe_video(final)
    total = meta.get("duration") or 0
    if total <= 0:
        task.result["video"] = _ingest(task, final, title)
        task.finish("success")
        return
    part_dur = total / split_parts
    videos = []
    for i in range(split_parts):
        a, b = i * part_dur, (total if i == split_parts - 1 else (i + 1) * part_dur)
        part = _unique_path(VIDEOS_DIR, f"{title}_第{i + 1}段")
        task.log(f"拆分第 {i + 1}/{split_parts} 段（{a:.1f}s-{b:.1f}s）…")
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-to", f"{b:.3f}", "-i", str(final),
             "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", str(part)],
            capture_output=True, timeout=1800,
        )
        if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
            part.unlink(missing_ok=True)
            task.log(f"第 {i + 1} 段拆分失败：{r.stderr.decode('utf-8', errors='replace')[-200:]}")
            task.finish("failed")
            return
        videos.append(_ingest(task, part, f"{title}（第{i + 1}/{split_parts}段）"))
        task.progress = 70 + (i + 1) / split_parts * 30
    final.unlink(missing_ok=True)
    task.result["videos"] = videos
    task.progress = 100.0
    task.finish("success")


class AnalyzeRequest(BaseModel):
    video_ids: list[int]
    """要识别的废片类型：intro 开头 / outro 结尾 / ad 广告 / silence 静音 / repeat 重复"""
    kinds: list[str] = ["intro", "outro", "ad"]
    """用户自定义剪辑要求（原样拼进 prompt）"""
    custom_prompt: str = ""


@router.post("/analyze")
def analyze_video(req: AnalyzeRequest):
    """AI 分析（本地静音/转写 + LLM 标废片段），支持多个视频，结果在 task.result。"""
    if not req.video_ids:
        raise HTTPException(400, "未指定视频")
    if len(req.video_ids) > 20:
        raise HTTPException(400, "一次最多分析 20 个视频")
    kinds = [k for k in req.kinds if k in KIND_DESCS] or list(DEFAULT_LLM_KINDS)
    items = []
    for vid in req.video_ids:
        v = get_video(vid)
        if not v:
            raise HTTPException(404, f"视频 {vid} 不存在")
        path = VIDEOS_DIR / v["filename"]
        if not path.exists():
            raise HTTPException(404, f"视频「{v['title']}」文件已丢失")
        items.append((v, path))
    names = "、".join(v["title"][:12] for v, _ in items)
    task = task_manager.create("edit", f"AI 分析 {len(items)} 个视频 → {names}")
    threading.Thread(target=_analyze, args=(task, items, kinds, req.custom_prompt), daemon=True).start()
    return {"task_id": task.id}


def _analyze(task, items: list[tuple[dict, Path]], kinds: list[str], custom_prompt: str):
    task.status = "running"
    analyses = []
    errors = []
    successful_steps = 0
    current_video_id = None
    try:
        for i, (video, path) in enumerate(items, 1):
            current_video_id = video["id"]
            task.log(f"[{i}/{len(items)}] 分析「{video['title']}」…")
            duration = video.get("duration") or (probe_video(path).get("duration") or 0)
            waste = []
            video_errors = []

            def fail(error_type: str, exc: Exception):
                error = {"video_id": video["id"], "type": error_type, "message": str(exc)}
                video_errors.append(error)
                errors.append(error)
                task.log(f"「{video['title']}」{error_type} 错误：{exc}")

            if "silence" in kinds:
                try:
                    waste.extend(detect_silence(path, log=task.log))
                    successful_steps += 1
                except Exception as exc:
                    fail("audio", exc)

            llm_kinds = [kind for kind in kinds if kind != "silence"]
            if llm_kinds:
                try:
                    segments = transcribe(path, log=task.log)
                except Exception as exc:
                    fail("transcription", exc)
                else:
                    try:
                        waste.extend(analyze_transcript(
                            segments, duration, kinds=llm_kinds,
                            custom_prompt=custom_prompt, log=task.log,
                        ))
                        successful_steps += 1
                    except AIResponseError as exc:
                        fail("parse", exc)
                    except AIServiceError as exc:
                        fail("ai", exc)
                    except Exception as exc:
                        fail("ai", exc)

            cleaned = clean_intervals(waste, duration, kinds=kinds)
            enriched = []
            for item in cleaned:
                source = item.get("source") or "llm"
                fingerprint = (
                    f"{video['id']}:{item['kind']}:{item['start']:.2f}:"
                    f"{item['end']:.2f}:{source}"
                ).encode("utf-8")
                enriched.append({
                    **item,
                    "id": "suggestion-" + hashlib.sha256(fingerprint).hexdigest()[:20],
                    "video_id": video["id"],
                    "source": source,
                })
            analysis = {
                "video_id": video["id"],
                "title": video["title"],
                "duration": duration,
                "waste": enriched,
                "keep": keep_segments(enriched, duration),
            }
            if video_errors:
                analysis["errors"] = video_errors
            analyses.append(analysis)

            if not enriched and not video_errors:
                task.log(f"「{video['title']}」未发现可剪区间")
            for item in enriched:
                task.log(
                    f"「{video['title']}」建议剪除 {item['start']:.1f}s-{item['end']:.1f}s"
                    f"（{item['kind']}：{item['reason']}）"
                )
            task.progress = i / len(items) * 100
    except Exception as exc:
        errors.append({"video_id": current_video_id, "type": "internal", "message": str(exc)})
        task.log(f"分析失败：{exc}")
    finally:
        task.result = {"analyses": analyses, "errors": errors}
        if not errors:
            task.finish("success")
        elif successful_steps:
            task.finish("partial")
        else:
            task.finish("failed")
