"""解说任务的文件态存储：spec/state/result 三份 JSON，天然可跨进程恢复。"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

TERMINAL = {"success", "needs_review", "failed", "cancelled"}
STATUSES = {"queued", "running", "awaiting_review", "success", "needs_review", "failed", "cancelled"}
MAX_LOG_CHARS = 12000


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.tasks_dir = self.root / "storage" / "tasks"
        self._lock = threading.RLock()
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    # ---- 基础读写 ----

    def task_dir(self, task_id: str) -> Path:
        if not task_id or "/" in task_id or "\\" in task_id or task_id in {".", ".."}:
            raise KeyError("非法任务 ID")
        path = (self.tasks_dir / task_id).resolve()
        if self.tasks_dir not in path.parents:
            raise KeyError("非法任务 ID")
        return path

    def exists(self, task_id: str) -> bool:
        return (self.task_dir(task_id) / "spec.json").is_file()

    def _read_json(self, path: Path, default: Any = None) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)

    # ---- 生命周期 ----

    def create(self, spec: dict[str, Any]) -> dict[str, Any]:
        task_id = spec["task_id"]
        directory = self.task_dir(task_id)
        with self._lock:
            directory.mkdir(parents=True, exist_ok=True)
            state = {
                "status": "queued",
                "progress": 0,
                "message": "任务已创建，等待引擎调度",
                "step_current": 0,
                "step_total": 6,
                "phase": "queued",
                "created_at": _now(),
                "updated_at": _now(),
            }
            self._write_json(directory / "spec.json", spec)
            self._write_json(directory / "state.json", state)
            (directory / "worker.log").touch()
        return self.public(task_id)

    def read_spec(self, task_id: str) -> dict[str, Any] | None:
        return self._read_json(self.task_dir(task_id) / "spec.json")

    def read_state(self, task_id: str) -> dict[str, Any] | None:
        return self._read_json(self.task_dir(task_id) / "state.json")

    def read_result(self, task_id: str) -> dict[str, Any] | None:
        return self._read_json(self.task_dir(task_id) / "result.json")

    def update_state(self, task_id: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            directory = self.task_dir(task_id)
            current = self._read_json(directory / "state.json", {}) or {}
            status = fields.get("status")
            if status and status not in STATUSES:
                fields.pop("status", None)
            current.update(fields)
            current["updated_at"] = _now()
            current.setdefault("created_at", _now())
            try:
                current["progress"] = max(0.0, min(100.0, float(current.get("progress", 0))))
            except (TypeError, ValueError):
                current["progress"] = 0.0
            self._write_json(directory / "state.json", current)
            return current

    def write_result(self, task_id: str, result: dict[str, Any]) -> None:
        with self._lock:
            self._write_json(self.task_dir(task_id) / "result.json", result)

    def read_script(self, task_id: str) -> list[dict[str, Any]] | None:
        data = self._read_json(self.task_dir(task_id) / "script.json", None)
        return data if isinstance(data, list) else None

    def write_script(self, task_id: str, items: list[dict[str, Any]]) -> Path:
        path = self.task_dir(task_id) / "script.json"
        with self._lock:
            self._write_json(path, items)
        return path

    def set_pid(self, task_id: str, pid: int) -> None:
        (self.task_dir(task_id) / "worker.pid").write_text(str(pid), encoding="ascii")

    def read_pid(self, task_id: str) -> int | None:
        try:
            return int((self.task_dir(task_id) / "worker.pid").read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    # ---- 进程与取消 ----

    @staticmethod
    def pid_alive(pid: int | None) -> bool:
        if not pid or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False

    def cancel(self, task_id: str, reason: str = "用户取消") -> dict[str, Any]:
        state = self.read_state(task_id) or {}
        if state.get("status") in TERMINAL:
            return self.public(task_id)
        pid = self.read_pid(task_id)
        killed = False
        if self.pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                killed = True
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    os.kill(pid, signal.SIGTERM)
                    killed = True
                except OSError:
                    pass
            if killed:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and self.pid_alive(pid):
                    time.sleep(0.1)
                if self.pid_alive(pid):
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except (ProcessLookupError, PermissionError, OSError):
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except OSError:
                            pass
        self.update_state(
            task_id,
            status="cancelled",
            phase="cancelled",
            message=reason,
            error=reason,
        )
        return self.public(task_id)

    def recover(self) -> list[str]:
        """服务重启后收口：进程已死的 running 任务标记失败。"""
        changed: list[str] = []
        if not self.tasks_dir.is_dir():
            return changed
        for directory in self.tasks_dir.iterdir():
            if not directory.is_dir() or not (directory / "spec.json").is_file():
                continue
            state = self._read_json(directory / "state.json", {}) or {}
            if state.get("status") not in {"queued", "running"}:
                continue
            pid = self.read_pid(directory.name)
            if self.pid_alive(pid):
                continue
            if state.get("status") == "queued" and not pid:
                # 从未被调度的任务由服务端重新拉起，这里保持 queued。
                updated = state.get("updated_at") or ""
                try:
                    age = time.time() - datetime.fromisoformat(updated).timestamp()
                except (TypeError, ValueError):
                    age = 999
                if age < 60:
                    continue
            self.update_state(
                directory.name,
                status="failed",
                phase="failed",
                message="生成进程已退出，任务中断",
                error="worker exited",
            )
            changed.append(directory.name)
        return changed

    # ---- 对外视图 ----

    def _log_tail(self, task_id: str) -> str:
        try:
            text = (self.task_dir(task_id) / "worker.log").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-MAX_LOG_CHARS:]

    def public(self, task_id: str, include_logs: bool = False) -> dict[str, Any]:
        directory = self.task_dir(task_id)
        spec = self._read_json(directory / "spec.json", {}) or {}
        state = self._read_json(directory / "state.json", {}) or {}
        result = self._read_json(directory / "result.json", {}) or {}
        if not spec:
            raise KeyError("任务不存在")

        status = str(state.get("status") or "queued")
        message = str(state.get("message") or "")
        error = str(state.get("error") or "")
        pid = self.read_pid(task_id)

        # 进程不在了但状态还停在 running：以文件为准标记失败，避免前端无限转圈。
        if status == "running" and not self.pid_alive(pid):
            updated = state.get("updated_at") or ""
            try:
                stale = time.time() - datetime.fromisoformat(updated).timestamp() > 15
            except (TypeError, ValueError):
                stale = True
            if stale:
                status = "failed"
                message = "生成进程已退出"
                error = error or "worker exited unexpectedly"

        payload = {
            "task_id": task_id,
            "status": status,
            "progress": round(float(state.get("progress") or 0), 1),
            "message": message,
            "phase": state.get("phase") or "",
            "step_current": state.get("step_current") or 0,
            "step_total": state.get("step_total") or 6,
            "created_at": state.get("created_at") or "",
            "updated_at": state.get("updated_at") or "",
            "finished_at": state.get("finished_at") or "",
            "error": error,
            "title": spec.get("title") or "",
            "content_type": spec.get("content_type") or "auto",
            "mode": spec.get("mode") or "auto",
            "video_name": spec.get("video_name") or "",
            "aspect": spec.get("aspect") or "",
            "voice_name": spec.get("voice_name") or "",
            "target_duration": spec.get("target_duration") or 0,
            "material_source": state.get("material_source") or "",
            "material_note": state.get("material_note") or "",
            "result": result if result else None,
            "has_script": (directory / "script.json").is_file(),
        }
        if include_logs:
            payload["logs"] = self._log_tail(task_id)
        return payload

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        rows: list[dict[str, Any]] = []
        if not self.tasks_dir.is_dir():
            return rows
        for directory in self.tasks_dir.iterdir():
            if not directory.is_dir() or not (directory / "spec.json").is_file():
                continue
            try:
                state = self._read_json(directory / "state.json", {}) or {}
                spec = self._read_json(directory / "spec.json", {}) or {}
            except OSError:
                continue
            rows.append(
                {
                    "task_id": directory.name,
                    "created_at": state.get("created_at") or "",
                    "title": spec.get("title") or "",
                    "status": state.get("status") or "queued",
                }
            )
        rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return rows[:limit]
