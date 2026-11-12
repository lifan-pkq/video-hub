"""Disk-backed jobs for analyze, export, and narration steps."""

from __future__ import annotations

import json
import threading
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

from engine.errors import EngineError
from engine.media import cancel_processes
from engine.settings import jobs_inline

_LOCK = threading.Lock()
_CANCEL: set[str] = set()


def _dir() -> Path:
    from app.services.videos import data_dir

    path = data_dir() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(job_id: str) -> Path:
    if not job_id.isalnum() or len(job_id) > 40:
        raise EngineError("任务编号不合法", code="job", status=404)
    return _dir() / f"{job_id}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(kind: str, label: str, payload: dict | None = None) -> dict:
    job = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "status": "pending",
        "progress": 0,
        "stage": "queued",
        "label": label,
        "input": payload or {},
        "logs": [],
        "error": None,
        "result": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    _write(job)
    _publish(job)
    return job


def _write(job: dict) -> None:
    target = _path(job["id"])
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def get(job_id: str) -> dict:
    path = _path(job_id)
    if not path.is_file():
        raise EngineError("任务不存在", code="not_found", action="它可能已被清理，重新发起这一步。", status=404)
    return json.loads(path.read_text(encoding="utf-8"))


def update(job_id: str, *, log: str | None = None, **fields) -> dict:
    with _LOCK:
        job = get(job_id)
        if job["status"] == "cancelled" and fields.get("status") not in {None, "cancelled"}:
            return job
        job.update(fields)
        if log:
            logs = list(job.get("logs") or [])
            logs.append(log)
            job["logs"] = logs[-200:]
        job["updated_at"] = _now()
        _write(job)
        _publish(job)
        return job


def cancelled(job_id: str) -> bool:
    if job_id in _CANCEL:
        return True
    try:
        return get(job_id).get("status") == "cancelled"
    except EngineError:
        return False


def cancel(job_id: str) -> dict:
    _CANCEL.add(job_id)
    cancel_processes(job_id)
    job = get(job_id)
    if job["status"] in {"success", "failed", "partial"}:
        return job
    return update(job_id, status="cancelled", stage="cancelled", label="已取消", log="用户取消了任务")


def _guard(job_id: str, fn) -> None:
    try:
        update(job_id, status="running", stage="running", label="处理中")
        fn(job_id)
        job = get(job_id)
        if job["status"] in {"running", "pending"}:
            update(job_id, status="success", progress=100, stage="done", label="完成")
    except EngineError as exc:
        if exc.code == "cancelled":
            update(job_id, status="cancelled", stage="cancelled", label="已取消", error=exc.as_dict(), log=exc.message)
            return
        update(job_id, status="failed", stage="failed", label="失败", error=exc.as_dict(), log=exc.message)
    except Exception as exc:  # noqa: BLE001
        update(
            job_id,
            status="failed",
            stage="failed",
            label="失败",
            error={
                "code": "internal",
                "message": str(exc) or type(exc).__name__,
                "action": "展开任务日志。若是依赖缺失，确认这台机器已安装 ffmpeg，并配置文本模型或本机 faster-whisper。",
            },
            log=traceback.format_exc()[-1500:],
        )


def submit(job: dict, fn) -> dict:
    if jobs_inline():
        _guard(job["id"], fn)
        return get(job["id"])
    threading.Thread(target=_guard, args=(job["id"], fn), daemon=True).start()
    return get(job["id"])


def fail_interrupted_jobs() -> int:
    """进程重启后，磁盘上仍标着进行中的任务已经没有线程在跑。"""
    count = 0
    for job in list_jobs(500):
        if job.get("status") not in {"pending", "running"}:
            continue
        update(
            job["id"],
            status="failed",
            stage="failed",
            label="已中断",
            error={
                "code": "interrupted",
                "message": "服务重启，任务中断。",
                "action": "在任务中心重试下载或抖音成片，或回到对应页面重新发起。",
            },
            log="服务重启，任务中断",
        )
        count += 1
    return count


def list_jobs(limit: int = 50) -> list[dict]:
    rows = []
    for path in _dir().glob("*.json"):
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return rows[: max(1, min(limit, 500))]


def _publish(job: dict) -> None:
    try:
        from app.services.job_bridge import publish

        publish(job)
    except Exception:
        return


def report(job_id: str, progress: float, stage: str, label: str) -> None:
    if cancelled(job_id):
        raise EngineError("任务已取消", code="cancelled", status=409)
    update(job_id, progress=max(0, min(100, progress)), stage=stage, label=label, log=label)
