"""视频库：列表/详情/播放流（支持 Range）/封面上传导入/重命名/删除。"""
import re
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import COVERS_DIR, VIDEOS_DIR, ensure_dirs
from app.db import add_video, delete_video, get_video, list_videos, update_video
from app.services.media import make_cover, probe_video, range_stream

router = APIRouter(prefix="/api/videos", tags=["videos"])

# 上传允许的视频扩展名白名单
ALLOWED_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".flv", ".avi"}


def _serialize(v: dict) -> dict:
    return {
        **v,
        "file_url": f"/api/videos/{v['id']}/file",
        "cover_url": f"/api/videos/{v['id']}/cover" if v.get("cover") else None,
    }


@router.get("")
def get_videos():
    return [_serialize(v) for v in list_videos()]


@router.get("/{video_id}")
def get_video_detail(video_id: int):
    v = get_video(video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    return _serialize(v)


@router.get("/{video_id}/file")
def stream_video(video_id: int, request: Request):
    v = get_video(video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    path = VIDEOS_DIR / v["filename"]
    if not path.exists():
        raise HTTPException(404, "视频文件已丢失")
    return range_stream(path, request.headers.get("range"))


@router.get("/{video_id}/cover")
def get_cover(video_id: int):
    v = get_video(video_id)
    if not v or not v.get("cover"):
        raise HTTPException(404, "无封面")
    path = COVERS_DIR / v["cover"]
    if not path.exists():
        raise HTTPException(404, "封面文件已丢失")
    return FileResponse(path)


@router.post("/upload")
async def upload_video(file: UploadFile = File(...), title: str = ""):
    """从本地上传一个视频导入视频库。"""
    ensure_dirs()
    suffix = (Path(file.filename or "video.mp4").suffix or ".mp4").lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"不支持的文件类型（仅支持 {' '.join(sorted(ALLOWED_SUFFIXES))}）")
    safe = re.sub(r'[\\/:*?"<>|#\n\r]', "_", Path(file.filename or "video").stem)[:80] or "video"
    final = VIDEOS_DIR / f"{safe}{suffix}"
    n = 1
    while final.exists():
        final = VIDEOS_DIR / f"{safe}_{n}{suffix}"
        n += 1
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    try:
        with open(final, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"文件超过大小上限（{MAX_UPLOAD_MB}MB）")
                f.write(chunk)
    except Exception:
        final.unlink(missing_ok=True)  # 删除半成品文件
        raise
    meta = probe_video(final)
    cover_name = final.stem + ".jpg"
    cover = cover_name if make_cover(final, COVERS_DIR / cover_name) else None
    video = add_video(
        title=title.strip() or final.stem,
        filename=final.name,
        size=size,
        duration=meta.get("duration"),
        width=meta.get("width"),
        height=meta.get("height"),
        cover=cover,
        source_platform="local",
        source_url=None,
    )
    return _serialize(video)


@router.delete("/{video_id}")
def remove_video(video_id: int):
    v = get_video(video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    for folder, name in ((VIDEOS_DIR, v["filename"]), (COVERS_DIR, v.get("cover"))):
        if name:
            p = folder / name
            if p.exists():
                p.unlink()
    delete_video(video_id)
    return {"ok": True}


class RenameRequest(BaseModel):
    title: str


@router.patch("/{video_id}")
def rename_video(video_id: int, req: RenameRequest):
    """重命名视频标题（只改元数据，不动文件）。"""
    v = get_video(video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    if len(title) > 120:
        raise HTTPException(400, "标题过长（最长 120 字）")
    update_video(video_id, title=title)
    return _serialize(get_video(video_id))


class TrimRequest(BaseModel):
    """截取时间段（秒，支持小数）。"""

    start: float
    end: float


@router.post("/{video_id}/trim")
def trim_video(video_id: int, req: TrimRequest):
    """ffmpeg 无损截取（-c copy）生成新视频入库，原视频不动。"""
    import shutil
    import subprocess

    v = get_video(video_id)
    if not v:
        raise HTTPException(404, "视频不存在")
    src = VIDEOS_DIR / v["filename"]
    if not src.exists():
        raise HTTPException(404, "视频文件已丢失")
    if not shutil.which("ffmpeg"):
        raise HTTPException(500, "服务器未安装 ffmpeg")
    start, end = round(req.start, 2), round(req.end, 2)
    if start < 0 or end <= start:
        raise HTTPException(400, "时间区间不合法（需 0 ≤ start < end）")
    if v.get("duration") and end > v["duration"] + 0.5:
        raise HTTPException(400, f"结束时间超出视频时长（{v['duration']:.1f}s）")

    stem = f"{src.stem}_截取{int(start)}-{int(end)}"
    final = VIDEOS_DIR / f"{stem}.mp4"
    n = 1
    while final.exists():
        final = VIDEOS_DIR / f"{stem}_{n}.mp4"
        n += 1
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-to", str(end), "-i", str(src),
             "-c", "copy", "-avoid_negative_ts", "make_zero", str(final)],
            capture_output=True, timeout=300,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(500, "截取超时")
    if r.returncode != 0 or not final.exists() or final.stat().st_size == 0:
        if final.exists():
            final.unlink()
        raise HTTPException(500, "截取失败：" + r.stderr.decode("utf-8", errors="replace")[-300:])

    meta = probe_video(final)
    cover_name = final.stem + ".jpg"
    cover = cover_name if make_cover(final, COVERS_DIR / cover_name) else None
    video = add_video(
        title=f"{v['title']}（截取 {int(start)}s-{int(end)}s）",
        filename=final.name,
        size=final.stat().st_size,
        duration=meta.get("duration"),
        width=meta.get("width"),
        height=meta.get("height"),
        cover=cover,
        source_platform=v.get("source_platform"),
        source_url=v.get("source_url"),
    )
    return _serialize(video)
