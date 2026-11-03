"""AI 解说：原生 API（替代 Streamlit iframe 与文件轮询）。"""

from __future__ import annotations

import json
import os
import subprocess

import requests
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import NARRATOAI_PYTHON, NARRATOAI_ROOT, NARRATO_PREVIEW_URL, NARRATO_TASKS_DIR
from app.services import legacy_tasks, narration, narration_preview
from app.services.narration import EngineUnavailable, NarrationError

router = APIRouter(prefix="/api/narrate", tags=["narrate"])


class CreateNarrationBody(BaseModel):
    video_id: int
    title: str = ""
    content_type: str = "auto"
    mode: str = "auto"
    topic: str = ""
    instruction: str = ""
    tone: str = ""
    target_duration: int = Field(default=60, ge=5, le=1800)
    aspect: str = "auto"
    language: str = "中文"
    voice_name: str = "zh-CN-XiaoxiaoNeural"
    tts_engine: str = "edge_tts"
    voice_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    original_volume: float = Field(default=0.35, ge=0, le=1.5)
    subtitles: bool = True
    font_name: str = "DroidSansFallbackFull.ttf"
    font_size: int = Field(default=54, ge=20, le=160)
    text_fore_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = Field(default=1.5, ge=0, le=10)
    subtitle_position: str = "bottom"
    custom_position: float = Field(default=70, ge=0, le=100)
    subtitle_y_percent: float = Field(default=88, ge=0, le=99)
    subtitle_max_chars: int = Field(default=12, ge=6, le=30)
    subtitle_mask_enabled: bool = False
    subtitle_mask_y: float = Field(default=78, ge=0, le=99)
    subtitle_mask_height: float = Field(default=14, ge=2, le=60)
    subtitle_mask_blur: int = Field(default=18, ge=0, le=60)
    subtitle_mask_opacity: int = Field(default=82, ge=0, le=100)
    clip_mode: str = "timeline"
    coverage: str = "highlights"
    analysis_mode: str = "deep"
    output_mode: str = "narration"
    pause_between: bool = True
    pause_seconds: float = Field(default=2.0, ge=0.5, le=6.0)
    review_mode: bool = True
    tts_pronunciation: bool = True
    script_text: str = ""
    transcript_text: str = ""


@router.get("/health")
def narration_health():
    try:
        return {"available": True, **narration.health()}
    except NarrationError as exc:
        return {"available": False, "error": str(exc)}


@router.get("/capabilities")
def narration_capabilities():
    try:
        return narration.capabilities()
    except EngineUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except NarrationError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/tasks")
def get_narration_tasks(limit: int = 30):
    # 引擎不可用时服务层只降级单条同步，历史任务仍然可见。
    return narration.list_tasks(limit)


@router.post("/tasks")
def create_narration_task(body: CreateNarrationBody):
    try:
        return narration.create_task(body.video_id, body.model_dump())
    except EngineUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except NarrationError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/tasks/{task_id}")
def get_narration_task(task_id: str):
    try:
        return narration.get_task(task_id)
    except NarrationError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/tasks/{task_id}/cancel")
def cancel_narration_task(task_id: str):
    try:
        return narration.cancel_task(task_id)
    except NarrationError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/tasks/{task_id}/approve")
def approve_task(task_id: str):
    try:
        return narration.approve_task(task_id)
    except NarrationError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/tasks/{task_id}/preview")
def preview_task(task_id: str):
    try:
        task = narration.get_task(task_id, sync=False)
    except NarrationError as exc:
        raise HTTPException(404, str(exc)) from exc
    engine_task_id = str(task.get("engine_task_id") or task.get("id") or "")
    try:
        return narration_preview.build_preview(engine_task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "低清预览超时") from exc


@router.get("/tasks/{task_id}/preview.mp4")
def preview_file(task_id: str):
    try:
        task = narration.get_task(task_id, sync=False)
    except NarrationError as exc:
        raise HTTPException(404, str(exc)) from exc
    engine_task_id = str(task.get("engine_task_id") or task.get("id") or "")
    path = NARRATO_TASKS_DIR / engine_task_id / "preview.mp4"
    if not path.is_file():
        raise HTTPException(404, "预览尚未生成")
    return FileResponse(path, media_type="video/mp4")


@router.get("/tasks/{task_id}/plan")
def task_plan(task_id: str):
    try:
        return narration.task_plan(task_id)
    except NarrationError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/tasks/{task_id}/import")
def import_narration_task(task_id: str):
    try:
        return narration.retry_import(task_id)
    except EngineUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except NarrationError as exc:
        raise HTTPException(400, str(exc)) from exc


class ExportJianyingBody(BaseModel):
    draft_name: str = ""


@router.post("/tasks/{task_id}/jianying")
def export_jianying(task_id: str, body: ExportJianyingBody):
    try:
        task = narration.get_task(task_id, sync=False)
    except NarrationError as exc:
        raise HTTPException(404, str(exc)) from exc
    engine_task_id = str(task.get("engine_task_id") or task.get("id") or "")
    if not engine_task_id:
        raise HTTPException(400, "任务缺少引擎 ID")
    if not NARRATOAI_PYTHON.is_file():
        raise HTTPException(503, f"narratoai 虚拟环境不存在：{NARRATOAI_PYTHON}")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(NARRATOAI_ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    try:
        proc = subprocess.run(
            [
                str(NARRATOAI_PYTHON),
                "-m",
                "engine.jianying_export",
                "--task-id",
                engine_task_id,
                "--draft-name",
                body.draft_name.strip(),
            ],
            cwd=str(NARRATOAI_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "剪映导出超时") from exc
    result_path = NARRATO_TASKS_DIR / engine_task_id / "jianying_export.json"
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        detail = (proc.stderr or proc.stdout or "剪映导出失败").strip()[-1200:]
        raise HTTPException(400, detail) from None
    if not payload.get("ok"):
        raise HTTPException(400, str(payload.get("error") or "剪映导出失败"))
    return payload


@router.get("/legacy-tasks")
def list_legacy_tasks():
    return legacy_tasks.list_legacy()


@router.post("/legacy-tasks/{task_id}/import")
def import_legacy_task(task_id: str):
    try:
        video = legacy_tasks.import_legacy(task_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "video": video}


@router.get("/settings")
def get_narration_settings():
    try:
        return narration.settings()
    except EngineUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except NarrationError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.put("/settings")
def save_narration_settings(payload: dict):
    try:
        return narration.update_settings(payload)
    except EngineUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except NarrationError as exc:
        raise HTTPException(400, str(exc)) from exc


class SubtitlePreviewBody(BaseModel):
    text: str = "字幕样式预览示例"
    font_name: str = "DroidSansFallbackFull.ttf"
    font_size: int = Field(default=54, ge=20, le=160)
    text_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = Field(default=1.5, ge=0, le=10)
    orientation: str = "portrait"
    subtitle_position: str = "bottom"
    subtitle_y_percent: float = Field(default=88, ge=0, le=100)


@router.post("/subtitle-preview")
def subtitle_preview(body: SubtitlePreviewBody):
    """代理到独立字幕预览服务，返回 PNG，不把样式预览逻辑塞进主后端。"""
    form = {
        "text": body.text[:80],
        "font_name": body.font_name,
        "font_size": body.font_size,
        "text_color": body.text_color,
        "stroke_color": body.stroke_color,
        "stroke_width": body.stroke_width,
        "orientation": body.orientation if body.orientation in {"portrait", "landscape"} else "portrait",
        "subtitle_position": body.subtitle_position,
        "subtitle_y_percent": body.subtitle_y_percent,
    }
    try:
        response = requests.post(
            f"{NARRATO_PREVIEW_URL}/api/preview/style",
            data=form,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise HTTPException(503, f"字幕预览服务不可用：{exc}") from exc
    if response.status_code != 200 or not response.content:
        detail = response.text[:200] if response.text else f"HTTP {response.status_code}"
        raise HTTPException(502, f"字幕预览失败：{detail}")
    return Response(content=response.content, media_type=response.headers.get("content-type", "image/png"))

