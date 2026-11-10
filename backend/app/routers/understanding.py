"""素材理解时间线与素材卡 API。"""

from fastapi import APIRouter, HTTPException

from app.services import understanding

router = APIRouter(prefix="/api/understanding", tags=["understanding"])


@router.get("/{video_id}")
def get_video_understanding(video_id: int):
    try:
        return understanding.get_analysis(video_id)
    except understanding.UnderstandingError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{video_id}")
def analyze_video_understanding(video_id: int):
    try:
        return understanding.analyze_video(video_id)
    except understanding.UnderstandingError as exc:
        raise HTTPException(400, str(exc)) from exc
