"""Video library for the standalone app.

When these routers are copied into an existing Video Hub, leave the host
`app/db.py` in place. This module is only the standalone implementation,
plus an optional read of VIDEO_HUB_EXTERNAL_DB.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

IS_STANDALONE_LIBRARY = True

_COLUMNS = {
    "id": ("id",),
    "title": ("title", "name"),
    "filename": ("filename", "file_name", "path", "filepath", "file_path"),
    "size": ("size", "file_size"),
    "duration": ("duration", "duration_sec", "length"),
    "width": ("width",),
    "height": ("height",),
    "cover": ("cover", "poster"),
    "source_platform": ("source_platform", "source", "platform"),
    "source_url": ("source_url",),
    "created_at": ("created_at", "created"),
}


def _db_file() -> Path:
    external = os.environ.get("VIDEO_HUB_EXTERNAL_DB", "").strip()
    if external:
        return Path(external).expanduser().resolve()
    configured = os.environ.get("VIDEO_HUB_DB", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        from app.config import data_dir

        path = data_dir() / "library.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _external() -> bool:
    return bool(os.environ.get("VIDEO_HUB_EXTERNAL_DB", "").strip())


def connect(readonly: bool = False) -> sqlite3.Connection:
    path = _db_file()
    if readonly or _external():
        if not path.is_file():
            raise sqlite3.OperationalError(f"数据库不存在：{path}")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                filename TEXT NOT NULL,
                size INTEGER,
                duration REAL,
                width INTEGER,
                height INTEGER,
                cover TEXT,
                source_platform TEXT,
                source_url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _table(conn: sqlite3.Connection) -> tuple[str, dict[str, str]]:
    names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    table = "videos" if "videos" in names else "video" if "video" in names else ""
    if not table:
        raise sqlite3.OperationalError("数据库里没有 videos 表")
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    mapping: dict[str, str] = {}
    for key, options in _COLUMNS.items():
        for option in options:
            if option in columns:
                mapping[key] = option
                break
    if "id" not in mapping or "filename" not in mapping:
        raise sqlite3.OperationalError(f"{table} 缺少 id 或 filename/path 列：{columns}")
    return table, mapping


def _row_to_dict(row: sqlite3.Row, mapping: dict[str, str]) -> dict:
    data = {key: row[column] for key, column in mapping.items()}
    data.setdefault("title", Path(str(data.get("filename") or "")).stem)
    data.setdefault("source_platform", "")
    return data


def list_videos() -> list[dict]:
    readonly = _external()
    with connect(readonly=readonly) as conn:
        table, mapping = _table(conn)
        wanted = [mapping[key] for key in mapping]
        rows = conn.execute(
            f"SELECT {', '.join(wanted)} FROM {table} ORDER BY {mapping['id']} DESC"
        ).fetchall()
    return [_row_to_dict(row, mapping) for row in rows]


def get_video(video_id: int) -> dict | None:
    with connect(readonly=_external()) as conn:
        table, mapping = _table(conn)
        wanted = [mapping[key] for key in mapping]
        row = conn.execute(
            f"SELECT {', '.join(wanted)} FROM {table} WHERE {mapping['id']} = ?",
            (video_id,),
        ).fetchone()
    return _row_to_dict(row, mapping) if row else None


def add_video(
    *,
    title: str,
    filename: str,
    size: int | None = None,
    duration: float | None = None,
    width: int | None = None,
    height: int | None = None,
    cover: str | None = None,
    source_platform: str | None = None,
    source_url: str | None = None,
) -> dict:
    if _external():
        path = _db_file()
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
    else:
        conn = connect()
    try:
        table, mapping = _table(conn)
        values = {
            "title": title,
            "filename": filename,
            "size": size,
            "duration": duration,
            "width": width,
            "height": height,
            "cover": cover,
            "source_platform": source_platform,
            "source_url": source_url,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        columns = [mapping[key] for key in values if key in mapping]
        keys = [key for key in values if key in mapping]
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [values[key] for key in keys],
        )
        conn.commit()
        video_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        conn.close()
    found = get_video(video_id)
    if not found:
        raise sqlite3.OperationalError("写入视频库后读不回来")
    return found
