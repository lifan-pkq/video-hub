"""SQLite 持久化：视频库、AI 图片、录制与任务。使用标准库 sqlite3，简单可靠。"""
import json
import sqlite3
import threading
from datetime import datetime

from app.config import DB_PATH, ensure_dirs

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        ensure_dirs()
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            filename TEXT NOT NULL UNIQUE,
            size INTEGER DEFAULT 0,
            duration REAL,
            width INTEGER,
            height INTEGER,
            cover TEXT,
            source_platform TEXT,
            source_url TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS narrato_imports (
            task_id TEXT PRIMARY KEY,
            video_id INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            platform TEXT,
            title TEXT,
            filename TEXT,
            status TEXT NOT NULL DEFAULT 'recording',
            size INTEGER DEFAULT 0,
            duration REAL,
            error TEXT,
            video_id INTEGER,
            quality TEXT DEFAULT 'best',
            fps INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS watchers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            url TEXT NOT NULL,
            name TEXT DEFAULT '',
            quality TEXT DEFAULT 'best',
            fps INTEGER DEFAULT 0,
            enabled INTEGER DEFAULT 1,
            live INTEGER DEFAULT 0,
            active_recording_id INTEGER,
            last_checked_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL UNIQUE,
            title TEXT DEFAULT '',
            prompt TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            width INTEGER,
            height INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS maintenance_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            enabled INTEGER DEFAULT 0,
            interval_hours INTEGER DEFAULT 24,
            retention_days INTEGER DEFAULT 7,
            clean_logs INTEGER DEFAULT 1,
            clear_videos INTEGER DEFAULT 0,
            last_run_at TEXT,
            next_run_at TEXT,
            last_result TEXT
        );
        CREATE TABLE IF NOT EXISTS narration_tasks (
            id TEXT PRIMARY KEY,
            video_id INTEGER,
            title TEXT NOT NULL,
            content_type TEXT DEFAULT 'auto',
            mode TEXT DEFAULT 'auto',
            aspect TEXT,
            voice_name TEXT,
            target_duration INTEGER,
            status TEXT NOT NULL DEFAULT 'queued',
            progress REAL DEFAULT 0,
            message TEXT DEFAULT '',
            error TEXT DEFAULT '',
            engine_task_id TEXT,
            output_video_id INTEGER,
            request_json TEXT,
            result_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT
        );
        """
    )
    # 老库兼容：recordings 补 quality/fps 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recordings)").fetchall()]
    if "quality" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN quality TEXT DEFAULT 'best'")
    if "fps" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN fps INTEGER DEFAULT 0")
    if "started_at" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN started_at TEXT")
    conn.execute("INSERT OR IGNORE INTO maintenance_settings (id) VALUES (1)")
    conn.commit()


def add_video(**fields) -> dict:
    fields.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    keys = ",".join(fields.keys())
    marks = ",".join("?" for _ in fields)
    with _lock:
        conn = _get_conn()
        cur = conn.execute(f"INSERT INTO videos ({keys}) VALUES ({marks})", list(fields.values()))
        conn.commit()
        row = conn.execute("SELECT * FROM videos WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


def _serialize_json(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def _narration_row(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    for field in ("request_json", "result_json"):
        raw = item.get(field)
        if raw:
            try:
                item[field[:-5]] = json.loads(raw)
            except (TypeError, ValueError):
                item[field[:-5]] = None
    item.pop("request_json", None)
    item.pop("result_json", None)
    return item


def create_narration_task(**fields) -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    fields.setdefault("created_at", now)
    fields.setdefault("updated_at", now)
    fields.setdefault("status", "queued")
    fields.setdefault("progress", 0)
    fields["request_json"] = _serialize_json(fields.get("request_json"))
    fields["result_json"] = _serialize_json(fields.get("result_json"))
    keys = ",".join(fields.keys())
    marks = ",".join("?" for _ in fields)
    with _lock:
        conn = _get_conn()
        conn.execute(f"INSERT INTO narration_tasks ({keys}) VALUES ({marks})", list(fields.values()))
        conn.commit()
        row = conn.execute("SELECT * FROM narration_tasks WHERE id=?", (fields["id"],)).fetchone()
        return _narration_row(row)


def update_narration_task(task_id: str, **fields) -> dict | None:
    if not fields:
        return get_narration_task(task_id)
    for field in ("request_json", "result_json"):
        if field in fields:
            fields[field] = _serialize_json(fields[field])
    fields["updated_at"] = datetime.now().isoformat(timespec="seconds")
    sets = ",".join(f"{key}=?" for key in fields)
    with _lock:
        conn = _get_conn()
        conn.execute(f"UPDATE narration_tasks SET {sets} WHERE id=?", [*fields.values(), task_id])
        conn.commit()
        row = conn.execute("SELECT * FROM narration_tasks WHERE id=?", (task_id,)).fetchone()
        return _narration_row(row)


def get_narration_task(task_id: str) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM narration_tasks WHERE id=?", (task_id,)).fetchone()
        return _narration_row(row)


def list_narration_tasks(limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit or 50), 200))
    with _lock:
        rows = _get_conn().execute(
            "SELECT * FROM narration_tasks ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_narration_row(row) for row in rows]


def add_ai_image(**fields) -> dict:
    fields.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    keys = ",".join(fields.keys())
    marks = ",".join("?" for _ in fields)
    with _lock:
        conn = _get_conn()
        cur = conn.execute(f"INSERT INTO ai_images ({keys}) VALUES ({marks})", list(fields.values()))
        conn.commit()
        row = conn.execute("SELECT * FROM ai_images WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)


def list_ai_images(limit: int = 100) -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            "SELECT * FROM ai_images ORDER BY created_at DESC, id DESC LIMIT ?", (max(1, min(limit, 300)),)
        ).fetchall()
        return [dict(row) for row in rows]


def get_ai_image(image_id: int) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM ai_images WHERE id=?", (image_id,)).fetchone()
        return dict(row) if row else None


def delete_ai_image(image_id: int) -> None:
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM ai_images WHERE id=?", (image_id,))
        conn.commit()


def get_maintenance_settings() -> dict:
    with _lock:
        row = _get_conn().execute("SELECT * FROM maintenance_settings WHERE id=1").fetchone()
        return dict(row) if row else {}


def update_maintenance_settings(**fields) -> dict:
    if "last_result" in fields:
        fields["last_result"] = _serialize_json(fields["last_result"])
    sets = ",".join(f"{key}=?" for key in fields)
    with _lock:
        conn = _get_conn()
        conn.execute(f"UPDATE maintenance_settings SET {sets} WHERE id=1", list(fields.values()))
        conn.commit()
        row = conn.execute("SELECT * FROM maintenance_settings WHERE id=1").fetchone()
        return dict(row) if row else {}


def clear_video_references() -> None:
    """视频被全部删除后，清理任务/录制记录里的悬空 video_id。"""
    with _lock:
        conn = _get_conn()
        conn.execute("UPDATE narration_tasks SET output_video_id=NULL WHERE output_video_id IS NOT NULL")
        conn.execute("UPDATE recordings SET video_id=NULL WHERE video_id IS NOT NULL")
        conn.execute("DELETE FROM narrato_imports")
        conn.commit()


def link_narration_video(task_id: str, video_id: int) -> dict | None:
    return update_narration_task(task_id, output_video_id=video_id)


def add_narrato_video(task_id: str, **fields) -> dict:
    """视频记录与 NarratoAI 导入台账在同一事务中提交。"""
    fields.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    keys = ",".join(fields.keys())
    marks = ",".join("?" for _ in fields)
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT video_id FROM narrato_imports WHERE task_id=?", (task_id,)
            ).fetchone()
            if existing:
                row = conn.execute("SELECT * FROM videos WHERE id=?", (existing["video_id"],)).fetchone()
                conn.commit()
                return dict(row)
            cur = conn.execute(f"INSERT INTO videos ({keys}) VALUES ({marks})", list(fields.values()))
            conn.execute("INSERT INTO narrato_imports (task_id, video_id) VALUES (?, ?)", (task_id, cur.lastrowid))
            conn.commit()
            row = conn.execute("SELECT * FROM videos WHERE id=?", (cur.lastrowid,)).fetchone()
            return dict(row)
        except Exception:
            conn.rollback()
            raise


def get_video(video_id: int) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM videos WHERE id=?", (video_id,)).fetchone()
        return dict(row) if row else None


def get_video_by_filename(filename: str) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM videos WHERE filename=?", (filename,)).fetchone()
        return dict(row) if row else None


def get_video_by_source(source_platform: str, source_url: str) -> dict | None:
    with _lock:
        row = _get_conn().execute(
            "SELECT * FROM videos WHERE source_platform=? AND source_url=?",
            (source_platform, source_url),
        ).fetchone()
        return dict(row) if row else None


def list_videos() -> list[dict]:
    with _lock:
        rows = _get_conn().execute("SELECT * FROM videos ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def update_video(video_id: int, **fields):
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock:
        conn = _get_conn()
        conn.execute(f"UPDATE videos SET {sets} WHERE id=?", [*fields.values(), video_id])
        conn.commit()


def delete_video(video_id: int):
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM videos WHERE id=?", (video_id,))
        conn.commit()


# ---------- 直播录制 ----------

def add_recording(url: str, platform: str, title: str, filename: str,
                  quality: str = "best", fps: int = 0) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO recordings (url, platform, title, filename, status, quality, fps, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (url, platform, title, filename, "recording", quality, fps,
             datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid


def update_recording(rec_id: int, **fields):
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock:
        conn = _get_conn()
        conn.execute(f"UPDATE recordings SET {sets} WHERE id=?", [*fields.values(), rec_id])
        conn.commit()


def get_recording(rec_id: int) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM recordings WHERE id=?", (rec_id,)).fetchone()
        return dict(row) if row else None


def list_recordings(limit: int = 100) -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            "SELECT * FROM recordings ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_recording(rec_id: int):
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
        conn.commit()


def fail_interrupted_recordings():
    """服务重启后，此前 recording 状态的任务实际已中断（ffmpeg 子进程不存活）。"""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE recordings SET status='failed', error='服务重启，录制中断',"
            " finished_at=? WHERE status='recording'",
            (datetime.now().isoformat(timespec="seconds"),),
        )
        conn.commit()


# ---------- 主播监控（自动录制） ----------

def add_watcher(platform: str, url: str, name: str, quality: str, fps: int) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO watchers (platform, url, name, quality, fps, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (platform, url, name, quality, fps, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return cur.lastrowid


def list_watchers(enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM watchers"
    if enabled_only:
        sql += " WHERE enabled=1"
    with _lock:
        rows = _get_conn().execute(sql + " ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def get_watcher(watcher_id: int) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM watchers WHERE id=?", (watcher_id,)).fetchone()
        return dict(row) if row else None


def update_watcher(watcher_id: int, **fields):
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    with _lock:
        conn = _get_conn()
        conn.execute(f"UPDATE watchers SET {sets} WHERE id=?", [*fields.values(), watcher_id])
        conn.commit()


def delete_watcher(watcher_id: int):
    with _lock:
        conn = _get_conn()
        conn.execute("DELETE FROM watchers WHERE id=?", (watcher_id,))
        conn.commit()
