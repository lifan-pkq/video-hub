"""直播录制：开始/停止/列表/改名/删除/边录边看预览。录制完成自动入库视频库。"""
import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import COVERS_DIR
from app.db import delete_recording, get_recording, list_recordings, update_recording
from app.services import recorder
from app.services.media import range_stream

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


class RecordRequest(BaseModel):
    url: str
    title: str = ""
    quality: str = "best"   # best/high/medium/low
    fps: int = 0            # 0 = 原始帧率


class RenameRecordingRequest(BaseModel):
    title: str


def _enrich(rec: dict) -> dict:
    """录制中的任务实时补充文件大小和已录时长。"""
    try:
        rec["segments"] = json.loads(rec.get("segments_json") or "[]")
    except (TypeError, ValueError):
        rec["segments"] = []
    if rec["status"] == "recording":
        rec["size"] = recorder._file_size(rec.get("filename"))
        started_at = rec.get("started_at")
        rec["starting"] = not bool(started_at)
        try:
            from datetime import datetime
            started = datetime.fromisoformat(started_at).timestamp() if started_at else None
            rec["elapsed"] = max(0, int(time.time() - started)) if started else 0
        except Exception:
            rec["elapsed"] = 0
    else:
        rec["starting"] = False
    return rec


@router.post("")
async def start(req: RecordRequest):
    if not req.url.strip():
        raise HTTPException(400, detail={
            "code": "url_missing",
            "message": "直播间或媒体流链接不能为空。",
            "action": "粘贴直播间、m3u8 或 flv 地址后再开始。",
        })
    if req.quality not in recorder.QUALITY_LEVELS:
        raise HTTPException(400, detail={
            "code": "quality_invalid",
            "message": "画质档位不合法。",
            "action": "请选择原画、高清、标清或流畅。",
        })
    if req.fps not in (0, 24, 30, 60):
        raise HTTPException(400, detail={
            "code": "fps_invalid",
            "message": "帧率不合法。",
            "action": "请选择原始帧率、24、30 或 60。",
        })
    try:
        rec = await asyncio.to_thread(
            recorder.start_recording, req.url, req.title.strip(), req.quality, req.fps)
    except recorder.LiveOfflineError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        raise HTTPException(400, f"录制启动失败: {e}")
    return _enrich(rec)


@router.get("")
def list_all():
    return [_enrich(r) for r in list_recordings()]


@router.get("/{rec_id}")
def get_one(rec_id: int):
    rec = get_recording(rec_id)
    if not rec:
        raise HTTPException(404, "录制任务不存在")
    return _enrich(rec)


@router.get("/{rec_id}/preview")
def preview(rec_id: int, request: Request):
    """边录边看：录制文件是 fMP4，录制中也能被浏览器播放（支持 Range）。"""
    rec = get_recording(rec_id)
    if not rec:
        raise HTTPException(404, "录制任务不存在")
    path = recorder.recording_file_path(rec)
    if not path:
        raise HTTPException(404, "录制文件不存在")
    return range_stream(path, request.headers.get("range"))


@router.patch("/{rec_id}")
def rename(rec_id: int, req: RenameRecordingRequest):
    """改录制标题；已入库的同步改视频库里的标题。"""
    rec = get_recording(rec_id)
    if not rec:
        raise HTTPException(404, "录制任务不存在")
    title = req.title.strip()
    if not title:
        raise HTTPException(400, "标题不能为空")
    update_recording(rec_id, title=title)
    if rec.get("video_id"):
        from app.db import update_video
        update_video(rec["video_id"], title=title)
    return get_recording(rec_id)


@router.post("/{rec_id}/stop")
def stop(rec_id: int):
    rec = get_recording(rec_id)
    if not rec:
        raise HTTPException(404, "录制任务不存在")
    if rec["status"] != "recording":
        raise HTTPException(400, "该任务不在录制中")
    recorder.stop_recording(rec_id)
    return get_recording(rec_id)


@router.delete("/{rec_id}")
def remove(rec_id: int, delete_file: bool = False):
    rec = get_recording(rec_id)
    if not rec:
        raise HTTPException(404, "录制任务不存在")
    if rec["status"] == "recording":
        recorder.stop_recording(rec_id)
        # 停止后文件名会从分段模板改成合并后的那一条，用新记录才能删到库里的文件
        rec = get_recording(rec_id) or rec
    if delete_file:
        for path in recorder._recording_paths(rec):
            try:
                path.unlink()
            except Exception:
                pass
        # 文件删除后，视频库里那条也没了（若已入库）
        if rec.get("video_id"):
            from app.db import delete_video, get_video
            v = get_video(rec["video_id"])
            if v:
                if v.get("cover"):
                    (COVERS_DIR / v["cover"]).unlink(missing_ok=True)
                delete_video(rec["video_id"])
    delete_recording(rec_id)
    return {"ok": True}
