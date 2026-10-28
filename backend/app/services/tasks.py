"""内存任务管理：下载/发布任务的状态、进度、日志，前端轮询 + WebSocket 实时推送。"""
import threading
import time
import uuid

from app.services.events import event_hub

# 进度广播节流：每个任务至少间隔这么久才推一次（终态不受限，必发）
PROGRESS_EMIT_INTERVAL = 0.4


class Task:
    def __init__(self, type_: str, title: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.type = type_            # download / publish
        self.title = title
        self._status = "pending"     # pending/running/success/failed/partial
        self._progress = 0.0         # 0~100，下载任务用
        self._needs_verify = False
        self._last_emit = 0.0        # 上次广播的 monotonic 时间（节流用）
        self.logs: list[str] = []
        self.result: dict = {}       # download → video 信息；publish → 各平台结果
        self.cancelled = False       # 超时取消标志（下载线程的 progress hook 检查它）
        self.created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.finished_at = None

    # ---- 状态/进度收口：setter 统一触发广播，调用方无需感知 ----

    @property
    def status(self) -> str:
        return self._status

    @status.setter
    def status(self, value: str):
        if value == self._status:
            return
        self._status = value
        self._emit(force=True)

    @property
    def progress(self) -> float:
        return self._progress

    @progress.setter
    def progress(self, value: float):
        self._progress = value
        self._emit()

    @property
    def needs_verify(self) -> bool:
        return self._needs_verify

    @needs_verify.setter
    def needs_verify(self, value: bool):
        if value == self._needs_verify:
            return
        self._needs_verify = value
        self._emit(force=True)

    def _emit(self, force: bool = False):
        """广播 task_updated 事件；非 force 时按任务节流，可从任意线程调用。"""
        now = time.monotonic()
        if not force and now - self._last_emit < PROGRESS_EMIT_INTERVAL:
            return
        self._last_emit = now
        event_hub.broadcast({"type": "task_updated", "task": self.to_dict(with_logs=False)})

    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        if len(self.logs) > 2000:
            self.logs = self.logs[-1000:]

    def finish(self, status: str):
        if self._status == "cancelled":
            return  # 已取消的任务不允许被后台线程改写终态
        self._status = status
        self.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._emit(force=True)  # 终态必发

    def to_dict(self, with_logs: bool = True) -> dict:
        d = {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "status": self.status,
            "progress": round(self.progress, 1),
            "result": self.result,
            "needs_verify": self.needs_verify,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }
        if with_logs:
            d["logs"] = self.logs[-500:]
        return d


class TaskManager:
    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()

    def create(self, type_: str, title: str = "") -> Task:
        t = Task(type_, title)
        with self._lock:
            self._tasks[t.id] = t
            # 只保留最近 200 个任务（进行中的任务不淘汰）
            if len(self._tasks) > 200:
                done = sorted((t for t in self._tasks.values()
                               if t.status not in ("pending", "running")),
                              key=lambda x: x.created_at)
                for old in done[:len(self._tasks) - 200]:
                    self._tasks.pop(old.id, None)
        t._emit(force=True)  # 任务创建广播
        return t

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, limit: int = 30) -> list[Task]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda x: x.created_at, reverse=True)[:limit]


task_manager = TaskManager()
