"""把磁盘任务映射进现有任务中心，不另起一套列表。"""

from __future__ import annotations

from engine.errors import EngineError
from app.jobs import get, list_jobs
from app.services import drama_store
from app.services.events import event_hub
from app.services.tasks import task_manager


def job_as_task(job: dict, *, with_logs: bool = False) -> dict:
    kind = str(job.get("kind") or "")
    if kind.startswith("narrate"):
        type_ = "narrate"
    else:
        type_ = "edit"
    error = job.get("error") or None
    result = job.get("result") if isinstance(job.get("result"), dict) else None
    task_id = (result or {}).get("task_id")
    payload = dict(job.get("input") or {}) if isinstance(job.get("input"), dict) else {}
    if task_id and "task_id" not in payload:
        payload["task_id"] = task_id
    logs = list(job.get("logs") or [])
    if isinstance(error, dict) and error.get("message"):
        line = str(error["message"])
        if error.get("action"):
            line = f"{line} {error['action']}"
        if line not in logs:
            logs.append(line)
    finished = None if job.get("status") in {"pending", "running"} else job.get("updated_at")
    item = {
        "id": job["id"],
        "type": type_,
        "kind": kind,
        "title": job.get("label") or kind or "任务",
        "status": job.get("status") or "pending",
        "progress": job.get("progress") or 0,
        "input": payload,
        "result": result,
        "error": error,
        "created_at": job.get("created_at") or "",
        "finished_at": finished,
        "stage": job.get("stage") or "",
    }
    if with_logs:
        item["logs"] = logs[-500:]
    return item


def publish(job: dict) -> None:
    event_hub.broadcast({"type": "task_updated", "task": job_as_task(job)})


def merged_tasks(*, with_logs: bool = False, limit: int = 50) -> list[dict]:
    memory = [task.to_dict(with_logs=with_logs) for task in task_manager.list(limit=limit)]
    seen = {item["id"] for item in memory}
    disk = []
    try:
        rows = list_jobs(limit)
    except Exception:
        rows = []
    for job in rows:
        if not job.get("id") or job["id"] in seen:
            continue
        disk.append(job_as_task(job, with_logs=with_logs))
    for project in drama_store.list_task_views(with_logs=with_logs):
        if project["id"] not in seen:
            disk.append(project)
    combined = memory + disk
    combined.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return combined[:limit]


def load_task_view(task_id: str, *, with_logs: bool = True) -> dict | None:
    if task_id.startswith("drama-"):
        project = drama_store.find_project_by_task_id(task_id)
        return drama_store.task_view(project, with_logs=with_logs) if project else None
    task = task_manager.get(task_id)
    if task:
        return task.to_dict(with_logs=with_logs)
    try:
        return job_as_task(get(task_id), with_logs=with_logs)
    except EngineError:
        return None
