"""本地音频分析：使用 FFmpeg 检测真实静音区间。"""
import re
import subprocess
from pathlib import Path

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_START_RE = re.compile(rf"\bsilence_start:\s*({_NUMBER})")
_END_RE = re.compile(rf"\bsilence_end:\s*({_NUMBER})")


def parse_silencedetect(stderr: str) -> list[dict]:
    """把 silencedetect 的 stderr 日志配对为静音建议。"""
    items = []
    pending_start = None
    for line in stderr.splitlines():
        start_match = _START_RE.search(line)
        if start_match:
            pending_start = max(0.0, float(start_match.group(1)))
            continue
        end_match = _END_RE.search(line)
        if not end_match or pending_start is None:
            continue
        end = float(end_match.group(1))
        if end - pending_start >= 0.3:
            items.append({
                "start": round(pending_start, 2),
                "end": round(end, 2),
                "kind": "silence",
                "reason": f"检测到 {end - pending_start:.1f} 秒静音（-35dB，最短 0.6 秒）",
                "source": "audio",
            })
        pending_start = None
    return items


def detect_silence(video_path: Path, log=print) -> list[dict]:
    """在本地媒体上运行 silencedetect，不向 AI 服务发送媒体。"""
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path),
        "-af", "silencedetect=noise=-35dB:d=0.6", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except OSError as exc:
        raise RuntimeError(f"无法启动 FFmpeg 静音检测：{exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "未知错误").strip()
        raise RuntimeError(f"FFmpeg 静音检测失败：{detail[-500:]}")
    items = parse_silencedetect(result.stderr or "")
    log(f"静音检测完成：{len(items)} 段")
    return items
