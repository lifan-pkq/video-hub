from __future__ import annotations

import copy
import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable

from app.config import DATA_DIR

DRAMA_DIR = DATA_DIR / "drama"
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def task_id_for(project_id: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{12}", project_id or ""):
        raise ValueError("项目编号不合法")
    return f"drama-{project_id}"


def _project_lock(project_id: str) -> threading.RLock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(project_id, threading.RLock())


def _project_path(project_id: str) -> Path:
    task_id_for(project_id)
    return DRAMA_DIR / project_id / "project.json"


def _artifact(
    project_id: str,
    value: Any,
    legacy_url: Any,
    *,
    revision: int,
    attempts: int,
    legacy: bool,
) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    name = str(source.get("name") or (value if isinstance(value, str) else "") or "")
    url = str(source.get("url") or legacy_url or "")
    if not name and url:
        name = Path(url).name
    if name and not url:
        url = f"/api/drama/projects/{project_id}/files/{name}"
    quality = source.get("quality")
    artifact_revision = int(source.get("revision") or revision)
    legacy_accepted = bool(
        artifact_revision == revision
        and (
            source.get("legacy_accepted")
            or (legacy and revision == 1 and name and quality is None)
        )
    )
    artifact = {
        "name": name,
        "url": url,
        "status": str(source.get("status") or ("ready" if name else "pending")),
        "attempts": int(source.get("attempts") if source.get("attempts") is not None else attempts),
        "quality": copy.deepcopy(quality),
        "revision": artifact_revision,
        "legacy_accepted": legacy_accepted,
    }
    return artifact


def normalize_project(project: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(project)
    project_id = str(normalized.get("id") or "")
    task_id = task_id_for(project_id)
    legacy = normalized.get("schema_version") != 2
    normalized["schema_version"] = 2
    normalized["task_id"] = task_id
    normalized["title"] = str(normalized.get("title") or "")
    normalized["logline"] = str(normalized.get("logline") or "")
    normalized["status"] = normalized.get("status") or "pending"
    normalized["planned"] = bool(normalized.get("planned"))
    normalized["stage"] = str(normalized.get("stage") or "")
    normalized["progress"] = normalized.get("progress") or 0
    normalized["error"] = normalized.get("error") or ""
    normalized["novel_chars"] = int(normalized.get("novel_chars") or 0)
    normalized["logs"] = list(normalized.get("logs") or [])
    options = normalized.get("options")
    if not isinstance(options, dict):
        options = {}
        normalized["options"] = options
    options.setdefault("quality_threshold", 70)
    options.setdefault("storyboard_frames", 3)
    normalized.setdefault("characters", [])
    normalized.setdefault("episodes", [])
    options.setdefault("episodes_count", len(normalized["episodes"]) or 1)
    options.setdefault("aspect_ratio", "9:16")
    options.setdefault("style", "realistic")
    options.setdefault("burn_subtitles", False)
    normalized.setdefault("created_at", "")
    normalized.setdefault("updated_at", normalized.get("created_at") or "")

    for character_position, character in enumerate(normalized.get("characters") or [], start=1):
        character["id"] = str(character.get("id") or f"c{character_position}")
        character["name"] = str(character.get("name") or f"角色{character_position}")
        character.setdefault("age", None)
        for key in ("gender", "appearance", "clothing", "personality", "profile_id", "anchor", "anchor_url"):
            character[key] = str(character.get(key) or "")
        character["status"] = str(character.get("status") or "pending")

    for episode_position, episode in enumerate(normalized.get("episodes") or [], start=1):
        episode_index = int(episode.get("index") or episode_position)
        episode["index"] = episode_index
        episode["title"] = str(episode.get("title") or f"第 {episode_index} 集")
        episode["summary"] = str(episode.get("summary") or "")
        episode["source_excerpt"] = str(episode.get("source_excerpt") or "")
        for key in ("video", "video_url", "srt", "srt_url"):
            episode[key] = str(episode.get(key) or "")
        episode["status"] = str(episode.get("status") or "pending")
        episode.setdefault("shots", [])
        for shot_position, shot in enumerate(episode.get("shots") or [], start=1):
            shot_index = int(shot.get("index") or shot_position)
            revision = int(shot.get("revision") or 1)
            attempts = int(shot.get("attempts") or 0)
            shot["index"] = shot_index
            shot["id"] = str(shot.get("id") or f"e{episode_index}s{shot_index}")
            shot["revision"] = revision
            shot["status"] = shot.get("status") or "pending"
            shot["prompt"] = str(shot.get("prompt") or "")
            shot["scene"] = str(shot.get("scene") or "")
            shot["action"] = str(shot.get("action") or "")
            shot["expression"] = str(shot.get("expression") or "")
            shot["camera"] = str(shot.get("camera") or "固定")
            shot["dialogue"] = str(shot.get("dialogue") or "")
            shot["seconds"] = int(shot.get("seconds") or 5)
            shot["reference_images"] = list(shot.get("reference_images") or [])
            shot["characters"] = list(shot.get("characters") or [])
            image_value = shot.get("image") if isinstance(shot.get("image"), dict) else shot.get("frame", "")
            shot["image"] = _artifact(
                project_id,
                image_value,
                shot.get("frame_url"),
                revision=revision,
                attempts=attempts,
                legacy=legacy,
            )
            shot["video"] = _artifact(
                project_id,
                shot.get("video"),
                shot.get("video_url"),
                revision=revision,
                attempts=attempts,
                legacy=legacy,
            )
            shot["quality_history"] = list(shot.get("quality_history") or [])
            panels = []
            for panel in shot.get("storyboard") or []:
                if not isinstance(panel, dict):
                    continue
                panel_name = str(panel.get("name") or "")
                panels.append({
                    "beat": int(panel.get("beat") or 1),
                    "label": str(panel.get("label") or ""),
                    "name": panel_name,
                    "url": str(panel.get("url") or (
                        f"/api/drama/projects/{project_id}/files/{panel_name}"
                        if panel_name else ""
                    )),
                    "status": str(panel.get("status") or "pending"),
                    "prompt": str(panel.get("prompt") or ""),
                    "quality": copy.deepcopy(panel.get("quality")),
                    "attempts": int(panel.get("attempts") or 0),
                    "revision": int(panel.get("revision") or revision),
                })
            panels.sort(key=lambda item: item["beat"])
            shot["storyboard"] = panels
            shot.pop("frame", None)
            if shot["image"]["url"]:
                shot["frame_url"] = shot["image"]["url"]
            if shot["video"]["url"]:
                shot["video_url"] = shot["video"]["url"]
    return normalized


def load_project(project_id: str) -> dict[str, Any]:
    path = _project_path(project_id)
    with _project_lock(project_id):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("项目不存在或已损坏") from exc
        if not isinstance(raw, dict):
            raise ValueError("项目不存在或已损坏")
        return normalize_project(raw)


def save_project(
    project: dict[str, Any], *, preserve_cancellation: bool = True
) -> dict[str, Any]:
    normalized = normalize_project(project)
    project_id = normalized["id"]
    path = _project_path(project_id)
    with _project_lock(project_id):
        if preserve_cancellation and path.is_file():
            persisted = load_project(project_id)
            if persisted.get("cancel_requested"):
                normalized["cancel_requested"] = True
                project["cancel_requested"] = True
        updated_at = time.strftime("%Y-%m-%d %H:%M:%S")
        normalized["updated_at"] = updated_at
        project["updated_at"] = updated_at
        project["schema_version"] = 2
        project["task_id"] = normalized["task_id"]
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")
        try:
            temp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)
        return normalized


def update_project(project_id: str, mutator: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    with _project_lock(project_id):
        project = load_project(project_id)
        mutator(project)
        return save_project(project, preserve_cancellation=False)


def interrupt_active_projects() -> int:
    interrupted = 0
    if not DRAMA_DIR.is_dir():
        return interrupted
    for path in DRAMA_DIR.glob("*/project.json"):
        project_id = path.parent.name
        try:
            project = load_project(project_id)
            if project.get("status") not in {"pending", "running"}:
                continue

            def interrupt(current: dict[str, Any]) -> None:
                if current.get("status") not in {"pending", "running"}:
                    return
                current["status"] = "interrupted"
                current["stage"] = "服务重启，等待继续"
                current["error"] = "服务重启，生成中断；已保留当前产物，可点继续续跑"
                current["recoverable"] = True
                for episode in current.get("episodes") or []:
                    for shot in episode.get("shots") or []:
                        if shot.get("status") == "running":
                            shot["status"] = "pending"

            update_project(project_id, interrupt)
            interrupted += 1
        except (OSError, ValueError):
            continue
    return interrupted


def task_view(project: dict[str, Any], *, with_logs: bool = False) -> dict[str, Any]:
    active = project.get("status") in {"pending", "running"}
    view = {
        "id": project["task_id"],
        "type": "ai_drama",
        "kind": "ai_drama",
        "title": f"AI 漫剧 · {project.get('title') or project['id']}",
        "status": project.get("status") or "pending",
        "progress": project.get("progress") or 0,
        "input": {"project_id": project["id"]},
        "result": {"project_id": project["id"]},
        "error": {"message": project.get("error")} if project.get("error") else None,
        "created_at": project.get("created_at") or "",
        "finished_at": None if active else project.get("updated_at"),
    }
    if with_logs:
        view["logs"] = list(project.get("logs") or [])[-500:]
    return view


def list_task_views(*, with_logs: bool = False) -> list[dict[str, Any]]:
    if not DRAMA_DIR.is_dir():
        return []
    views = []
    for path in DRAMA_DIR.glob("*/project.json"):
        try:
            views.append(task_view(load_project(path.parent.name), with_logs=with_logs))
        except (OSError, ValueError):
            continue
    return views


def find_project_by_task_id(task_id: str) -> dict[str, Any] | None:
    match = re.fullmatch(r"drama-([a-f0-9]{12})", task_id or "")
    if not match:
        return None
    try:
        return load_project(match.group(1))
    except (OSError, ValueError):
        return None


def _find_shot(project: dict[str, Any], shot_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for episode in project.get("episodes") or []:
        for shot in episode.get("shots") or []:
            if shot.get("id") == shot_id:
                return episode, shot
    raise ValueError("镜头不存在")


def validate_reference_name(value: Any) -> str:
    name = str(value or "")
    if not re.fullmatch(r"(?:ref|lib)_[0-9a-f]{32}\.(?:png|jpg|jpeg|webp)", name):
        raise ValueError("参考图必须是安全的 ref_ 或 lib_ 文件名")
    return name


def update_shot(
    project_id: str,
    shot_id: str,
    *,
    prompt: str | None = None,
    seconds: int | None = None,
    reference_images: list[str] | None = None,
) -> dict[str, Any]:
    if prompt is None and seconds is None and reference_images is None:
        raise ValueError("没有需要修改的镜头字段")
    if seconds is not None and not 4 <= seconds <= 12:
        raise ValueError("镜头时长必须为 4 到 12 秒")
    references = None
    if reference_images is not None:
        references = [validate_reference_name(name) for name in reference_images]
    image_changed = prompt is not None or references is not None
    updated: dict[str, Any] = {}

    def update(project: dict[str, Any]) -> None:
        nonlocal updated
        episode, shot = _find_shot(project, shot_id)
        if prompt is not None:
            shot["prompt"] = prompt
        if seconds is not None:
            shot["seconds"] = seconds
        if references is not None:
            shot["reference_images"] = references
        shot["revision"] = int(shot.get("revision") or 1) + 1
        shot["status"] = "pending"
        shot["error"] = ""
        shot["recoverable"] = False
        if image_changed:
            shot["image"].update({
                "status": "stale",
                "quality": None,
                "legacy_accepted": False,
            })
        elif shot["image"].get("status") == "ready" and (
            (shot["image"].get("quality") or {}).get("passed") is True
            or (
                shot["image"].get("quality") is None
                and shot["image"].get("legacy_accepted") is True
            )
        ):
            shot["image"]["revision"] = shot["revision"]
        shot["video"]["status"] = "stale"
        shot["video"]["legacy_accepted"] = False
        episode["status"] = "stale"
        if not project.get("cancel_requested"):
            project["status"] = "waiting_review"
            project["error"] = f"镜头 {shot_id} 已修改，请重试生成"
        updated = copy.deepcopy(shot)

    update_project(project_id, update)
    return updated


def mark_shot_cancelled(
    project_id: str, shot_id: str, *, expected_revision: int | None = None
) -> dict[str, Any]:
    def update(project: dict[str, Any]) -> None:
        episode, shot = _find_shot(project, shot_id)
        if expected_revision is not None and int(shot.get("revision") or 1) != expected_revision:
            return
        shot["status"] = "cancelled"
        shot["error"] = "生成已取消"
        shot["recoverable"] = True
        episode["status"] = "waiting_review"
        if not project.get("cancel_requested"):
            project["status"] = "waiting_review"
            project["error"] = f"镜头 {shot_id} 已取消，可修改后重试"

    return update_project(project_id, update)


def mark_shot_failed(
    project_id: str,
    shot_id: str,
    error: str,
    *,
    recoverable: bool = False,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    def update(project: dict[str, Any]) -> None:
        episode, shot = _find_shot(project, shot_id)
        if expected_revision is not None and int(shot.get("revision") or 1) != expected_revision:
            return
        shot["status"] = "failed"
        shot["error"] = str(error)[:400]
        shot["recoverable"] = recoverable
        episode["status"] = "failed"
        if not project.get("cancel_requested"):
            project["status"] = "failed"
            project["error"] = str(error)[:400]
            project["recoverable"] = recoverable

    return update_project(project_id, update)


def mark_project_cancelled(project_id: str) -> dict[str, Any]:
    def update(project: dict[str, Any]) -> None:
        project["cancel_requested"] = True
        project["status"] = "cancelled"
        project["error"] = ""

    return update_project(project_id, update)


def project_cancelled(project_id: str) -> bool:
    return bool(load_project(project_id).get("cancel_requested"))


def accept_artifact(
    project_id: str,
    shot_id: str,
    stage: str,
    revision: int,
    artifact: dict[str, Any],
) -> bool:
    if stage not in {"image", "video"}:
        raise ValueError("产物阶段不合法")
    incoming = copy.deepcopy(artifact)
    path_value = str(incoming.pop("path", "") or "")
    generated_path = Path(path_value) if path_value else None
    with _project_lock(project_id):
        project = load_project(project_id)
        episode, shot = _find_shot(project, shot_id)
        quality = incoming.get("quality")
        qualified = isinstance(quality, dict) and quality.get("passed") is True
        current = (
            int(shot.get("revision") or 1) == int(revision)
            and not project.get("cancel_requested")
            and shot.get("status") != "cancelled"
        )
        if not current or not qualified:
            if generated_path is not None:
                generated_path.unlink(missing_ok=True)
            return False
        incoming.update({
            "status": "ready",
            "revision": int(revision),
            "legacy_accepted": False,
            "attempts": int(incoming.get("attempts") or 1),
        })
        if not incoming.get("url") and incoming.get("name"):
            incoming["url"] = (
                f"/api/drama/projects/{project_id}/files/{incoming['name']}"
            )
        shot[stage] = incoming
        shot["status"] = "framed" if stage == "image" else "done"
        shot["error"] = ""
        shot["recoverable"] = False
        if stage == "image":
            shot["frame_url"] = incoming.get("url") or ""
        else:
            shot["video_url"] = incoming.get("url") or ""
        episode["status"] = "framed" if stage == "image" else "videos"
        save_project(project, preserve_cancellation=False)
        return True


def accept_board(
    project_id: str,
    shot_id: str,
    beat: int,
    revision: int,
    panel: dict[str, Any],
) -> bool:
    """收录一张连环画分镜图：只接受过质量门的图，否则删文件、不写库。"""
    incoming = copy.deepcopy(panel)
    path_value = str(incoming.pop("path", "") or "")
    generated_path = Path(path_value) if path_value else None
    with _project_lock(project_id):
        project = load_project(project_id)
        episode, shot = _find_shot(project, shot_id)
        quality = incoming.get("quality")
        qualified = isinstance(quality, dict) and quality.get("passed") is True
        current = (
            int(shot.get("revision") or 1) == int(revision)
            and not project.get("cancel_requested")
            and shot.get("status") != "cancelled"
        )
        if not current or not qualified:
            if generated_path is not None:
                generated_path.unlink(missing_ok=True)
            return False
        incoming.update({
            "beat": int(beat),
            "status": "ready",
            "revision": int(revision),
            "attempts": int(incoming.get("attempts") or 1),
        })
        if not incoming.get("url") and incoming.get("name"):
            incoming["url"] = (
                f"/api/drama/projects/{project_id}/files/{incoming['name']}"
            )
        panels = [
            item for item in (shot.get("storyboard") or [])
            if not (
                int(item.get("beat") or 0) == int(beat)
                and int(item.get("revision") or 0) == int(revision)
            )
        ]
        panels.append(incoming)
        panels.sort(key=lambda item: int(item.get("beat") or 0))
        shot["storyboard"] = panels
        save_project(project, preserve_cancellation=False)
        return True


def publish_episode(
    project_id: str,
    episode_index: int,
    expected_revisions: dict[str, int],
    *,
    temp_video: Path,
    temp_srt: Path,
    video_name: str,
    srt_name: str,
    duration: float,
) -> bool:
    with _project_lock(project_id):
        project = load_project(project_id)
        episode = next(
            (item for item in project.get("episodes") or [] if int(item.get("index") or 0) == episode_index),
            None,
        )
        if episode is None or project.get("cancel_requested"):
            return False
        shots = episode.get("shots") or []
        current_revisions = {
            str(shot.get("id")): int(shot.get("revision") or 1) for shot in shots
        }
        if current_revisions != expected_revisions:
            return False
        for shot in shots:
            artifact = shot.get("video") or {}
            quality = artifact.get("quality")
            qualified = (
                isinstance(quality, dict) and quality.get("passed") is True
            ) or (
                quality is None and artifact.get("legacy_accepted") is True
            )
            if (
                shot.get("status") == "cancelled"
                or artifact.get("status") != "ready"
                or int(artifact.get("revision") or 0) != int(shot.get("revision") or 1)
                or not qualified
            ):
                return False
        output = temp_video.parent / video_name
        srt_output = temp_srt.parent / srt_name
        temp_video.replace(output)
        temp_srt.replace(srt_output)
        episode["video"] = video_name
        episode["video_url"] = f"/api/drama/projects/{project_id}/files/{video_name}"
        episode["srt"] = srt_name
        episode["srt_url"] = f"/api/drama/projects/{project_id}/files/{srt_name}"
        episode["duration"] = round(duration, 1)
        episode["status"] = "done"
        episode["composition_revisions"] = dict(expected_revisions)
        save_project(project, preserve_cancellation=False)
        return True
