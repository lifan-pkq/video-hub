"""成品套装导出任务与文件契约。"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, NARRATO_TASKS_DIR, VIDEOS_DIR
from app.db import get_video
from app.services.export_bundle import ExportBundleError, export_bundle

EXPORTS_DIR = DATA_DIR / "exports"
UNDERSTANDING_DIR = DATA_DIR / "understanding"
STATUS_FILE = "status.json"
MANIFEST_FILE = "manifest.json"

# 同一视频只允许一份进行中的导出：video_id -> export_id（进程重启后由磁盘 status.json 收口）
_running: dict[int, str] = {}
_cancel: set[str] = set()
_lock = threading.Lock()


class ExportError(RuntimeError):
    pass


def create_export(video_id: int) -> dict[str, Any]:
    """创建后台导出任务并立刻返回 running 记录；同视频已有进行中导出时复用它。"""
    video = get_video(video_id)
    if not video:
        raise ExportError("视频不存在")
    source = VIDEOS_DIR / video["filename"]
    if not source.is_file():
        raise ExportError("视频文件已丢失")
    with _lock:
        running_id = _running.get(video_id)
        if running_id and (EXPORTS_DIR / running_id / STATUS_FILE).is_file():
            return _status_record(running_id)
        export_id = uuid.uuid4().hex[:16]
        _running[video_id] = export_id
    try:
        (EXPORTS_DIR / export_id).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        with _lock:
            _running.pop(video_id, None)
        raise ExportError(f"导出目录创建失败：{exc}") from exc
    _write_status(export_id, "running", video_id=video_id)
    threading.Thread(
        target=_run_export,
        args=(
            video_id,
            export_id,
            source,
            _task_dir(video),
            str(video.get("title") or "Video Hub 成片"),
        ),
        daemon=True,
        name=f"export-{export_id}",
    ).start()
    return _status_record(export_id)


def cancel_export(export_id: str) -> dict[str, Any]:
    """请求取消进行中的导出。已经成功或失败的记录保持原样。"""
    if not export_id.isalnum() or len(export_id) > 32:
        raise ExportError("导出 ID 不合法")
    status = _read_status(export_id)
    if not status and not (EXPORTS_DIR / export_id).is_dir():
        raise ExportError("导出不存在")
    if status.get("status") in {"success", "failed", "cancelled"}:
        return _record(status, export_id)
    with _lock:
        _cancel.add(export_id)
    if not _is_live(export_id):
        return _write_status(export_id, "cancelled", error="导出已取消")
    return _record({**status, "status": "running", "label": "正在取消"}, export_id)


def get_export(export_id: str) -> dict[str, Any]:
    """读取导出记录：running/failed 读 status.json，success 读并回写富化后的 manifest。"""
    if not export_id.isalnum() or len(export_id) > 32:
        raise ExportError("导出 ID 不合法")
    status = _read_status(export_id)
    if status.get("status") == "running" and not _is_live(export_id):
        # 进程重启：没有线程在跑了，收口为 failed，避免永远停在 running
        return _write_status(export_id, "failed", error="导出中断：服务重启，请重新导出")
    manifest_path = EXPORTS_DIR / export_id / MANIFEST_FILE
    if status.get("status") == "success":
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _write_status(export_id, "failed", error="导出清单丢失")
        if not isinstance(manifest, dict):
            return _write_status(export_id, "failed", error="导出清单损坏")
        return _enrich(export_id, manifest, manifest_path)
    if status:
        return _record(status, export_id)
    if manifest_path.is_file():
        return _write_status(export_id, "failed", error="导出未完成：服务重启中断，请重新导出")
    raise ExportError("导出不存在")


def select_export(export_id: str, cover: str = "", title: str = "") -> dict[str, Any]:
    manifest_path = EXPORTS_DIR / export_id / MANIFEST_FILE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExportError("导出尚未完成") from exc
    if not isinstance(manifest, dict) or not manifest.get("archive"):
        raise ExportError("导出尚未完成")
    if cover:
        if cover not in (manifest.get("files") or {}).get("covers", []):
            raise ExportError("封面不在本次导出中")
        manifest["selected_cover"] = cover
    if title:
        manifest["selected_title"] = title[:120]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_export(export_id)


def export_file(export_id: str, name: str) -> Path:
    if get_export(export_id).get("status") != "success":
        raise ExportError("导出尚未完成")
    safe = Path(name).name
    if safe != name:
        raise ExportError("文件不存在")
    path = EXPORTS_DIR / export_id / safe
    if not path.is_file():
        raise ExportError("文件不存在")
    return path


def _is_live(export_id: str) -> bool:
    with _lock:
        return export_id in _running.values()


def _task_dir(video: dict) -> Path | None:
    source_url = str(video.get("source_url") or "")
    if not source_url.startswith("narrato://task/"):
        return None
    candidate = NARRATO_TASKS_DIR / source_url.removeprefix("narrato://task/")
    return candidate if candidate.is_dir() else None


def _subject_track(video_id: int) -> Path | None:
    """后端理解产物（含 subject_box 的视觉轨迹）；不存在时不传参，交给引擎自行回落。"""
    path = UNDERSTANDING_DIR / f"{video_id}.json"
    return path if path.is_file() else None


def run_bundle(
    source: Path,
    work_dir: Path,
    title: str,
    task_dir: Path | None,
    subject_track: Path | None,
    on_progress=None,
) -> dict[str, Any]:
    """导出套装。测试会替换这个函数，避免真的跑 ffmpeg。"""
    return export_bundle(
        source, work_dir, title, task_dir, subject_track, on_progress=on_progress,
    )


def _run_export(video_id: int, export_id: str, source: Path, task_dir: Path | None, title: str) -> None:
    work_dir = EXPORTS_DIR / export_id
    track = _subject_track(video_id)

    def on_progress(value: float, label: str) -> None:
        if _cancelled(export_id):
            raise ExportBundleError("导出已取消")
        _write_status(export_id, "running", video_id=video_id, progress=round(value, 1), label=label)

    try:
        try:
            manifest = run_bundle(source, work_dir, title, task_dir, track, on_progress=on_progress)
        except ExportBundleError as exc:
            state = "cancelled" if "取消" in str(exc) else "failed"
            _write_status(export_id, state, video_id=video_id, error=str(exc)[-1000:] or "导出失败")
            return
        except (OSError, ValueError) as exc:
            _write_status(export_id, "failed", video_id=video_id, error=str(exc)[-1000:] or "导出失败")
            return
        except Exception as exc:  # noqa: BLE001 - 导出线程必须把原因写进状态
            _write_status(export_id, "failed", video_id=video_id, error=str(exc)[-1000:] or "导出失败")
            return
        if not isinstance(manifest, dict) or not manifest.get("archive"):
            _write_status(export_id, "failed", video_id=video_id, error="导出未完成打包")
            return
        _enrich(export_id, manifest, work_dir / MANIFEST_FILE)
        _write_status(export_id, "success", video_id=video_id, progress=100, label="导出完成")
    finally:
        _forget(video_id, export_id)


def _cancelled(export_id: str) -> bool:
    with _lock:
        return export_id in _cancel


def _forget(video_id: int, export_id: str) -> None:
    with _lock:
        _cancel.discard(export_id)
        if _running.get(video_id) == export_id:
            _running.pop(video_id, None)


def _record(status: dict[str, Any], export_id: str) -> dict[str, Any]:
    return {**status, "export_id": export_id}


def _enrich(export_id: str, manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    """GET 记录 = 磁盘 manifest + export_id/status/download_url（manifest 本身仍保持干净）。"""
    record = {"status": "success", "download_url": f"/api/exports/{export_id}/download", **manifest}
    record["export_id"] = export_id
    _write_manifest(path, record)
    return record


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _write_manifest(path: Path, record: dict[str, Any]) -> None:
    payload = {k: v for k, v in record.items() if k not in ("status", "download_url", "error")}
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = None
    if current == payload:
        return
    try:
        _atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    except OSError:
        pass


def _read_status(export_id: str) -> dict[str, Any]:
    try:
        payload = json.loads((EXPORTS_DIR / export_id / STATUS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _status_record(export_id: str) -> dict[str, Any]:
    return _record(_read_status(export_id), export_id)


def _write_status(export_id: str, status: str, **fields: Any) -> dict[str, Any]:
    payload = {"status": status, "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), **fields}
    try:
        _atomic_text(
            EXPORTS_DIR / export_id / STATUS_FILE,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
    except OSError:
        pass
    return _record(payload, export_id)
