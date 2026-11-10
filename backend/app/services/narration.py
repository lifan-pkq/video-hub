"""AI 解说领域服务：引擎客户端 + 本地任务台账 + 成片统一入库。

设计要点：
- NarratoAI 只作为"生成引擎"运行，前端不再 iframe 它的 Streamlit 页面。
- 解说任务是 Video Hub 的一等公民：SQLite 有记录、视频库有归档、来源可追溯。
- 引擎不可用时给出明确错误，不把半成品写进视频库。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from app.config import (
    NARRATO_ENGINE_TIMEOUT,
    NARRATO_ENGINE_TOKEN,
    NARRATO_ENGINE_URL,
    NARRATO_TASKS_DIR,
)
from app.db import (
    create_narration_task,
    get_narration_task,
    get_video,
    list_narration_tasks,
    update_narration_task,
)
from app.services import narrato_import

logger = logging.getLogger(__name__)

ENGINE_TIMEOUT = max(2.0, float(NARRATO_ENGINE_TIMEOUT or 8))
TERMINAL = {"success", "needs_review", "failed", "cancelled"}
CONTENT_TYPES = {"auto", "knowledge", "documentary", "vlog", "product", "tutorial", "interview", "film", "custom"}
PERSONAS = {"auto", "chenze", "chenbo"}
PERSONA_STRENGTHS = {"natural", "signature", "strong"}
ASPECTS = {"auto", "9:16", "16:9", "1:1", "3:4", "4:3"}
SCRIPT_MODES = {"auto", "visual", "transcript", "asr", "script"}
LANGUAGES = {"中文", "英文", "中英双语"}


class NarrationError(RuntimeError):
    """解说任务的业务错误。"""


class EngineUnavailable(NarrationError):
    """引擎进程不可达。"""


class EngineRejected(NarrationError):
    """引擎拒绝请求（参数/配置问题）。"""


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if NARRATO_ENGINE_TOKEN:
        headers["X-Engine-Token"] = NARRATO_ENGINE_TOKEN
    return headers


def _request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
    """引擎请求：瞬时错误（超时、502/503/504）最多重试 2 次，间隔约 1 秒。

    400 类参数错误不重试。EngineRejected 会带上 status_code，供 _sync
    判断瞬时错误时使用。
    """
    url = f"{NARRATO_ENGINE_URL}{path}"
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.request(
                method,
                url,
                json=payload,
                headers=_headers(),
                timeout=timeout or ENGINE_TIMEOUT,
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(1)
                continue
            raise EngineUnavailable(f"AI 解说引擎不可用：{exc}") from exc
        if response.status_code in (502, 503, 504) and attempt < 2:
            last_exc = RuntimeError(f"HTTP {response.status_code}")
            time.sleep(1)
            continue
        try:
            data = response.json()
        except ValueError as exc:
            raise EngineRejected(f"引擎返回了无效响应（HTTP {response.status_code}）") from exc
        if response.status_code >= 400 or not isinstance(data, dict):
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("detail") or data.get("error") or "")
            exc = EngineRejected(detail or f"引擎请求失败（HTTP {response.status_code}）")
            exc.status_code = response.status_code  # type: ignore[attr-defined]
            raise exc
        return data
    # 正常情况下循环内已返回或抛错；这里只为类型检查兜底。
    raise EngineUnavailable(f"AI 解说引擎不可用：{last_exc}")


def health() -> dict[str, Any]:
    return _request("GET", "/health", timeout=4)


def capabilities() -> dict[str, Any]:
    data = _request("GET", "/capabilities", timeout=6)
    data["available"] = True
    data["engine_url"] = NARRATO_ENGINE_URL
    return data


def settings() -> dict[str, Any]:
    data = _request("GET", "/settings", timeout=6)
    return data.get("settings") or {}


def update_settings(payload: dict[str, Any]) -> dict[str, Any]:
    data = _request("PUT", "/settings", payload=payload, timeout=10)
    return data.get("settings") or {}


def preview_voice(payload: dict[str, Any]) -> Path:
    data = _request("POST", "/tts/preview", payload=payload, timeout=180)
    preview = data.get("preview") or {}
    name = Path(str(preview.get("name") or "")).name
    path = NARRATO_TASKS_DIR.parent / "previews" / name
    if not name or not path.is_file():
        raise NarrationError("试听文件未生成")
    return path


def _aspect_for(video: dict[str, Any], requested: str) -> str:
    if requested in ASPECTS and requested != "auto":
        return requested
    width = float(video.get("width") or 0)
    height = float(video.get("height") or 0)
    if width <= 0 or height <= 0:
        return "9:16"
    ratio = width / height
    if ratio > 1.2:
        return "16:9"
    if ratio > 0.9:
        return "1:1"
    if ratio > 0.65:
        return "3:4"
    return "9:16"


def _text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _engine_spec(video: dict[str, Any], payload: dict[str, Any], task_id: str) -> dict[str, Any]:
    # 截断标记：先按原始长度判断，再截断。_public 会原样回显这些标记，
    # 让前端能看到“文稿被截断了”，而不是静默丢尾巴。
    script_truncated = len(str(payload.get("script_text") or "").strip()) > 20000
    transcript_truncated = len(str(payload.get("transcript_text") or "").strip()) > 50000
    tone_truncated = len(str(payload.get("tone") or "").strip()) > 200
    script_text = _text(payload.get("script_text"), 20000)
    transcript_text = _text(payload.get("transcript_text"), 50000)
    requested_mode = str(payload.get("mode") or "auto").strip().lower()
    if requested_mode not in SCRIPT_MODES:
        requested_mode = "auto"
    if script_text:
        mode = "script"
    elif requested_mode == "asr":
        mode = "asr"
    elif transcript_text:
        mode = "transcript"
    elif requested_mode in {"transcript", "script"}:
        raise NarrationError("缺少文稿：请填写解说稿或口播文稿")
    else:
        mode = "auto"

    content_type = str(payload.get("content_type") or "auto").strip().lower()
    if content_type not in CONTENT_TYPES:
        content_type = "auto"
    persona = str(payload.get("persona") or "auto").strip().lower()
    if persona not in PERSONAS:
        persona = "auto"
    persona_strength = str(payload.get("persona_strength") or "natural").strip().lower()
    if persona_strength not in PERSONA_STRENGTHS:
        persona_strength = "natural"
    language = str(payload.get("language") or "中文").strip()
    if language not in LANGUAGES:
        language = "中文"

    def number(name: str, default: float, low: float, high: float) -> float:
        try:
            value = float(payload.get(name, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    output_mode = "edit" if str(payload.get("output_mode") or "narration").strip().lower() == "edit" else "narration"
    title = _text(payload.get("title"), 80) or f"AI 解说 · {video.get('title') or ''}"
    if output_mode == "edit" and not title.startswith("AI 剪辑"):
        title = f"AI 剪辑 · {title}"
    title = title[:80]
    from app.services.profiles import resolve_bundle

    bundle = resolve_bundle(
        _text(payload.get("character_profile_id"), 80),
        _text(payload.get("scene_profile_id"), 80),
        _text(payload.get("style_profile_id"), 80),
    )
    engine_bundle = dict(bundle)
    if bundle.get("character"):
        engine_bundle["character"] = {
            **bundle["character"],
            "references": [
                {key: value for key, value in reference.items() if key != "data_uri"}
                for reference in bundle["character"].get("references") or []
            ],
        }
    engine_bundle["references"] = engine_bundle["character"].get("references", []) if engine_bundle.get("character") else []

    return {
        "task_id": task_id,
        "video_path": str(Path(video["path"])),
        "video_width": int(video.get("width") or 0),
        "video_height": int(video.get("height") or 0),
        "video_duration": float(video.get("duration") or 0),
        "title": title,
        "content_type": content_type,
        "persona": persona,
        "persona_strength": persona_strength,
        "topic": _text(payload.get("topic"), 500),
        "instruction": _text(payload.get("instruction"), 1500),
        "tone": _text(payload.get("tone"), 200),
        "script_truncated": script_truncated,
        "transcript_truncated": transcript_truncated,
        "tone_truncated": tone_truncated,
        "target_duration": int(number("target_duration", 60, 5, 1800)),
        "language": language,
        "mode": mode,
        "clip_mode": "narration" if str(payload.get("clip_mode") or "timeline").strip().lower() == "narration" else "timeline",
        "coverage": "full" if str(payload.get("coverage") or "highlights").strip().lower() == "full" else "highlights",
        "analysis_mode": "fast" if str(payload.get("analysis_mode") or "deep").strip().lower() == "fast" else "deep",
        "output_mode": output_mode,
        "pause_between": bool(payload.get("pause_between", True)),
        "pause_seconds": number("pause_seconds", 2.0, 0.5, 6.0),
        "review_mode": bool(payload.get("review_mode", True)),
        "tts_pronunciation": bool(payload.get("tts_pronunciation", True)),
        "aspect": _aspect_for(video, str(payload.get("aspect") or "auto")),
        "voice_name": _text(payload.get("voice_name"), 80) or "zh-CN-XiaoxiaoNeural",
        "tts_engine": _text(payload.get("tts_engine"), 40) or "edge_tts",
        "voice_rate": number("voice_rate", 1.0, 0.5, 2.0),
        "voice_emotion": _text(payload.get("voice_emotion"), 20) if _text(payload.get("voice_emotion"), 20) in {"平静", "激动", "哽咽", "幽默"} else "平静",
        "original_volume": number("original_volume", 0.35, 0.0, 1.5),
        "subtitles": bool(payload.get("subtitles", True)),
        "font_name": _text(payload.get("font_name"), 120),
        "font_size": int(number("font_size", 54, 20, 160)),
        "text_fore_color": _text(payload.get("text_fore_color"), 9) or "#FFFFFF",
        "stroke_color": _text(payload.get("stroke_color"), 9) or "#000000",
        "stroke_width": number("stroke_width", 1.5, 0.0, 10.0),
        "subtitle_position": _text(payload.get("subtitle_position"), 10) or "bottom",
        "custom_position": number("custom_position", 70.0, 0.0, 100.0),
        "subtitle_y_percent": number("subtitle_y_percent", 88.0, 0.0, 99.0),
        "subtitle_max_chars": int(number("subtitle_max_chars", 12, 6, 30)),
        "subtitle_mask_enabled": bool(payload.get("subtitle_mask_enabled", False)),
        "subtitle_mask_y": number("subtitle_mask_y", 78.0, 0.0, 99.0),
        "subtitle_mask_height": number("subtitle_mask_height", 14.0, 2.0, 60.0),
        "subtitle_mask_blur": int(number("subtitle_mask_blur", 18, 0, 60)),
        "subtitle_mask_opacity": int(number("subtitle_mask_opacity", 82, 0, 100)),
        "script_text": script_text,
        "transcript_text": transcript_text,
        "character_profile_id": _text(payload.get("character_profile_id"), 80),
        "scene_profile_id": _text(payload.get("scene_profile_id"), 80),
        "style_profile_id": _text(payload.get("style_profile_id"), 80),
        "profile_snapshot": engine_bundle,
        "pronunciation": bundle.get("pronunciation") or {},
        "variant_group_id": _text(payload.get("variant_group_id"), 80),
        "variant_key": _text(payload.get("variant_key"), 20),
    }


def _video_row(video_id: int) -> dict[str, Any]:
    video = get_video(video_id)
    if not video:
        raise NarrationError("视频不存在")
    from app.config import VIDEOS_DIR

    path = VIDEOS_DIR / video["filename"]
    if not path.is_file():
        raise NarrationError("视频文件已丢失")
    return {**video, "path": str(path)}


def create_task(video_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    video = _video_row(video_id)
    task_id = "narrate-" + uuid.uuid4().hex[:12]
    spec = _engine_spec(video, payload, task_id)
    title = spec["title"] or f"AI 解说 · {video['title']}"[:80]

    engine = _request("POST", "/tasks", payload=spec, timeout=max(15.0, ENGINE_TIMEOUT))
    engine_task = engine.get("task") or {}
    now = datetime.now().isoformat(timespec="seconds")
    row = create_narration_task(
        id=task_id,
        video_id=video_id,
        title=title,
        content_type=spec["content_type"],
        mode=spec["mode"],
        aspect=spec["aspect"],
        voice_name=spec["voice_name"],
        target_duration=spec["target_duration"],
        status=str(engine_task.get("status") or "queued"),
        progress=float(engine_task.get("progress") or 0),
        message=str(engine_task.get("message") or "任务已提交"),
        engine_task_id=task_id,
        request_json=spec,
        created_at=now,
        updated_at=now,
    )
    return _public(row, with_source=True)


def _result_path(row: dict[str, Any]) -> Path | None:
    """优先用共享任务目录推导，兼容引擎与后端不同挂载点。"""
    task_id = row.get("engine_task_id") or row.get("id")
    if task_id:
        candidate = Path(NARRATO_TASKS_DIR) / str(task_id) / "combined.mp4"
        if candidate.is_file():
            return candidate
    result = row.get("result") or {}
    raw = result.get("output_path") if isinstance(result, dict) else None
    if raw:
        candidate = Path(str(raw))
        if candidate.is_file():
            return candidate
    return None


def import_result(task_id: str) -> dict[str, Any]:
    row = get_narration_task(task_id)
    if not row:
        raise NarrationError("任务不存在")
    if row.get("output_video_id"):
        return _public(row, with_source=True)
    source = _result_path(row)
    if not source:
        raise NarrationError("成片尚未生成或任务目录不可见")
    try:
        video = narrato_import.import_task(task_id, source, title=row.get("title"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI 解说成片入库失败")
        update_narration_task(task_id, message=f"成片已生成，入库失败：{exc}", error=str(exc))
        raise NarrationError(f"成片入库失败：{exc}") from exc
    update_narration_task(task_id, output_video_id=video["id"], error="")
    row = get_narration_task(task_id) or row
    return _public(row, with_source=True)


def _sync(row: dict[str, Any], force: bool = False) -> dict[str, Any]:
    status = row.get("status")
    # 本地已 cancelled 的任务永远以 cancelled 为准，force 也不复活。
    if status == "cancelled":
        return row
    # force=True 时跳过终态提前返回（approve 流程用），非 force 时保持原有终态短路。
    if not force and status in TERMINAL and (status != "success" or row.get("output_video_id") or row.get("error")):
        return row
    engine_task_id = row.get("engine_task_id") or row.get("id")
    try:
        data = _request("GET", f"/tasks/{engine_task_id}", timeout=min(ENGINE_TIMEOUT, 4))
    except EngineUnavailable as exc:
        update_narration_task(row["id"], message=str(exc))
        return get_narration_task(row["id"]) or row
    except NarrationError as exc:
        # 瞬时错误不直接置 failed：保持原状态、只记 message，等下次同步。
        # 500/502/503/504、无效 JSON、超时都算瞬时；其他（如 400/404）仍按原逻辑置 failed。
        text = str(exc)
        transient = (
            getattr(exc, "status_code", None) in (500, 502, 503, 504)
            or "无效响应" in text
            or "超时" in text
            or "timeout" in text.lower()
            or any(code in text for code in ("HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"))
        )
        if transient:
            update_narration_task(row["id"], message=f"引擎同步暂失败，等待下次同步：{exc}")
            return get_narration_task(row["id"]) or row
        update_narration_task(row["id"], status="failed", message=str(exc), error=str(exc))
        return get_narration_task(row["id"]) or row

    task = data.get("task") or {}
    engine_status = str(task.get("status") or row.get("status") or "queued")
    fields: dict[str, Any] = {
        "status": engine_status,
        "progress": float(task.get("progress") or 0),
        "message": str(task.get("message") or ""),
        "error": str(task.get("error") or ""),
    }
    if task.get("result"):
        fields["result_json"] = task["result"]
    if engine_status in TERMINAL:
        fields["finished_at"] = task.get("finished_at") or datetime.now().isoformat(timespec="seconds")
    # 主动取消后引擎可能仍报告 running 的短暂窗口，以本地 cancelled 为准。
    if status == "cancelled" and engine_status == "running":
        fields["status"] = "cancelled"
    if fields["status"] == "success" and not row.get("output_video_id"):
        source = Path(NARRATO_TASKS_DIR) / str(engine_task_id) / "combined.mp4"
        request = row.get("request") or {}
        source_platform = "ai_edit" if request.get("output_mode") == "edit" else "narrato"
        try:
            video = narrato_import.import_task(
                engine_task_id,
                source,
                title=row.get("title"),
                source_platform=source_platform,
            )
            fields["output_video_id"] = video["id"]
            fields["message"] = "视频生成完成，已加入视频库"
            fields["error"] = ""
        except Exception as exc:  # noqa: BLE001
            logger.warning("成片入库暂未成功，稍后重试：%s", exc)
            fields["message"] = f"成片已生成，等待入库：{exc}"
            fields["error"] = str(exc)
    update_narration_task(row["id"], **fields)
    return get_narration_task(row["id"]) or row


def _public(row: dict[str, Any], with_source: bool = False) -> dict[str, Any]:
    item = dict(row)
    request = item.pop("request", None)
    result = item.pop("result", None)
    item["has_script"] = bool(isinstance(request, dict) and request.get("mode") in {"auto", "transcript"})
    if isinstance(request, dict):
        item["request"] = {
            key: request.get(key)
            for key in (
                "content_type",
                "persona",
                "persona_strength",
                "mode",
                "topic",
                "instruction",
                "tone",
                "tone_truncated",
                "script_truncated",
                "transcript_truncated",
                "voice_name",
                "voice_rate",
                "voice_emotion",
                "tts_engine",
                "target_duration",
                "aspect",
                "language",
                "original_volume",
                "subtitles",
                "font_name",
                "font_size",
                "text_fore_color",
                "stroke_color",
                "stroke_width",
                "subtitle_position",
                "subtitle_y_percent",
                "subtitle_max_chars",
                "subtitle_mask_enabled",
                "clip_mode",
                "coverage",
                "analysis_mode",
                "output_mode",
                "pause_between",
                "pause_seconds",
                "review_mode",
                "tts_pronunciation",
                "character_profile_id",
                "scene_profile_id",
                "style_profile_id",
                "variant_group_id",
                "variant_key",
            )
            if request.get(key) not in (None, "")
        }
    if isinstance(result, dict):
        item["result"] = {
            key: result.get(key)
            for key in ("duration", "width", "height", "size", "filename", "completed_at", "quality")
            if result.get(key) not in (None, "")
        }
    if with_source:
        video = get_video(item["video_id"]) if item.get("video_id") else None
        item["source_video"] = (
            {
                "id": video["id"],
                "title": video["title"],
                "cover_url": f"/api/videos/{video['id']}/cover" if video.get("cover") else None,
                "duration": video.get("duration"),
            }
            if video
            else None
        )
        output = get_video(item["output_video_id"]) if item.get("output_video_id") else None
        item["output_video"] = (
            {
                "id": output["id"],
                "title": output["title"],
                "cover_url": f"/api/videos/{output['id']}/cover" if output.get("cover") else None,
                "file_url": f"/api/videos/{output['id']}/file",
                "duration": output.get("duration"),
                "width": output.get("width"),
                "height": output.get("height"),
            }
            if output
            else None
        )
    return item


def get_task(task_id: str, sync: bool = True) -> dict[str, Any]:
    row = get_narration_task(task_id)
    if not row:
        raise NarrationError("任务不存在")
    if sync:
        row = _sync(row)
    return _public(row, with_source=True)


def list_tasks(limit: int = 30) -> list[dict[str, Any]]:
    rows = list_narration_tasks(limit)
    synced: list[dict[str, Any]] = []
    budget = 6
    for row in rows:
        if budget > 0 and (row.get("status") in {"queued", "running", "awaiting_review"} or (row.get("status") == "success" and not row.get("output_video_id"))):
            row = _sync(row)
            budget -= 1
        synced.append(_public(row, with_source=True))
    return synced


def cancel_task(task_id: str) -> dict[str, Any]:
    row = get_narration_task(task_id)
    if not row:
        raise NarrationError("任务不存在")
    if row.get("status") in TERMINAL:
        return _public(row, with_source=True)
    engine_task_id = row.get("engine_task_id") or row.get("id")
    try:
        _request("POST", f"/tasks/{engine_task_id}/cancel", payload={}, timeout=10)
    except NarrationError:
        # 引擎已挂或任务已不在引擎：本地也收口，避免前端永远显示进行中。
        update_narration_task(
            task_id,
            status="cancelled",
            message="引擎不可用或任务已丢失，已在本地取消",
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
        return get_task(task_id, sync=False)
    update_narration_task(
        task_id,
        status="cancelled",
        message="已取消",
        finished_at=datetime.now().isoformat(timespec="seconds"),
    )
    return get_task(task_id, sync=False)


def approve_task(task_id: str) -> dict[str, Any]:
    row = get_narration_task(task_id)
    if not row:
        raise NarrationError("任务不存在")
    engine_task_id = row.get("engine_task_id") or row.get("id")
    try:
        _request("POST", f"/tasks/{engine_task_id}/approve", payload={}, timeout=15)
    except NarrationError as exc:
        raise NarrationError(f"确认渲染失败：{exc}") from exc
    update_narration_task(
        task_id,
        status="queued",
        message="审片已确认，等待渲染",
        error="",
    )
    return _sync(get_narration_task(task_id) or row, force=True)


def update_task_plan(task_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    """保存完整审片结构，由引擎校验时间戳、排序和渲染字段。"""
    row = get_narration_task(task_id)
    if not row:
        raise NarrationError("任务不存在")
    engine_task_id = row.get("engine_task_id") or row.get("id")
    data = _request("PUT", f"/tasks/{engine_task_id}/script", payload={"items": items}, timeout=15)
    return {"task_id": task_id, "items": data.get("items") or []}


def task_plan(task_id: str) -> dict[str, Any]:
    row = get_narration_task(task_id)
    if not row:
        raise NarrationError("任务不存在")
    engine_task_id = row.get("engine_task_id") or row.get("id")
    path = Path(NARRATO_TASKS_DIR) / str(engine_task_id) / "analysis" / "plan.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        script_path = Path(NARRATO_TASKS_DIR) / str(engine_task_id) / "script.json"
        try:
            items = json.loads(script_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise NarrationError("规划尚未生成") from exc
        data = {"items": items}
    data["task_id"] = task_id
    analysis_dir = Path(NARRATO_TASKS_DIR) / str(engine_task_id) / "analysis"
    for filename, key in (("storyboard.json", "storyboard"), ("script_review.json", "script_review")):
        try:
            data[key] = json.loads((analysis_dir / filename).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    return data


def compose_variants(base_task_id: str, selections: list[dict[str, Any]]) -> dict[str, Any]:
    base = get_narration_task(base_task_id)
    if not base:
        raise NarrationError("基础方案不存在")
    if not selections or len(selections) > 200:
        raise NarrationError("请选择要拼接的方案段落")
    combined: list[dict[str, Any]] = []
    segment_ids: set[str] = set()
    for selection in selections:
        source_id = str(selection.get("source_task_id") or "")
        segment_id = str(selection.get("segment_id") or "")
        if not source_id or not segment_id:
            raise NarrationError("跨版拼段缺少来源方案或段落 ID")
        plan = task_plan(source_id)
        source = next((item for item in plan.get("items") or [] if str(item.get("segment_id") or item.get("_id")) == segment_id), None)
        if not source:
            raise NarrationError(f"方案 {source_id} 中找不到段落 {segment_id}")
        composed_id = f"{source_id}:{segment_id}"
        if composed_id in segment_ids:
            raise NarrationError(f"同一段落被重复选择：{segment_id}")
        segment_ids.add(composed_id)
        combined.append({**source, "origin_segment_id": segment_id, "segment_id": composed_id})
    for index, item in enumerate(combined):
        item["_id"] = index + 1
    task_id = "narrate-" + uuid.uuid4().hex[:12]
    request = dict(base.get("request") or {})
    request.update({
        "task_id": task_id,
        "title": f"{base.get('title') or 'AI 解说'} · 跨版精选"[:80],
        "mode": "script",
        "script_text": "",
        "transcript_text": "",
        "script_items": combined,
        "review_mode": True,
        "variant_key": "composed",
    })
    engine = _request("POST", "/tasks", payload=request, timeout=max(15.0, ENGINE_TIMEOUT))
    engine_task = engine.get("task") or {}
    now = datetime.now().isoformat(timespec="seconds")
    row = create_narration_task(
        id=task_id,
        video_id=base["video_id"],
        title=request["title"],
        content_type=request.get("content_type") or "auto",
        mode="script",
        aspect=request.get("aspect"),
        voice_name=request.get("voice_name"),
        target_duration=request.get("target_duration"),
        status=str(engine_task.get("status") or "queued"),
        progress=float(engine_task.get("progress") or 0),
        message=str(engine_task.get("message") or "跨版拼段已提交"),
        engine_task_id=task_id,
        request_json=request,
        created_at=now,
        updated_at=now,
    )
    return _public(row, with_source=True)


def retry_import(task_id: str) -> dict[str, Any]:
    row = get_narration_task(task_id)
    if not row:
        raise NarrationError("任务不存在")
    if row.get("status") not in {"success", "needs_review"}:
        raise NarrationError("成片尚未生成，不能复核入库")
    return import_result(task_id)
