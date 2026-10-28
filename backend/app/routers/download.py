"""下载：解析分享链接 + 后台下载任务 + 批量队列 + 任务查询。"""
import asyncio
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import PUBLISHER_DIR
from app.services.downloader import parse_video, run_download, run_download_queued
from app.services.tasks import task_manager

router = APIRouter(prefix="/api", tags=["download"])


class ParseRequest(BaseModel):
    text: str


class DownloadRequest(BaseModel):
    text: str


@router.post("/download/parse")
async def parse(req: ParseRequest):
    try:
        return await asyncio.to_thread(parse_video, req.text)
    except Exception as e:
        msg = str(e)
        if "Fresh cookies" in msg or "cookie" in msg.lower():
            msg += "（提示：抖音解析依赖登录 cookie，请到「平台账号」页检查抖音 cookie）"
        raise HTTPException(400, msg)


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
        raise HTTPException(400, "至少提供一条链接")
    if len(texts) > 100:
        raise HTTPException(400, "单次最多 100 条")
    task_ids = []
    for i, text in enumerate(texts):
        task = task_manager.create("download", f"[{i + 1}/{len(texts)}] {text[:56]}")
        asyncio.create_task(run_download_queued(task, text))
        task_ids.append(task.id)
    return {"task_ids": task_ids, "count": len(task_ids)}


@router.get("/tasks")
def list_tasks():
    return [t.to_dict(with_logs=False) for t in task_manager.list()]


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    t = task_manager.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    return t.to_dict()


@router.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    """取消进行中/排队中的任务（下载线程在下一个进度回调处中断）。"""
    t = task_manager.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.status not in ("pending", "running"):
        raise HTTPException(400, "任务已结束，无法取消")
    task_manager.cancel(task_id)
    return {"ok": True, "task": t.to_dict(with_logs=False)}


class VerifyCodeRequest(BaseModel):
    code: str


@router.post("/tasks/{task_id}/verify-code")
def submit_verify_code(task_id: str, req: VerifyCodeRequest):
    """抖音发布触发短信验证时，把用户输入的验证码写入引擎读取的文件。"""
    t = task_manager.get(task_id)
    if not t:
        raise HTTPException(404, "任务不存在")
    if t.status != "running":
        raise HTTPException(400, "任务已结束，无法提交验证码")
    code = req.code.strip()
    if not re.fullmatch(r"\d{4,8}", code):
        raise HTTPException(400, "验证码是 4-8 位数字")
    (PUBLISHER_DIR / "verify_code.txt").write_text(code, encoding="utf-8")
    t.log("[系统] 验证码已提交，等待引擎自动填入…")
    return {"ok": True, "message": "验证码已提交"}
