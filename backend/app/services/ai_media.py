"""Agnes 图像/视频生成的任务、档案与素材库兼容层。"""

from __future__ import annotations

import base64
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from app.config import (
    AGNES_VIDEO_MODEL,
    COVERS_DIR,
    DATA_DIR,
    PUBLIC_BASE_URL,
    VIDEOS_DIR,
)
from app.db import add_video
from app.services.ai_pool import AI_POOL
from app.services.media import make_cover, probe_video
from app.services.media_generation import (
    GENERATED_IMAGE_DIR,
    GENERATED_VIDEO_DIR,
    MediaAIError,
    _is_public_url,
    _validate_reference_values,
    generate_image_asset,
    generate_video_asset,
)
from app.services.tasks import task_manager

GENERATED_REFERENCE_DIR = DATA_DIR / "generated" / "references"


def _profile_context(
    prompt: str,
    character_id: str = "",
    scene_id: str = "",
    style_id: str = "",
    transition: str = "",
) -> tuple[str, list[str], dict[str, Any] | None]:
    from app.services.profiles import resolve_bundle

    bundle = resolve_bundle(character_id, scene_id, style_id)
    character = bundle.get("character")
    if not character and re.search(r"人物|男人|女人|老人|女孩|男孩|角色|陈默|回声", prompt):
        raise MediaAIError("含人物的生成必须先选择人物档案，避免每次生成换一张脸")
    sections = [bundle.get("prompt") or "", prompt.strip()]
    if transition == "morph":
        sections.append(
            "关键转场：同一人物在同一机位一对一缓慢 morph，只改变档案年龄规则允许变化的部位；"
            "服装、痣、发型、背景摆设和光线保持连续，禁止叠化出第二个人影或更换场景。"
        )
    references = [item.get("data_uri") or item.get("url") for item in bundle.get("references") or []]
    return "\n".join(part for part in sections if part), [item for item in references if item], character


def _image_consistency(path: Path, character: dict[str, Any] | None) -> dict[str, Any]:
    from app.config import VISION_MODEL

    if not character:
        return {"status": "skipped", "reason": "未使用人物档案"}
    if not VISION_MODEL:
        return {"status": "unknown", "reason": "未配置视觉模型，无法自动比对人物一致性"}
    try:
        from app.services.understanding import _request_json

        content: list[dict[str, Any]] = [{
            "type": "text",
            "text": (
                "对比人物档案参考图和新生成图。检查是否同一人、固定面部特征、服装和年龄规则。"
                "只输出 JSON：{\"score\":0,\"passed\":false,\"mismatches\":[],\"reason\":\"\"}。"
                "80 分及以上才算通过。\n人物档案：" + str({
                    key: character.get(key) for key in ("name", "fixed_features", "clothing", "age_rules")
                })
            ),
        }]
        for reference in character.get("references") or []:
            data_uri = reference.get("data_uri")
            if data_uri:
                content.append({"type": "image_url", "image_url": {"url": data_uri}})
        mime = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        generated = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "text", "text": "下面是新生成图"})
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{generated}"}})
        result = _request_json([{"role": "user", "content": content}], vision=True)
        score = max(0, min(100, float(result.get("score") or 0)))
        return {
            "status": "pass" if score >= 80 and result.get("passed", True) else "review",
            "score": score,
            "mismatches": result.get("mismatches") or [],
            "reason": result.get("reason") or "",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unknown", "reason": f"一致性比对失败：{exc}"[:300]}


def save_reference(data: bytes, filename: str) -> dict[str, Any]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    GENERATED_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ref_{uuid.uuid4().hex}{suffix}"
    target = GENERATED_REFERENCE_DIR / name
    target.write_bytes(data)
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix[1:]}"
    public_ok = _is_public_url(PUBLIC_BASE_URL)
    return {
        # 公网地址不可用时 url 置空，绝不发一个必定失败的 127.0.0.1 地址；
        # 前端应优先用 data_uri（见 AIMediaPage 的 refValue）。
        "url": f"{PUBLIC_BASE_URL}/api/ai/media/references/{name}" if public_ok else "",
        "url_usable": public_ok,
        "url_warning": (
            None if public_ok
            else "PUBLIC_BASE_URL 不是公网地址（当前是内网/回环地址），"
                 "url 字段已置空；请把 VIDEO_HUB_PUBLIC_BASE_URL 改成公网地址，"
                 "或直接用本次返回的 data_uri 作为参考图。"
        ),
        "data_uri": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
        "name": name,
        "size": len(data),
    }


def _require_keys() -> None:
    if not AI_POOL.has_keys():
        raise MediaAIError("没有配置 AI API Key")


def generate_image(
    prompt: str,
    size: str = "2K",
    ratio: str = "16:9",
    images: list[str] | None = None,
    character_profile_id: str = "",
    scene_profile_id: str = "",
    style_profile_id: str = "",
) -> dict[str, Any]:
    _require_keys()
    prompt, profile_refs, character = _profile_context(prompt, character_profile_id, scene_profile_id, style_profile_id)
    all_images = [*profile_refs, *(images or [])][:5]
    result: dict[str, Any] = {}
    for attempt in range(1, 3):
        result = _generate_image_once(prompt, size, ratio, all_images)
        consistency = _image_consistency(GENERATED_IMAGE_DIR / result["name"], character)
        result["consistency"] = consistency
        result["attempts"] = attempt
        if consistency.get("status") != "review":
            break
    return result


def _generate_image_once(prompt: str, size: str, ratio: str, images: list[str]) -> dict[str, Any]:
    media = generate_image_asset(prompt, size=size, ratio=ratio, images=images)
    return {
        "ok": True,
        "url": f"/api/ai/media/images/{media.path.name}",
        "name": media.path.name,
        "model": media.model,
        "size": media.metadata["size"],
    }


def _video_consistency(path: Path, character: dict[str, Any] | None) -> dict[str, Any]:
    if not character:
        return {"status": "skipped", "reason": "未使用人物档案"}
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="consistency-", dir=DATA_DIR / "tmp") as temp:
        frame = Path(temp) / "frame.jpg"
        result = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-ss", "0.5", "-i", str(path),
             "-frames:v", "1", "-q:v", "3", str(frame)],
            capture_output=True, timeout=120, check=False,
        )
        if result.returncode != 0 or not frame.is_file():
            return {"status": "unknown", "reason": "无法抽取生成视频的关键帧"}
        return _image_consistency(frame, character)


def _generate_video_task(
    task,
    prompt: str,
    seconds: str,
    aspect_ratio: str,
    first_frame: str = "",
    last_frame: str = "",
    images: list[str] | None = None,
    save_to_library: bool = True,
    character: dict[str, Any] | None = None,
) -> None:
    task.status = "running"
    mode = "keyframe" if (first_frame or last_frame) else ("reference" if images else "text")
    task.log(f"提交 {AGNES_VIDEO_MODEL}：{seconds}s {aspect_ratio} · {mode}")
    error_chain: list[str] = []

    def update_progress(value: float, message: str) -> None:
        task.progress = value
        task.log(message)

    for attempt in range(1, 4):
        if task.cancelled:
            task.log("任务已取消")
            return
        if attempt > 1:
            task.log(f"第 {attempt}/3 次生成，自动换 Key 重试…")
            task.progress = 0
        try:
            media = generate_video_asset(
                prompt,
                int(seconds),
                aspect_ratio=aspect_ratio,
                first_frame=first_frame,
                last_frame=last_frame,
                images=images,
                should_cancel=lambda: bool(task.cancelled),
                progress=update_progress,
            )
            temp = media.path
            url = media.source_url
            consistency = _video_consistency(temp, character)
            if consistency.get("status") == "review" and attempt == 1:
                temp.unlink(missing_ok=True)
                task.log("人物/场景一致性未通过，自动重生成一次")
                continue
            if consistency.get("status") == "review":
                save_to_library = False
            meta = probe_video(temp)
            title = (prompt[:60] or "AI 生成视频").strip()
            if save_to_library:
                final = VIDEOS_DIR / f"ai_{uuid.uuid4().hex}.mp4"
                shutil.move(str(temp), final)
                cover_name = final.stem + ".jpg"
                cover = cover_name if make_cover(final, COVERS_DIR / cover_name) else None
                video = add_video(
                    title=title,
                    filename=final.name,
                    size=final.stat().st_size,
                    duration=meta.get("duration"),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    cover=cover,
                    source_platform="ai_generated",
                    source_url=url,
                )
                task.result = {
                    "video": video,
                    "video_url": f"/api/videos/{video['id']}/download",
                    "saved_to_library": True,
                    "model": media.model,
                }
            else:
                name = f"ai_{uuid.uuid4().hex}.mp4"
                final = GENERATED_VIDEO_DIR / name
                shutil.move(str(temp), final)
                task.result = {
                    "video": None,
                    "video_url": f"/api/ai/media/videos/{name}",
                    "saved_to_library": False,
                    "title": title,
                    "duration": meta.get("duration"),
                    "size": final.stat().st_size,
                    "model": media.model,
                }
            task.result["consistency"] = consistency
            task.log("视频已生成" + ("并加入素材库" if save_to_library else "（未加入素材库）"))
            task.progress = 100.0
            task.finish("partial" if consistency.get("status") == "review" else "success")
            return
        except Exception as exc:  # noqa: BLE001
            if task.cancelled:
                task.log("任务已取消")
                return
            error_chain.append(f"第 {attempt} 次：{exc}")
            # 排队超时是确定性失败（已等满总时限），外层再重试 3 次只会
            # 把总等待拉到 3 倍，没有意义，直接失败并保留完整错误链。
            if "排队超时" in str(exc):
                task.error = {
                    "code": "ai_video_timeout",
                    "message": str(exc)[:300],
                    "action": "生成队列已满或等待过久。稍后重试，或先确认 Key 池里有可用密钥。",
                }
                task.result = {"error": task.error["message"], "error_chain": error_chain}
                task.log(f"AI 视频生成失败（排队超时，不再重试）：{exc}")
                task.finish("failed")
                return
            if attempt < 3:
                task.log(f"第 {attempt}/3 次生成失败：{str(exc)[:180]}，10 秒后换 Key 重试")
                time.sleep(10)
                continue
            task.error = {
                "code": "ai_keys_missing" if "没有配置 AI API Key" in str(exc) else "ai_video_failed",
                "message": str(exc)[:300],
                "action": "在服务器配置 VIDEO_HUB_AI_API_KEY 或 AGNES_API_KEY 后重启后端。" if "没有配置 AI API Key" in str(exc) else "查看任务日志里的失败原因，改描述或参考图后再试。",
            }
            task.result = {"error": task.error["message"], "error_chain": error_chain}
            task.log(f"AI 视频生成失败（已重试 3 次）：{exc}")
            task.finish("failed")


def start_video_generation(
    prompt: str,
    seconds: int = 5,
    aspect_ratio: str = "16:9",
    first_frame: str = "",
    last_frame: str = "",
    images: list[str] | None = None,
    save_to_library: bool = True,
    character_profile_id: str = "",
    scene_profile_id: str = "",
    style_profile_id: str = "",
    transition: str = "",
    batch_id: str = "",
) -> dict[str, Any]:
    _require_keys()
    if transition == "morph" and (not first_frame or not last_frame):
        raise MediaAIError("一对一 morph 转场必须同时提供首帧和尾帧")
    prompt, profile_refs, character = _profile_context(
        prompt, character_profile_id, scene_profile_id, style_profile_id, transition
    )
    images = [*(profile_refs if not (first_frame or last_frame) else []), *(images or [])][:5]
    _validate_reference_values([v for v in ([first_frame, last_frame] + list(images or [])) if v])
    seconds = max(4, min(int(seconds or 5), 12))
    if aspect_ratio not in {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
        aspect_ratio = "16:9"
    mode = "keyframe" if (first_frame or last_frame) else ("reference" if images else "text")
    task_input = {"kind": "ai_video"}
    if batch_id:
        task_input["batch_id"] = batch_id
    task = task_manager.create(
        "ai_video",
        f"AI 视频 · {prompt[:30]}",
        input=task_input,
    )
    threading.Thread(
        target=_generate_video_task,
        args=(task, prompt, str(seconds), aspect_ratio, first_frame, last_frame, images, save_to_library, character),
        daemon=True,
    ).start()
    result = {"task_id": task.id, "mode": mode}
    if batch_id:
        result["batch_id"] = batch_id
    return result


def start_video_batch(shots: list[dict[str, Any]], shared: dict[str, Any]) -> dict[str, Any]:
    if not shots or len(shots) > 10:
        raise MediaAIError("连续镜头批量生成需要 1-10 个镜头")
    batch_id = uuid.uuid4().hex[:12]
    tasks = []
    for index, shot in enumerate(shots):
        prompt = str(shot.get("prompt") or "").strip()
        if not prompt:
            raise MediaAIError(f"第 {index + 1} 个镜头缺少描述")
        result = start_video_generation(
            prompt,
            seconds=int(shot.get("seconds") or 5),
            aspect_ratio=str(shot.get("aspect_ratio") or shared.get("aspect_ratio") or "16:9"),
            first_frame=str(shot.get("first_frame") or ""),
            last_frame=str(shot.get("last_frame") or ""),
            save_to_library=bool(shot.get("save_to_library", True)),
            character_profile_id=str(shared.get("character_profile_id") or ""),
            scene_profile_id=str(shared.get("scene_profile_id") or ""),
            style_profile_id=str(shared.get("style_profile_id") or ""),
            transition=str(shot.get("transition") or ""),
            batch_id=batch_id,
        )
        tasks.append(result)
    return {"tasks": tasks, "count": len(tasks)}


def get_video_batch(batch_id: str) -> dict[str, Any]:
    tasks = [
        task.to_dict(with_logs=False)
        for task in task_manager.list(limit=None)
        if task.type == "ai_video" and task.input.get("batch_id") == batch_id
    ]
    return {"batch_id": batch_id, "tasks": tasks, "count": len(tasks)}
