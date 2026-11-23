"""独立口播解说：选已有视频，审核短句文稿，再用原声渲染。"""
from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services.ai_edit import AIResponseError
from app.services import speech_script

router = APIRouter(prefix="/api/narrate", tags=["narrate"])


class CreateBody(BaseModel):
    video_id: int
    target_seconds: float = Field(default=30, ge=5, le=180)


class BeatBody(BaseModel):
    text: str = ""
    start: float = 0
    end: float = 0
    split_note: str = ""


class ReviewBody(BaseModel):
    beats: list[BeatBody] = Field(min_length=1, max_length=80)


def _fail(exc: AIResponseError):
    from fastapi import HTTPException

    raise HTTPException(422, str(exc)) from exc


@router.post("/tasks")
def create_task(body: CreateBody):
    try:
        return speech_script.create_draft(body.video_id, body.target_seconds)
    except AIResponseError as exc:
        _fail(exc)


@router.get("/tasks/{task_id}")
def read_task(task_id: str):
    from app.db import get_narration_task

    row = get_narration_task(task_id)
    if not row:
        from fastapi import HTTPException
        raise HTTPException(404, "找不到这篇解说")
    return row


@router.post("/tasks/{task_id}/script")
def save_script(task_id: str, body: ReviewBody):
    try:
        return speech_script.save_review(task_id, [item.model_dump() for item in body.beats])
    except AIResponseError as exc:
        _fail(exc)


@router.post("/tasks/{task_id}/render")
def render_task(task_id: str):
    try:
        return speech_script.render_original(task_id)
    except AIResponseError as exc:
        _fail(exc)


@router.get("/tasks/{task_id}/file")
def download_task(task_id: str):
    try:
        path = speech_script.output_file(task_id)
    except AIResponseError as exc:
        _fail(exc)
    return FileResponse(path, media_type="video/mp4", filename="narration.mp4")
