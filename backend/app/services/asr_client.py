"""可选的远端转写客户端。默认不使用；只有设置了 VIDEO_HUB_ASR_URL 才走这里。"""
import math
from pathlib import Path

import requests

from app.services.ai_edit import AIResponseError, AIServiceError


def transcribe_remote(video_path: Path, url: str, log=print) -> list[dict]:
    """通过 OpenAI 兼容接口转写，返回秒级 start/end/text segments。

    注意：requests 的 multipart 编码会把整个文件读进内存，所以调用方
    必须只传小音频文件（ai_edit._transcribe_shared 会先抽音频再按 300s
    分段），不要直接传原视频。本函数不改成流式上传（那是库行为）。
    连接 10 秒、读取 1800 秒，避免远端一直不返回时把任务挂死。
    """
    try:
        with video_path.open("rb") as media:
            response = requests.post(
                url,
                files={"file": (video_path.name, media)},
                data={
                    "model": "whisper-1",
                    "language": "zh",
                    "response_format": "verbose_json",
                },
                timeout=(10, 1800),
            )
            response.raise_for_status()
    except (OSError, requests.RequestException) as exc:
        raise AIServiceError(
            f"远端转写不可用：{exc}。检查 VIDEO_HUB_ASR_URL，或留空改用本机 faster-whisper。"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AIResponseError("远端转写返回了无效 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise AIResponseError("远端转写响应缺少 segments。接口需要返回 {\"segments\":[{\"start\",\"end\",\"text\"}]}。")

    segments = []
    for item in payload["segments"]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("start", 0))
            end = float(item.get("end", start))
        except (TypeError, ValueError) as exc:
            raise AIResponseError("远端转写返回了无效时间戳") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise AIResponseError("远端转写返回了无效时间区间")
        segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})

    language = payload.get("language") or "unknown"
    duration = payload.get("duration")
    log(f"转写完成：{len(segments)} 句（远端转写，检测语言 {language}，时长 {duration or 0}s）")
    return segments
