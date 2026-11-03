"""Agnes 图像/视频生成：多 Key 池、异步视频任务、结果直接入库。"""

from __future__ import annotations

import base64
import binascii
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from app.config import (
    AGNES_IMAGE_MODEL,
    AGNES_MEDIA_BASE_URL,
    AGNES_VIDEO_MODEL,
    AGNES_VIDEO_QUERY_URL,
    COVERS_DIR,
    DATA_DIR,
    PUBLIC_BASE_URL,
    VIDEOS_DIR,
)
from app.db import add_video
from app.services.ai_pool import AI_POOL, AIPoolError
from app.services.media import make_cover, probe_video
from app.services.tasks import task_manager

GENERATED_IMAGE_DIR = DATA_DIR / "generated" / "images"
GENERATED_VIDEO_DIR = DATA_DIR / "generated" / "videos"
GENERATED_REFERENCE_DIR = DATA_DIR / "generated" / "references"


class MediaAIError(RuntimeError):
    pass


def _download(url: str, target: Path, timeout: float = 180) -> None:
    response = requests.get(url, stream=True, timeout=timeout)
    response.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        for chunk in response.iter_content(1024 * 512):
            if chunk:
                handle.write(chunk)
    if not target.is_file() or target.stat().st_size <= 0:
        raise MediaAIError("下载生成结果失败")


def save_reference(data: bytes, filename: str) -> dict[str, Any]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    GENERATED_REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ref_{uuid.uuid4().hex}{suffix}"
    target = GENERATED_REFERENCE_DIR / name
    target.write_bytes(data)
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix[1:]}"
    return {
        "url": f"{PUBLIC_BASE_URL}/api/ai/media/references/{name}",
        "data_uri": f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}",
        "name": name,
        "size": len(data),
    }


def generate_image(prompt: str, size: str = "2K", ratio: str = "16:9", images: list[str] | None = None) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        raise MediaAIError("提示词不能为空")
    payload: dict[str, Any] = {
        "model": AGNES_IMAGE_MODEL,
        "prompt": prompt,
        "size": size if size in {"1K", "2K", "3K", "4K"} else "2K",
        "ratio": ratio,
        "extra_body": {"response_format": "url"},
    }
    if images:
        payload["image"] = images[:5]
    try:
        response = AI_POOL.request(
            "POST",
            f"{AGNES_MEDIA_BASE_URL}/images/generations",
            json_body=payload,
            timeout=240,
        )
    except AIPoolError as exc:
        raise MediaAIError(f"图像接口不可用：{exc}") from exc
    if response.status_code != 200:
        raise MediaAIError(f"图像接口错误 {response.status_code}：{response.text[:300]}")
    data = response.json()
    item = (data.get("data") or [{}])[0]
    GENERATED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ai_{uuid.uuid4().hex}.png"
    target = GENERATED_IMAGE_DIR / name
    if item.get("url"):
        _download(str(item["url"]), target)
    elif item.get("b64_json"):
        try:
            target.write_bytes(base64.b64decode(item["b64_json"]))
        except (ValueError, binascii.Error) as exc:
            raise MediaAIError("图像 Base64 解码失败") from exc
    else:
        raise MediaAIError("图像接口没有返回图片")
    return {
        "ok": True,
        "url": f"/api/ai/media/images/{name}",
        "name": name,
        "model": AGNES_IMAGE_MODEL,
        "size": target.stat().st_size,
    }


def _create_video(
    prompt: str,
    seconds: str,
    aspect_ratio: str,
    task,
    first_frame: str = "",
    last_frame: str = "",
    images: list[str] | None = None,
) -> dict[str, Any]:
    images = [item for item in (images or []) if item][:5]
    if first_frame or last_frame:
        mode = "keyframe"
    elif images:
        mode = "reference"
    else:
        mode = "text"
    payload = {
        "model": AGNES_VIDEO_MODEL,
        "prompt": prompt,
        "seconds": seconds,
        "mode": mode,
        "size": "720P",
        "aspect_ratio": aspect_ratio,
    }
    if mode == "keyframe":
        if first_frame:
            payload["first_frame"] = first_frame
        if last_frame:
            payload["last_frame"] = last_frame
    elif mode == "reference":
        payload["images"] = images
    last_error = ""
    attempt = 0
    while not task.cancelled:
        attempt += 1
        try:
            response = AI_POOL.request(
                "POST",
                f"{AGNES_MEDIA_BASE_URL}/videos",
                json_body=payload,
                timeout=90,
                busy_markers=("video_queue_full",),
            )
        except AIPoolError as exc:
            last_error = str(exc)
            task.log(f"视频接口暂不可用：{last_error[:120]}，30 秒后换 Key 再试")
            time.sleep(30)
            continue
        if response.status_code == 200:
            data = response.json()
            video_id = data.get("video_id") or data.get("id")
            if video_id:
                data["_key"] = AI_POOL.last_key
                return data
            last_error = "视频接口没有返回 video_id"
            task.log(last_error + "，继续重试")
            time.sleep(15)
            continue
        last_error = f"{response.status_code} {response.text[:300]}"
        if "queue_full" in last_error.lower() or "队列已满" in last_error:
            if attempt == 1 or attempt % 5 == 0:
                task.log(f"视频队列已满，继续排队（第 {attempt} 次，最多 60 秒后再试）")
            time.sleep(min(60, 15 + attempt * 5))
            continue
        if response.status_code in (400, 401, 403, 404, 422):
            raise MediaAIError(last_error)
        task.log(f"视频接口 HTTP {response.status_code}，30 秒后重试")
        time.sleep(30)
    raise MediaAIError("视频任务已取消")


def _poll_video(video_id: str, task, task_key: str = "", timeout: float = 900) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    params = {"video_id": video_id, "model_name": AGNES_VIDEO_MODEL}
    while time.monotonic() < deadline:
        if task.cancelled:
            raise MediaAIError("视频任务已取消")
        key = task_key or AI_POOL.acquire()
        if not key:
            time.sleep(5)
            continue
        try:
            response = requests.get(
                AGNES_VIDEO_QUERY_URL,
                params=params,
                headers={"Authorization": f"Bearer {key}"},
                timeout=60,
            )
        except requests.RequestException as exc:
            task.log(f"视频查询网络错误：{exc}")
            time.sleep(5)
            continue
        if response.status_code == 429:
            AI_POOL.mark_failure(key, 429, response.text[:160])
            time.sleep(5)
            continue
        if response.status_code == 404:
            raise MediaAIError("视频任务不存在或当前 Key 无权访问")
        if response.status_code != 200:
            AI_POOL.mark_failure(key, response.status_code, response.text[:160])
            task.log(f"视频查询 HTTP {response.status_code}")
            time.sleep(10)
            continue
        AI_POOL.mark_success(key)
        data = response.json()
        status = str(data.get("status") or "")
        progress = float(data.get("progress") or 0)
        task.progress = min(90.0, max(5.0, progress * 0.9))
        task.log(f"视频生成中：{status or 'pending'} {progress:.0f}%")
        if status == "completed":
            return data
        if status == "failed":
            raise MediaAIError(str(data.get("error") or "视频生成失败"))
        time.sleep(5)
    raise MediaAIError("视频生成超时")


def _generate_video_task(
    task,
    prompt: str,
    seconds: str,
    aspect_ratio: str,
    first_frame: str = "",
    last_frame: str = "",
    images: list[str] | None = None,
    save_to_library: bool = True,
) -> None:
    task.status = "running"
    mode = "keyframe" if (first_frame or last_frame) else ("reference" if images else "text")
    task.log(f"提交 {AGNES_VIDEO_MODEL}：{seconds}s {aspect_ratio} · {mode}")

    for attempt in range(1, 4):
        if task.cancelled:
            task.log("任务已取消")
            return
        if attempt > 1:
            task.log(f"第 {attempt}/3 次生成，自动换 Key 重试…")
            task.progress = 0
        try:
            created = _create_video(prompt, seconds, aspect_ratio, task, first_frame, last_frame, images)
            video_id = str(created.get("video_id") or created.get("id"))
            task_key = str(created.get("_key") or "")
            task.log(f"视频任务已创建：{video_id}")
            data = _poll_video(video_id, task, task_key)
            url = data.get("url")
            if not url:
                raise MediaAIError("视频任务完成但没有下载地址")
            GENERATED_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
            temp = GENERATED_VIDEO_DIR / f"ai_{uuid.uuid4().hex}.mp4"
            task.log("正在下载生成视频…")
            _download(str(url), temp, timeout=600)
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
                    "model": AGNES_VIDEO_MODEL,
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
                    "model": AGNES_VIDEO_MODEL,
                }
            task.log("视频已生成" + ("并加入素材库" if save_to_library else "（未加入素材库）"))
            task.progress = 100.0
            task.finish("success")
            return
        except Exception as exc:  # noqa: BLE001
            if task.cancelled:
                task.log("任务已取消")
                return
            if attempt < 3:
                task.log(f"第 {attempt}/3 次生成失败：{str(exc)[:180]}，10 秒后换 Key 重试")
                time.sleep(10)
                continue
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
) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    if not prompt:
        raise MediaAIError("提示词不能为空")
    seconds = max(4, min(int(seconds or 5), 12))
    if aspect_ratio not in {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}:
        aspect_ratio = "16:9"
    mode = "keyframe" if (first_frame or last_frame) else ("reference" if images else "text")
    task = task_manager.create("ai", f"AI 视频 · {prompt[:30]}")
    threading.Thread(
        target=_generate_video_task,
        args=(task, prompt, str(seconds), aspect_ratio, first_frame, last_frame, images, save_to_library),
        daemon=True,
    ).start()
    return {"task_id": task.id, "mode": mode}
