"""共享 ASR HTTP 客户端：复用 NarratoAI 的 faster-whisper 服务。"""
import math
from pathlib import Path

import requests

from app.services.ai_edit import AIResponseError, AIServiceError


def transcribe_remote(video_path: Path, url: str, log=print) -> list[dict]:
    """通过 OpenAI 兼容接口转写，返回秒级 start/end/text segments。"""
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
        raise AIServiceError(f"共享 ASR 服务不可用：{exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AIResponseError("共享 ASR 返回了无效 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise AIResponseError("共享 ASR 响应缺少 segments")

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
            raise AIResponseError("共享 ASR 返回了无效时间戳") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            raise AIResponseError("共享 ASR 返回了无效时间区间")
        segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})

    language = payload.get("language") or "unknown"
    duration = payload.get("duration")
    log(f"转写完成：{len(segments)} 句（共享 ASR，检测语言 {language}，时长 {duration or 0}s）")
    return segments
