"""智能剪辑：分析切点、精彩保留、模型精排、按时间线导出。"""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from engine.schemas import Suggestion
from app.jobs import create, submit
from app.services.edit_flow import analyze_videos, build_highlight, export_clips, plan_suggestions
from app.services.videos import fetch

router = APIRouter(prefix="/api/edit", tags=["edit"])


class AnalyzeBody(BaseModel):
    video_ids: list[int] = Field(min_length=1, max_length=12)
    silence_db: float = -32
    min_silence: float = Field(default=0.45, ge=0.2, le=5)
    min_black: float = Field(default=0.3, ge=0.15, le=5)
    scene_threshold: float = Field(default=0.3, ge=0.05, le=0.8)


class PlanBody(BaseModel):
    video_id: int
    suggestions: list[Suggestion]
    instruction: str = ""
    use_llm: bool = False


class ExportClip(BaseModel):
    video_id: int = 0
    start: float = Field(default=0, ge=0)
    end: float = Field(default=0, ge=0)
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    freeze: float = Field(default=0.0, ge=0, le=30)
    kind: Literal["video", "image"] = "video"
    image_id: int = 0
    image_name: str = ""
    video_name: str = ""
    overlay: bool = False
    title: str = ""

    model_config = {"extra": "ignore"}


class ExportBody(BaseModel):
    title: str = "剪辑成片"
    clips: list[ExportClip] = Field(min_length=1, max_length=40)


class HighlightSource(BaseModel):
    video_id: int
    duration: float = 0
    title: str = ""
    segments: list[dict] = Field(default_factory=list)
    scenes: list = Field(default_factory=list)
    silence: list[dict] = Field(default_factory=list)
    black: list[dict] = Field(default_factory=list)
    frames: list[dict] = Field(default_factory=list)
    suggestions: list[dict] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class HighlightBody(BaseModel):
    videos: list[HighlightSource] = Field(min_length=1, max_length=12)
    instruction: str = ""
    use_llm: bool = True


@router.post("/analyze")
def analyze(body: AnalyzeBody):
    for video_id in body.video_ids:
        fetch(video_id)
    job = create("edit.analyze", "分析切点", {"video_ids": list(body.video_ids)})

    def run(job_id: str) -> None:
        analyze_videos(
            job_id,
            body.video_ids,
            silence_db=body.silence_db,
            min_silence=body.min_silence,
            min_black=body.min_black,
            scene_threshold=body.scene_threshold,
        )

    return submit(job, run)


@router.post("/plan")
def plan(body: PlanBody):
    return plan_suggestions(
        body.video_id,
        [item.model_dump() for item in body.suggestions],
        instruction=body.instruction,
        use_llm=body.use_llm,
    )


@router.post("/highlight")
def highlight(body: HighlightBody):
    for item in body.videos:
        fetch(item.video_id)
    return build_highlight(
        [item.model_dump() for item in body.videos],
        instruction=body.instruction,
        use_llm=body.use_llm,
    )


@router.post("/export")
def export(body: ExportBody):
    clips = [item.model_dump() for item in body.clips]
    title = body.title.strip()[:80] or "剪辑成片"
    for clip in clips:
        video_id = int(clip.get("video_id") or 0)
        if clip.get("kind") == "image" or video_id <= 0:
            continue
        fetch(video_id)
    job = create("edit.export", "导出成片", {"title": title, "clips": clips})
    return submit(job, lambda job_id: export_clips(job_id, title, clips))


@router.post("/concat")
def concat(body: ExportBody):
    """旧路径别名。新页面走 /export。"""
    return export(body)
