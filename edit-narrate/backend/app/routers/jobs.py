"""Shared job status for long edit and narration work."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from engine.errors import EngineError
from app.jobs import cancel, get
from app.services.videos import data_dir, videos_dir

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _inside(path: Path) -> Path:
    resolved = path.resolve()
    for root in (videos_dir(), data_dir()):
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise EngineError("拒绝下载这个路径", code="path", status=403)


@router.get("/{job_id}")
def read_job(job_id: str):
    return get(job_id)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    return cancel(job_id)


@router.get("/{job_id}/file")
def job_file(job_id: str):
    job = get(job_id)
    raw = (job.get("result") or {}).get("output_path")
    if not raw:
        raise EngineError("这个任务没有可下载的文件", code="missing_file", action="等导出完成，或看任务错误。", status=404)
    path = _inside(Path(str(raw)))
    if not path.is_file():
        raise EngineError("导出文件已不在磁盘上", code="missing_file", status=404)
    return FileResponse(path, media_type="video/mp4", filename=path.name)
