"""小红书图文笔记：从页面 __INITIAL_STATE__ 取 imageList 原图。

视频笔记仍交给 yt-dlp。这里不登录、不发布。公开页拿不到图片时，
调用方可以再用本地 Cookie 重试一次。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests

from app.services.image_gallery import GalleryCancelled, download_slides

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
REFERER = "https://www.xiaohongshu.com/"
TIMEOUT = 45
_MIN_FREE_BYTES = 512 * 1024 * 1024


def is_xiaohongshu_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in ("xiaohongshu.com", "xhslink.com"))


def _brace_object(html: str, marker: str) -> str | None:
    start = html.find(marker)
    if start < 0:
        return None
    i = start + len(marker)
    while i < len(html) and html[i].isspace():
        i += 1
    if i >= len(html) or html[i] != "{":
        return None
    depth = 0
    j = i
    in_str = False
    esc = False
    while j < len(html):
        char = html[j]
        if esc:
            esc = False
        elif char == "\\":
            esc = True
        elif in_str:
            if char == '"':
                in_str = False
        elif char == '"':
            in_str = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return html[i:j + 1]
        j += 1
    return None


def _parse_state(raw: str) -> dict | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from yt_dlp.utils import js_to_json

            value = json.loads(js_to_json(raw))
        except (ValueError, TypeError, json.JSONDecodeError):
            return None
    return value if isinstance(value, dict) else None


def _unwrap_note(value: object, depth: int = 0) -> dict | None:
    if depth > 5 or not isinstance(value, dict):
        return None
    current = value
    nested = current.get("_value")
    if isinstance(nested, dict):
        current = nested
    note = current.get("note")
    if isinstance(note, dict):
        inner = _unwrap_note(note, depth + 1)
        if inner:
            return inner
    if any(key in current for key in ("imageList", "image_list", "type", "video")):
        return current
    return None


def _note_from_state(state: dict, note_id: str | None) -> dict | None:
    note_map = (state.get("note") or {}).get("noteDetailMap") if isinstance(state.get("note"), dict) else None
    if not isinstance(note_map, dict):
        return None
    ordered: list[object] = []
    if note_id and note_id in note_map:
        ordered.append(note_map[note_id])
    ordered.extend(value for key, value in note_map.items() if key != note_id)
    for candidate in ordered:
        note = _unwrap_note(candidate)
        if note:
            return note
    return None


def _has_video(note: dict) -> bool:
    if str(note.get("type") or "").lower() == "video":
        return True
    video = note.get("video") if isinstance(note.get("video"), dict) else {}
    streams = (video.get("media") or {}).get("stream") if isinstance(video.get("media"), dict) else None
    if isinstance(streams, dict):
        return any(isinstance(items, list) and items for items in streams.values())
    return False


def _image_url(image: dict) -> str | None:
    info_list = image.get("infoList") or image.get("info_list") or []
    if isinstance(info_list, list):
        for scene in ("WB_DFT",):
            for item in info_list:
                if isinstance(item, dict) and item.get("imageScene") == scene and isinstance(item.get("url"), str):
                    if item["url"].startswith("http"):
                        return item["url"]
    for key in ("urlDefault", "url_default", "url"):
        value = image.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    if isinstance(info_list, list):
        for item in info_list:
            if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"].startswith("http"):
                return item["url"]
    preview = image.get("urlPre") or image.get("url_pre")
    if isinstance(preview, str) and preview.startswith("http"):
        return preview
    return None


def _slides(note: dict) -> list[dict]:
    raw = note.get("imageList") or note.get("image_list") or []
    if not isinstance(raw, list):
        return []
    slides: list[dict] = []
    for image in raw:
        if not isinstance(image, dict):
            continue
        url = _image_url(image)
        if not url:
            continue
        slides.append({"urls": [url], "width": image.get("width"), "height": image.get("height")})
    return slides


def _note_id(url: str) -> str | None:
    import re

    match = re.search(r"/(?:explore|discovery/item)/([0-9a-fA-F]{16,32})", url)
    return match.group(1) if match else None


def parse_page(html: str, page_url: str) -> dict | None:
    """从已拿到的 HTML 解析图文。视频笔记返回 None。"""
    raw = _brace_object(html or "", "window.__INITIAL_STATE__=")
    if raw is None:
        raw = _brace_object(html or "", "window.__INITIAL_STATE__ =")
    if not raw:
        raise RuntimeError("小红书页面没有公开图文数据")
    state = _parse_state(raw)
    if not state:
        raise RuntimeError("小红书页面数据无法解析")
    note = _note_from_state(state, _note_id(page_url))
    if not note:
        raise RuntimeError("小红书页面没有笔记内容")
    if _has_video(note):
        return None
    slides = _slides(note)
    if not slides:
        raise RuntimeError("小红书图文作品没有可下载的原图地址")
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    note_id = str(note.get("noteId") or note.get("note_id") or _note_id(page_url) or "")
    title = (note.get("title") or note.get("desc") or "").strip() or f"小红书图集_{note_id or 'note'}"
    return {
        "id": note_id or "xhs",
        "title": title,
        "uploader": (user.get("nickname") or user.get("nickName") or "").strip() or None,
        "webpage_url": page_url,
        "extractor_key": "xiaohongshu",
        "thumbnail": slides[0]["urls"][0],
        "media_kind": "images",
        "image_count": len(slides),
        "_image_slides": slides,
    }


def _session(cookies: dict | None) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({
        "User-Agent": BROWSER_UA,
        "Referer": REFERER,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    })
    if cookies:
        sess.headers["Cookie"] = "; ".join(f"{name}={value}" for name, value in cookies.items())
    return sess


def fetch_image_post(url: str, cookies: dict | None = None) -> dict | None:
    sess = _session(cookies)
    try:
        resp = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"小红书页面请求失败：{exc}") from exc
    return parse_page(resp.text or "", str(resp.url or url))


def load_image_post(url: str) -> dict | None:
    """先匿名解析。只有匿名失败且本地有 Cookie 时才重试。"""
    from app.services.cookies import storage_state_to_requests_cookies

    try:
        return fetch_image_post(url, cookies=None)
    except Exception as first:
        cookies = storage_state_to_requests_cookies("xiaohongshu")
        if not cookies:
            raise
        try:
            return fetch_image_post(url, cookies=cookies)
        except Exception as second:
            raise RuntimeError(f"{second}（匿名：{first}）") from second


def download_images(
    url: str,
    out_dir: Path,
    *,
    should_cancel=None,
    max_bytes: int,
    deadline_s: float,
) -> dict:
    if should_cancel and should_cancel():
        raise GalleryCancelled("下载已取消")
    info = load_image_post(url)
    if not info:
        raise RuntimeError("小红书链接没有可保存的图集")
    slides = info.get("_image_slides") or []
    dest = Path(out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(dest).free
    if free < _MIN_FREE_BYTES:
        raise RuntimeError(f"磁盘剩余 {free // (1024 * 1024)}MB，低于 512MB，已停止图文下载")
    files = download_slides(
        slides,
        dest / "slides",
        session=_session(None),
        referer=REFERER,
        should_cancel=should_cancel,
        max_bytes=max_bytes,
        deadline_s=deadline_s,
    )
    public = {key: value for key, value in info.items() if not str(key).startswith("_")}
    public["media_kind"] = "images"
    public["image_count"] = len(files)
    public["image_files"] = files
    return public
