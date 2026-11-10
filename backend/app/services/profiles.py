"""人物、场景、风格与创作偏好档案；任务保存快照，避免后续编辑改变历史。"""

from __future__ import annotations

import base64
import json
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config import DATA_DIR

PROFILES_DIR = DATA_DIR / "profiles"
REFERENCES_DIR = PROFILES_DIR / "references"
KINDS = {"characters", "scenes", "styles", "preferences"}
_LOCK = threading.RLock()


class ProfileError(ValueError):
    pass


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ProfileError("未知档案类型")
    return kind


def _identifier(value: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", text):
        raise ProfileError("档案 ID 不合法")
    return text


def _path(kind: str, profile_id: str) -> Path:
    return PROFILES_DIR / f"{_kind(kind)}-{_identifier(profile_id)}.json"


def _public(profile: dict[str, Any], include_data: bool = False) -> dict[str, Any]:
    result = dict(profile)
    references = []
    for reference in result.get("references") or []:
        item = dict(reference) if isinstance(reference, dict) else {}
        name = Path(str(item.get("name") or "")).name
        if not name:
            continue
        path = REFERENCES_DIR / name
        if not path.is_file():
            # 引用图丢失不再静默丢弃：保留条目并标记，前端与生成侧都要能看见缺口
            references.append({
                "name": name,
                "url": f"/api/profiles/references/{name}",
                "missing": True,
            })
            continue
        public = {
            "name": name,
            "url": f"/api/profiles/references/{name}",
            "size": path.stat().st_size,
        }
        if include_data:
            mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            public["data_uri"] = f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
        references.append(public)
    result["references"] = references
    return result


def _default_preferences() -> dict[str, Any]:
    """发音词典来源：优先带 is_default 标记的偏好档案，否则取最近更新的一个。

    此前用 list_profiles 的名字排序取第一个，多个偏好档案时会取错。
    """
    rows = list_profiles("preferences")
    if not rows:
        return {}
    marked = [row for row in rows if row.get("is_default")]
    candidates = marked or rows
    return sorted(candidates, key=lambda row: (str(row.get("updated_at") or ""),
                                               str(row.get("name") or "")))[-1]


def list_profiles(kind: str) -> list[dict[str, Any]]:
    _kind(kind)
    rows = []
    for path in PROFILES_DIR.glob(f"{kind}-*.json"):
        try:
            rows.append(_public(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    return sorted(rows, key=lambda item: item.get("name") or item.get("id") or "")


def get_profile(kind: str, profile_id: str, include_data: bool = False) -> dict[str, Any]:
    path = _path(kind, profile_id)
    try:
        return _public(json.loads(path.read_text(encoding="utf-8")), include_data=include_data)
    except FileNotFoundError as exc:
        raise ProfileError("档案不存在") from exc
    except (OSError, ValueError) as exc:
        raise ProfileError("档案无法读取") from exc


def save_profile(kind: str, payload: dict[str, Any], profile_id: str = "") -> dict[str, Any]:
    _kind(kind)
    if not isinstance(payload, dict):
        raise ProfileError("档案必须是 JSON 对象")
    name = str(payload.get("name") or "").strip()[:80]
    if not name:
        raise ProfileError("档案名称不能为空")
    profile_id = profile_id or str(payload.get("id") or uuid.uuid4().hex[:12])
    _identifier(profile_id)
    existing: dict[str, Any] = {}
    try:
        existing = get_profile(kind, profile_id)
    except ProfileError:
        pass
    now = datetime.now().isoformat(timespec="seconds")
    clean = {
        key: value for key, value in payload.items()
        if key not in {"created_at", "updated_at", "version"} and not key.startswith("_")
    }
    clean.update({
        "id": profile_id,
        "kind": kind,
        "name": name,
        "version": int(existing.get("version") or 0) + 1,
        "created_at": existing.get("created_at") or now,
        "updated_at": now,
    })
    if kind == "characters":
        references = clean.get("references") or []
        if not isinstance(references, list) or not 0 <= len(references) <= 5:
            raise ProfileError("人物参考图最多 5 张")
        clean["references"] = [{"name": Path(str(item.get("name") or "")).name} for item in references if isinstance(item, dict)]
    path = _path(kind, profile_id)
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    return _public(clean)


def _reference_users() -> dict[str, set[str]]:
    """参考图当前被哪些人物档案引用：{图片文件名: {档案 ID}}。"""
    users: dict[str, set[str]] = {}
    for path in PROFILES_DIR.glob("characters-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        profile_id = str(data.get("id") or path.stem[len("characters-"):])
        for reference in data.get("references") or []:
            if not isinstance(reference, dict):
                continue
            name = Path(str(reference.get("name") or "")).name
            if name:
                users.setdefault(name, set()).add(profile_id)
    return users


def _snapshot_usage(profile_id: str) -> list[str]:
    """引用该档案的任务快照 ID（任务台账里的 request_json，快照本身不受删除影响）。"""
    try:
        from app.db import list_narration_tasks
    except Exception:  # noqa: BLE001 - 数据库不可用时只失去提示，不影响删除
        return []
    used_by = []
    for task in list_narration_tasks(200):
        request = task.get("request")
        if not isinstance(request, dict):
            continue
        ids = {str(request.get(key) or "") for key in (
            "character_profile_id", "scene_profile_id", "style_profile_id")}
        if profile_id and profile_id in ids:
            used_by.append(str(task.get("id") or ""))
    return used_by


def delete_profile(kind: str, profile_id: str) -> dict[str, Any]:
    """删除档案，并清理删除后再无人物档案引用的参考图；返回快照引用计数与已删图片。"""
    path = _path(kind, profile_id)
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProfileError("档案不存在") from exc
    except (OSError, ValueError) as exc:
        raise ProfileError("档案无法读取") from exc
    if profile.get("builtin"):
        raise ProfileError("内置档案不可删除，可自行新建档案覆盖使用")
    own = [Path(str(item.get("name") or "")).name
           for item in profile.get("references") or [] if isinstance(item, dict)]
    own = [name for name in own if name]
    users = _reference_users()
    for name in own:
        users.get(name, set()).discard(profile_id)
    used_by = _snapshot_usage(profile_id)
    path.unlink()
    removed = []
    for name in own:
        if users.get(name):
            continue
        target = REFERENCES_DIR / name
        if target.is_file():
            target.unlink()
            removed.append(name)
    return {
        "id": profile_id,
        "kind": kind,
        "references_removed": removed,
        "task_snapshots": used_by,
    }


def save_reference(data: bytes, filename: str) -> dict[str, Any]:
    if not data or len(data) > 12 * 1024 * 1024:
        raise ProfileError("参考图不能为空且不能超过 12MB")
    suffix = Path(filename).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ProfileError("参考图只支持 PNG/JPEG/WebP")
    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"profile-{uuid.uuid4().hex}{suffix}"
    (REFERENCES_DIR / name).write_bytes(data)
    return {"name": name, "url": f"/api/profiles/references/{name}", "size": len(data)}


def reference_path(name: str) -> Path:
    safe = Path(name).name
    if safe != name or not safe.startswith("profile-"):
        raise ProfileError("参考图不存在")
    path = REFERENCES_DIR / safe
    if not path.is_file():
        raise ProfileError("参考图不存在")
    return path


def resolve_bundle(character_id: str = "", scene_id: str = "", style_id: str = "") -> dict[str, Any]:
    character = get_profile("characters", character_id, include_data=True) if character_id else None
    scene = get_profile("scenes", scene_id) if scene_id else None
    style = get_profile("styles", style_id) if style_id else None
    pronunciation = _default_preferences().get("pronunciation") or {}
    sections = []
    if character:
        sections.append("人物固定档案：" + json.dumps({
            key: character.get(key) for key in (
                "name", "fixed_features", "clothing", "age_rules", "voice", "catchphrases"
            ) if character.get(key) not in (None, "", [], {})
        }, ensure_ascii=False))
    if scene:
        sections.append("场景固定档案：" + json.dumps({
            key: scene.get(key) for key in ("name", "layout", "lighting", "color_palette", "description")
            if scene.get(key) not in (None, "", [], {})
        }, ensure_ascii=False))
    if style:
        sections.append("系列风格档案：" + json.dumps({
            key: style.get(key) for key in (
                "name", "color_grade", "subtitle", "transitions", "music", "narration_tone"
            ) if style.get(key) not in (None, "", [], {})
        }, ensure_ascii=False))
    return {
        "character": character,
        "scene": scene,
        "style": style,
        "prompt": "\n".join(sections),
        "references": (character or {}).get("references") or [],
        "pronunciation": pronunciation if isinstance(pronunciation, dict) else {},
    }


BUILTIN_SEEDS: list[dict[str, Any]] = [
    {
        "kind": "characters",
        "id": "builtin_chen_mo",
        "name": "陈默",
        "description": "短视频男主，都市青年感，干净利落，适合口播、剧情与产品种草出镜。",
        "fixed_features": "干净利落短发，五官清爽，眉眼利落，皮肤自然偏暖，无明显夸张妆造。",
        "clothing": "深色夹克或简约外套，内搭纯色 T 恤，都市休闲风格，避免夸张 logo。",
        "age_rules": "固定 25-30 岁都市青年形象，不随意变老变幼。",
        "voice": "沉稳清晰的青年男声，语速中等偏快，适合短视频节奏。",
        "catchphrases": "兄弟们、先说结论、划重点",
        "references": [],
        "builtin": True,
    },
    {
        "kind": "characters",
        "id": "builtin_lin_xia",
        "name": "林夏",
        "description": "短视频女主，亲和自然，适合生活分享、口播讲解与轻剧情。",
        "fixed_features": "长直发，五官柔和，气质亲和，妆容清淡自然。",
        "clothing": "简约白 T 或浅色上衣，基础款牛仔裤或休闲裙，干净不花哨。",
        "age_rules": "固定 22-28 岁年轻女性形象，保持清爽亲和。",
        "voice": "柔和清晰的女声，亲切自然，不夸张卖萌。",
        "catchphrases": "今天分享、你们可以试试、重点来了",
        "references": [],
        "builtin": True,
    },
    {
        "kind": "characters",
        "id": "builtin_voiceover",
        "name": "回声旁白",
        "description": "不出镜解说型人物档案：只提供旁白人设与语气，无形象参考图即可。",
        "fixed_features": "不出镜，无固定面部形象；如需画面请依赖场景与素材，不要生成人物脸。",
        "clothing": "无出镜服装要求。",
        "age_rules": "不适用出镜年龄设定。",
        "voice": "沉稳客观的解说旁白，咬字清楚，适合知识科普与纪实叙事。",
        "catchphrases": "换句话说、可以看到、总结一下",
        "references": [],
        "builtin": True,
    },
    {
        "kind": "scenes",
        "id": "builtin_living_day",
        "name": "都市客厅日景",
        "description": "现代都市公寓客厅，白天自然光从落地窗进入，适合口播与生活剧情。",
        "layout": "沙发、茶几、绿植、简洁电视墙；空间整洁，少杂物。",
        "lighting": "白天柔和自然光，主光来自窗户，阴影干净不压抑。",
        "color_palette": "暖白、浅木色、低饱和灰蓝。",
        "builtin": True,
    },
    {
        "kind": "scenes",
        "id": "builtin_neon_street",
        "name": "夜市街景霓虹",
        "description": "都市夜市街景，霓虹招牌与人流，适合氛围感短视频与行走跟拍。",
        "layout": "窄街店铺、霓虹招牌、路边摊位与行人；前景可有轻微运动模糊。",
        "lighting": "夜间霓虹主光，粉紫与青色点缀，路面有湿润反光。",
        "color_palette": "霓虹粉、电光青、深夜蓝黑。",
        "builtin": True,
    },
    {
        "kind": "scenes",
        "id": "builtin_studio_white",
        "name": "极简摄影棚白底",
        "description": "干净白底摄影棚，适合产品展示、人物定妆与无干扰口播。",
        "layout": "纯白无缝背景，地面干净，可有轻柔阴影；无多余道具。",
        "lighting": "柔光箱均匀布光，高键光，阴影浅淡。",
        "color_palette": "纯白、浅灰、中性肤色与产品本色。",
        "builtin": True,
    },
    {
        "kind": "styles",
        "id": "builtin_style_vertical",
        "name": "竖屏短视频",
        "description": "竖屏 9:16 短视频风格：快切、高饱和、信息密度高，适合抖音/短视频平台。",
        "narration_tone": "直接抓人，句短有力，开头三秒给结论。",
        "color_grade": "高饱和、对比偏强，肤色自然偏暖。",
        "transitions": "快切为主，偶尔甩镜/缩放强调重点，少用长淡入淡出。",
        "music": "节奏感强的流行或电子底噪，卡点明显。",
        "subtitle": "大字号居中或底部，关键词高亮。",
        "builtin": True,
    },
    {
        "kind": "styles",
        "id": "builtin_style_cinematic",
        "name": "电影感",
        "description": "电影感影像：浅景深、胶片调色、节奏克制，适合故事短片与品牌片。",
        "narration_tone": "克制、有画面感，允许留白与情绪停顿。",
        "color_grade": "胶片感低饱和，偏青绿阴影与暖色高光。",
        "transitions": "慢推拉、叠化与硬切结合，避免花哨转场特效。",
        "music": "氛围弦乐或低伏电子铺底，不抢人声。",
        "subtitle": "小字号、低干扰，可偏电影字幕排版。",
        "builtin": True,
    },
    {
        "kind": "styles",
        "id": "builtin_style_doc",
        "name": "纪实纪录片",
        "description": "纪实纪录片风格：自然光、手持稳定、沉稳旁白，强调真实感。",
        "narration_tone": "客观沉稳，少煽情，事实与观察优先。",
        "color_grade": "接近自然光原色，轻微对比，不做浓重滤镜。",
        "transitions": "硬切为主，偶尔叠化用于时间跳跃。",
        "music": "极简环境音与轻铺底，音量低于旁白。",
        "subtitle": "清晰可读的说明字幕，不花哨。",
        "builtin": True,
    },
]


def ensure_profile_dirs() -> None:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    REFERENCES_DIR.mkdir(parents=True, exist_ok=True)


def _has_name(kind: str, name: str) -> bool:
    target = str(name or "").strip()
    for row in list_profiles(kind):
        if str(row.get("name") or "").strip() == target:
            return True
    return False


def seed_builtins(force: bool = False) -> dict[str, Any]:
    """安装内置人物/场景/风格；默认幂等，已存在同 id 或同名则跳过。"""
    ensure_profile_dirs()
    created: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    for seed in BUILTIN_SEEDS:
        kind = str(seed["kind"])
        profile_id = str(seed["id"])
        name = str(seed["name"])
        path = _path(kind, profile_id)
        if not force and (path.is_file() or _has_name(kind, name)):
            skipped.append({"kind": kind, "id": profile_id, "name": name})
            continue
        payload = {key: value for key, value in seed.items() if key != "kind"}
        payload["builtin"] = True
        saved = save_profile(kind, payload, profile_id)
        created.append({"kind": kind, "id": saved.get("id") or profile_id, "name": saved.get("name") or name})
    return {
        "ok": True,
        "created": created,
        "skipped": skipped,
        "counts": {
            "characters": len(list_profiles("characters")),
            "scenes": len(list_profiles("scenes")),
            "styles": len(list_profiles("styles")),
        },
    }

