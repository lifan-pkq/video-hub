"""抖音专用备用下载：iesdouyin 分享页 + www.douyin.com detail API（无需 Cookie）。

图文作品保存 images / image_post_info 里的原图，不因缺少 play_addr 拒绝。

参考 https://github.com/HuanNan520/dyfetch 、Mai0313/discordbot、CharlesPikachu/videodl。
yt-dlp 主站路径需要新鲜 Cookie 时走这条链路。

注意：/share/video/{id}/ 带尾斜杠时常返回无 videoInfoRes 的壳页；
无尾斜杠的 /share/video/{id} 与 /share/note/{id} 更稳，且为概率性风控，需重试。
"""
from __future__ import annotations

import json
import math
import re
import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
    "Mobile/15E148 Safari/604.1"
)
REFERER = "https://www.douyin.com/"
TIMEOUT = 45
MIN_MP4_BYTES = 100_000
_READ_TIMEOUT = 15
_MIN_FREE_BYTES = 512 * 1024 * 1024


class FallbackCancelled(RuntimeError):
    """备用下载被取消。调用方转成下载取消，不要当成平台失败。"""


def douyin_duration_seconds(value: object) -> float | None:
    """aweme duration 是毫秒。短于 10 秒时数值不超过 10000，不能只在大于 10000 时才换算。"""
    try:
        raw = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(raw) or raw <= 0:
        return None
    if raw >= 1000:
        raw /= 1000.0
    return raw
SHARE_PATHS = (
    "/share/video/{id}",
    "/share/note/{id}",
    "/share/video/{id}/",
    "/share/note/{id}/",
)
MAX_SHARE_ATTEMPTS = 4


def is_douyin_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in ("douyin.com", "iesdouyin.com"))


def _session() -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": MOBILE_UA,
        "Referer": REFERER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    return sess


def resolve_aweme_id(url: str, sess: requests.Session | None = None) -> str:
    """从短链 / 完整链接中解析 aweme_id。"""
    sess = sess or _session()
    text = (url or "").strip()
    m = re.search(r"/(?:video|note|share/video|share/note)/(\d{10,30})", text)
    if m:
        return m.group(1)
    m = re.search(r"[?&](?:modal_id|aweme_id|item_ids)=(\d{10,30})", text)
    if m:
        return m.group(1)

    try:
        resp = sess.head(text, allow_redirects=False, timeout=TIMEOUT)
        location = resp.headers.get("Location") or resp.headers.get("location") or ""
        if not location:
            resp = sess.get(text, allow_redirects=True, timeout=TIMEOUT, stream=True)
            location = str(resp.url or "")
            resp.close()
        for candidate in (location, text):
            m = re.search(r"/(?:video|note|share/video|share/note)/(\d{10,30})", candidate)
            if m:
                return m.group(1)
            m = re.search(r"[?&](?:modal_id|aweme_id|item_ids)=(\d{10,30})", candidate)
            if m:
                return m.group(1)
            m = re.search(r"(\d{15,25})", candidate)
            if m:
                return m.group(1)
    except requests.RequestException as exc:
        raise RuntimeError(f"无法解析抖音短链：{exc}") from exc

    m = re.search(r"(\d{15,25})", text)
    if m:
        return m.group(1)
    raise RuntimeError("无法从抖音链接中提取 aweme_id")


def _extract_router_json(html: str) -> dict | None:
    m = re.search(r"window\._ROUTER_DATA\s*=\s*", html)
    if not m:
        return None
    i = m.end()
    while i < len(html) and html[i].isspace():
        i += 1
    if i >= len(html) or html[i] != "{":
        return None
    depth = 0
    j = i
    in_str = False
    esc = False
    while j < len(html):
        c = html[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif in_str:
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                j += 1
                break
        j += 1
    try:
        return json.loads(html[i:j])
    except json.JSONDecodeError:
        return None


def _item_from_router(data: dict) -> dict | None:
    loader = data.get("loaderData") if isinstance(data, dict) else None
    if isinstance(loader, dict):
        for value in loader.values():
            if isinstance(value, dict) and isinstance(value.get("videoInfoRes"), dict):
                items = value["videoInfoRes"].get("item_list") or []
                if items and isinstance(items[0], dict):
                    return items[0]
                # soft-fail: deleted / private
                filters = value["videoInfoRes"].get("filter_list") or []
                if filters:
                    raise RuntimeError("抖音分享页标记该作品不可用（已删除/私密/区域限制）")
    stack: list[Any] = [data]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            if isinstance(cur.get("item_list"), list) and cur["item_list"]:
                first = cur["item_list"][0]
                if isinstance(first, dict) and ("video" in first or "desc" in first):
                    return first
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return None


def _no_watermark(url: str) -> str:
    return url.replace("/playwm/", "/play/").replace("playwm", "play")


def _pick_play_url(url_list: list, video: dict | None = None) -> str | None:
    urls = [u for u in url_list if isinstance(u, str) and u.startswith("http")]
    if urls:
        preferred = next((u for u in urls if "douyinvod.com" in u), None)
        return _no_watermark(preferred or urls[0])
    # CharlesPikachu 风格：用 play_addr.uri 拼 iesdouyin play 接口
    if isinstance(video, dict):
        uri = (video.get("play_addr") or {}).get("uri") or video.get("uri")
        if uri and not str(uri).startswith("http"):
            return (
                f"https://www.iesdouyin.com/aweme/v1/play/"
                f"?video_id={uri}&ratio=1080p&line=0"
            )
    return None


def _http_urls(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [url for url in values if isinstance(url, str) and url.startswith("http")]


def _unique_urls(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return ordered


def _named_urls(image: dict, *keys: str) -> list[str]:
    urls: list[str] = []
    for key in keys:
        urls.extend(_http_urls(image.get(key)))
    return urls


def _slide_urls(image: dict) -> list[str]:
    """无水印原图优先。download_url_list 常带「抖音号」水印，只在没有干净地址时兜底。"""
    preferred: list[str] = []
    preferred.extend(_named_urls(image, "watermark_free_download_url_list", "watermarkFreeDownloadUrlList"))
    display = image.get("display_image") or image.get("displayImage")
    if isinstance(display, dict):
        preferred.extend(_named_urls(display, "watermark_free_download_url_list", "watermarkFreeDownloadUrlList"))
        preferred.extend(_named_urls(display, "url_list", "urlList"))
    for label in ("label_origin", "label_large", "labelOrigin", "labelLarge"):
        nested = image.get(label)
        if isinstance(nested, dict):
            preferred.extend(_named_urls(nested, "url_list", "urlList"))
    preferred.extend(_named_urls(image, "url_list", "urlList"))
    fallback = _named_urls(image, "download_url_list", "downloadUrlList")
    return _unique_urls(preferred + fallback)


def _slides_from_list(images) -> list[dict]:
    if not isinstance(images, list):
        return []
    slides: list[dict] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        urls = _slide_urls(image)
        if not urls:
            continue
        display = image.get("display_image") if isinstance(image.get("display_image"), dict) else {}
        slides.append({
            "urls": urls,
            "width": image.get("width") or display.get("width"),
            "height": image.get("height") or display.get("height"),
        })
    return slides


def original_images(item: dict) -> list[dict]:
    """按展示顺序抽出图文原图。视频封面不在 images 里，不会被当成图集。"""
    if not isinstance(item, dict):
        return []
    slides = _slides_from_list(item.get("images"))
    if slides:
        return slides
    post = item.get("image_post_info") if isinstance(item.get("image_post_info"), dict) else {}
    if not post and isinstance(item.get("imagePostInfo"), dict):
        post = item["imagePostInfo"]
    slides = _slides_from_list(post.get("images") or post.get("image_list") or post.get("imageList"))
    if slides:
        return slides
    return _slides_from_list(item.get("image_infos"))


def _has_image_payload(item: dict) -> bool:
    images = item.get("images")
    if isinstance(images, list) and images:
        return True
    post = item.get("image_post_info") if isinstance(item.get("image_post_info"), dict) else {}
    if not post and isinstance(item.get("imagePostInfo"), dict):
        post = item["imagePostInfo"]
    if post.get("images") or post.get("image_list") or post.get("imageList"):
        return True
    infos = item.get("image_infos") or item.get("imageInfos")
    return isinstance(infos, list) and bool(infos)


def _cover_from_item(item: dict) -> str | None:
    video = item.get("video") or {}
    for key in ("origin_cover", "cover", "dynamic_cover"):
        cover = video.get(key) or {}
        urls = cover.get("url_list") if isinstance(cover, dict) else None
        if urls:
            return urls[0]
    return None


def _info_from_item(item: dict, aweme_id: str, webpage_url: str) -> dict:
    video = item.get("video") or {}
    play_addr = video.get("play_addr") or {}
    play_url = _pick_play_url(play_addr.get("url_list") or [], video)
    if not play_url:
        play_url = _pick_play_url((video.get("download_addr") or {}).get("url_list") or [], video)
    author = item.get("author") or {}
    width = play_addr.get("width") or video.get("width")
    height = play_addr.get("height") or video.get("height")
    duration = douyin_duration_seconds(video.get("duration") or item.get("duration") or 0)
    slides = original_images(item)
    # 图文常带一条背景音乐占位视频。有原图时以静图为准，不把占位片当成作品本身。
    image_post = bool(slides) or _has_image_payload(item)
    if image_post:
        webpage_url = f"https://www.douyin.com/note/{item.get('aweme_id') or aweme_id}"
    title = (item.get("desc") or "").strip() or (
        f"抖音图集_{aweme_id}" if image_post else f"抖音视频_{aweme_id}"
    )
    info = {
        "id": str(item.get("aweme_id") or aweme_id),
        "title": title,
        "uploader": (author.get("nickname") or author.get("unique_id") or "").strip() or None,
        "thumbnail": (slides[0]["urls"][0] if slides else None) or _cover_from_item(item),
        "duration": None if image_post else duration,
        "webpage_url": webpage_url,
        "extractor_key": "douyin",
        "width": width,
        "height": height,
        "_play_url": None if image_post else play_url,
        "_image_slides": slides,
        "formats": (
            [{
                "url": play_url,
                "ext": "mp4",
                "vcodec": "h264",
                "width": width,
                "height": height,
                "format_note": "iesdouyin-share",
            }]
            if play_url and not image_post else []
        ),
    }
    if image_post:
        info["media_kind"] = "images"
        info["image_count"] = len(slides)
    return info


def _from_share_page(aweme_id: str, sess: requests.Session) -> dict | None:
    """尝试多种分享页路径；风控壳页时重试。"""
    last_err: Exception | None = None
    for attempt in range(MAX_SHARE_ATTEMPTS):
        for template in SHARE_PATHS:
            path = template.format(id=aweme_id)
            url = f"https://www.iesdouyin.com{path}"
            try:
                resp = sess.get(url, timeout=TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException as exc:
                last_err = exc
                continue
            html = resp.text or ""
            if "videoInfoRes" not in html:
                continue
            data = _extract_router_json(html)
            if not data:
                continue
            try:
                item = _item_from_router(data)
            except RuntimeError:
                raise
            if not item:
                continue
            info = _info_from_item(item, aweme_id, f"https://www.douyin.com/video/{aweme_id}")
            if info.get("media_kind") == "images" or info.get("_play_url"):
                return info
            continue
        if attempt + 1 < MAX_SHARE_ATTEMPTS:
            time.sleep(0.4 * (attempt + 1))
    if last_err:
        raise RuntimeError(f"分享页请求失败：{last_err}") from last_err
    return None


def _from_detail_api(aweme_id: str, sess: requests.Session) -> dict:
    # 先访问主站拿 ttwid，降低 Argus 403 概率
    try:
        sess.get("https://www.douyin.com/", timeout=TIMEOUT)
    except requests.RequestException:
        pass
    api = (
        "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        f"?aweme_id={aweme_id}&device_platform=webapp&aid=6383&channel=channel_pc_web"
    )
    resp = sess.get(api, timeout=TIMEOUT, headers={
        "Referer": f"https://www.douyin.com/video/{aweme_id}",
        "Accept": "application/json, text/plain, */*",
    })
    if resp.status_code == 403:
        raise RuntimeError(f"抖音 detail API 403：{resp.text[:120]}")
    resp.raise_for_status()
    try:
        payload = resp.json()
    except ValueError as exc:
        raise RuntimeError("抖音 detail API 返回非 JSON") from exc
    detail = payload.get("aweme_detail")
    if not isinstance(detail, dict):
        raise RuntimeError(
            f"抖音 detail API 无 aweme_detail（status_code={payload.get('status_code')}）"
        )
    info = _info_from_item(detail, aweme_id, f"https://www.douyin.com/video/{aweme_id}")
    if info.get("media_kind") == "images":
        return info
    if not info.get("_play_url"):
        raise RuntimeError("抖音 detail API 未返回可下载的 play_addr")
    return info


def parse_douyin(url: str) -> dict:
    """解析抖音链接元数据（含 _play_url），不下载文件。"""
    sess = _session()
    aweme_id = resolve_aweme_id(url, sess)
    try:
        info = _from_share_page(aweme_id, sess)
    except RuntimeError:
        raise
    except Exception:
        info = None
    if info is None:
        info = _from_detail_api(aweme_id, sess)
    return info


def _download_image_post(
    info: dict,
    out_path: Path,
    *,
    should_cancel,
    max_bytes: int,
    deadline_s: float,
) -> dict:
    """下载图文原图。不写 mp4，也不使用 Cookie。"""
    from app.services.image_gallery import GalleryCancelled, download_slides

    slides = info.get("_image_slides") or []
    if not slides:
        raise RuntimeError("该抖音链接是图文作品，但没有可下载的原图地址")
    if should_cancel and should_cancel():
        raise FallbackCancelled("下载已取消")
    dest = Path(out_path).parent / "slides"
    free = shutil.disk_usage(Path(out_path).parent).free
    if free < _MIN_FREE_BYTES:
        raise RuntimeError(f"磁盘剩余 {free // (1024 * 1024)}MB，低于 512MB，已停止备用下载")
    sess = _session()
    try:
        files = download_slides(
            slides,
            dest,
            session=sess,
            referer=REFERER,
            should_cancel=should_cancel,
            max_bytes=max_bytes,
            deadline_s=deadline_s,
        )
    except GalleryCancelled as exc:
        raise FallbackCancelled("下载已取消") from exc
    public = {key: value for key, value in info.items() if not str(key).startswith("_")}
    public["media_kind"] = "images"
    public["image_count"] = len(files)
    public["image_files"] = files
    return public


def download_douyin(
    url: str,
    out_path: Path,
    *,
    should_cancel=None,
    max_bytes: int | None = None,
    deadline_s: float | None = None,
) -> dict:
    """解析并下载无水印 mp4；图文作品则下载原图。每个数据块检查取消、总时限和大小上限。"""
    if max_bytes is None or deadline_s is None:
        from app.config import DOWNLOAD_TIMEOUT, MAX_UPLOAD_MB

        if max_bytes is None:
            max_bytes = MAX_UPLOAD_MB * 1024 * 1024
        if deadline_s is None:
            deadline_s = float(DOWNLOAD_TIMEOUT)
    if should_cancel and should_cancel():
        raise FallbackCancelled("下载已取消")
    info = parse_douyin(url)
    if info.get("media_kind") == "images":
        return _download_image_post(
            info,
            out_path,
            should_cancel=should_cancel,
            max_bytes=max_bytes,
            deadline_s=float(deadline_s),
        )
    play_url = info.get("_play_url")
    if not play_url:
        raise RuntimeError("未能解析抖音无水印播放地址")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out_path.parent).free
    if free < _MIN_FREE_BYTES:
        raise RuntimeError(f"磁盘剩余 {free // (1024 * 1024)}MB，低于 512MB，已停止备用下载")
    sess = _session()
    deadline = time.monotonic() + float(deadline_s)
    try:
        with sess.get(
            play_url, timeout=(10, _READ_TIMEOUT), stream=True, allow_redirects=True,
            headers={"Referer": REFERER, "User-Agent": MOBILE_UA},
        ) as resp:
            resp.raise_for_status()
            declared = resp.headers.get("Content-Length")
            if declared and str(declared).isdigit() and int(declared) > max_bytes:
                raise RuntimeError(f"抖音备用下载超过大小上限（{max_bytes // (1024 * 1024)}MB）")
            written = 0
            with open(out_path, "wb") as handle:
                for chunk in resp.iter_content(chunk_size=256 * 1024):
                    if should_cancel and should_cancel():
                        raise FallbackCancelled("下载已取消")
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"timeout: 抖音备用下载超过 {int(deadline_s)} 秒仍未完成")
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise RuntimeError(f"抖音备用下载超过大小上限（{max_bytes // (1024 * 1024)}MB）")
                    handle.write(chunk)
        size = out_path.stat().st_size if out_path.exists() else 0
        if size < MIN_MP4_BYTES:
            raise RuntimeError(f"抖音备用下载文件过小（{size} bytes），可能被风控拦截")
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    public = {k: v for k, v in info.items() if not k.startswith("_")}
    return public
