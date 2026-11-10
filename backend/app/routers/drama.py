"""AI 短剧 API：小说一键生成多集短剧，断点续跑、产物文件访问。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services import ai_drama

router = APIRouter(prefix="/api/drama", tags=["drama"])

ASPECT_RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}


class DramaOptions(BaseModel):
    episodes_count: int = Field(default=2, ge=1, le=10)
    quality_threshold: int = Field(default=70, ge=1, le=100)
    aspect_ratio: str = "9:16"
    style: str = "realistic"
    burn_subtitles: bool = False
    storyboard_frames: int = Field(default=3, ge=1, le=4)


class DramaBody(DramaOptions):
    novel: str
    title: str = ""


class ShotUpdateBody(BaseModel):
    prompt: str | None = None
    seconds: int | None = Field(default=None, ge=4, le=12)
    reference_images: list[str] | None = None


class ShotRetryBody(BaseModel):
    stage: Literal["image", "video", "all", "board"] = "all"


def _error(exc: ai_drama.DramaError) -> HTTPException:
    text = str(exc)
    if "没有配置 AI API Key" in text:
        return HTTPException(status_code=409, detail={
            "code": "ai_keys_missing",
            "message": "没有配置可用的 AI Key，无法生成短剧。",
            "action": "在服务器环境变量写入 VIDEO_HUB_AI_API_KEYS 后重启后端。",
        })
    if "首帧尚未通过质量门" in text:
        return HTTPException(status_code=409, detail={"code": "shot_image_required", "message": text})
    return HTTPException(status_code=400, detail={"code": "drama_failed", "message": text})


def _shot_response(call):
    try:
        return call()
    except ai_drama.DramaError as exc:
        raise _error(exc) from exc


@router.get("/projects")
def list_projects():
    return ai_drama.list_projects()


@router.post("/projects")
def create_project(body: DramaBody):
    options = body.model_dump(exclude={"novel", "title"})
    if options["aspect_ratio"] not in ASPECT_RATIOS:
        options["aspect_ratio"] = "9:16"
    if options["style"] not in ai_drama.STYLES:
        options["style"] = "realistic"
    try:
        return ai_drama.create_project(body.novel, body.title, options)
    except ai_drama.DramaError as exc:
        raise _error(exc) from exc


@router.get("/projects/{project_id}")
def project_detail(project_id: str):
    try:
        return ai_drama.project_detail(project_id)
    except ai_drama.DramaError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/projects/{project_id}/resume")
def resume_project(project_id: str):
    try:
        return ai_drama.resume_project(project_id)
    except ai_drama.DramaError as exc:
        raise _error(exc) from exc


@router.post("/projects/{project_id}/start")
def start_generation(project_id: str):
    try:
        return ai_drama.start_generation(project_id)
    except ai_drama.DramaError as exc:
        raise _error(exc) from exc


@router.post("/projects/{project_id}/cancel")
def cancel_project(project_id: str):
    try:
        return ai_drama.cancel_project(project_id)
    except ai_drama.DramaError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/projects/{project_id}/shots/{shot_id}")
def update_shot(project_id: str, shot_id: str, body: ShotUpdateBody):
    return _shot_response(lambda: ai_drama.update_shot(
        project_id, shot_id, **body.model_dump(exclude_none=True)
    ))


@router.post("/projects/{project_id}/shots/{shot_id}/retry")
def retry_shot(project_id: str, shot_id: str, body: ShotRetryBody):
    return _shot_response(lambda: ai_drama.retry_shot(project_id, shot_id, body.stage))


@router.post("/projects/{project_id}/shots/{shot_id}/cancel")
def cancel_shot(project_id: str, shot_id: str):
    return _shot_response(lambda: ai_drama.cancel_shot(project_id, shot_id))


@router.post("/projects/{project_id}/storyboard")
def generate_storyboard(project_id: str):
    return _shot_response(lambda: ai_drama.generate_storyboard(project_id))


@router.post("/projects/{project_id}/shots/{shot_id}/storyboard")
def generate_shot_storyboard(project_id: str, shot_id: str):
    return _shot_response(lambda: ai_drama.retry_shot(project_id, shot_id, "board"))


@router.delete("/projects/{project_id}")
def delete_project(project_id: str):
    try:
        ai_drama.delete_project(project_id)
        return {"ok": True}
    except ai_drama.DramaError as exc:
        raise _error(exc) from exc


@router.get("/projects/{project_id}/files/{name}")
def project_file(project_id: str, name: str):
    try:
        return FileResponse(ai_drama.asset_path(project_id, name))
    except ai_drama.DramaError as exc:
        raise HTTPException(404, str(exc)) from exc
