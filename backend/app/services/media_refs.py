"""Resolve library / Agnes stills and b-roll. Missing files return None."""

from __future__ import annotations

from pathlib import Path

from engine.errors import EngineError


def _safe_generated(name: str) -> str:
    cleaned = Path(str(name or "")).name
    if not cleaned.startswith("ai_") or cleaned != str(name or "").strip():
        return ""
    if "/" in str(name) or "\\" in str(name):
        return ""
    return cleaned


def _image_dirs() -> list[Path]:
    folders: list[Path] = []
    try:
        from app.services.videos import data_dir

        folders.append(data_dir() / "generated" / "images")
    except Exception:
        pass
    try:
        from app.services.ai_media import GENERATED_IMAGE_DIR

        folders.append(GENERATED_IMAGE_DIR)
    except Exception:
        pass
    unique: list[Path] = []
    seen: set[str] = set()
    for folder in folders:
        key = str(folder)
        if key not in seen:
            seen.add(key)
            unique.append(folder)
    return unique


def _video_dirs() -> list[Path]:
    folders: list[Path] = []
    try:
        from app.services.videos import data_dir

        folders.append(data_dir() / "generated" / "videos")
    except Exception:
        pass
    try:
        from app.services.ai_media import GENERATED_VIDEO_DIR

        folders.append(GENERATED_VIDEO_DIR)
    except Exception:
        pass
    unique: list[Path] = []
    seen: set[str] = set()
    for folder in folders:
        key = str(folder)
        if key not in seen:
            seen.add(key)
            unique.append(folder)
    return unique


def resolve_image(image_id: int = 0, image_name: str = "") -> Path | None:
    if int(image_id or 0) > 0:
        try:
            from app.services.ai_images import path_for

            path = path_for(int(image_id))
            if path.is_file():
                return path
        except Exception:
            pass
    name = _safe_generated(image_name)
    if not name:
        return None
    for folder in _image_dirs():
        path = folder / name
        if path.is_file():
            return path
    return None


def resolve_broll(video_id: int = 0, video_name: str = "") -> tuple[Path, dict] | None:
    if int(video_id or 0) > 0:
        try:
            from app.services.videos import fetch, resolve_media

            video = fetch(int(video_id))
            return resolve_media(video), video
        except EngineError:
            return None
    name = _safe_generated(video_name)
    if not name:
        return None
    for folder in _video_dirs():
        path = folder / name
        if path.is_file():
            return path, {"id": 0, "title": name, "filename": name}
    return None


def attach_inserts(beats: list[dict]) -> tuple[list[dict], list[str]]:
    """Copy beats and attach filesystem paths. Missing inserts are cleared, not fatal."""
    warnings: list[str] = []
    ready: list[dict] = []
    for beat in beats:
        item = dict(beat)
        kind = str(item.get("insert_kind") or "none")
        label = str(item.get("id") or "这一段")
        if kind == "image":
            path = resolve_image(int(item.get("image_id") or 0), str(item.get("image_name") or ""))
            if path is None:
                warnings.append(f"{label} 的插图找不到，已改回原画面。")
                item["insert_kind"] = "none"
            else:
                item["_image_path"] = str(path)
        elif kind == "video":
            found = resolve_broll(int(item.get("video_id") or 0), str(item.get("video_name") or ""))
            if found is None:
                warnings.append(f"{label} 的插入视频找不到，已改回原画面。")
                item["insert_kind"] = "none"
            else:
                item["_broll_path"] = str(found[0])
        ready.append(item)
    return ready, warnings
