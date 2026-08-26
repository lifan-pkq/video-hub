"""多平台视频解析与下载。

免 cookie 优先链：每个平台先试免 cookie 通道，失败自动回退到 cookie 通道。

- 抖音：先试 requests 抓分享页 RENDER_DATA 直链（免 cookie），失败回退 yt-dlp+cookie
- 快手：不被 yt-dlp 支持，走自定义路径：requests 抓作品页解析 window.INIT_STATE
  里的播放地址后直接流式下载 mp4；先试匿名，403/解析失败再带 cookie 重试
- 小红书：yt-dlp 不强制 cookie，先匿名试，失败且有 cookie 再带 cookie 重试
- B站：yt-dlp 带浏览器 UA/Referer/Origin 头规避 412 风控，按 URL 域名自动带 cookie
  （发布引擎里保存的 storage_state，自动转 Netscape 格式喂给 yt-dlp）
"""
import asyncio
import json
import re
import urllib.parse
from pathlib import Path

import requests
import yt_dlp

from app.config import COVERS_DIR, DOWNLOAD_TIMEOUT, TMP_DIR, VIDEOS_DIR, ensure_dirs
from app.db import add_video
from app.services.cookies import make_ytdlp_cookiefile, storage_state_to_requests_cookies
from app.services.media import ensure_h264, make_cover, probe_video

URL_RE = re.compile(r"https?://[^\s，。\"'）)】]+")

# 常见桌面 UA：快手网页版/直链 CDN 对 UA 敏感
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
# 移动端 UA：抖音分享页对移动端 UA 更友好（匿名也能拿到完整 RENDER_DATA）
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15"


def extract_url(text: str) -> str:
    """从分享文本中提取第一个 URL（支持抖音完整分享口令）。"""
    m = URL_RE.search(text.strip())
    if not m:
        raise ValueError("未在文本中找到链接")
    return m.group(0)


# URL 域名 → cookie 平台名（平台名以 config.PLATFORMS 为准）
_DOMAIN_PLATFORM = (
    (("douyin.com", "iesdouyin.com"), "douyin"),
    (("kuaishou.com",), "kuaishou"),
    (("xiaohongshu.com", "xhslink.com"), "xiaohongshu"),
    (("bilibili.com", "b23.tv"), "bilibili"),
)


def _cookiefile_for(url: str) -> Path | None:
    for domains, platform in _DOMAIN_PLATFORM:
        if any(d in url for d in domains):
            return make_ytdlp_cookiefile(platform)
    return None


def _is_kuaishou_url(url: str) -> bool:
    return "kuaishou.com" in url


def _is_douyin_url(url: str) -> bool:
    return "douyin.com" in url or "iesdouyin.com" in url


def _is_xhs_url(url: str) -> bool:
    return "xiaohongshu.com" in url or "xhslink.com" in url


def _is_bilibili_url(url: str) -> bool:
    return "bilibili.com" in url or "b23.tv" in url


def _base_opts(url: str, use_cookie: bool = True) -> dict:
    """yt-dlp 基础参数。use_cookie=False 时强制匿名（免 cookie 优先链的第一步）。"""
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if use_cookie:
        cf = _cookiefile_for(url)
        if cf:
            opts["cookiefile"] = str(cf)
    if _is_bilibili_url(url):
        # B站单视频：带浏览器请求头规避 412 风控
        opts["http_headers"] = {
            "User-Agent": BROWSER_UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
    return opts


def parse_video(text: str) -> dict:
    """解析分享链接，返回标题/封面/时长/平台等信息（不下载）。"""
    url = extract_url(text)
    opts = {**_base_opts(url), "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return {
        "url": url,
        "title": info.get("title") or "未命名视频",
        "author": info.get("uploader") or info.get("channel"),
        "cover_url": info.get("thumbnail"),
        "duration": info.get("duration"),
        "platform": (info.get("extractor_key") or "unknown").lower(),
        "video_id": info.get("id"),
    }


# 部分 yt-dlp 版本无 DownloadCancelled，回退到基类
DownloadCancelled = getattr(yt_dlp.utils, "DownloadCancelled", yt_dlp.utils.YoutubeDLError)


def _progress_hook(task, info: dict):
    # 超时分支会置 cancelled 标志，这里中断仍在运行的下载线程
    if getattr(task, "cancelled", False):
        raise DownloadCancelled("下载已超时取消")
    if info.get("status") == "downloading":
        total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
        done = info.get("downloaded_bytes") or 0
        if total:
            task.progress = min(done / total * 100, 99.0)


def _cleanup_partial_files():
    """清理超时取消残留的 yt-dlp 临时文件（.part / .ytdl），防止孤儿文件。"""
    for pattern in ("*.part", "*.ytdl"):
        for p in TMP_DIR.glob(pattern):
            try:
                p.unlink()
            except OSError:
                pass


# ================= 快手自定义下载（yt-dlp 2026.08 尚不支持 kuaishou） =================

_KS_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Referer": "https://www.kuaishou.com/",
}


def _collect_ks_candidates(obj, out: list):
    """深搜 JSON 树，收集含 playUrl/photoUrl/srcNoMark/mainMvUrls 键的字典（候选作品信息）。"""
    if isinstance(obj, dict):
        has_direct = any(isinstance(obj.get(k), str) and obj[k].startswith("http")
                         for k in ("playUrl", "photoUrl", "srcNoMark"))
        if has_direct or isinstance(obj.get("mainMvUrls"), list):
            out.append(obj)
        for v in obj.values():
            _collect_ks_candidates(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_ks_candidates(v, out)


def _ks_video_url(cand: dict) -> str | None:
    """从候选作品字典里取 mp4 直链（srcNoMark 是匿名可用的无水印地址）。"""
    for k in ("playUrl", "photoUrl", "srcNoMark"):
        v = cand.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    for item in cand.get("mainMvUrls") or []:
        if isinstance(item, dict) and isinstance(item.get("url"), str) and item["url"].startswith("http"):
            return item["url"]
    return None


def _ks_is_video_candidate(cand: dict) -> bool:
    """候选是否为真正的视频作品：直链含 .mp4 或带 mainMvUrls 视频特征。"""
    url = _ks_video_url(cand)
    if not url:
        return False
    return ".mp4" in url or bool(cand.get("mainMvUrls"))


def _parse_ks_init_state(html: str, vid: str | None = None) -> dict:
    """从作品页 HTML 解析 window.INIT_STATE（JSON），返回选中的作品信息字典。

    vid 为当前作品 ID（从页面 URL 解析），用于在候选中优先匹配本作品，
    防止同页推荐位等其它作品混入导致下载错视频。
    """
    m = re.search(r"window\.INIT_STATE\s*=\s*", html)
    if not m:
        raise RuntimeError("未能在快手页面中找到作品数据（INIT_STATE），cookie 可能已失效")
    start = m.end()
    end = html.find(";(function()", start)
    raw = html[start:end if end != -1 else len(html)].strip().rstrip(";")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # INIT_STATE 里偶发出现裸 undefined，不是合法 JSON，先替换再重试
        try:
            data = json.loads(re.sub(r"(?<=[:\[,])undefined(?=[,\]}])", "null", raw))
        except json.JSONDecodeError:
            raise RuntimeError("快手页面数据解析失败，cookie 可能已失效或作品不存在")
    candidates: list[dict] = []
    _collect_ks_candidates(data, candidates)
    # 优先匹配当前作品 ID 的候选，没有匹配再退化到全部候选
    if vid:
        matched = [c for c in candidates if str(c.get("photoId") or "") == vid]
        if matched:
            candidates = matched
    # 只接受视频候选：图文作品的 photoUrl 是 jpg，不能当 mp4 下载
    for cand in candidates:
        if _ks_is_video_candidate(cand):
            return cand
    raise RuntimeError("图文作品暂不支持下载")


def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _download_kuaishou_once(url: str, task, use_cookie: bool) -> tuple[Path, dict]:
    """快手作品下载（单次尝试）：requests 抓页面 → 解析 INIT_STATE → 流式下载 mp4 到 TMP_DIR。

    use_cookie=False 为匿名模式（KS-Downloader 验证过快手分享页可匿名拿到 srcNoMark）。
    """
    http = requests.Session()
    http.headers.update(_KS_HEADERS)
    if use_cookie:
        http.cookies.update(storage_state_to_requests_cookies("kuaishou"))
    # 短链（v.kuaishou.com 等）跟随重定向到作品页
    r = http.get(url, timeout=30, allow_redirects=True)
    # 403（触发风控/需要登录）会在这里抛 HTTPError，由外层决定是否带 cookie 重试
    r.raise_for_status()
    page_url = r.url
    # 先从页面 URL 解析当前作品 ID，传给解析器按 ID 匹配候选，防下载错视频
    m = re.search(r"/(?:short-video|fw/photo|photo)/([A-Za-z0-9_-]+)", page_url)
    vid = m.group(1) if m else ""
    cand = _parse_ks_init_state(r.text, vid or None)
    play_url = _ks_video_url(cand)
    if play_url.startswith("//"):
        play_url = "https:" + play_url

    if not vid:
        vid = str(cand.get("photoId") or cand.get("id") or "video")
    # 视频 id 消毒，防拼路径时越界
    vid = re.sub(r"[^A-Za-z0-9_-]", "_", vid)[:64] or "video"
    title = (cand.get("caption") or cand.get("title") or "未命名视频").strip() or "未命名视频"
    duration_ms = _safe_int(cand.get("duration"))
    info = {
        "id": vid,
        "title": title,
        # 快手的 duration 通常是毫秒
        "duration": round(duration_ms / 1000) if duration_ms else None,
        "width": _safe_int(cand.get("width")),
        "height": _safe_int(cand.get("height")),
        "webpage_url": page_url,
        "extractor_key": "kuaishou",
    }

    # tmp 文件名带任务 id，避免并发下载同一链接时互相截断
    tmp_path = TMP_DIR / f"ks_{vid}_{task.id}.mp4"
    with http.get(play_url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = _safe_int(resp.headers.get("Content-Length")) or 0
        done = 0
        try:
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    # 超时分支会置 cancelled 标志，这里中断下载
                    if getattr(task, "cancelled", False):
                        raise DownloadCancelled("下载已超时取消")
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        task.progress = min(done / total * 100, 99.0)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    return tmp_path, info


def _download_kuaishou(url: str, task) -> tuple[Path, dict]:
    """快手免 cookie 优先：先匿名抓分享页，403/解析失败再带 cookie 重试。"""
    try:
        return _download_kuaishou_once(url, task, use_cookie=False)
    except DownloadCancelled:
        raise
    except Exception as e:
        task.log(f"快手免 cookie 解析失败（{e}），改用 cookie 重试…")
        return _download_kuaishou_once(url, task, use_cookie=True)


# ================= 抖音免 cookie 直链解析（RENDER_DATA） =================

def _collect_douyin_urls(obj, out: list):
    """深搜 RENDER_DATA JSON，收集 playApi/play_addr/url_list 下的 http 地址。"""
    if isinstance(obj, dict):
        for k in ("playApi", "play_addr", "url_list"):
            v = obj.get(k)
            if isinstance(v, str) and v.startswith("http"):
                out.append(v)
            elif isinstance(v, list):
                out.extend(u for u in v if isinstance(u, str) and u.startswith("http"))
            elif isinstance(v, dict):
                _collect_douyin_urls(v, out)
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _collect_douyin_urls(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_douyin_urls(v, out)


def _find_first_str(obj, key: str):
    """深搜 JSON，返回第一个指定 key 的非空字符串值（用于兜底取标题）。"""
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        for v in obj.values():
            r = _find_first_str(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_first_str(v, key)
            if r:
                return r
    return None


def _pick_douyin_play_url(urls: list[str]) -> str:
    """从候选地址里挑出视频直链（排除封面图等静态资源），并转为无水印地址。"""
    videos = [u for u in urls
              if ("playwm" in u or "/play/" in u or ".mp4" in u)
              and "cover" not in u and "image" not in u and "imagex" not in u]
    if not videos:
        raise RuntimeError("未能从抖音分享页解析出视频地址（可能触发风控或页面结构变更）")
    # 无水印：播放地址里 playwm 换成 play
    return videos[0].replace("playwm", "play")


def _download_douyin_nocookie(url: str, task) -> tuple[Path, dict]:
    """抖音免 cookie 下载：短链跟随重定向 → 分享页 RENDER_DATA → 无水印 play 地址流式下载。

    任何一步失败都抛异常，由外层回退到 yt-dlp+cookie 通道。
    """
    http = requests.Session()
    http.headers.update({
        "User-Agent": MOBILE_UA,
        "Referer": "https://www.douyin.com/",
    })
    r = http.get(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    page_url = r.url
    m = re.search(r'<script id="RENDER_DATA"[^>]*>(.*?)</script>', r.text, re.S)
    if not m:
        raise RuntimeError("抖音分享页未找到 RENDER_DATA（可能触发风控或页面结构变更）")
    data = json.loads(urllib.parse.unquote(m.group(1)))
    urls: list[str] = []
    _collect_douyin_urls(data, urls)
    play_url = _pick_douyin_play_url(urls)
    if play_url.startswith("//"):
        play_url = "https:" + play_url

    mvid = re.search(r"/(?:video|note)/(\d+)", page_url)
    vid = mvid.group(1) if mvid else "video"
    info = {
        "id": vid,
        "title": _find_first_str(data, "desc") or "抖音视频",
        "duration": None,
        "webpage_url": page_url,
        "extractor_key": "douyin",
    }

    # tmp 文件名带任务 id，避免并发下载同一链接时互相截断
    tmp_path = TMP_DIR / f"dy_{vid}_{task.id}.mp4"
    with http.get(play_url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = _safe_int(resp.headers.get("Content-Length")) or 0
        done = 0
        try:
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    # 超时分支会置 cancelled 标志，这里中断下载
                    if getattr(task, "cancelled", False):
                        raise DownloadCancelled("下载已超时取消")
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        task.progress = min(done / total * 100, 99.0)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
    return tmp_path, info


def _ytdlp_download(task, url: str, use_cookie: bool = True) -> tuple[Path, dict]:
    """yt-dlp 下载到 TMP_DIR，返回 (downloaded_path, info)。"""
    out_tmpl = str(TMP_DIR / "%(id)s.%(ext)s")
    opts = {
        **_base_opts(url, use_cookie=use_cookie),
        "outtmpl": out_tmpl,
        # 优先 H.264(avc1)+音频的 mp4 组合：HEVC 浏览器解不了（只有声音没有画面）
        "format": "bv*[vcodec^=avc1]+ba/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "progress_hooks": [lambda i: _progress_hook(task, i)],
        "writethumbnail": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = Path(ydl.prepare_filename(info))
        if downloaded.suffix != ".mp4" and downloaded.with_suffix(".mp4").exists():
            downloaded = downloaded.with_suffix(".mp4")
    return downloaded, info


def _download_with_fallback(task, url: str) -> tuple[Path, dict]:
    """按平台走「免 cookie → 失败回退 cookie 通道」优先链，都失败抛最后的错。"""
    if _is_kuaishou_url(url):
        task.log("检测到快手链接，使用内置解析器下载…")
        # 内部自带 匿名→cookie 回退
        return _download_kuaishou(url, task)
    if _is_douyin_url(url):
        try:
            task.log("抖音：先尝试免 cookie 直链解析…")
            downloaded, info = _download_douyin_nocookie(url, task)
            task.log("免 cookie 直链解析成功")
            return downloaded, info
        except DownloadCancelled:
            raise
        except Exception as e:
            task.log(f"抖音免 cookie 解析失败（{e}），回退 yt-dlp + cookie 通道…")
            return _ytdlp_download(task, url)
    if _is_xhs_url(url):
        # 小红书不强制 cookie：先匿名 yt-dlp，报需要登录/解析失败且有 cookie 再带 cookie 重试
        try:
            task.log("小红书：先尝试免 cookie 解析…")
            return _ytdlp_download(task, url, use_cookie=False)
        except DownloadCancelled:
            raise
        except Exception as e:
            if not make_ytdlp_cookiefile("xiaohongshu"):
                raise
            task.log(f"小红书免 cookie 解析失败（{e}），使用 cookie 重试…")
            return _ytdlp_download(task, url, use_cookie=True)
    return _ytdlp_download(task, url)


def _do_download(task, text: str) -> tuple[Path, dict]:
    """下载视频到视频库目录，返回 (final_path, info)。转码等后处理在别处做。"""
    url = extract_url(text)
    ensure_dirs()
    downloaded, info = _download_with_fallback(task, url)

    title = (info.get("title") or "未命名视频").strip()
    # 视频 id 消毒后拼入文件名，防路径穿越
    vid = re.sub(r'[^A-Za-z0-9_-]', '_', str(info.get('id') or ''))[:64] or "video"
    safe_title = re.sub(r'[\\/:*?"<>|#\n\r]', "_", title)[:80].strip("_") or f"video_{vid}"
    final_path = VIDEOS_DIR / f"{safe_title}_{vid}.mp4"
    if final_path.exists():
        final_path = VIDEOS_DIR / f"{safe_title}_{vid}_{task.id}.mp4"
    final_path = final_path.resolve()
    if VIDEOS_DIR.resolve() not in final_path.parents:
        raise RuntimeError("非法的下载文件名（路径越界）")
    downloaded.replace(final_path)
    return final_path, info


def _post_process(task, final_path: Path, info: dict) -> dict:
    """转码 H.264 + 探测元数据 + 封面 + 入库。长视频转码可能很慢，不占下载超时。"""
    # 浏览器只认 H.264：抖音常返回 H.265(bytevc1)，下载后统一转码
    if ensure_h264(final_path):
        task.log("原视频为 H.265，已转码为 H.264 以保证浏览器可播放")

    meta = probe_video(final_path)
    cover_name = final_path.stem + ".jpg"
    if make_cover(final_path, COVERS_DIR / cover_name):
        meta["cover"] = cover_name

    title = (info.get("title") or "未命名视频").strip()
    video = add_video(
        title=title,
        filename=final_path.name,
        size=final_path.stat().st_size,
        duration=meta.get("duration") or info.get("duration"),
        width=meta.get("width") or info.get("width"),
        height=meta.get("height") or info.get("height"),
        cover=meta.get("cover"),
        source_platform=(info.get("extractor_key") or "").lower() or None,
        source_url=info.get("webpage_url") or None,
    )
    return video


async def run_download(task, text: str):
    task.status = "running"
    task.log("开始解析链接…")
    try:
        final_path, info = await asyncio.wait_for(
            asyncio.to_thread(_do_download, task, text), timeout=DOWNLOAD_TIMEOUT
        )
    except asyncio.TimeoutError:
        # 置取消标志：progress hook 在下载线程里抛异常中断 yt-dlp
        task.cancelled = True
        task.log(f"下载超时（{DOWNLOAD_TIMEOUT}s）")
        _cleanup_partial_files()
        task.finish("failed")
        return
    except Exception as e:
        msg = str(e)
        if "Fresh cookies" in msg or "cookie" in msg.lower():
            msg += "（提示：下载依赖登录 cookie，请到「平台账号」页检查对应平台的 cookie 是否有效）"
        task.log(f"下载失败: {msg}")
        task.result = {"error": msg}
        task.finish("failed")
        return

    try:
        task.log("下载完成，转码/入库处理中…")
        video = await asyncio.to_thread(_post_process, task, final_path, info)
        task.progress = 100.0
        task.result = {"video": video}
        task.log(f"下载完成: {video['title']}")
        task.finish("success")
    except Exception as e:
        task.log(f"后处理失败: {e}")
        task.result = {"error": str(e)}
        task.finish("failed")


# 全局下载锁：批量下载时排队串行执行（抖音风控对并发解析敏感，串行更稳）
download_lock = asyncio.Lock()


async def run_download_queued(task, text: str):
    """批量下载入口：任务保持 pending 排队，拿到锁才开始下载。"""
    if download_lock.locked():
        task.log("排队等待中…")
    async with download_lock:
        await run_download(task, text)
