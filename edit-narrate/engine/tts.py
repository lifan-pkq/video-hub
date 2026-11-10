"""Speech synthesis. Tone mode is explicit and never the silent default."""

from __future__ import annotations

import asyncio
import importlib.util
import shutil
import subprocess
from pathlib import Path

import httpx

from engine.errors import EngineError
from engine.media import ffmpeg, probe
from engine.settings import tts_api_key, tts_base_url, tts_model, tts_voice


VOICES = [
    {"engine": "edge", "id": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 女声"},
    {"engine": "edge", "id": "zh-CN-YunxiNeural", "label": "云希 · 男声"},
    {"engine": "edge", "id": "zh-CN-YunjianNeural", "label": "云健 · 男声"},
    {"engine": "edge", "id": "zh-CN-XiaoyiNeural", "label": "晓伊 · 女声"},
    {"engine": "openai", "id": "alloy", "label": "OpenAI alloy"},
    {"engine": "espeak", "id": "zh", "label": "espeak 中文"},
    {"engine": "tone", "id": "tone", "label": "测试音（不是人声）"},
]


def edge_available() -> bool:
    return importlib.util.find_spec("edge_tts") is not None


def espeak_bin() -> str | None:
    return shutil.which("espeak-ng") or shutil.which("espeak")


def openai_available() -> bool:
    return bool(tts_base_url() and tts_api_key())


def engine_status() -> list[dict]:
    return [
        {"id": "edge", "available": edge_available(), "detail": "已安装 edge-tts" if edge_available() else "未安装 edge-tts"},
        {"id": "openai", "available": openai_available(), "detail": "已配置 VIDEO_HUB_TTS_BASE_URL 或复用文本模型地址" if openai_available() else "未配置 TTS 接口"},
        {"id": "espeak", "available": bool(espeak_bin()), "detail": espeak_bin() or "未安装 espeak-ng"},
        {"id": "tone", "available": True, "detail": "只用于测试，生成正弦音，不是解说人声"},
    ]


def resolve_engine(requested: str) -> str:
    name = (requested or "auto").lower()
    if name == "auto":
        if edge_available():
            return "edge"
        if openai_available():
            return "openai"
        if espeak_bin():
            return "espeak"
        raise EngineError(
            "没有可用的语音合成",
            code="tts_unconfigured",
            action="任选其一：pip install edge-tts；或设置 VIDEO_HUB_TTS_BASE_URL 与 VIDEO_HUB_TTS_API_KEY；或安装 espeak-ng。不要用测试音冒充成片。",
            status=409,
        )
    if name == "edge" and not edge_available():
        raise EngineError("未安装 edge-tts", code="tts_unconfigured", action="在后端环境执行 pip install edge-tts，或改选其他引擎。", status=409)
    if name == "openai" and not openai_available():
        raise EngineError(
            "未配置 OpenAI 兼容 TTS",
            code="tts_unconfigured",
            action="设置 VIDEO_HUB_TTS_BASE_URL、VIDEO_HUB_TTS_API_KEY、VIDEO_HUB_TTS_MODEL。",
            status=409,
        )
    if name == "espeak" and not espeak_bin():
        raise EngineError("未找到 espeak-ng", code="tts_unconfigured", action="安装 espeak-ng，或改用 edge / 兼容接口。", status=409)
    if name not in {"edge", "openai", "espeak", "tone"}:
        raise EngineError(f"未知语音引擎：{name}", code="tts_engine", action="使用 auto、edge、openai、espeak。", status=400)
    return name


def _edge(text: str, dest: Path, voice: str, rate: float) -> None:
    import edge_tts

    percent = int(round((rate - 1) * 100))
    rate_text = f"{percent:+d}%"

    async def _run() -> None:
        communicate = edge_tts.Communicate(text, voice or "zh-CN-XiaoxiaoNeural", rate=rate_text)
        await communicate.save(str(dest))

    try:
        asyncio.run(_run())
    except Exception as exc:  # edge-tts raises its own types
        raise EngineError(
            f"edge-tts 失败：{exc}",
            code="tts",
            action="检查网络是否能访问微软语音服务，或改用 VIDEO_HUB_TTS_BASE_URL。",
            status=502,
        ) from exc
    if not dest.is_file() or dest.stat().st_size < 80:
        raise EngineError("edge-tts 没有写出音频", code="tts", action="换一个音色名后再试。", status=502)


def _openai(text: str, dest: Path, voice: str) -> None:
    base = tts_base_url()
    url = base if base.endswith("/audio/speech") else base + "/audio/speech"
    try:
        response = httpx.post(
            url,
            headers={"Authorization": f"Bearer {tts_api_key()}"},
            json={
                "model": tts_model(),
                "input": text,
                "voice": voice or tts_voice(),
                "response_format": "wav",
            },
            timeout=60,
        )
    except httpx.HTTPError as exc:
        raise EngineError(f"TTS 接口连不上：{exc}", code="tts", action=f"检查 {url}", status=502) from exc
    if response.status_code >= 400 or not response.content:
        raise EngineError(
            f"TTS 接口 HTTP {response.status_code}：{response.text[:240]}",
            code="tts",
            action="核对 VIDEO_HUB_TTS_MODEL 和音色名。",
            status=502,
        )
    dest.write_bytes(response.content)


def _espeak(text: str, dest: Path, rate: float) -> None:
    binary = espeak_bin()
    assert binary
    speed = str(int(160 * rate))
    proc = subprocess.run([binary, "-v", "zh", "-s", speed, "-w", str(dest), text], capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.is_file():
        raise EngineError(
            "espeak 失败：" + (proc.stderr or proc.stdout or "")[-240:],
            code="tts",
            action="espeak 的中文效果有限。成片建议改用 edge-tts。",
            status=500,
        )


def _tone(text: str, dest: Path, rate: float) -> None:
    seconds = max(0.45, min(20.0, len(text.strip()) / (4.0 * max(rate, 0.5))))
    ffmpeg(
        ["-f", "lavfi", "-i", f"sine=frequency=523:sample_rate=44100:duration={seconds:.2f}", "-c:a", "pcm_s16le", str(dest)],
        timeout=30,
    )


def synthesize(text: str, dest: Path, *, engine: str, voice: str, rate: float) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise EngineError("有一段口播是空的", code="tts", action="删掉空段或补上文字。", status=422)
    chosen = resolve_engine(engine)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if chosen == "edge":
        _edge(cleaned, dest, voice, rate)
    elif chosen == "openai":
        _openai(cleaned, dest, voice)
    elif chosen == "espeak":
        _espeak(cleaned, dest, rate)
    else:
        _tone(cleaned, dest, rate)
    return chosen


def fit_to_picture(source: Path, dest: Path, picture_seconds: float) -> None:
    spoken = float(probe(source)["duration"])
    picture = float(picture_seconds)
    if picture < 0.35:
        raise EngineError(
            f"画面段只有 {picture:.2f}s，放不下一句口播",
            code="beat_too_short",
            action="把该段结束时间往后拉，或删掉这一段。",
            status=422,
        )
    ratio = spoken / picture
    if ratio <= 1.08:
        ffmpeg(
            ["-i", str(source), "-af", f"apad=whole_dur={picture:.3f}", "-t", f"{picture:.3f}", "-c:a", "pcm_s16le", str(dest)],
            timeout=60,
        )
        return
    if ratio <= 1.35:
        ffmpeg(
            ["-i", str(source), "-filter:a", f"atempo={ratio:.4f}", "-t", f"{picture:.3f}", "-c:a", "pcm_s16le", str(dest)],
            timeout=60,
        )
        return
    raise EngineError(
        f"口播 {spoken:.1f}s 长于画面 {picture:.1f}s，最快也只能压到 1.35 倍",
        code="beat_fit",
        action="改短这句，或把该段结束时间往后拉，然后再渲染。",
        status=422,
    )
