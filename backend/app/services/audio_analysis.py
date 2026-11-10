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


def analyze_loudness(video_path: Path) -> dict:
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(video_path),
        "-af", "ebur128=peak=true", "-vn", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError):
        return {"status": "unknown"}
    output: dict = {"status": "ok" if result.returncode == 0 else "unknown"}
    for line in (result.stderr or "").splitlines():
        integrated = re.search(r"I:\s*([-0-9.]+)\s*LUFS", line)
        peak = re.search(r"Peak:\s*([-0-9.]+)\s*dBFS", line)
        if integrated:
            output["integrated_lufs"] = float(integrated.group(1))
        if peak:
            output["true_peak_dbfs"] = float(peak.group(1))
    return output


def detect_beats(video_path: Path) -> list[float]:
    from array import array
    from statistics import median

    command = [
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(video_path),
        "-af", "highpass=f=60,lowpass=f=180", "-ac", "1", "-ar", "8000",
        "-f", "s16le", "-acodec", "pcm_s16le", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=1800, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    samples = array("h")
    samples.frombytes(result.stdout)
    window = 960
    energies = [
        sum(abs(value) for value in samples[offset:offset + window]) / max(1, len(samples[offset:offset + window]))
        for offset in range(0, len(samples), window)
    ]
    if len(energies) < 3 or max(energies, default=0) < 100:
        return []
    baseline = max(1.0, median(energies))
    beats: list[float] = []
    previous = -1.0
    for index in range(1, len(energies) - 1):
        value = energies[index]
        if value < max(baseline * 1.7, energies[index - 1] * 1.35) or value < energies[index + 1]:
            continue
        timestamp = index * window / 8000
        if timestamp - previous >= 0.28:
            beats.append(round(timestamp, 3))
            previous = timestamp
    return beats
