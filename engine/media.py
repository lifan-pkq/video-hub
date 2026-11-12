"""ffmpeg / ffprobe helpers. One place talks to the binaries."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

from engine.errors import EngineError

_PROCS: dict[str, subprocess.Popen] = {}
_LOCK = threading.Lock()


def require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise EngineError(
            "未找到 ffmpeg",
            code="ffmpeg_missing",
            action="在这台机器安装 ffmpeg，并确认它在 PATH 里。",
            status=500,
        )
    return path


def require_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise EngineError(
            "未找到 ffprobe",
            code="ffprobe_missing",
            action="安装与 ffmpeg 配套的 ffprobe。",
            status=500,
        )
    return path


def cancel_processes(job_id: str) -> None:
    with _LOCK:
        proc = _PROCS.get(job_id)
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.kill()


def run_cmd(
    args: list[str],
    *,
    timeout: int = 900,
    job_id: str | None = None,
    should_cancel=None,
) -> tuple[int, str]:
    """Run a command, drain stderr, and allow cooperative cancel."""
    proc = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if job_id:
        with _LOCK:
            _PROCS[job_id] = proc
    chunks: list[str] = []

    def _read() -> None:
        assert proc.stderr is not None
        try:
            data = proc.stderr.read()
            chunks.append(data.decode("utf-8", errors="replace"))
        finally:
            proc.stderr.close()

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    started = time.monotonic()
    try:
        while proc.poll() is None:
            if should_cancel and should_cancel():
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                reader.join(timeout=2)
                raise EngineError("任务已取消", code="cancelled", action="可以重新开始这一步。", status=409)
            if time.monotonic() - started > timeout:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                reader.join(timeout=2)
                raise EngineError(
                    f"命令超时（{timeout}s）：{Path(args[0]).name}",
                    code="timeout",
                    action="缩短素材，或稍后重试。",
                    status=504,
                )
            time.sleep(0.25)
        reader.join(timeout=3)
        return proc.returncode or 0, "".join(chunks)
    finally:
        if job_id:
            with _LOCK:
                _PROCS.pop(job_id, None)


def ffmpeg(
    args: list[str],
    *,
    timeout: int = 900,
    job_id: str | None = None,
    should_cancel=None,
    loglevel: str = "error",
) -> str:
    require_ffmpeg()
    code, err = run_cmd(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", loglevel, *args],
        timeout=timeout,
        job_id=job_id,
        should_cancel=should_cancel,
    )
    if code != 0:
        tail = err.strip()[-500:] or f"exit {code}"
        raise EngineError(
            f"ffmpeg 失败：{tail}",
            code="ffmpeg",
            action="用播放器确认原片能打开。若是字幕烧录失败，可先关掉字幕再渲染。",
            status=500,
        )
    return err


def probe(path: Path) -> dict:
    require_ffprobe()
    if not path.is_file():
        raise EngineError(
            f"文件不存在：{path.name}",
            code="missing_file",
            action="回素材库确认这条视频还在磁盘上。",
            status=404,
        )
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise EngineError(
            "ffprobe 读不出这条视频：" + (proc.stderr or "")[-300:],
            code="probe",
            action="文件可能损坏，换一条素材试。",
            status=422,
        )
    import json

    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise EngineError("ffprobe 返回的不是 JSON", code="probe", status=500) from exc
    duration = 0.0
    fmt = payload.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    width = height = 0
    has_audio = False
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") == "video" and not width:
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
        if stream.get("codec_type") == "audio":
            has_audio = True
    if duration <= 0:
        raise EngineError(
            f"读不到时长：{path.name}",
            code="probe",
            action="用 ffprobe 单独检查该文件。",
            status=422,
        )
    return {
        "duration": duration,
        "width": width,
        "height": height,
        "has_audio": has_audio,
        "size": path.stat().st_size,
    }


def even(value: int) -> int:
    value = max(2, int(value or 2))
    return value if value % 2 == 0 else value - 1


def filter_path(path: Path) -> str:
    text = path.resolve().as_posix()
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
