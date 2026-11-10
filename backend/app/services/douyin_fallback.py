"""抖音专用备用下载：iesdouyin 分享页 + www.douyin.com detail API（无需 Cookie）。

参考 https://github.com/HuanNan520/dyfetch 、Mai0313/discordbot、CharlesPikachu/videodl。
yt-dlp 主站路径需要新鲜 Cookie 时走这条链路。

注意：/share/video/{id}/ 带尾斜杠时常返回无 videoInfoRes 的壳页；
无尾斜杠的 /share/video/{id} 与 /share/note/{id} 更稳，且为概率性风控，需重试。
"""
from __future__ import annotations

import json
import re
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
    duration_ms = video.get("duration") or item.get("duration") or 0
    try:
        duration_ms = int(duration_ms)
    except (TypeError, ValueError):
        duration_ms = 0
    duration = duration_ms / 1000.0 if duration_ms > 10000 else float(duration_ms or 0) or None
    if duration == 0:
        duration = None
    title = (item.get("desc") or "").strip() or f"抖音视频_{aweme_id}"
    return {
        "id": str(item.get("aweme_id") or aweme_id),
        "title": title,
        "uploader": (author.get("nickname") or author.get("unique_id") or "").strip() or None,
        "thumbnail": _cover_from_item(item),
        "duration": duration,
        "webpage_url": webpage_url,
        "extractor_key": "douyin",
        "width": width,
        "height": height,
        "_play_url": play_url,
        "formats": (
            [{
                "url": play_url,
                "ext": "mp4",
                "vcodec": "h264",
                "width": width,
                "height": height,
                "format_note": "iesdouyin-share",
            }]
            if play_url else []
        ),
    }


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
            if not info.get("_play_url"):
                images = item.get("images") or []
                if images:
                    raise RuntimeError("该抖音链接是图文作品，当前备用下载器仅支持视频")
                continue
            return info
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


def download_douyin(url: str, out_path: Path) -> dict:
    """解析并下载无水印 mp4 到 out_path，返回 yt-dlp 风格 info dict。"""
    info = parse_douyin(url)
    play_url = info.get("_play_url")
    if not play_url:
        raise RuntimeError("未能解析抖音无水印播放地址")
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sess = _session()
    with sess.get(play_url, timeout=240, stream=True, allow_redirects=True,
                  headers={"Referer": REFERER, "User-Agent": MOBILE_UA}) as resp:
        resp.raise_for_status()
        with open(out_path, "wb") as handle:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if chunk:
                    handle.write(chunk)
    size = out_path.stat().st_size
    if size < MIN_MP4_BYTES:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"抖音备用下载文件过小（{size} bytes），可能被风控拦截")
    public = {k: v for k, v in info.items() if not k.startswith("_")}
    return public
