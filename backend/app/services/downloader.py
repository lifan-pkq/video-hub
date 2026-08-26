"""抖音（及其他 yt-dlp 支持平台）视频解析与下载。

抖音目前必须带 cookie 才能解析：优先使用发布引擎里登录保存的
douyin cookie（storage_state），自动转成 Netscape 格式喂给 yt-dlp。
"""
import asyncio
import re
from pathlib import Path

import yt_dlp

from app.config import COVERS_DIR, DOWNLOAD_TIMEOUT, TMP_DIR, VIDEOS_DIR, ensure_dirs
from app.db import add_video
from app.services.cookies import make_ytdlp_cookiefile
from app.services.media import ensure_h264, make_cover, probe_video

URL_RE = re.compile(r"https?://[^\s，。\"'）)】]+")


def extract_url(text: str) -> str:
    """从分享文本中提取第一个 URL（支持抖音完整分享口令）。"""
    m = URL_RE.search(text.strip())
    if not m:
        raise ValueError("未在文本中找到链接")
    return m.group(0)


def _cookiefile_for(url: str) -> Path | None:
    if "douyin.com" in url or "iesdouyin.com" in url:
        return make_ytdlp_cookiefile("douyin")
    return None


def _base_opts(url: str) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    cf = _cookiefile_for(url)
    if cf:
        opts["cookiefile"] = str(cf)
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


def _do_download(task, text: str) -> tuple[Path, dict]:
    """yt-dlp 下载视频到视频库目录，返回 (final_path, info)。转码等后处理在别处做。"""
    url = extract_url(text)
    ensure_dirs()
    out_tmpl = str(TMP_DIR / "%(id)s.%(ext)s")
    opts = {
        **_base_opts(url),
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
            msg += "（提示：抖音下载依赖登录 cookie，请到「平台账号」页检查抖音 cookie 是否有效）"
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
