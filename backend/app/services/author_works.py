"""作者全部作品列表：识别作者主页链接并抓取其全部作品。

- B站走 wbi 签名直接调官方接口（免登录，无需浏览器）
- 抖音/快手/小红书用 patchright(chromium) 打开作者主页，拦截分页接口 JSON
  并滚动触发加载；浏览器抓取全局串行（author_lock），防止 1.9GB 内存爆掉
"""
import asyncio
import hashlib
import json
import re
import threading
import time
import urllib.parse

import requests

from app.config import PLATFORM_NAMES
from app.services.cookies import cookie_exists, cookie_path
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


# ================= B 站：wbi 签名调官方接口（免登录，替代 yt-dlp space） =================

# wbi 混淆表（bilibili-API-collect 标准表）：imgKey+subKey 按下标重排取前 32 位得 mixin_key
_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]

# wbi 密钥模块级缓存（12 小时）：nav 接口拿 imgKey/subKey，不必每个请求都取
_wbi_cache = {"img_key": "", "sub_key": "", "expires": 0.0}
_WBI_CACHE_TTL = 12 * 3600

# B站风控码：触发时重试，仍失败则引导用户稍后重试或配 cookie
_BILI_RISK_CODES = (-352, -412)


def _get_wbi_keys() -> tuple[str, str]:
    """取 wbi 签名用的 imgKey/subKey（nav 接口返回的 wbi_img 文件名），带 12 小时缓存。"""
    now = time.time()
    if _wbi_cache["img_key"] and now < _wbi_cache["expires"]:
        return _wbi_cache["img_key"], _wbi_cache["sub_key"]
    r = requests.get("https://api.bilibili.com/x/web-interface/nav",
                     headers={"User-Agent": BROWSER_UA}, timeout=15)
    wbi_img = r.json()["data"]["wbi_img"]
    img_key = wbi_img["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub_key = wbi_img["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    _wbi_cache.update(img_key=img_key, sub_key=sub_key, expires=now + _WBI_CACHE_TTL)
    return img_key, sub_key


def _wbi_sign(params: dict) -> dict:
    """wbi 签名：参数加 wts，按 key 排序 urlencode，w_rid = md5(query + mixin_key)。"""
    img_key, sub_key = _get_wbi_keys()
    mixin_key = "".join((img_key + sub_key)[i] for i in _MIXIN_KEY_ENC_TAB)[:32]
    params = {**params, "wts": int(time.time())}
    # 按 wbi 规范过滤 value 中的 !'()* 字符
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(sorted(params.items()))
    params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
    return params


def _bili_get_ticket(session: requests.Session) -> None:
    """申请 bili_ticket（匿名风控票据）写入 session cookie，显著降低 -352/412 概率。"""
    import hmac
    ts = int(time.time())
    hexsign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
    r = session.post(
        "https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket",
        params={"key_id": "ec02", "hexsign": hexsign, "context[ts]": ts, "csrf": ""},
        headers={"Referer": "https://www.bilibili.com/"}, timeout=15)
    ticket = (r.json().get("data") or {}).get("ticket")
    if ticket:
        session.cookies.set("bili_ticket", ticket, domain=".bilibili.com")


def _bili_prepare_session(mid: str) -> tuple[requests.Session, dict]:
    """构建带匿名风控 cookie（buvid3 + bili_ticket）与完整浏览器请求头的会话。"""
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": f"https://space.bilibili.com/{mid}",
        "Origin": "https://space.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    session = requests.Session()
    try:
        session.get("https://www.bilibili.com/", headers=headers, timeout=15)  # 拿 buvid3/b_nut
        _bili_get_ticket(session)
    except requests.RequestException:
        pass
    return session, headers


def _bili_get_json(session: requests.Session, url: str, params: dict, headers: dict) -> dict:
    """调 B站接口并处理风控（412/-352 是间歇性的，实测同一参数重试几次可过）。"""
    code = None
    for attempt in range(8):  # 首次 + 最多 7 次重试（风控为概率拦截，实测重试可过）
        if attempt:
            time.sleep(3)
            if attempt % 2 == 0:  # 隔次换票据，避免换票过频反而触发风控
                try:
                    _bili_get_ticket(session)
                except requests.RequestException:
                    pass
        r = session.get(url, params=params, headers=headers, timeout=15)
        if r.status_code == 412:
            continue
        r.raise_for_status()
        data = r.json()
        code = data.get("code", 0)
        if code == 0:
            return data
        if code in _BILI_RISK_CODES:
            continue
        break
    if code in _BILI_RISK_CODES:
        raise ValueError("B站风控拦截，请稍后重试或到「平台账号」页配置 B站 cookie")
    if code is None:
        raise ValueError("B站风控拦截（412），请稍后重试或到「平台账号」页配置 B站 cookie")
    raise RuntimeError(f"B站接口返回错误（code={code}）：{(data.get('message') or '').strip() or '未知错误'}")


def _bili_duration(v) -> int | None:
    """"mm:ss"/"hh:mm:ss" 时长字符串转秒（兼容已经是数字的情况）。"""
    if not isinstance(v, str) or ":" not in v:
        return _safe_int(v)
    try:
        parts = [int(p) for p in v.split(":")]
    except ValueError:
        return None
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + p
    return seconds


def _bilibili_works(author: dict) -> dict:
    """wbi 签名分页拉作者全部投稿（上限 MAX_WORKS 条），免登录无需 cookie。"""
    m = re.search(r"space\.bilibili\.com/(\d+)", author["url"])
    mid = m.group(1)
    session, headers = _bili_prepare_session(mid)
    api = "https://api.bilibili.com/x/space/wbi/arc/search"
    works: list[dict] = []
    author_name = ""
    pn = 1
    deadline = time.time() + 90  # 分页总时长熔断：超时返回已抓到的部分（路由上限 120s）
    while len(works) < MAX_WORKS and time.time() < deadline:
        params = _wbi_sign({"mid": mid, "pn": pn, "ps": 30, "order": "pubdate",
                            "platform": "web", "web_location": 1550101})
        try:
            data = _bili_get_json(session, api, params, headers)
        except ValueError:
            if works:
                # 翻页中途被风控：返回已抓到的部分，不让整个请求失败
                break
            raise
        body = data.get("data") or {}
        vlist = (body.get("list") or {}).get("vlist") or []
        for v in vlist:
            bvid = v.get("bvid")
            if not bvid:
                continue
            if not author_name:
                author_name = (v.get("author") or "").strip()
            pic = v.get("pic") or ""
            if pic.startswith("//"):
                pic = "https:" + pic
            works.append({
                "id": bvid,
                "title": (v.get("title") or "").strip() or "未命名视频",
                "cover_url": pic,
                "url": f"https://www.bilibili.com/video/{bvid}",
                "duration": _bili_duration(v.get("length")),
                "like_count": None,
                "create_time": _safe_int(v.get("created")),
            })
        total = _safe_int((body.get("page") or {}).get("count")) or 0
        if not vlist or pn * 30 >= total:
            break
        pn += 1
        time.sleep(3)  # 请求间隔防风控（风控敏感接口，宁慢勿封）
    return {
        "author": {
            "name": author_name or PLATFORM_NAMES["bilibili"],
            "avatar": "",
            "url": author["url"],
            "platform": "bilibili",
        },
        "works": works[:MAX_WORKS],
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
