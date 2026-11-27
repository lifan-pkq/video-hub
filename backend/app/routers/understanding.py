"""素材理解时间线、素材卡和整段内容分析文档。"""

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse

from app.db import get_video
from app.services import understanding

router = APIRouter(prefix="/api/understanding", tags=["understanding"])


def _document_response(video_id: int, download: bool):
    video = get_video(video_id)
    if not video:
        raise HTTPException(404, "视频不存在")
    path = understanding.ensure_document(video_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "内容分析文档还没生成")
    if not download:
        return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")
    title = re.sub(r'[\\/:*?"<>|#\n\r]', "_", str(video.get("title") or path.stem))[:80] or "内容分析"
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=f"{title}-内容分析.md")


@router.get("/{video_id}/document")
def get_content_document(video_id: int, download: int = 0):
    return _document_response(video_id, bool(download))


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
