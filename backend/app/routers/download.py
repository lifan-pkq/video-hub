"""下载：解析分享链接 + 后台下载任务 + 批量队列 + 任务查询。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engine.errors import EngineError
from app.jobs import cancel as cancel_job
from app.services.downloader import explain_download_error, parse_video, run_download, run_download_queued
from app.services.job_bridge import load_task_view, merged_tasks
from app.services.tasks import task_manager

router = APIRouter(prefix="/api", tags=["download"])


class ParseRequest(BaseModel):
    text: str


class DownloadRequest(BaseModel):
    text: str


@router.post("/download/parse")
async def parse(req: ParseRequest):
    if not req.text.strip():
        raise HTTPException(400, detail=explain_download_error(""))
    try:
        return await asyncio.to_thread(parse_video, req.text)
    except Exception as exc:
        raise HTTPException(400, detail=explain_download_error(str(exc))) from exc


@router.post("/download")
async def download(req: DownloadRequest):
    task = task_manager.create("download", req.text[:60])
    asyncio.create_task(run_download(task, req.text))
    return {"task_id": task.id}


class BatchDownloadRequest(BaseModel):
    """批量下载：每个元素是一条分享口令/链接。"""

    texts: list[str]


@router.post("/download/batch")
async def download_batch(req: BatchDownloadRequest):
    texts = [t.strip() for t in req.texts if t.strip()]
    if not texts:
        raise HTTPException(400, detail=explain_download_error(""))
    if len(texts) > 100:
        raise HTTPException(400, detail={
            "code": "batch_too_large",
            "message": "单次最多 100 条链接。",
            "action": "把列表拆成几批再提交，进度在任务中心查看。",
        })
    task_ids = []
    for i, text in enumerate(texts):
        task = task_manager.create("download", f"[{i + 1}/{len(texts)}] {text[:56]}")
        asyncio.create_task(run_download_queued(task, text))
        task_ids.append(task.id)
    return {"task_ids": task_ids, "count": len(task_ids)}


@router.get("/tasks")
def list_tasks():
    return merged_tasks(with_logs=False, limit=50)


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = load_task_view(task_id, with_logs=True)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """取消进行中/排队中的任务。内存任务与剪辑/解说磁盘任务都走这里。"""
    if task_id.startswith("drama-"):
        from app.services import ai_drama

        try:
            ai_drama.cancel_project(task_id.removeprefix("drama-"))
        except ai_drama.DramaError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "task": load_task_view(task_id, with_logs=False)}
    t = task_manager.get(task_id)
    if t:
        if t.status not in ("pending", "running"):
            raise HTTPException(400, "任务已结束，无法取消")
        task_manager.cancel(task_id)
        return {"ok": True, "task": t.to_dict(with_logs=False)}
    try:
        job = cancel_job(task_id)
    except EngineError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    if job.get("status") not in ("pending", "running", "cancelled"):
        raise HTTPException(400, "任务已结束，无法取消")
    from app.services.job_bridge import job_as_task

    return {"ok": True, "task": job_as_task(job)}



