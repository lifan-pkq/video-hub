"""智能剪辑：分析切点、模型精排、按保留段导出。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from engine.schemas import Suggestion
from app.jobs import create, submit
from app.services.edit_flow import analyze_videos, export_clips, plan_suggestions

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
    video_id: int
    start: float = Field(ge=0)
    end: float = Field(gt=0)


class ExportBody(BaseModel):
    title: str = "剪辑成片"
    clips: list[ExportClip] = Field(min_length=1, max_length=40)


@router.post("/analyze")
def analyze(body: AnalyzeBody):
    job = create("edit.analyze", "分析切点")

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


@router.post("/export")
def export(body: ExportBody):
    job = create("edit.export", "导出成片")
    clips = [item.model_dump() for item in body.clips]
    title = body.title.strip()[:80] or "剪辑成片"
    return submit(job, lambda job_id: export_clips(job_id, title, clips))


@router.post("/concat")
def concat(body: ExportBody):
    """旧路径别名。新页面走 /export。"""
    return export(body)
