"""作者全部作品列表：识别作者主页链接并抓取其全部作品。

- B站走 yt-dlp flat playlist（无需浏览器）
- 抖音/快手/小红书用 patchright(chromium) 打开作者主页，拦截分页接口 JSON
  并滚动触发加载；浏览器抓取全局串行（author_lock），防止 1.9GB 内存爆掉
"""
import asyncio
import json
import re
import threading
import time

import requests

from app.config import PLATFORM_NAMES
from app.services.cookies import cookie_exists, cookie_path, make_ytdlp_cookiefile
from app.services.downloader import BROWSER_UA, extract_url

# 单个作者最多返回的作品数
MAX_WORKS = 200
# 浏览器抓取整体超时（秒）
SCRAPE_TIMEOUT = 90
# 滚动加载的最大屏数
MAX_SCROLLS = 15

# 全局并发锁：chromium 抓取同一时间只允许一个（内存敏感）
# 注意：锁在同步工作线程内 acquire/release，即使外层 wait_for 超时取消，
# 线程也会持有锁直到浏览器关闭，不会提前放锁导致并发
author_lock = threading.Lock()


class AuthorBusyError(Exception):
    """已有抓取任务在进行中（对应 HTTP 429）。"""


# ================= 作者主页链接识别 =================

# 短链域名：需要先跟随重定向拿到最终 URL 再识别
_SHORT_DOMAINS = ("v.douyin.com", "iesdouyin.com", "xhslink.com",
                  "v.kuaishou.com", "chenzhongtech.com", "b23.tv")

# 最终 URL → (平台, 作者主页 URL 模板)
_AUTHOR_PATTERNS = (
    ("douyin", re.compile(r"(?:www\.)?douyin\.com/user/([A-Za-z0-9_-]+)"),
     "https://www.douyin.com/user/{}"),
    ("xiaohongshu", re.compile(r"xiaohongshu\.com/user/profile/([A-Za-z0-9]+)"),
     "https://www.xiaohongshu.com/user/profile/{}"),
    ("kuaishou", re.compile(r"kuaishou\.com/profile/([A-Za-z0-9_-]+)"),
     "https://www.kuaishou.com/profile/{}"),
    ("bilibili", re.compile(r"space\.bilibili\.com/(\d+)"),
     "https://space.bilibili.com/{}"),
)


def _match_author(url: str) -> dict | None:
    for platform, pattern, tmpl in _AUTHOR_PATTERNS:
        m = pattern.search(url)
        if m:
            return {"platform": platform, "url": tmpl.format(m.group(1))}
    return None


def _follow_redirect(url: str) -> str:
    """短链跟随重定向，返回最终 URL。"""
    r = requests.get(url, headers={"User-Agent": BROWSER_UA},
                     timeout=15, allow_redirects=True)
    return r.url


def resolve_author_url(text: str) -> dict:
    """从分享文本识别作者主页，返回 {"platform": ..., "url": 作者主页 URL}。"""
    url = extract_url(text)
    info = _match_author(url)
    if not info and any(d in url for d in _SHORT_DOMAINS):
        info = _match_author(_follow_redirect(url))
    if not info:
        raise ValueError("无法识别的作者主页链接，目前支持 抖音/快手/小红书/B站")
    return info


# ================= B 站：yt-dlp flat playlist =================

def _bilibili_works(author: dict) -> dict:
    import yt_dlp

    opts = {
        "quiet": True, "no_warnings": True,
        "extract_flat": True,  # 只取列表元数据，不逐条解析
        "noplaylist": False,
    }
    cf = make_ytdlp_cookiefile("bilibili")
    if cf:
        opts["cookiefile"] = str(cf)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(author["url"], download=False)

    works = []
    # extract_flat 下 entries 可能是惰性生成器，先物化再截断
    for e in list(info.get("entries") or [])[:MAX_WORKS]:
        bvid = e.get("id")
        if not bvid:
            continue
        works.append({
            "id": bvid,
            "title": e.get("title") or "未命名视频",
            "cover_url": "",
            "url": f"https://www.bilibili.com/video/{bvid}",
            "duration": e.get("duration"),
            "like_count": None,
            "create_time": None,
        })
    return {
        "author": {
            "name": info.get("uploader") or info.get("channel") or PLATFORM_NAMES["bilibili"],
            "avatar": "",
            "url": author["url"],
            "platform": "bilibili",
        },
        "works": works,
    }


# ================= 浏览器拦截解析（抖音/小红书/快手） =================

def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _norm_ts(v) -> int | None:
    """时间戳归一化为秒（部分平台给的是毫秒）。"""
    ts = _safe_int(v)
    if ts and ts > 10**11:
        ts = ts // 1000
    return ts


def _find_photo_items(obj, out: list):
    """深搜快手 graphql 响应 JSON，收集含 photoId 的作品字典。"""
    if isinstance(obj, dict):
        if obj.get("photoId"):
            out.append(obj)
        for v in obj.values():
            _find_photo_items(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _find_photo_items(v, out)


def _find_author_info(obj):
    """深搜 JSON，返回 (authorName, headUrl)，找不到返回 None。"""
    if isinstance(obj, dict):
        name = obj.get("authorName")
        if isinstance(name, str) and name.strip():
            head = obj.get("headUrl")
            return name.strip(), head if isinstance(head, str) else ""
        for v in obj.values():
            r = _find_author_info(v)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_author_info(v)
            if r:
                return r
    return None


def _parse_response(platform: str, resp_url: str, data, works: dict, author: dict):
    """从拦截到的接口响应里提取作品条目（按 id 去重写入 works）。"""
    if platform == "douyin":
        if "aweme/v1/web/aweme/post" not in resp_url:
            return
        for item in data.get("aweme_list") or []:
            aweme_id = item.get("aweme_id")
            if not aweme_id or aweme_id in works:
                continue
            video = item.get("video") or {}
            cover_list = (video.get("cover") or {}).get("url_list") or []
            duration = _safe_int(item.get("duration") or video.get("duration"))
            au = item.get("author") or {}
            if au.get("nickname") and not author.get("_captured"):
                author["name"] = au["nickname"]
                avatars = (au.get("avatar_thumb") or {}).get("url_list") or []
                author["avatar"] = avatars[0] if avatars else ""
                author["_captured"] = True
            works[aweme_id] = {
                "id": aweme_id,
                "title": (item.get("desc") or "").strip() or "未命名视频",
                "cover_url": cover_list[0] if cover_list else "",
                "url": f"https://www.douyin.com/video/{aweme_id}",
                # 抖音 duration 是毫秒
                "duration": round(duration / 1000) if duration else None,
                "like_count": _safe_int((item.get("statistics") or {}).get("digg_count")),
                "create_time": _safe_int(item.get("create_time")),
            }
    elif platform == "xiaohongshu":
        if "api/sns/web/v1/user_posted" not in resp_url:
            return
        for item in ((data.get("data") or {}).get("notes") or []):
            note_id = item.get("note_id")
            if not note_id or note_id in works:
                continue
            cover = item.get("cover") or {}
            xsec = item.get("xsec_token") or ""
            user = item.get("user") or {}
            if user.get("nickname") and not author.get("_captured"):
                author["name"] = user["nickname"]
                author["avatar"] = user.get("avatar") or ""
                author["_captured"] = True
            works[note_id] = {
                "id": note_id,
                "title": (item.get("display_title") or "").strip() or "未命名笔记",
                "cover_url": cover.get("url_default") or cover.get("url") or "",
                "url": f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec}",
                "duration": None,
                "like_count": _safe_int((item.get("interact_info") or {}).get("liked_count")),
                "create_time": _norm_ts(item.get("time")),
            }
    elif platform == "kuaishou":
        if "graphql" not in resp_url:
            return
        # 从作者主页 URL 提取 profile userId，用于校验作品归属
        m = re.search(r"/profile/([A-Za-z0-9_-]+)", author.get("url") or "")
        profile_id = m.group(1) if m else ""
        # 快手 graphql 响应混有推荐流等非本作者数据：URL 带操作名特征
        # （如 visionProfilePhoto）的才整体信任，否则逐条校验 authorId
        is_profile_api = "visionProfilePhoto" in resp_url
        items: list[dict] = []
        _find_photo_items(data, items)
        accepted: list[dict] = []
        for item in items:
            if not is_profile_api:
                item_author = str(item.get("authorId") or "")
                # 无操作名特征且无法校验作者归属的条目一律跳过
                if not profile_id or not item_author or item_author != profile_id:
                    continue
            accepted.append(item)
        # 补作者名/头像：先从已确认归属的作品条目上找，找不到保持兜底逻辑
        if accepted and not author.get("_captured"):
            info = _find_author_info(accepted)
            if info:
                author["name"], author["avatar"] = info
                author["_captured"] = True
        for item in accepted:
            photo_id = str(item.get("photoId"))
            if photo_id in works:
                continue
            duration = _safe_int(item.get("duration"))
            works[photo_id] = {
                "id": photo_id,
                "title": (item.get("caption") or "").strip() or "未命名视频",
                "cover_url": item.get("coverUrl") or "",
                "url": f"https://www.kuaishou.com/short-video/{photo_id}",
                # 快手 duration 是毫秒
                "duration": round(duration / 1000) if duration else None,
                "like_count": _safe_int(item.get("likeCount")),
                "create_time": _norm_ts(item.get("timestamp")),
            }


def _author_from_title(title: str, platform: str) -> str:
    """从页面 title 兜底取作者名（如「某某的主页 - 抖音」）。"""
    name = re.split(r"[-_—|]", title or "")[0]
    name = re.sub(r"的主页|的个人空间|主页", "", name).strip()
    return name or PLATFORM_NAMES[platform]


def _scrape_with_browser(platform: str, url: str) -> dict:
    """patchright 打开作者主页，滚动加载 + 拦截分页接口，收集全部作品。"""
    from patchright.sync_api import sync_playwright

    works: dict[str, dict] = {}
    author = {"name": "", "avatar": "", "url": url, "platform": platform}

    deadline = time.time() + SCRAPE_TIMEOUT

    def on_response(resp):
        # 超过整体时限后直接丢弃响应，避免 resp.json() 在挂起的响应流上卡死
        if time.time() > deadline or len(works) >= MAX_WORKS:
            return
        try:
            _parse_response(platform, resp.url, resp.json(), works, author)
        except Exception:
            pass  # 非 JSON 响应或结构不符都跳过，不中断抓取

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        # watchdog：到达 deadline 后若抓取仍卡住（如 resp.json() 挂起），
        # 强制关闭浏览器以中断阻塞中的调用，保证 finally 走通、author_lock 被释放
        scrape_done = threading.Event()

        def _watchdog():
            if not scrape_done.wait(max(deadline - time.time(), 0)):
                try:
                    browser.close()
                except Exception:
                    pass

        threading.Thread(target=_watchdog, daemon=True).start()
        try:
            context = browser.new_context(
                storage_state=str(cookie_path(platform)),
                user_agent=BROWSER_UA,
            )
            try:
                page = context.new_page()
                page.on("response", on_response)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                # 滚动触发分页加载：作品数连续多屏不再增长就停止
                last_count, stable = -1, 0
                for _ in range(MAX_SCROLLS):
                    if time.time() > deadline or len(works) >= MAX_WORKS:
                        break
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1500)
                    if len(works) == last_count:
                        stable += 1
                        if stable >= 3:
                            break
                    else:
                        stable = 0
                        last_count = len(works)
                if not author["name"]:
                    try:
                        author["name"] = _author_from_title(page.title(), platform)
                    except Exception:
                        author["name"] = PLATFORM_NAMES[platform]
            finally:
                context.close()
        finally:
            browser.close()
            scrape_done.set()

    author.pop("_captured", None)
    if not author["name"]:
        author["name"] = PLATFORM_NAMES[platform]
    return {"author": author, "works": list(works.values())[:MAX_WORKS]}


def _scrape_locked(platform: str, url: str) -> dict:
    """带全局锁的浏览器抓取：锁被占用直接报错（429），不排队（防内存叠加）。"""
    if not author_lock.acquire(blocking=False):
        raise AuthorBusyError("另一个抓取任务进行中，请稍后再试")
    try:
        return _scrape_with_browser(platform, url)
    finally:
        author_lock.release()


async def parse_author_works(text: str) -> dict:
    """解析分享文本 → 作者全部作品列表（统一返回结构见 routers/author.py）。"""
    author = await asyncio.to_thread(resolve_author_url, text)
    platform = author["platform"]
    if platform == "bilibili":
        return await asyncio.to_thread(_bilibili_works, author)
    # 浏览器抓取的平台必须有登录 cookie
    if not cookie_exists(platform):
        raise ValueError(
            f"缺少{PLATFORM_NAMES[platform]}登录 cookie，请先到「平台账号」页扫码登录该平台")
    return await asyncio.to_thread(_scrape_locked, platform, author["url"])
