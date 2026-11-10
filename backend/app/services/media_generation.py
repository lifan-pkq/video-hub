"""Agnes 单次媒体生成，不管理任务状态或素材库。"""

from __future__ import annotations

import base64
import binascii
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

from app.config import (
    AGNES_IMAGE_MODEL,
    AGNES_MEDIA_BASE_URL,
    AGNES_VIDEO_CREATE_TIMEOUT,
    AGNES_VIDEO_MODEL,
    AGNES_VIDEO_QUERY_URL,
    DATA_DIR,
)
from app.services.ai_pool import AI_POOL, AIPoolError

GENERATED_IMAGE_DIR = DATA_DIR / "generated" / "images"
GENERATED_VIDEO_DIR = DATA_DIR / "generated" / "videos"


class MediaAIError(RuntimeError):
    pass


class TransientMediaAIError(MediaAIError):
    pass


class GenerationCancelled(MediaAIError):
    pass


def _cancellable_sleep(seconds: float, should_cancel: CancelCheck | None = None) -> None:
    remaining = max(0.0, seconds)
    while remaining > 0:
        if should_cancel and should_cancel():
            raise GenerationCancelled("生成已取消")
        interval = min(0.2, remaining)
        time.sleep(interval)
        remaining -= interval


@dataclass(frozen=True)
class GeneratedMedia:
    path: Path
    source_url: str
    model: str
    metadata: dict[str, Any]


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[float, str], None]


def _is_public_url(url: str) -> bool:
    import ipaddress
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host or host in {"localhost"}:
        return False
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_global
    except ValueError:
        return True


def _validate_reference_values(values: list[str]) -> None:
    for value in values:
        value = (value or "").strip()
        if not value or value.startswith("data:image/"):
            continue
        if value.startswith("http://") or value.startswith("https://"):
            if not _is_public_url(value):
                raise MediaAIError(
                    "参考图地址是内网/回环地址，Agnes 无法从它的服务器上访问："
                    f"{value[:80]}。请把 VIDEO_HUB_PUBLIC_BASE_URL 改成公网地址，"
                    "或改用新上传的参考图（会优先发 data_uri）。"
                )


def _download(url: str, target: Path, timeout: float = 180) -> None:
    try:
        response = requests.get(url, stream=True, timeout=timeout)
        response.raise_for_status()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            for chunk in response.iter_content(1024 * 512):
                if chunk:
                    handle.write(chunk)
    except requests.RequestException as exc:
        target.unlink(missing_ok=True)
        status = int(getattr(exc.response, "status_code", 0) or 0) if isinstance(exc, requests.HTTPError) else 0
        error_type = (
            MediaAIError
            if 400 <= status < 500 and status not in {408, 409, 425, 429}
            else TransientMediaAIError
        )
        raise error_type(f"下载生成结果网络错误：{exc}") from exc
    if not target.is_file() or target.stat().st_size <= 0:
        raise TransientMediaAIError("下载生成结果失败")


def _image_request(
    prompt: str,
    *,
    size: str,
    ratio: str,
    images: list[str],
    should_cancel: CancelCheck | None,
) -> GeneratedMedia:
    if should_cancel and should_cancel():
        raise GenerationCancelled("生成已取消")
    if not prompt.strip():
        raise MediaAIError("提示词不能为空")
    _validate_reference_values(images)
    payload: dict[str, Any] = {
        "model": AGNES_IMAGE_MODEL,
        "prompt": prompt,
        "size": size if size in {"1K", "2K", "3K", "4K"} else "2K",
        "ratio": ratio,
        "extra_body": {"response_format": "url"},
    }
    if images:
        payload["image"] = images
    try:
        response = AI_POOL.request(
            "POST",
            f"{AGNES_MEDIA_BASE_URL}/images/generations",
            json_body=payload,
            timeout=240,
        )
    except AIPoolError as exc:
        permanent = any(marker in str(exc) for marker in ("没有配置", "HTTP 401", "HTTP 403"))
        error_type = MediaAIError if permanent else TransientMediaAIError
        raise error_type(f"图像接口不可用：{exc}") from exc
    if response.status_code != 200:
        message = f"图像接口错误 {response.status_code}：{response.text[:300]}"
        error_type = (
            TransientMediaAIError
            if response.status_code in {408, 409, 425, 429} or response.status_code >= 500
            else MediaAIError
        )
        raise error_type(message)
    data = response.json()
    item = (data.get("data") or [{}])[0]
    GENERATED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"ai_{uuid.uuid4().hex}.png"
    target = GENERATED_IMAGE_DIR / name
    source_url = str(item.get("url") or "")
    if source_url:
        _download(source_url, target)
    elif item.get("b64_json"):
        try:
            target.write_bytes(base64.b64decode(item["b64_json"]))
        except (ValueError, binascii.Error) as exc:
            raise MediaAIError("图像 Base64 解码失败") from exc
    else:
        raise MediaAIError("图像接口没有返回图片")
    return GeneratedMedia(
        path=target,
        source_url=source_url,
        model=AGNES_IMAGE_MODEL,
        metadata={"name": name, "size": target.stat().st_size},
    )


def _create_video(
    prompt: str,
    seconds: int,
    aspect_ratio: str,
    *,
    first_frame: str = "",
    last_frame: str = "",
    images: list[str] | None = None,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    images = [item for item in (images or []) if item][:5]
    _validate_reference_values(
        [v for v in ([first_frame, last_frame] + images) if v]
    )
    if first_frame or last_frame:
        mode = "keyframe"
    elif images:
        mode = "reference"
    else:
        mode = "text"
    payload = {
        "model": AGNES_VIDEO_MODEL,
        "prompt": prompt,
        "seconds": str(seconds),
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
    effective_timeout = max(60, int(AGNES_VIDEO_CREATE_TIMEOUT or 1800))
    deadline = time.monotonic() + effective_timeout
    while True:
        if should_cancel and should_cancel():
            raise GenerationCancelled("生成已取消")
        if time.monotonic() >= deadline:
            raise MediaAIError(
                f"视频创建排队超时（已等待 {effective_timeout} 秒）。"
                f"最后一次错误：{last_error or '无'}"
            )
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
            if progress:
                progress(0, f"视频接口暂不可用：{last_error[:120]}，30 秒后换 Key 再试")
            _cancellable_sleep(min(30, max(0, deadline - time.monotonic())), should_cancel)
            continue
        if response.status_code == 200:
            data = response.json()
            video_id = data.get("video_id") or data.get("id")
            if video_id:
                data["_key"] = getattr(response, "_ai_pool_key", "")
                return data
            last_error = "视频接口没有返回 video_id"
            if progress:
                progress(0, last_error + "，继续重试")
            _cancellable_sleep(min(15, max(0, deadline - time.monotonic())), should_cancel)
            continue
        last_error = f"{response.status_code} {response.text[:300]}"
        if "queue_full" in last_error.lower() or "队列已满" in last_error:
            elapsed = int(time.monotonic() - (deadline - effective_timeout))
            if progress and (attempt == 1 or attempt % 5 == 0):
                progress(
                    0,
                    f"视频队列已满，已排队约 {elapsed} 秒，继续排队"
                    f"（第 {attempt} 次，总时限 {effective_timeout} 秒）",
                )
            _cancellable_sleep(
                min(60, 15 + attempt * 5, max(0, deadline - time.monotonic())), should_cancel
            )
            continue
        if response.status_code in (400, 401, 403, 404, 422):
            raise MediaAIError(last_error)
        if progress:
            progress(0, f"视频接口 HTTP {response.status_code}，30 秒后重试")
        _cancellable_sleep(min(30, max(0, deadline - time.monotonic())), should_cancel)


def _poll_video(
    video_id: str,
    task_key: str = "",
    timeout: float = 900,
    *,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    params = {"video_id": video_id, "model_name": AGNES_VIDEO_MODEL}
    while time.monotonic() < deadline:
        if should_cancel and should_cancel():
            raise GenerationCancelled("生成已取消")
        key = task_key or AI_POOL.acquire()
        if not key:
            _cancellable_sleep(5, should_cancel)
            continue
        try:
            response = requests.get(
                AGNES_VIDEO_QUERY_URL,
                params=params,
                headers={"Authorization": f"Bearer {key}"},
                timeout=60,
            )
        except requests.RequestException as exc:
            if progress:
                progress(0, f"视频查询网络错误：{exc}")
            _cancellable_sleep(5, should_cancel)
            continue
        if response.status_code == 429:
            AI_POOL.mark_failure(key, 429, response.text[:160])
            _cancellable_sleep(5, should_cancel)
            continue
        if response.status_code == 404:
            raise MediaAIError("视频任务不存在或当前 Key 无权访问")
        if response.status_code != 200:
            AI_POOL.mark_failure(key, response.status_code, response.text[:160])
            if progress:
                progress(0, f"视频查询 HTTP {response.status_code}")
            _cancellable_sleep(10, should_cancel)
            continue
        AI_POOL.mark_success(key)
        data = response.json()
        status = str(data.get("status") or "")
        remote_progress = float(data.get("progress") or 0)
        if progress:
            progress(
                min(90.0, max(5.0, remote_progress * 0.9)),
                f"视频生成中：{status or 'pending'} {remote_progress:.0f}%",
            )
        if status == "completed":
            return data
        if status == "failed":
            raise MediaAIError(str(data.get("error") or "视频生成失败"))
        _cancellable_sleep(5, should_cancel)
    raise MediaAIError("视频生成超时")


def _video_request(
    prompt: str,
    seconds: int,
    *,
    aspect_ratio: str,
    first_frame: str,
    last_frame: str,
    images: list[str],
    should_cancel: CancelCheck | None,
    progress: ProgressCallback | None,
) -> GeneratedMedia:
    created = _create_video(
        prompt,
        seconds,
        aspect_ratio,
        first_frame=first_frame,
        last_frame=last_frame,
        images=images,
        should_cancel=should_cancel,
        progress=progress,
    )
    video_id = str(created.get("video_id") or created.get("id"))
    task_key = str(created.get("_key") or "")
    if progress:
        progress(0, f"视频任务已创建：{video_id}")
    data = _poll_video(
        video_id,
        task_key,
        should_cancel=should_cancel,
        progress=progress,
    )
    url = data.get("url")
    if not url:
        raise MediaAIError("视频任务完成但没有下载地址")
    GENERATED_VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    target = GENERATED_VIDEO_DIR / f"ai_{uuid.uuid4().hex}.mp4"
    if progress:
        progress(90, "正在下载生成视频…")
    _download(str(url), target, timeout=600)
    return GeneratedMedia(
        path=target,
        source_url=str(url),
        model=AGNES_VIDEO_MODEL,
        metadata={"video_id": video_id},
    )


def generate_image_asset(
    prompt: str,
    *,
    size: str = "2K",
    ratio: str = "16:9",
    images: list[str] | None = None,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> GeneratedMedia:
    if should_cancel and should_cancel():
        raise GenerationCancelled("生成已取消")
    if progress:
        progress(0, "提交图片生成")
    result = _image_request(
        prompt, size=size, ratio=ratio, images=images or [], should_cancel=should_cancel
    )
    if should_cancel and should_cancel():
        result.path.unlink(missing_ok=True)
        raise GenerationCancelled("生成已取消")
    if progress:
        progress(100, "图片已下载")
    return result


def generate_video_asset(
    prompt: str,
    seconds: int,
    *,
    aspect_ratio: str = "16:9",
    first_frame: str = "",
    last_frame: str = "",
    images: list[str] | None = None,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> GeneratedMedia:
    if not 4 <= int(seconds) <= 12:
        raise MediaAIError("视频时长必须为 4–12 秒")
    if should_cancel and should_cancel():
        raise GenerationCancelled("生成已取消")
    result = _video_request(
        prompt, int(seconds), aspect_ratio=aspect_ratio,
        first_frame=first_frame, last_frame=last_frame, images=images or [],
        should_cancel=should_cancel, progress=progress,
    )
    if should_cancel and should_cancel():
        result.path.unlink(missing_ok=True)
        raise GenerationCancelled("生成已取消")
    return result
