"""内存任务管理：下载/发布任务的状态、进度、日志，前端轮询获取。"""
import threading
import time
import uuid


class Task:
    def __init__(self, type_: str, title: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.type = type_            # download / publish
        self.title = title
        self.status = "pending"      # pending/running/success/failed/partial
        self.progress = 0.0          # 0~100，下载任务用
        self.logs: list[str] = []
        self.result: dict = {}       # download → video 信息；publish → 各平台结果
        self.needs_verify = False    # 抖音发布触发短信验证，等待用户输入验证码
        self.created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self.finished_at = None

    def log(self, msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.logs.append(line)
        if len(self.logs) > 2000:
            self.logs = self.logs[-1000:]

    def finish(self, status: str):
        self.status = status
        self.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

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
            # 只保留最近 200 个任务
            if len(self._tasks) > 200:
                for old in sorted(self._tasks.values(), key=lambda x: x.created_at)[:-200]:
                    self._tasks.pop(old.id, None)
        return t

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self, limit: int = 30) -> list[Task]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda x: x.created_at, reverse=True)[:limit]


task_manager = TaskManager()
