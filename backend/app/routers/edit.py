"""智能剪辑：抖音一键成片，以及手动切点、精彩保留、按时间线导出。"""

from typing import Literal

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from engine.schemas import Suggestion
from app.jobs import create, submit
from app.services.douyin_flow import download_name, pack_file, produce_douyin, send_to_library, short_file
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


class DouyinProduceBody(BaseModel):
    video_id: int
    instruction: str = ""
    max_clips: int = Field(default=0, ge=0, le=10)


class DouyinLibraryBody(BaseModel):
    indexes: list[int] = Field(default_factory=list, max_length=10)


@router.post("/douyin")
def douyin_produce(body: DouyinProduceBody):
    """从素材库一条原片生成多条竖屏短视频。不发布，只给预览、下载和入库。"""
    fetch(body.video_id)
    instruction = body.instruction.strip()[:400]
    job = create(
        "edit.douyin",
        "抖音成片",
        {"video_id": body.video_id, "instruction": instruction, "max_clips": body.max_clips},
    )
    return submit(
        job,
        lambda job_id: produce_douyin(job_id, body.video_id, instruction, body.max_clips),
    )


@router.get("/douyin/{job_id}/shorts/{index}/file")
def douyin_short_video(job_id: str, index: int, download: int = 0):
    path = short_file(job_id, index)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=download_name(job_id, index) if download else path.name,
        content_disposition_type="attachment" if download else "inline",
    )


@router.get("/douyin/{job_id}/shorts/{index}/cover")
def douyin_short_cover(job_id: str, index: int):
    path = short_file(job_id, index, cover=True)
    return FileResponse(path, media_type="image/jpeg", filename=download_name(job_id, index, cover=True))


@router.get("/douyin/{job_id}/pack")
def douyin_pack(job_id: str):
    path = pack_file(job_id)
    return FileResponse(path, media_type="application/zip", filename="douyin-shorts.zip")


@router.post("/douyin/{job_id}/library")
def douyin_library(job_id: str, body: DouyinLibraryBody):
    return send_to_library(job_id, body.indexes)


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
