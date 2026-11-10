"""Resolve a library id to a file. This is the drop-in seam."""

from __future__ import annotations

import os
import re
from pathlib import Path

from engine.errors import EngineError
from engine.media import ffmpeg, probe


def data_dir() -> Path:
    configured = os.environ.get("VIDEO_HUB_DATA_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        try:
            from app.config import data_dir as configured_dir

            if os.environ.get("VIDEO_HUB_DATA_DIR"):
                path = configured_dir()
            else:
                import app.config as cfg

                if getattr(cfg, "IS_STANDALONE_CONFIG", False):
                    path = configured_dir()
                elif hasattr(cfg, "DATA_DIR"):
                    path = Path(cfg.DATA_DIR).resolve()
                else:
                    path = configured_dir()
        except Exception:
            path = Path(__file__).resolve().parents[3] / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def videos_dir() -> Path:
    override = os.environ.get("VIDEO_HUB_VIDEOS_DIR", "").strip()
    if override:
        path = Path(override).expanduser().resolve()
    else:
        path = data_dir() / "videos"
        try:
            import app.config as cfg

            if not getattr(cfg, "IS_STANDALONE_CONFIG", False) and hasattr(cfg, "VIDEOS_DIR"):
                path = Path(cfg.VIDEOS_DIR).resolve()
        except Exception:
            pass
    path.mkdir(parents=True, exist_ok=True)
    return path


def covers_dir() -> Path:
    path = data_dir() / "covers"
    try:
        import app.config as cfg

        if not getattr(cfg, "IS_STANDALONE_CONFIG", False) and hasattr(cfg, "COVERS_DIR"):
            path = Path(cfg.COVERS_DIR).resolve()
    except Exception:
        pass
    path.mkdir(parents=True, exist_ok=True)
    return path


def _as_dict(video) -> dict:
    if isinstance(video, dict):
        return dict(video)
    if hasattr(video, "keys"):
        return {key: video[key] for key in video.keys()}
    raise EngineError("视频记录格式无法识别", code="library", status=500)


def fetch(video_id: int) -> dict:
    from app.db import get_video

    try:
        video = get_video(int(video_id))
    except Exception as exc:
        raise EngineError(
            f"读取素材库失败：{exc}",
            code="library",
            action="确认 app.db.get_video 可用。独立运行时检查 VIDEO_HUB_DB。",
            status=500,
        ) from exc
    if not video:
        raise EngineError(
            f"视频 {video_id} 不存在",
            code="not_found",
            action="回素材库刷新列表，确认编号还在。",
            status=404,
        )
    return _as_dict(video)


def resolve_media(video: dict) -> Path:
    for key in ("path", "filepath", "file_path", "absolute_path"):
        raw = video.get(key)
        if raw and Path(str(raw)).is_file():
            return Path(str(raw)).resolve()
    raw_name = video.get("filename") or video.get("file_name") or ""
    if not raw_name:
        raise EngineError(
            "这条素材没有文件名",
            code="library",
            action="视频库记录需要 filename 或 path 字段。",
            status=422,
        )
    candidate = Path(str(raw_name))
    if candidate.is_file():
        return candidate.resolve()
    stored = videos_dir() / candidate.name
    if stored.is_file():
        return stored.resolve()
    raise EngineError(
        f"视频文件不在磁盘上：{stored}",
        code="missing_file",
        action="检查 VIDEO_HUB_VIDEOS_DIR 是否指向 Video Hub 的 videos 目录。",
        status=404,
    )


def public_video(video: dict) -> dict:
    video_id = int(video["id"])
    filename = Path(str(video.get("filename") or "")).name
    return {
        "id": video_id,
        "title": video.get("title") or filename or f"视频 {video_id}",
        "filename": filename,
        "duration": video.get("duration"),
        "width": video.get("width"),
        "height": video.get("height"),
        "size": video.get("size"),
        "cover": video.get("cover"),
        "source_platform": video.get("source_platform") or "",
        "file_url": video.get("file_url") or f"/api/videos/{video_id}/file",
        "poster_url": f"/api/videos/{video_id}/poster",
    }


def unique_path(directory: Path, stem: str, suffix: str = ".mp4") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    cleaned = re.sub(r'[\\/:*?"<>|#\n\r]+', "_", stem).strip(" ._")[:60] or "clip"
    candidate = directory / f"{cleaned}{suffix}"
    number = 1
    while candidate.exists():
        candidate = directory / f"{cleaned}_{number}{suffix}"
        number += 1
    return candidate


def make_cover(video: Path, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        ffmpeg(["-ss", "0.4", "-i", str(video), "-frames:v", "1", "-q:v", "4", str(dest)], timeout=60)
    except EngineError:
        return False
    return dest.is_file() and dest.stat().st_size > 0


def ingest(path: Path, title: str, platform: str) -> dict:
    from app.db import add_video

    if path.parent.resolve() != videos_dir().resolve():
        target = unique_path(videos_dir(), path.stem, path.suffix or ".mp4")
        target.write_bytes(path.read_bytes())
        path = target
    meta = probe(path)
    cover_name = f"{path.stem}.jpg"
    cover_ok = make_cover(path, covers_dir() / cover_name)
    try:
        video = add_video(
            title=title[:80] or path.stem,
            filename=path.name,
            size=meta["size"],
            duration=meta["duration"],
            width=meta["width"],
            height=meta["height"],
            cover=cover_name if cover_ok else None,
            source_platform=platform,
            source_url=None,
        )
    except TypeError as exc:
        raise EngineError(
            f"入库参数不被当前视频库接受：{exc}",
            code="library_ingest",
            action=f"成片已经在 {path}。按 DEPLOY.md 核对 app.db.add_video 的参数。",
            status=500,
        ) from exc
    except Exception as exc:
        raise EngineError(
            f"成片已写出，但没有写入视频库：{exc}",
            code="library_ingest",
            action=f"文件在 {path}。可以手动放进素材库。",
            status=500,
        ) from exc
    return public_video(_as_dict(video))
