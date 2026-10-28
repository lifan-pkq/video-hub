"""媒体工具：ffprobe 探测视频信息、ffmpeg 生成封面/拼接渲染、Range 视频流。两者不存在时静默降级。"""
import json
import os
import re
import selectors
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from fastapi import Response
from fastapi.responses import FileResponse, StreamingResponse

# 渲染/AI 分析并发上限（2 核服务器，防多请求同时跑 ffmpeg/whisper 打爆内存）
render_semaphore = threading.Semaphore(2)


def probe_video(path: Path) -> dict:
    """返回 {duration, width, height, has_audio}，失败返回空 dict。"""
    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,codec_name,width,height",
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout or "{}")
        info: dict = {}
        if data.get("format", {}).get("duration"):
            info["duration"] = round(float(data["format"]["duration"]), 1)
        streams = data.get("streams", [])
        for s in streams:
            if s.get("codec_type") == "video":
                info["width"] = s.get("width")
                info["height"] = s.get("height")
                break
        info["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
        for s in streams:
            if s.get("codec_type") == "video":
                info["codec"] = s.get("codec_name")
                break
        return info
    except Exception:
        return {}


def ensure_h264(path: Path) -> bool:
    """若视频不是 H.264（浏览器无法解码，如抖音 bytevc1/HEVC），转码为 H.264。

    原地替换文件（音频直接 copy）。已是 H.264 或无 ffmpeg 时不动，返回是否做了转码。
    """
    codec = probe_video(path).get("codec")
    if codec in (None, "h264") or not shutil.which("ffmpeg"):
        return False
    tmp = path.with_suffix(".h264tmp.mp4")
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path),
         "-c:v", "libx264", "-crf", "20", "-preset", "veryfast",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(tmp)],
        capture_output=True, timeout=600,
    )
    if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"转码 H.264 失败（原编码 {codec}）：{r.stderr.decode('utf-8', errors='replace')[-200:]}")
    tmp.replace(path)
    return True


def probe_has_audio(path: Path) -> bool:
    """是否有音轨（探测失败时按有音轨处理，避免无声误判）。"""
    if not shutil.which("ffprobe"):
        return True
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        return bool(out.stdout.strip())
    except Exception:
        return True


def concat_clips(clips: list[dict], out_path: Path, on_progress=None):
    """拼接渲染入口：信号量限并发，多请求排队执行。"""
    with render_semaphore:
        _concat_clips(clips, out_path, on_progress)


def _concat_clips(clips: list[dict], out_path: Path, on_progress=None):
    """按顺序裁剪 + 拼接多个片段为单个 mp4。

    clips: [{"path": Path, "start": float, "end": float}]
    画布取首段分辨率（上限 1920），其余片段等比缩放居中加黑边；
    无音轨的片段补静音轨。on_progress(percent) 回调渲染进度。
    失败抛 RuntimeError。
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("服务器未安装 ffmpeg")
    n = len(clips)
    first = probe_video(clips[0]["path"])
    fw, fh = first.get("width") or 1080, first.get("height") or 1920
    scale = min(1.0, 1920 / max(fw, fh, 1))
    w, h = max(2, int(fw * scale) // 2 * 2), max(2, int(fh * scale) // 2 * 2)

    cmd = ["ffmpeg", "-y"]
    filters: list[str] = []
    total = 0.0
    for i, c in enumerate(clips):
        start, end = float(c["start"]), float(c["end"])
        dur = end - start
        total += dur
        cmd += ["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(c["path"])]
        filters.append(
            f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p,setsar=1[v{i}]"
        )
        if probe_has_audio(c["path"]):
            filters.append(f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[a{i}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.3f}[a{i}]")
    filters.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]")

    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats", "-v", "warning",
        str(out_path),
    ]
    deadline = time.time() + 1800
    proc = None
    try:
        with tempfile.TemporaryFile() as stderr_file:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=stderr_file)
            assert proc.stdout is not None
            pending = b""
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        proc.kill()
                        proc.wait()
                        raise RuntimeError("渲染超时（30 分钟）")
                    if not selector.select(timeout=min(1.0, remaining)):
                        continue
                    chunk = os.read(proc.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    pending += chunk
                    while b"\n" in pending:
                        raw, pending = pending.split(b"\n", 1)
                        line = raw.decode("utf-8", errors="replace")
                        if line.startswith("out_time_ms=") and on_progress and total > 0:
                            try:
                                us = int(line.split("=", 1)[1].strip())
                                on_progress(min(99.0, us / 1e6 / total * 100))
                            except ValueError:
                                pass
            try:
                code = proc.wait(timeout=max(0.001, deadline - time.time()))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                raise RuntimeError("渲染超时（30 分钟）")
            proc.stdout.close()
            stderr_file.seek(0)
            err = stderr_file.read().decode("utf-8", errors="replace")
        if code != 0 or not out_path.exists() or out_path.stat().st_size == 0:
            raise RuntimeError("渲染失败：" + err[-300:])
    except BaseException:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc is not None and proc.stdout is not None:
            proc.stdout.close()
        out_path.unlink(missing_ok=True)
        raise
    if on_progress:
        on_progress(100.0)


def make_cover(video_path: Path, cover_path: Path, seek: float = 1.0) -> bool:
    """截取视频第 seek 秒作为封面 jpg。"""
    if not shutil.which("ffmpeg"):
        return False
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(seek), "-i", str(video_path),
             "-frames:v", "1", "-q:v", "3", str(cover_path)],
            capture_output=True, timeout=120,
        )
        return cover_path.exists() and cover_path.stat().st_size > 0
    except Exception:
        return False


def range_stream(path: Path, range_header: str | None):
    """支持 Range 的视频流响应（拖动进度条/边录边看都靠它）。"""
    file_size = path.stat().st_size
    if not range_header:
        return FileResponse(path, media_type="video/mp4")
    m = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
    if not m or file_size == 0 or (not m.group(1) and not m.group(2)):
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    if not m.group(1):
        suffix_length = int(m.group(2))
        if suffix_length == 0:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
        start = max(0, file_size - suffix_length)
        end = file_size - 1
    else:
        start = int(m.group(1))
        end = min(int(m.group(2)), file_size - 1) if m.group(2) else file_size - 1
    if start >= file_size or start > end:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{file_size}"})
    length = end - start + 1

    def iterfile():
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(
        iterfile(), status_code=206, media_type="video/mp4",
        headers={
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        },
    )
