"""视频转动图：素材库视频或本地上传，后台任务导出 GIF / 动态 WebP。"""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse

from engine.errors import EngineError
from app.config import MAX_UPLOAD_MB
from app.services import motion
from app.services.videos import data_dir

router = APIRouter(prefix="/api/motion", tags=["motion"])

_ALLOWED = {".mp4", ".mov", ".webm", ".mkv", ".flv", ".avi"}


@router.post("")
def create_from_library(body: dict):
    if not isinstance(body, dict):
        raise EngineError("请求格式不对", code="input", action="用 JSON 提交参数。", status=400)
    if body.get("video_id") in (None, ""):
        raise EngineError("要指定素材库里的视频", code="input", action="选择一条视频，或改用上传。", status=400)
    return motion.start(body)


@router.post("/upload")
async def create_from_upload(
    file: UploadFile = File(...),
    start: str = Form(""),
    end: str = Form(""),
    duration: str = Form(""),
    width: str = Form("480"),
    fps: str = Form("12"),
    format: str = Form("gif"),  # noqa: A002 - 与请求字段同名
    loop: str = Form("0"),
    colors: str = Form("256"),
    dither: str = Form("true"),
    quality: str = Form("75"),
    ingest: str = Form("false"),
):
    # 只保留文件名，丢掉上传名里的目录和 ..。
    original = Path(file.filename or "video.mp4").name
    suffix = Path(original).suffix.lower()
    if suffix not in _ALLOWED:
        raise EngineError(
            "不支持的视频格式",
            code="format",
            action="请上传 mp4、mov、webm、mkv、flv 或 avi。",
            status=400,
        )
    tmp_dir = data_dir() / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp = tmp_dir / f"motion-{uuid.uuid4().hex}{suffix}"
    size = 0
    try:
        with tmp.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_MB * 1024 * 1024:
                    raise EngineError(
                        f"文件超过大小上限（{MAX_UPLOAD_MB}MB）",
                        code="upload",
                        action="换一个更短的视频，或先截好再上传。",
                        status=413,
                    )
                handle.write(chunk)
        if size <= 0:
            raise EngineError("上传的文件是空的", code="upload", action="重新选择视频文件。", status=400)
        raw = {
            "start": start,
            "end": end,
            "duration": duration,
            "width": width,
            "fps": fps,
            "format": format,
            "loop": loop,
            "colors": colors,
            "dither": dither,
            "quality": quality,
            "ingest": ingest,
        }
        title = Path(original).stem[:40] or "上传视频"
        return motion.start(raw, source=tmp, source_title=title)
    finally:
        tmp.unlink(missing_ok=True)
        await file.close()


@router.get("/{job_id}/file")
def download_motion(job_id: str):
    path, media_type, filename = motion.output_file(job_id)
    return FileResponse(path, media_type=media_type, filename=filename)


@router.post("/{job_id}/ingest")
def ingest_motion(job_id: str):
    return motion.ingest_job(job_id)
