"""把视频片段转成 GIF 或动态 WebP。

GIF 走 palettegen + paletteuse 两步，保证调色板画质。
WebP 用 libwebp_anim。机器上的 FFmpeg 没编进 libwebp 时直接拒绝，并提示改用 GIF。
"""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

from engine.errors import EngineError
from engine.media import even, ffmpeg, probe, require_ffmpeg
from app.jobs import cancelled, create, get, report, submit, update
from app.services.media import ensure_free_mb, render_semaphore
from app.services.videos import data_dir, fetch, resolve_media

KIND = "motion"
MAX_SECONDS = 30.0
MIN_SECONDS = 0.2
ALLOWED_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".flv", ".avi"}

_webp_lock = threading.Lock()
_webp_cached: bool | None = None


def motion_root() -> Path:
    path = data_dir() / "exports" / "motion"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_id(value: object, *, what: str = "编号") -> str:
    """只接受短的字母数字 id，拒绝路径和 ..。"""
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 40
        or not text.isalnum()
        or ".." in text
        or "/" in text
        or "\\" in text
    ):
        raise EngineError(
            f"{what}不合法",
            code="id",
            action="从页面选择素材或任务，不要手写路径。",
            status=400,
        )
    return text


def job_dir(job_id: str) -> Path:
    token = safe_id(job_id, what="任务编号")
    root = motion_root().resolve()
    path = (root / token).resolve()
    if path.parent != root:
        raise EngineError("拒绝这个路径", code="path", action="任务编号不能包含路径。", status=400)
    return path


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise EngineError("拒绝这个路径", code="path", action="文件必须留在导出目录里。", status=400) from exc
    return resolved


def webp_supported() -> bool:
    global _webp_cached
    with _webp_lock:
        if _webp_cached is None:
            require_ffmpeg()
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            _webp_cached = "libwebp_anim" in (proc.stdout or "")
        return bool(_webp_cached)


def _webp_error() -> EngineError:
    return EngineError(
        "这台机器的 FFmpeg 没有 libwebp，不能导出动态 WebP。",
        code="webp_unsupported",
        action="改用 GIF 再生成。安装带 libwebp 的 FFmpeg 后即可使用 WebP。",
        status=400,
    )


def _disk() -> None:
    try:
        ensure_free_mb(motion_root(), 512)
    except OSError as exc:
        raise EngineError(
            str(exc),
            code="disk",
            action="先清理临时文件或素材，腾出至少 512MB 再生成。",
            status=507,
        ) from exc


def _parse_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool):
        raise EngineError(f"{name}必须是整数", code="input", action=f"{name}请填 {low} 到 {high}。", status=400)
    if isinstance(value, float) and not value.is_integer():
        raise EngineError(f"{name}必须是整数", code="input", action=f"{name}请填 {low} 到 {high}。", status=400)
    if isinstance(value, str):
        text = value.strip()
        if not text or not (text.isdigit() or (text.startswith("-") and text[1:].isdigit())):
            raise EngineError(f"{name}必须是整数", code="input", action=f"{name}请填 {low} 到 {high}。", status=400)
        number = int(text)
    else:
        try:
            number = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise EngineError(
                f"{name}必须是整数",
                code="input",
                action=f"{name}请填 {low} 到 {high}。",
                status=400,
            ) from exc
    if number < low or number > high:
        raise EngineError(f"{name}超出范围", code="input", action=f"{name}请填 {low} 到 {high}。", status=400)
    return number


def _parse_optional_float(value: object, name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise EngineError(f"{name}必须是数字", code="input", action="用秒数，例如 1.5。", status=400)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise EngineError(f"{name}必须是数字", code="input", action="用秒数，例如 1.5。", status=400) from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise EngineError(f"{name}必须是数字", code="input", action="用秒数，例如 1.5。", status=400)
    return number


def _parse_bool(value: object, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise EngineError("开关参数只能是开或关", code="input", action="用页面上的开关选择。", status=400)


def _parse_video_id(value: object) -> int | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if ".." in text or "/" in text or "\\" in text or not text.isdigit():
        raise EngineError(
            "素材编号不合法",
            code="id",
            action="从素材库选择视频。编号只能是数字，不能是路径。",
            status=400,
        )
    number = int(text)
    if number <= 0 or number > 2_000_000_000:
        raise EngineError("素材编号不合法", code="id", action="从素材库重新选择这条视频。", status=400)
    return number


def parse_options(raw: dict) -> dict:
    """把请求收成渲染参数。此时还不读视频文件。"""
    if not isinstance(raw, dict):
        raise EngineError("请求格式不对", code="input", action="用 JSON 提交参数。", status=400)
    fmt = str(raw.get("format") or "gif").strip().lower()
    if fmt not in {"gif", "webp"}:
        raise EngineError("格式只支持 gif 或 webp", code="format", action="在 GIF 和动态 WebP 里选一个。", status=400)
    if fmt == "webp" and not webp_supported():
        raise _webp_error()
    fps = _parse_int(raw.get("fps", 12), "帧率", 5, 30)
    width = even(_parse_int(raw.get("width", 480), "宽度", 16, 1920))
    if width < 16:
        width = 16
    colors = _parse_int(raw.get("colors", 256), "颜色数", 2, 256)
    quality = _parse_int(raw.get("quality", 75), "画质", 0, 100)
    loop = _parse_int(raw.get("loop", 0), "循环", -1, 65535)
    start = _parse_optional_float(raw.get("start"), "开始时间")
    end = _parse_optional_float(raw.get("end"), "结束时间")
    duration = _parse_optional_float(raw.get("duration"), "时长")
    if start is None:
        start = 0.0
    if start < 0:
        raise EngineError("开始时间不能小于 0", code="range", action="把开始时间改成 0 或更晚。", status=400)
    if end is not None and duration is not None:
        raise EngineError(
            "结束时间和时长只能填一个",
            code="range",
            action="用起止时间，或用开始时间加时长。",
            status=400,
        )
    if end is not None:
        if end <= start:
            raise EngineError("结束时间必须晚于开始时间", code="range", action="把结束时间调到开始时间之后。", status=400)
        length = end - start
    elif duration is not None:
        if duration <= 0:
            raise EngineError("时长必须大于 0", code="range", action="至少保留 0.2 秒。", status=400)
        length = duration
    else:
        length = None
    if length is not None and length - MAX_SECONDS > 1e-3:
        raise EngineError("片段最长 30 秒", code="range", action="缩短起止时间。更长的片段请先截成多段。", status=400)
    if length is not None and length < MIN_SECONDS:
        raise EngineError("片段至少 0.2 秒", code="range", action="把结束时间再往后一点。", status=400)
    return {
        "video_id": _parse_video_id(raw.get("video_id")),
        "format": fmt,
        "fps": fps,
        "width": width,
        "colors": colors,
        "quality": quality,
        "loop": loop,
        "dither": _parse_bool(raw.get("dither", True), True),
        "ingest": _parse_bool(raw.get("ingest", False), False),
        "start": round(start, 3),
        # 统一存时长，重试时不会再同时带上 end 和 duration。
        "duration": None if length is None else round(length, 3),
    }


def clip_window(spec: dict, source_duration: float) -> tuple[float, float]:
    start = float(spec["start"])
    if source_duration <= 0:
        raise EngineError("读不到视频时长", code="probe", action="换一条能正常播放的视频。", status=422)
    if start >= source_duration:
        raise EngineError("开始时间已经超过视频结尾", code="range", action="把开始时间往前移。", status=400)
    if spec.get("duration") is None:
        length = min(MAX_SECONDS, source_duration - start)
    else:
        length = float(spec["duration"])
        if start + length > source_duration + 0.05:
            raise EngineError(
                f"片段超出视频时长（{source_duration:.1f} 秒）",
                code="range",
                action="把结束时间收进视频长度里。",
                status=400,
            )
        length = min(length, max(0.0, source_duration - start))
    if length - MAX_SECONDS > 1e-3:
        raise EngineError("片段最长 30 秒", code="range", action="缩短起止时间。更长的片段请先截成多段。", status=400)
    if length < MIN_SECONDS:
        raise EngineError("片段至少 0.2 秒", code="range", action="把结束时间再往后一点。", status=400)
    return round(start, 3), round(length, 3)


def _scale(width: int, fps: int) -> str:
    return f"fps={int(fps)},scale={int(width)}:-2:flags=lanczos"


def _should_cancel(job_id: str):
    return lambda: cancelled(job_id)


def _gif_loop(loop: int) -> str:
    # GIF muxer：-1 不循环，0 无限循环。
    return str(int(loop))


def _webp_loop(loop: int) -> str:
    # WebP muxer 没有 -1。0 是无限循环，1 表示只播放一轮。
    if int(loop) < 0:
        return "1"
    return str(int(loop))


def _render_gif(source: Path, dest: Path, spec: dict, start: float, length: float, job_id: str) -> None:
    palette = dest.with_name("palette.png")
    vf = _scale(spec["width"], spec["fps"])
    report(job_id, 28, "palette", "正在生成调色板")
    ffmpeg(
        [
            "-ss", f"{start:.3f}",
            "-t", f"{length:.3f}",
            "-i", str(source),
            "-vf", f"{vf},palettegen=max_colors={int(spec['colors'])}:stats_mode=diff",
            str(palette),
        ],
        job_id=job_id,
        should_cancel=_should_cancel(job_id),
        timeout=180,
    )
    if not palette.is_file() or palette.stat().st_size <= 0:
        raise EngineError("调色板没有生成", code="ffmpeg", action="换一段画面更明显的片段再试。", status=500)
    report(job_id, 62, "encode", "正在套用调色板生成 GIF")
    dither = "sierra2_4a" if spec.get("dither", True) else "none"
    try:
        ffmpeg(
            [
                "-ss", f"{start:.3f}",
                "-t", f"{length:.3f}",
                "-i", str(source),
                "-i", str(palette),
                "-lavfi", f"{vf}[x];[x][1:v]paletteuse=dither={dither}:diff_mode=rectangle",
                "-loop", _gif_loop(int(spec["loop"])),
                str(dest),
            ],
            job_id=job_id,
            should_cancel=_should_cancel(job_id),
            timeout=180,
        )
    finally:
        palette.unlink(missing_ok=True)


def _render_webp(source: Path, dest: Path, spec: dict, start: float, length: float, job_id: str) -> None:
    if not webp_supported():
        raise _webp_error()
    report(job_id, 40, "encode", "正在编码动态 WebP")
    ffmpeg(
        [
            "-ss", f"{start:.3f}",
            "-t", f"{length:.3f}",
            "-i", str(source),
            "-vf", _scale(spec["width"], spec["fps"]),
            "-an",
            "-c:v", "libwebp_anim",
            "-lossless", "0",
            "-quality", str(int(spec["quality"])),
            "-loop", _webp_loop(int(spec["loop"])),
            str(dest),
        ],
        job_id=job_id,
        should_cancel=_should_cancel(job_id),
        timeout=180,
    )


def render_motion(source: Path, dest: Path, spec: dict, *, job_id: str) -> dict:
    if not source.is_file():
        raise EngineError(
            "视频文件不在磁盘上",
            code="missing_file",
            action="回素材库确认原片还在，或重新上传。",
            status=404,
        )
    _disk()
    meta = probe(source)
    start, length = clip_window(spec, float(meta["duration"]))
    report(job_id, 12, "probe", "已读取片段")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if cancelled(job_id):
        raise EngineError("任务已取消", code="cancelled", action="可以重新生成。", status=409)
    report(job_id, 18, "queue", "等待渲染空位")
    with render_semaphore:
        _disk()
        if cancelled(job_id):
            raise EngineError("任务已取消", code="cancelled", action="可以重新生成。", status=409)
        if spec["format"] == "gif":
            _render_gif(source, dest, spec, start, length, job_id)
        else:
            _render_webp(source, dest, spec, start, length, job_id)
    if not dest.is_file() or dest.stat().st_size <= 0:
        raise EngineError("没有写出动图", code="ffmpeg", action="缩短片段或降低宽度后再试。", status=500)
    report(job_id, 96, "write", "动图已写出")
    return {"start": start, "duration": length, "size": dest.stat().st_size}


def _source_for(spec: dict, work: Path) -> tuple[Path, str]:
    video_id = spec.get("video_id")
    if video_id:
        video = fetch(int(video_id))
        return resolve_media(video), str(spec.get("title") or video.get("title") or "视频")
    name = str(spec.get("upload_name") or "")
    if name != Path(name).name or not name.startswith("source."):
        raise EngineError("上传文件名不合法", code="path", action="重新上传视频。", status=400)
    return _inside(work / name, work), str(spec.get("title") or "上传视频")


def _import_result(path: Path, title: str) -> dict:
    from app.services.ai_images import AIImageError, import_local

    try:
        return import_local(path, title or "动图", "视频转动图")
    except AIImageError as exc:
        raise EngineError(
            str(exc),
            code="library",
            action="动图已经生成，可以下载，或稍后再放入图片库。",
            status=500,
        ) from exc


def _run(job_id: str) -> None:
    job = get(job_id)
    spec = dict(job.get("input") or {})
    work = job_dir(job_id)
    source, title = _source_for(spec, work)
    ext = "gif" if spec.get("format") == "gif" else "webp"
    dest = work / f"motion.{ext}"
    info = render_motion(source, dest, spec, job_id=job_id)
    result = {
        "output_path": str(dest.resolve()),
        "filename": dest.name,
        "format": spec.get("format"),
        "size": info["size"],
        "start": info["start"],
        "duration": info["duration"],
        "width": spec.get("width"),
        "fps": spec.get("fps"),
        "content_type": "image/gif" if ext == "gif" else "image/webp",
        "download_url": f"/api/motion/{job_id}/file",
        "preview_url": f"/api/motion/{job_id}/file",
    }
    if spec.get("ingest"):
        try:
            result["image"] = _import_result(dest, title)
        except EngineError as exc:
            result["ingest_error"] = exc.as_dict()
    update(
        job_id,
        status="success",
        progress=100,
        stage="done",
        label=f"转动图 · {title[:36]}",
        result=result,
        log="动图已生成",
    )


def _public(job: dict) -> dict:
    from app.services.job_bridge import job_as_task

    return job_as_task(job)


def start(raw: dict, *, source: Path | None = None, source_title: str = "") -> dict:
    options = parse_options(raw)
    video_id = options["video_id"]
    if video_id is not None:
        video = fetch(video_id)
        resolve_media(video)
        title = str(video.get("title") or source_title or "视频")[:80]
    else:
        if source is None or not Path(source).is_file():
            raise EngineError("没有视频", code="input", action="从素材库选一条，或上传一个视频文件。", status=400)
        media = Path(source)
        title = (source_title or media.stem or "上传视频")[:80]
    _disk()
    options["title"] = title
    label = f"转动图 · {title[:36]}"
    job = create(KIND, label, options)
    work = job_dir(job["id"])
    work.mkdir(parents=True, exist_ok=True)
    if video_id is None:
        suffix = media.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES:
            suffix = ".mp4"
        stored = work / f"source{suffix}"
        try:
            shutil.copy2(media, stored)
        except OSError as exc:
            update(
                job["id"],
                status="failed",
                stage="failed",
                label="失败",
                error={"code": "disk", "message": f"保存原片失败：{exc}", "action": "检查磁盘空间后重试。"},
            )
            raise EngineError(f"保存原片失败：{exc}", code="disk", action="检查磁盘空间后重试。", status=507) from exc
        options = {**options, "upload_name": stored.name}
        update(job["id"], input=options)
    saved = submit(get(job["id"]), _run)
    return _public(saved)


def output_file(job_id: str) -> tuple[Path, str, str]:
    token = safe_id(job_id, what="任务编号")
    job = get(token)
    if job.get("kind") != KIND:
        raise EngineError("这个任务没有动图", code="missing_file", action="打开对应的转动图任务。", status=404)
    if job.get("status") != "success":
        raise EngineError("动图还没完成", code="missing_file", action="等生成完成后再下载。", status=404)
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    raw = (result or {}).get("output_path")
    if not raw:
        raise EngineError("没有可下载的动图", code="missing_file", action="重新生成。", status=404)
    path = _inside(Path(str(raw)), job_dir(token))
    if path.name not in {"motion.gif", "motion.webp"}:
        raise EngineError("拒绝下载这个文件", code="path", action="只下载本次生成的动图。", status=403)
    if not path.is_file():
        raise EngineError("动图文件已不在磁盘上", code="missing_file", action="重新生成。", status=404)
    media_type = "image/gif" if path.suffix == ".gif" else "image/webp"
    return path, media_type, path.name


def ingest_job(job_id: str) -> dict:
    token = safe_id(job_id, what="任务编号")
    job = get(token)
    if job.get("kind") != KIND:
        raise EngineError("这不是转动图任务", code="kind", action="打开转动图页面再入库。", status=400)
    if job.get("status") != "success":
        raise EngineError("动图还没完成", code="busy", action="等生成成功后再放入图片库。", status=409)
    result = dict(job.get("result") or {})
    if isinstance(result.get("image"), dict) and result["image"].get("id"):
        return result["image"]
    path, _, _ = output_file(token)
    image = _import_result(path, str((job.get("input") or {}).get("title") or "动图"))
    result["image"] = image
    result.pop("ingest_error", None)
    update(token, result=result, log="已放入图片素材库")
    return image


def retry(job_id: str) -> dict:
    token = safe_id(job_id, what="任务编号")
    job = get(token)
    if job.get("kind") != KIND:
        raise EngineError("这个任务不能按转动图重试", code="retry", action="回到转动图页面重新发起。", status=400)
    if job.get("status") in {"pending", "running"}:
        raise EngineError("任务还在进行", code="busy", action="等它结束，或先取消。", status=409)
    spec = dict(job.get("input") or {})
    source = None
    if not spec.get("video_id"):
        work = job_dir(token)
        name = str(spec.get("upload_name") or "")
        if name != Path(name).name or not name.startswith("source."):
            raise EngineError("上传的原片已经不在", code="missing_file", action="重新上传这段视频。", status=404)
        source = _inside(work / name, work)
        if not source.is_file():
            raise EngineError("上传的原片已经不在", code="missing_file", action="重新上传这段视频。", status=404)
    return start(spec, source=source, source_title=str(spec.get("title") or ""))
