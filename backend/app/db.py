"""SQLite 持久化：视频库 + 发布历史。使用标准库 sqlite3，简单可靠。"""
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
        CREATE TABLE IF NOT EXISTS publish_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT NOT NULL,
            video_id INTEGER,
            video_title TEXT,
            platform TEXT NOT NULL,
            account TEXT DEFAULT 'default',
            title TEXT,
            desc TEXT,
            tags TEXT,
            status TEXT NOT NULL,
            log TEXT,
            created_at TEXT NOT NULL
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
        """
    )
    # 老库兼容：publish_records 补 account/schedule 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(publish_records)").fetchall()]
    if "account" not in cols:
        conn.execute("ALTER TABLE publish_records ADD COLUMN account TEXT DEFAULT 'default'")
    if "schedule" not in cols:
        conn.execute("ALTER TABLE publish_records ADD COLUMN schedule TEXT")
    # 老库兼容：recordings 补 quality/fps 列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(recordings)").fetchall()]
    if "quality" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN quality TEXT DEFAULT 'best'")
    if "fps" not in cols:
        conn.execute("ALTER TABLE recordings ADD COLUMN fps INTEGER DEFAULT 0")
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


def add_publish_record(task_id: str, video_id: int | None, video_title: str, platform: str,
                       title: str, desc: str, tags: list[str], account: str = "default",
                       schedule: str | None = None) -> int:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(
            "INSERT INTO publish_records (task_id, video_id, video_title, platform, account, title, desc, tags, status, log, created_at, schedule)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, video_id, video_title, platform, account, title, desc, json.dumps(tags, ensure_ascii=False),
             "running", "", datetime.now().isoformat(timespec="seconds"), schedule),
        )
        conn.commit()
        return cur.lastrowid


def finish_publish_record(record_id: int, status: str, log: str):
    with _lock:
        conn = _get_conn()
        conn.execute("UPDATE publish_records SET status=?, log=? WHERE id=?", (status, log, record_id))
        conn.commit()


def list_publish_records(limit: int = 50) -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            "SELECT * FROM publish_records ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


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
