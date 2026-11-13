"""下载：解析分享链接 + 后台下载任务 + 批量队列 + 任务查询。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from engine.errors import EngineError
from app.jobs import cancel as cancel_job
from app.problems import problem
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
    task = task_manager.create("download", req.text[:60], input={"text": req.text})
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
        task = task_manager.create(
            "download",
            f"[{i + 1}/{len(texts)}] {text[:56]}",
            input={"text": text},
        )
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
        raise problem(404, "not_found", "任务不存在", "它可能已被清理，回到对应页面重新发起。")
    return task


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """取消进行中的任务。内存任务、磁盘剪辑任务和漫剧父任务都走这里。"""
    if task_id.startswith("drama-"):
        from app.services import ai_drama

        try:
            ai_drama.cancel_project(task_id.removeprefix("drama-"))
        except ai_drama.DramaError as exc:
            raise problem(404, "not_found", str(exc), "打开 AI 短剧列表确认项目还在。") from exc
        return {"ok": True, "task": load_task_view(task_id, with_logs=False)}
    t = task_manager.get(task_id)
    if t:
        if t.status not in ("pending", "running"):
            raise problem(400, "finished", "任务已结束，无法取消", "可以重试失败或已取消的下载和抖音成片。")
        task_manager.cancel(task_id)
        return {"ok": True, "task": t.to_dict(with_logs=False)}
    try:
        job = cancel_job(task_id)
    except EngineError as exc:
        raise problem(exc.status, exc.code or "job", exc.message, exc.action) from exc
    if job.get("status") not in ("pending", "running", "cancelled"):
        raise problem(400, "finished", "任务已结束，无法取消", "可以重试失败或已取消的下载和抖音成片。")
    from app.services.job_bridge import job_as_task

    return {"ok": True, "task": job_as_task(job)}


@router.post("/tasks/{task_id}/retry")
def retry_task(task_id: str):
    """失败或已取消的下载、抖音成片从任务中心再开一条。漫剧仍在项目页按镜头重试。"""
    view = load_task_view(task_id, with_logs=False)
    if not view:
        raise problem(404, "not_found", "任务不存在", "回到对应页面重新发起。")
    if view.get("status") in {"pending", "running"}:
        raise problem(409, "busy", "任务还在进行", "等它结束，或先取消。")
    kind = str(view.get("kind") or "")
    if kind == "edit.douyin":
        from app.routers.edit import douyin_retry

        return {"ok": True, "task": douyin_retry(task_id)}
    if kind == "motion":
        from app.services.motion import retry as retry_motion

        try:
            task = retry_motion(task_id)
        except EngineError as exc:
            raise problem(exc.status, exc.code or "retry", exc.message, exc.action) from exc
        return {"ok": True, "task": task}
    if view.get("type") == "download":
        text = str((view.get("input") or {}).get("text") or "").strip()
        if not text:
            raise problem(400, "input", "这条下载没有留下原始链接", "回到下载页重新粘贴。")
        task = task_manager.create("download", text[:60], input={"text": text, "retry_of": task_id})
        asyncio.create_task(run_download(task, text))
        return {"ok": True, "task": task.to_dict(with_logs=False)}
    if task_id.startswith("drama-") or kind.startswith("drama"):
        raise problem(400, "retry", "漫剧请在项目里重试单个镜头", "打开 AI 短剧详情，按图片或视频阶段重试。")
    raise problem(400, "retry", "这个任务不能从任务中心重试", "打开对应页面重新发起。")



