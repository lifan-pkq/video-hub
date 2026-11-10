"""把一条高光渲染成竖屏成片：主体裁切、烧录字幕、抽封面。一次只处理一条，不把整段视频读进内存。"""

from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from engine.errors import EngineError
from engine.media import ffmpeg, filter_path, probe
from app.services.douyin_plan import build_ass, sample_times, skin_subject_box, vertical_filter
from app.services.media import render_semaphore

_FONT_CANDIDATES = (
    ("WenQuanYi Micro Hei", Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")),
    ("Droid Sans Fallback", Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")),
    ("Noto Sans CJK SC", Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")),
    ("Noto Sans CJK SC", Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf")),
)

_font_cache: tuple[str, Path | None] | None = None


def caption_font() -> tuple[str, Path | None]:
    global _font_cache
    if _font_cache is not None:
        return _font_cache
    for name, path in _FONT_CANDIDATES:
        if path.is_file():
            _font_cache = (name, path)
            return _font_cache
    _font_cache = ("Sans", None)
    return _font_cache


def reset_font_cache() -> None:
    global _font_cache
    _font_cache = None


def sample_frame_rgb(source: Path, timestamp: float, width: int = 96, height: int = 54) -> bytes | None:
    """抽一帧缩小的 RGB。失败返回 None，裁切会退回居中。"""
    if not shutil.which("ffmpeg"):
        return None
    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{max(0.0, timestamp):.3f}", "-i", str(source),
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-",
            ],
            capture_output=True,
            timeout=40,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    needed = width * height * 3
    if proc.returncode != 0 or len(proc.stdout) < needed:
        return None
    return proc.stdout[:needed]


def sample_skin_boxes(source: Path, start: float, end: float, segments: list[dict]) -> list[dict]:
    samples = []
    for moment in sample_times(start, end, segments):
        frame = sample_frame_rgb(source, moment)
        if not frame:
            continue
        box = skin_subject_box(frame, 96, 54)
        if box:
            samples.append({"time": moment, "box": box})
    return samples


def _encode(
    source: Path,
    dest: Path,
    *,
    start: float,
    duration: float,
    graph: str,
    has_audio: bool,
    threads: int,
    job_id: str | None,
    should_cancel,
) -> None:
    preroll = min(2.0, max(0.0, start))
    seek = max(0.0, start - preroll)
    complex_graph = ";" in graph
    args = [
        "-ss", f"{seek:.3f}",
        "-i", str(source),
        "-ss", f"{preroll:.3f}",
        "-t", f"{max(0.3, duration):.3f}",
    ]
    if complex_graph:
        args.extend(["-filter_complex", graph])
        if "[vout]" in graph:
            args.extend(["-map", "[vout]"])
        if has_audio:
            args.extend(["-map", "0:a:0"])
    else:
        args.extend(["-vf", graph])
    args.extend([
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-threads", str(max(1, threads)),
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ])
    if has_audio:
        args.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
    else:
        args.append("-an")
    args.append(str(dest))
    with render_semaphore:
        ffmpeg(args, timeout=900, job_id=job_id, should_cancel=should_cancel)


def render_short(
    source: Path,
    dest: Path,
    *,
    start: float,
    end: float,
    src_w: int,
    src_h: int,
    has_audio: bool,
    track: list[dict],
    captions: list[dict],
    out_w: int,
    out_h: int,
    threads: int = 1,
    layout: str = "interview",
    job_id: str | None = None,
    should_cancel=None,
) -> dict:
    """裁成竖屏并尽量烧上字幕。字幕滤镜失败时保留无字幕成片，由调用方记警告。

    layout=interview 跟着说话人裁切。layout=podcast 把完整画面居中放进 9:16，
    背景用模糊铺满，不再跟说话人裁切。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.3, float(end) - float(start))
    font_name, font_path = caption_font()
    ass_path = dest.parent / "captions.ass"
    burned = False
    if captions:
        ass_path.write_text(build_ass(captions, out_w, out_h, font_name), encoding="utf-8")

    portrait = "podcast" if layout == "podcast" else "interview"

    def graph_for(points: list[dict], with_subs: bool) -> tuple[str, str]:
        video_filter, mode = vertical_filter(src_w, src_h, points, out_w, out_h, portrait)
        if with_subs and captions and ass_path.is_file():
            fonts = f":fontsdir='{filter_path(font_path.parent)}'" if font_path else ""
            video_filter += f",subtitles='{filter_path(ass_path)}'{fonts}"
        if mode == "podcast":
            video_filter += "[vout]"
        return video_filter, mode

    attempts: list[list[dict]] = [[]] if portrait == "podcast" else [track]
    if portrait != "podcast":
        if len(track) > 1:
            attempts.append([track[len(track) // 2]])
        if track:
            attempts.append([])
    last_error: EngineError | None = None
    mode = "center"
    partial = dest.with_suffix(".part.mp4")
    for points in attempts:
        for with_subs in ((True, False) if captions else (False,)):
            video_filter, mode = graph_for(points, with_subs)
            try:
                _encode(
                    source, partial,
                    start=start, duration=duration, graph=video_filter, has_audio=has_audio,
                    threads=threads, job_id=job_id, should_cancel=should_cancel,
                )
            except EngineError as exc:
                if exc.code == "cancelled":
                    partial.unlink(missing_ok=True)
                    raise
                last_error = exc
                partial.unlink(missing_ok=True)
                continue
            if not partial.is_file() or partial.stat().st_size < 1024:
                partial.unlink(missing_ok=True)
                last_error = EngineError("渲染结果为空", code="ffmpeg", action="确认原片能播放，然后重试。", status=500)
                continue
            partial.replace(dest)
            burned = with_subs
            return {"framing": mode, "captions_burned": burned, "width": out_w, "height": out_h}
    if last_error:
        raise last_error
    raise EngineError("没有生成竖屏成片", code="ffmpeg", action="确认 ffmpeg 可用后重试。", status=500)


def write_cover(video: Path, dest: Path, *, job_id: str | None = None, should_cancel=None) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with render_semaphore:
            ffmpeg(
                ["-ss", "0.8", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(dest)],
                timeout=60,
                job_id=job_id,
                should_cancel=should_cancel,
            )
    except EngineError:
        return False
    return dest.is_file() and dest.stat().st_size > 0


def write_copy_files(folder: Path, clip: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    payload = {
        "title": clip.get("title") or "",
        "hashtags": list(clip.get("hashtags") or []),
        "description": clip.get("description") or "",
        "hook": clip.get("hook") or "",
        "start": clip.get("start"),
        "end": clip.get("end"),
    }
    (folder / "copy.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tags = " ".join(payload["hashtags"])
    text = f"标题：{payload['title']}\n话题：{tags}\n简介：{payload['description']}\n"
    if payload["hook"]:
        text += f"钩子：{payload['hook']}\n"
    (folder / "copy.txt").write_text(text, encoding="utf-8")


def build_clip_zip(folder: Path) -> bool:
    """单条发布包：视频、封面、文案。缺视频就跳过，不让整次出片失败。"""
    video = folder / "video.mp4"
    if not video.is_file():
        return False
    dest = folder / "kit.zip"
    temporary = folder / "kit.part.zip"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            for name in ("video.mp4", "cover.jpg", "copy.txt"):
                path = folder / name
                if path.is_file():
                    archive.write(path, name)
        temporary.replace(dest)
    except OSError:
        temporary.unlink(missing_ok=True)
        return False
    return dest.is_file() and dest.stat().st_size > 0


def build_pack_zip(root: Path, shorts: list[dict]) -> Path:
    """整包：每条成片一个目录，里面是视频、封面和文案。视频已压缩，zip 不再二次压缩。"""
    manifest = {
        "shorts": [
            {
                "index": item.get("index"),
                "title": item.get("title"),
                "hashtags": item.get("hashtags") or [],
                "description": item.get("description") or "",
                "hook": item.get("hook") or "",
                "start": item.get("start"),
                "end": item.get("end"),
                "framing": item.get("framing"),
                "captions_burned": item.get("captions_burned"),
            }
            for item in shorts
        ]
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    dest = root / "pack.zip"
    temporary = root / "pack.part.zip"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(root / "manifest.json", "manifest.json")
        for item in shorts:
            index = int(item.get("index") or 0)
            folder_name = f"{index:02d}"
            folder = root / folder_name
            for name in ("video.mp4", "cover.jpg", "copy.txt", "copy.json"):
                path = folder / name
                if path.is_file():
                    archive.write(path, f"{folder_name}/{name}")
    temporary.replace(dest)
    return dest


def probe_source(path: Path) -> dict:
    meta = probe(path)
    if int(meta.get("width") or 0) <= 0 or int(meta.get("height") or 0) <= 0:
        raise EngineError(
            "读不到原片画面尺寸",
            code="probe",
            action="用播放器确认原片能打开。",
            status=422,
        )
    return meta
