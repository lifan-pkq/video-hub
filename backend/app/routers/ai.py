"""AI 媒体与 Key 池 API。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.services import ai_images, ai_media
from app.services.ai_pool import AI_POOL

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ImageBody(BaseModel):
    prompt: str
    size: str = "2K"
    ratio: str = "16:9"
    images: list[str] = []


class SaveImageBody(BaseModel):
    name: str
    title: str = ""
    prompt: str = ""


class SaveImageBody(BaseModel):
    name: str
    title: str = ""
    prompt: str = ""


class VideoBody(BaseModel):
    prompt: str
    seconds: int = Field(default=5, ge=4, le=12)
    aspect_ratio: str = "16:9"
    first_frame: str = ""
    last_frame: str = ""
    images: list[str] = []
    save_to_library: bool = True
    first_frame: str = ""
    last_frame: str = ""
    images: list[str] = []
    save_to_library: bool = True


@router.get("/pool")
def pool_status():
    return AI_POOL.status()


@router.get("/images")
def list_images():
    return ai_images.list_images()


@router.post("/images/save")
def save_generated_image(body: SaveImageBody):
    try:
        return ai_images.save_generated(body.name, body.title, body.prompt)
    except ai_images.AIImageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/images")
async def upload_library_image(file: UploadFile = File(...), title: str = Form("")):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "只支持图片")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "图片不能超过 20MB")
    try:
        return ai_images.upload(data, file.filename or "image.png", title)
    except OSError as exc:
        raise HTTPException(500, f"保存图片失败：{exc}") from exc


@router.delete("/images/{image_id}")
def delete_library_image(image_id: int):
    ai_images.remove(image_id)
    return {"ok": True}


@router.get("/images/{image_id}/file")
def library_image_file(image_id: int):
    try:
        return FileResponse(ai_images.path_for(image_id))
    except ai_images.AIImageError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/images/{image_id}/download")
def library_image_download(image_id: int):
    try:
        path = ai_images.path_for(image_id)
    except ai_images.AIImageError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=path.name)


@router.post("/media/references")
async def upload_reference(file: UploadFile = File(...)):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "只支持图片参考")
    data = await file.read()
    if len(data) > 12 * 1024 * 1024:
        raise HTTPException(400, "参考图不能超过 12MB")
    try:
        return ai_media.save_reference(data, file.filename or "reference.png")
    except OSError as exc:
        raise HTTPException(500, f"保存参考图失败：{exc}") from exc


@router.get("/media/references/{name}")
def get_reference(name: str):
    safe = Path(name).name
    if safe != name or not safe.startswith("ref_"):
        raise HTTPException(404, "参考图不存在")
    path = ai_media.GENERATED_REFERENCE_DIR / safe
    if not path.is_file():
        raise HTTPException(404, "参考图不存在")
    return FileResponse(path)


@router.post("/media/images")
def generate_image(body: ImageBody):
    try:
        return ai_media.generate_image(body.prompt, body.size, body.ratio, body.images)
    except ai_media.MediaAIError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/media/images/{name}")
def get_generated_image(name: str):
    safe = Path(name).name
    if safe != name or not safe.startswith("ai_"):
        raise HTTPException(404, "图片不存在")
    path = ai_media.GENERATED_IMAGE_DIR / safe
    if not path.is_file():
        raise HTTPException(404, "图片不存在")
    return FileResponse(path, media_type="image/png")


@router.post("/media/videos")
def generate_video(body: VideoBody):
    try:
        return ai_media.start_video_generation(
            body.prompt,
            body.seconds,
            body.aspect_ratio,
            first_frame=body.first_frame,
            last_frame=body.last_frame,
            images=body.images,
            save_to_library=body.save_to_library,
        )
    except ai_media.MediaAIError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/media/videos/{name}")
def get_generated_video(name: str):
    safe = Path(name).name
    if safe != name or not safe.startswith("ai_"):
        raise HTTPException(404, "视频不存在")
    path = ai_media.GENERATED_VIDEO_DIR / safe
    if not path.is_file():
        raise HTTPException(404, "视频不存在")
    return FileResponse(path, media_type="video/mp4")
