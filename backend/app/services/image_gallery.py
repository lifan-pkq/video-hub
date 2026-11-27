"""把图文作品的原图下载到临时目录，再复制进图片素材库。

原图按张保留。这里不生成幻灯片视频，也不把静图改名成 mp4。
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

from app.services.ai_images import AIImageError, import_downloaded, remove

MIN_IMAGE_BYTES = 32
_READ_TIMEOUT = 15


class GalleryCancelled(RuntimeError):
    """图集下载被取消。调用方转成下载取消。"""


def sniff_image_suffix(data: bytes, content_type: str = "") -> str | None:
    """只接受浏览器能直接打开的静图。HTML 或未知容器返回 None。"""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    head = data[:64].lstrip().lower()
    if head.startswith((b"<!doctype", b"<html", b"{", b"<?xml")):
        return None
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    return {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(ct)


def download_slides(
    slides: list[dict],
    dest_dir: Path,
    *,
    session: requests.Session,
    referer: str,
    should_cancel=None,
    max_bytes: int,
    deadline_s: float,
) -> list[Path]:
    """按原顺序保存每一张。同一张的多个地址依次尝试，直到得到一张真图。"""
    if not slides:
        raise RuntimeError("图文作品没有可下载的原图地址")
    dest_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + float(deadline_s)
    written: list[Path] = []
    total = 0
    try:
        for index, slide in enumerate(slides, start=1):
            if should_cancel and should_cancel():
                raise GalleryCancelled("下载已取消")
            urls = [url for url in (slide.get("urls") or []) if isinstance(url, str) and url.startswith("http")]
            if not urls:
                raise RuntimeError(f"图文作品第 {index} 张没有原图地址")
            path = _download_one(
                urls,
                dest_dir / f"{index:02d}",
                session=session,
                referer=referer,
                should_cancel=should_cancel,
                max_bytes=max_bytes - total,
                deadline=deadline,
                deadline_s=deadline_s,
                label=index,
            )
            total += path.stat().st_size
            written.append(path)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return written


def _download_one(
    urls: list[str],
    stem: Path,
    *,
    session: requests.Session,
    referer: str,
    should_cancel,
    max_bytes: int,
    deadline: float,
    deadline_s: float,
    label: int,
) -> Path:
    errors: list[str] = []
    for url in urls:
        if should_cancel and should_cancel():
            raise GalleryCancelled("下载已取消")
        if time.monotonic() > deadline:
            raise TimeoutError(f"timeout: 图文原图下载超过 {int(deadline_s)} 秒仍未完成")
        if max_bytes <= MIN_IMAGE_BYTES:
            raise RuntimeError("图文原图超过大小上限")
        try:
            return _write_url(url, stem, session=session, referer=referer, should_cancel=should_cancel,
                              max_bytes=max_bytes, deadline=deadline, deadline_s=deadline_s)
        except GalleryCancelled:
            raise
        except TimeoutError:
            raise
        except RuntimeError as exc:
            if "大小上限" in str(exc):
                raise
            errors.append(str(exc))
        except requests.RequestException as exc:
            errors.append(str(exc))
    detail = errors[-1] if errors else "没有可用地址"
    raise RuntimeError(f"图文作品第 {label} 张原图下载失败：{detail}")


def _write_url(
    url: str,
    stem: Path,
    *,
    session: requests.Session,
    referer: str,
    should_cancel,
    max_bytes: int,
    deadline: float,
    deadline_s: float,
) -> Path:
    partial: Path | None = None
    try:
        with session.get(
            url,
            timeout=(10, _READ_TIMEOUT),
            stream=True,
            allow_redirects=True,
            headers={"Referer": referer},
        ) as resp:
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type") or ""
            if "text/html" in content_type.lower():
                raise RuntimeError("返回了网页而不是图片")
            declared = resp.headers.get("Content-Length")
            if declared and str(declared).isdigit() and int(declared) > max_bytes:
                raise RuntimeError("图文原图超过大小上限")
            blob = bytearray()
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if should_cancel and should_cancel():
                    raise GalleryCancelled("下载已取消")
                if time.monotonic() > deadline:
                    raise TimeoutError(f"timeout: 图文原图下载超过 {int(deadline_s)} 秒仍未完成")
                if not chunk:
                    continue
                blob.extend(chunk)
                if len(blob) > max_bytes:
                    raise RuntimeError("图文原图超过大小上限")
        if len(blob) < MIN_IMAGE_BYTES:
            raise RuntimeError(f"图片过小（{len(blob)} bytes）")
        suffix = sniff_image_suffix(bytes(blob), content_type)
        if not suffix:
            raise RuntimeError("内容不是可查看的 JPEG、PNG、WebP 或 GIF")
        target = stem.with_suffix(suffix)
        partial = target
        target.write_bytes(blob)
        return target
    except Exception:
        if partial is not None:
            partial.unlink(missing_ok=True)
        raise


def store_originals(
    paths: list[Path],
    *,
    title: str,
    source_url: str,
    platform: str,
    gallery_id: str,
) -> tuple[list[dict], str]:
    """复制进图片素材库。标题带图集编号，方便在图片库里搜到同一组。"""
    if not paths:
        raise RuntimeError("图文作品没有保存到原图")
    query = f"图集 {gallery_id}".strip()
    short = " ".join((title or "").split())
    records: list[dict] = []
    total = len(paths)
    try:
        for index, path in enumerate(paths, start=1):
            label = f"{query} · {index}/{total}"
            if short:
                label = f"{query} · {short[:24]} · {index}/{total}"
            prompt = f"图集原图 {index}/{total}\n来源：{source_url}\n平台：{platform or 'unknown'}"
            records.append(import_downloaded(path, title=label[:80], prompt=prompt[:1000]))
    except Exception:
        for record in records:
            try:
                remove(int(record["id"]))
            except (OSError, AIImageError, KeyError, TypeError, ValueError):
                pass
        raise
    return records, query
