"""视频下载以 yt-dlp 为主；抖音在 Cookie 失效时走 iesdouyin 分享页备用链路。

图文作品把原图存进图片素材库。抖音图文沿用免 Cookie 备用链路；
本地 Cookie 只在 yt-dlp 匿名失败后作为可选重试。"""

import asyncio
import json
import re
import tempfile
import threading
from pathlib import Path

import yt_dlp

from app.config import COVERS_DIR, DOWNLOAD_TIMEOUT, MAX_UPLOAD_MB, TMP_DIR, VIDEOS_DIR, ensure_dirs
from app.db import add_video, update_video
from app.services.cookies import make_ytdlp_cookiefile
from app.services.douyin_fallback import FallbackCancelled, download_douyin, is_douyin_url, parse_douyin
from app.services.image_gallery import GalleryCancelled, store_originals
from app.services.xiaohongshu_images import download_images as download_xiaohongshu_images
from app.services.xiaohongshu_images import is_xiaohongshu_url, load_image_post as load_xiaohongshu_images
from app.services.media import ensure_h264, make_cover, probe_video
from app.services.quality import assess_source
from app.services.understanding import start_analysis

URL_RE = re.compile(r"https?://[^\s，。\"'）)】]+")
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
DownloadCancelled = getattr(yt_dlp.utils, "DownloadCancelled", yt_dlp.utils.YoutubeDLError)
_download_cancel_events: dict[str, threading.Event] = {}


def explain_download_error(message: str) -> dict:
    """把 yt-dlp / 解析异常收成前端能直接展示的中文原因和下一步。"""
    text = (message or "").strip()
    lower = text.lower()
    if not text or "未在文本中找到链接" in text:
        return {
            "code": "url_missing",
            "message": "没有识别到可用的视频链接。",
            "action": "每行粘贴一条以 http 开头的分享链接后再解析或下载。",
        }
    if "图文作品" in text or "图集原图" in text:
        return {
            "code": "image_post_failed",
            "message": (text[:400] or "图文作品下载失败"),
            "action": "确认链接仍可打开后重试。成功后原图会进入图片素材库，可以逐张查看和下载。",
        }
    if any(marker in text for marker in ("iesdouyin", "detail API", "aweme_id", "备用下载")):
        return {
            "code": "download_failed",
            "message": (text[:400] or "下载失败"),
            "action": "确认链接仍可在抖音 App / 浏览器打开。视频仍失败时，可以把原片上传到素材库。",
        }
    if any(marker in lower for marker in ("timed out", "timeout", "timeouterror", "read timed out")):
        return {
            "code": "download_timeout",
            "message": "解析或下载超时，平台没有在时限内返回视频。",
            "action": "检查网络和代理后重试；仍失败就把原片上传到素材库。",
        }
    if any(marker in lower for marker in ("fresh cookies", "cookies are needed", "sign in to confirm", "login required", "cookie")):
        return {
            "code": "cookies_stale",
            "message": "平台要求更新登录 Cookie，当前 Cookie 已失效或不够新。",
            "action": "更新服务器上该平台的 Cookie 文件后重试，或把原片上传到素材库。",
        }
    return {
        "code": "download_failed",
        "message": (text[:400] or "下载失败"),
        "action": "确认链接仍可在浏览器打开；拿不到高清源时，下载原片后到素材库上传。",
    }


def extract_url(text: str) -> str:
    match = URL_RE.search(text.strip())
    if not match:
        raise ValueError("未在文本中找到链接")
    return match.group(0)


def _cookiefile_for(url: str) -> Path | None:
    for domains, platform in (
        (("douyin.com", "iesdouyin.com"), "douyin"),
        (("kuaishou.com",), "kuaishou"),
        (("xiaohongshu.com", "xhslink.com"), "xiaohongshu"),
        (("bilibili.com", "b23.tv"), "bilibili"),
    ):
        if any(domain in url for domain in domains):
            return make_ytdlp_cookiefile(platform)
    return None


def _base_opts(url: str, use_cookie: bool = True) -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    cookiefile = _cookiefile_for(url) if use_cookie else None
    if cookiefile:
        opts["cookiefile"] = str(cookiefile)
    if "bilibili.com" in url or "b23.tv" in url:
        opts["http_headers"] = {
            "User-Agent": BROWSER_UA,
            "Referer": "https://www.bilibili.com/",
            "Origin": "https://www.bilibili.com",
        }
    return opts


def _best_format(info: dict) -> dict:
    candidates = [item for item in info.get("formats") or [] if isinstance(item, dict) and item.get("vcodec") not in (None, "none")]
    if not candidates:
        return {
            "width": info.get("width"),
            "height": info.get("height"),
            "format_note": info.get("format_note"),
            "tbr": info.get("tbr"),
        }
    best = max(candidates, key=lambda item: (
        int(item.get("height") or 0) * int(item.get("width") or 0),
        float(item.get("tbr") or 0),
    ))
    return {
        "width": best.get("width"),
        "height": best.get("height"),
        "format_note": best.get("format_note"),
        "tbr": best.get("tbr"),
        "vcodec": best.get("vcodec"),
    }


def _image_parse_result(url: str, info: dict, *, fallback: str | None = None, ytdlp_error: str | None = None) -> dict:
    count = int(info.get("image_count") or len(info.get("_image_slides") or []) or 0)
    result = {
        "url": url,
        "title": info.get("title") or "未命名图集",
        "author": info.get("uploader") or info.get("channel"),
        "cover_url": info.get("thumbnail"),
        "duration": None,
        "platform": (info.get("extractor_key") or "unknown").lower(),
        "video_id": info.get("id"),
        "media_kind": "images",
        "image_count": count,
        "available_best": {"format_note": f"图集原图 {count} 张"},
    }
    if fallback:
        result["fallback"] = fallback
    if ytdlp_error:
        result["ytdlp_error"] = ytdlp_error[:200]
    return result


def _douyin_note_url(*values: object) -> bool:
    for value in values:
        if "/note/" in str(value or ""):
            return True
    return False


def parse_video(text: str) -> dict:
    url = extract_url(text)
    try:
        with yt_dlp.YoutubeDL({**_base_opts(url), "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
        if is_douyin_url(url) and _douyin_note_url(url, info.get("webpage_url")):
            try:
                note = parse_douyin(url)
            except Exception:
                note = None
            if note and note.get("media_kind") == "images":
                return _image_parse_result(url, note, fallback="iesdouyin")
        if is_xiaohongshu_url(url):
            formats = [item for item in info.get("formats") or [] if isinstance(item, dict)]
            has_video = any(item.get("vcodec") not in (None, "none", "") for item in formats)
            if not has_video:
                try:
                    note = load_xiaohongshu_images(url)
                except Exception:
                    note = None
                if note:
                    return _image_parse_result(url, note)
        return {
            "url": url,
            "title": info.get("title") or "未命名视频",
            "author": info.get("uploader") or info.get("channel"),
            "cover_url": info.get("thumbnail"),
            "duration": info.get("duration"),
            "platform": (info.get("extractor_key") or "unknown").lower(),
            "video_id": info.get("id"),
            "available_best": _best_format(info),
        }
    except Exception as ytdlp_exc:
        if is_douyin_url(url):
            try:
                info = parse_douyin(url)
            except Exception as fallback_exc:
                raise RuntimeError(
                    f"yt-dlp 解析失败且 iesdouyin 备用解析也失败：{fallback_exc}"
                ) from fallback_exc
            if info.get("media_kind") == "images":
                return _image_parse_result(url, info, fallback="iesdouyin", ytdlp_error=str(ytdlp_exc))
            return {
                "url": url,
                "title": info.get("title") or "未命名视频",
                "author": info.get("uploader"),
                "cover_url": info.get("thumbnail"),
                "duration": info.get("duration"),
                "platform": "douyin",
                "video_id": info.get("id"),
                "available_best": _best_format(info),
                "fallback": "iesdouyin",
                "ytdlp_error": str(ytdlp_exc)[:200],
            }
        if is_xiaohongshu_url(url):
            try:
                note = load_xiaohongshu_images(url)
            except Exception as image_exc:
                raise RuntimeError(
                    f"yt-dlp 解析失败，小红书页面也没有解析出图集：{image_exc}"
                ) from image_exc
            if note:
                return _image_parse_result(url, note, ytdlp_error=str(ytdlp_exc))
        raise


def _task_tmp_dir(task) -> Path:
    return getattr(task, "download_dir", None) or TMP_DIR


def _progress_hook(task, info: dict):
    cancel_event = _download_cancel_events.get(task.id)
    if getattr(task, "cancelled", False) or (cancel_event is not None and cancel_event.is_set()):
        raise DownloadCancelled("下载已取消")
    if info.get("status") == "downloading":
        total = info.get("total_bytes") or info.get("total_bytes_estimate") or 0
        done = info.get("downloaded_bytes") or 0
        if total:
            task.progress = min(done / total * 100, 99.0)


def _ytdlp_download(task, url: str, use_cookie: bool = True) -> tuple[Path, dict]:
    opts = {
        **_base_opts(url, use_cookie=use_cookie),
        "outtmpl": str(_task_tmp_dir(task) / "%(id)s.%(ext)s"),
        "format": "bv*[vcodec^=avc1]+ba/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "progress_hooks": [lambda item: _progress_hook(task, item)],
        "writethumbnail": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = Path(ydl.prepare_filename(info))
        if downloaded.suffix != ".mp4" and downloaded.with_suffix(".mp4").exists():
            downloaded = downloaded.with_suffix(".mp4")
    return downloaded, info


def _download_cancelled(task) -> bool:
    if getattr(task, "cancelled", False):
        return True
    event = _download_cancel_events.get(getattr(task, "id", None))
    return event is not None and event.is_set()


def _as_image_files(info: dict) -> list[Path]:
    return [Path(path) for path in (info.get("image_files") or [])]


def _douyin_fallback_download(task, url: str) -> tuple[Path | list[Path], dict]:
    task.log("使用 iesdouyin 分享页备用下载（无需 Cookie）…")
    out = _task_tmp_dir(task) / "douyin_fallback.mp4"
    try:
        info = download_douyin(
            url,
            out,
            should_cancel=lambda: _download_cancelled(task),
            max_bytes=MAX_UPLOAD_MB * 1024 * 1024,
            deadline_s=float(DOWNLOAD_TIMEOUT),
        )
    except FallbackCancelled as exc:
        out.unlink(missing_ok=True)
        raise DownloadCancelled("下载已取消") from exc
    if _download_cancelled(task):
        out.unlink(missing_ok=True)
        raise DownloadCancelled("下载已取消")
    if info.get("media_kind") == "images":
        files = _as_image_files(info)
        out.unlink(missing_ok=True)
        if not files:
            raise RuntimeError("该抖音链接是图文作品，但没有下载到原图")
        task.log(f"这是图文作品，已下载 {len(files)} 张原图")
        task.progress = max(float(getattr(task, "progress", 0) or 0), 90.0)
        return files, info
    # 用 aweme id 作为文件名，与 yt-dlp 下游命名习惯一致
    vid = re.sub(r"[^A-Za-z0-9_-]", "_", str(info.get("id") or "douyin"))[:64]
    named = out.with_name(f"{vid}.mp4")
    if named != out:
        if named.exists():
            named.unlink()
        out.replace(named)
        out = named
    task.progress = max(float(getattr(task, "progress", 0) or 0), 90.0)
    return out, info


def _discard_download(downloaded: Path | list[Path] | None) -> None:
    if isinstance(downloaded, list):
        for path in downloaded:
            Path(path).unlink(missing_ok=True)
        return
    if isinstance(downloaded, Path):
        downloaded.unlink(missing_ok=True)


def _note_images_instead(task, url: str, downloaded: Path, info: dict) -> tuple[list[Path], dict] | None:
    """图文链接即使被 yt-dlp 下成占位视频，也改存原图。普通视频链接不走这里。"""
    if not is_douyin_url(url) or not _douyin_note_url(url, info.get("webpage_url"), info.get("original_url")):
        return None
    try:
        files, image_info = _douyin_fallback_download(task, url)
    except DownloadCancelled:
        _discard_download(downloaded)
        raise
    except Exception as exc:
        task.log(f"图文原图备用解析失败，保留已下载的视频：{exc}")
        return None
    if image_info.get("media_kind") != "images":
        _discard_download(files if isinstance(files, Path) else None)
        return None
    downloaded.unlink(missing_ok=True)
    return files, image_info


def _prefer_originals(task, url: str, downloaded: Path, info: dict) -> tuple[Path | list[Path], dict]:
    images = _note_images_instead(task, url, downloaded, info)
    if images is not None:
        return images
    return downloaded, info


def _xiaohongshu_image_download(task, url: str) -> tuple[list[Path], dict]:
    task.log("尝试下载小红书图文原图（匿名优先，Cookie 仅作可选重试）…")
    dest = _task_tmp_dir(task) / "xhs_images"
    try:
        info = download_xiaohongshu_images(
            url,
            dest,
            should_cancel=lambda: _download_cancelled(task),
            max_bytes=MAX_UPLOAD_MB * 1024 * 1024,
            deadline_s=float(DOWNLOAD_TIMEOUT),
        )
    except GalleryCancelled as exc:
        raise DownloadCancelled("下载已取消") from exc
    files = _as_image_files(info)
    if not files:
        raise RuntimeError("小红书图文作品没有下载到原图")
    task.log(f"这是图文作品，已下载 {len(files)} 张原图")
    task.progress = max(float(getattr(task, "progress", 0) or 0), 90.0)
    return files, info


def _download_with_fallback(task, url: str) -> tuple[Path | list[Path], dict]:
    """yt-dlp 匿名 → 本地 Cookie → 免 Cookie 图文/抖音备用链路。"""
    last_exc: Exception | None = None
    try:
        task.log("使用 yt-dlp 获取最高质量无水印可用源…")
        downloaded, info = _ytdlp_download(task, url, use_cookie=False)
        return _prefer_originals(task, url, downloaded, info)
    except DownloadCancelled:
        raise
    except Exception as exc:
        last_exc = exc
        cookiefile = _cookiefile_for(url)
        if cookiefile:
            task.log("匿名解析失败，使用本地 Cookie 重试 yt-dlp…")
            try:
                downloaded, info = _ytdlp_download(task, url)
                return _prefer_originals(task, url, downloaded, info)
            except DownloadCancelled:
                raise
            except Exception as cookie_exc:
                last_exc = cookie_exc

    if is_douyin_url(url):
        try:
            return _douyin_fallback_download(task, url)
        except DownloadCancelled:
            raise
        except Exception as fallback_exc:
            raise RuntimeError(
                f"yt-dlp 与 iesdouyin 备用下载均失败：{fallback_exc}（此前：{last_exc}）"
            ) from fallback_exc

    if is_xiaohongshu_url(url):
        try:
            return _xiaohongshu_image_download(task, url)
        except DownloadCancelled:
            raise
        except Exception as image_exc:
            raise RuntimeError(
                f"无法通过 yt-dlp 获取视频，小红书页面也没有可保存的图集：{image_exc}（此前：{last_exc}）"
            ) from image_exc

    raise RuntimeError(
        f"无法通过 yt-dlp 获取视频，建议下载原片后上传：{last_exc}"
    ) from last_exc


def _store_image_post(task, downloaded: list[Path], info: dict) -> dict:
    files = [Path(path) for path in downloaded if Path(path).is_file()]
    if not files:
        raise RuntimeError("图文作品没有保存到原图")
    gallery_id = re.sub(r"[^A-Za-z0-9_-]", "_", str(info.get("id") or task.id))[:64] or task.id
    platform = (info.get("extractor_key") or "").lower()
    records, query = store_originals(
        files,
        title=(info.get("title") or "").strip(),
        source_url=str(info.get("webpage_url") or ""),
        platform=platform,
        gallery_id=gallery_id,
    )
    public = {key: value for key, value in info.items() if key != "image_files" and not str(key).startswith("_")}
    public["media_kind"] = "images"
    public["images"] = records
    public["gallery_query"] = query
    public["image_count"] = len(records)
    return public


def _do_download(task, text: str) -> tuple[Path | None, dict]:
    url = extract_url(text)
    ensure_dirs()
    with tempfile.TemporaryDirectory(prefix=f"download-{task.id}-", dir=TMP_DIR) as temp_dir:
        task.download_dir = Path(temp_dir)
        try:
            downloaded, info = _download_with_fallback(task, url)
            if info.get("media_kind") == "images" or isinstance(downloaded, list):
                files = downloaded if isinstance(downloaded, list) else []
                return None, _store_image_post(task, files, info)
            title = (info.get("title") or "未命名视频").strip()
            vid = re.sub(r"[^A-Za-z0-9_-]", "_", str(info.get("id") or ""))[:64] or "video"
            safe_title = re.sub(r'[\\/:*?"<>|#\n\r]', "_", title)[:80].strip("_") or f"video_{vid}"
            final_path = VIDEOS_DIR / f"{safe_title}_{vid}.mp4"
            if final_path.exists():
                final_path = VIDEOS_DIR / f"{safe_title}_{vid}_{task.id}.mp4"
            final_path = final_path.resolve()
            if VIDEOS_DIR.resolve() not in final_path.parents:
                raise RuntimeError("非法的下载文件名（路径越界）")
            downloaded.replace(final_path)
            return final_path, info
        finally:
            task.download_dir = None


def _post_process(task, final_path: Path, info: dict) -> dict:
    if ensure_h264(final_path):
        task.log("原视频为 H.265，已转码为 H.264 以保证浏览器可播放")
    meta = probe_video(final_path)
    cover_name = final_path.stem + ".jpg"
    if make_cover(final_path, COVERS_DIR / cover_name):
        meta["cover"] = cover_name
    video = add_video(
        title=(info.get("title") or "未命名视频").strip(),
        filename=final_path.name,
        size=final_path.stat().st_size,
        duration=meta.get("duration") or info.get("duration"),
        width=meta.get("width") or info.get("width"),
        height=meta.get("height") or info.get("height"),
        cover=meta.get("cover"),
        source_platform=(info.get("extractor_key") or "").lower() or None,
        source_url=info.get("webpage_url") or None,
    )
    quality = assess_source(final_path)
    quality["available_best"] = _best_format(info)
    update_video(
        video["id"],
        quality_score=quality["score"],
        quality_json=json.dumps(quality, ensure_ascii=False),
    )
    start_analysis(video["id"])
    return {**video, "quality_score": quality["score"], "quality": quality}


async def run_download(task, text: str):
    async with download_lock:
        task.status = "running"
        task.log("开始解析链接…")
        cancel_event = threading.Event()
        _download_cancel_events[task.id] = cancel_event
        download_future = asyncio.create_task(asyncio.to_thread(_do_download, task, text))
        try:
            try:
                final_path, info = await asyncio.wait_for(asyncio.shield(download_future), timeout=DOWNLOAD_TIMEOUT)
            except asyncio.TimeoutError:
                cancel_event.set()
                task.cancelled = True
                try:
                    await download_future
                except Exception:
                    pass
                task.error = {
                    "code": "download_timeout",
                    "message": f"下载超过 {DOWNLOAD_TIMEOUT} 秒仍未完成。",
                    "action": "稍后重试，或下载原片后到素材库上传。",
                }
                task.log(task.error["message"])
                task.finish("failed")
                return
            except asyncio.CancelledError:
                cancel_event.set()
                task.cancelled = True
                try:
                    await download_future
                except Exception:
                    pass
                raise
            except Exception as exc:
                problem = explain_download_error(str(exc))
                task.error = problem
                task.result = {"error": problem["message"]}
                task.log(f"下载失败: {problem['message']} {problem['action']}")
                task.finish("failed")
                return
        finally:
            _download_cancel_events.pop(task.id, None)
        if info.get("media_kind") == "images":
            images = info.get("images") or []
            task.progress = 100.0
            task.result = {
                "media_kind": "images",
                "images": images,
                "gallery": {
                    "title": info.get("title") or "图集",
                    "count": len(images),
                    "platform": (info.get("extractor_key") or "").lower() or None,
                    "source_url": info.get("webpage_url"),
                    "query": info.get("gallery_query") or "",
                },
            }
            task.log(f"已保存 {len(images)} 张原图到图片素材库")
            task.finish("success")
            return
        try:
            task.log("下载完成，转码、质量分析与入库处理中…")
            video = await asyncio.to_thread(_post_process, task, final_path, info)
            task.progress = 100.0
            task.result = {"video": video}
            task.log(f"下载完成: {video['title']}")
            task.finish("success")
        except Exception as exc:
            task.error = {
                "code": "download_postprocess",
                "message": f"文件已下载，但入库失败：{exc}",
                "action": "查看任务日志。若文件损坏，重新下载或改用本地上传。",
            }
            task.log(f"后处理失败: {exc}")
            task.result = {"error": task.error["message"]}
            task.finish("failed")


download_lock = asyncio.Lock()


async def run_download_queued(task, text: str):
    if download_lock.locked():
        task.log("排队等待中…")
    await run_download(task, text)
