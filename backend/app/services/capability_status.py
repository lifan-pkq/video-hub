"""能力状态的安全摘要。只看本机配置和文件是否存在，不发网络请求，不回显秘密。"""
from __future__ import annotations

import importlib.util
import shutil


def _item(item_id: str, state: str, label: str, summary: str, next_step: str = "") -> dict:
    if state not in {"ready", "configured", "unconfigured", "unknown", "limited"}:
        state = "unknown"
    return {
        "id": item_id,
        "state": state,
        "label": label,
        "summary": summary[:240],
        "next": next_step[:240],
    }


def _public_name(value: str) -> str:
    text = (value or "").strip()
    if not text or len(text) > 64 or any(char in text for char in " \n\t@/\\"):
        return ""
    return text


def _sensevoice_files():
    """只检查文件在不在。不下载、不加载模型。"""
    try:
        from app.services.sensevoice import model_dir

        folder = model_dir()
        present = (folder / "model.int8.onnx").is_file() and (folder / "tokens.txt").is_file()
        return present
    except Exception:
        return None


def build_capabilities() -> list[dict]:
    from engine.settings import (
        ai_api_keys,
        ai_base_url,
        ai_model,
        asr_url,
        llm_configured,
        vision_api_keys,
        vision_base_url,
        vision_configured,
        vision_model,
    )
    from app.config import DATA_DIR
    from app.services.media import free_mb
    from app.services.transcription import asr_engine

    items = []
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        items.append(_item(
            "ffmpeg", "ready", "ffmpeg",
            "本机找到 ffmpeg 和 ffprobe。这只说明工具在，不保证每条成片都能渲染成功。",
        ))
    elif shutil.which("ffmpeg"):
        items.append(_item(
            "ffmpeg", "limited", "ffmpeg",
            "找到 ffmpeg，没有找到 ffprobe。时长测量和部分验收会不完整。",
            "安装 ffprobe，它通常和 ffmpeg 在同一个包里。",
        ))
    else:
        items.append(_item(
            "ffmpeg", "unconfigured", "ffmpeg",
            "本机没有 ffmpeg，剪辑和导出无法出片。",
            "安装 ffmpeg 后再试。",
        ))

    if llm_configured():
        model = _public_name(ai_model())
        named = f"模型名是 {model}。" if model else "模型名已填写，这里不展示原文。"
        items.append(_item(
            "llm", "configured", "文本模型",
            f"地址、模型和密钥都已填写。{named}没有向服务商发请求，不能据此判断连通或选段效果。",
            "要确认连通，需另做一次你授权的调用。此接口不会主动探测。",
        ))
    else:
        missing = []
        if not ai_base_url():
            missing.append("VIDEO_HUB_AI_BASE_URL")
        if not ai_api_keys():
            missing.append("VIDEO_HUB_AI_API_KEY")
        if not ai_model():
            missing.append("VIDEO_HUB_AI_MODEL")
        items.append(_item(
            "llm", "unconfigured", "文本模型",
            "文本模型未配置。智能剪辑会改用口播密度，不会调用付费接口。",
            "补齐 " + "、".join(missing or ["地址、密钥和模型名"]) + "。未读取生产环境，也不能把缺配置当成线上故障原因。",
        ))

    if vision_configured():
        model = _public_name(vision_model())
        named = f"模型名是 {model}。" if model else "模型名已填写，这里不展示原文。"
        items.append(_item(
            "vision", "configured", "画面模型",
            f"视觉地址、模型和密钥都已填写。{named}没有发请求，不能当成画面说明可用。",
            "连通性未知。此接口不会发试探请求。",
        ))
    else:
        missing = []
        if not vision_base_url():
            missing.append("VIDEO_HUB_VISION_BASE_URL")
        if not vision_api_keys():
            missing.append("VIDEO_HUB_VISION_API_KEY")
        if not vision_model():
            missing.append("VIDEO_HUB_VISION_MODEL")
        items.append(_item(
            "vision", "unconfigured", "画面模型",
            "画面模型未配置。抽帧可以留下，但不会生成画面说明。",
            "补齐 " + "、".join(missing) + "。文本模型的密钥不能代替视觉模型名。未发请求。",
        ))

    remote = asr_url()
    engine = asr_engine()
    if remote:
        items.append(_item(
            "asr", "configured", "语音识别",
            "已设置外部转写地址，当前会走远端而不是本机 SenseVoice 或 Whisper。没有发请求试连通，地址本身不返回。",
            "要停用远端，清空 VIDEO_HUB_ASR_URL。连通性仍是未知。",
        ))
    elif engine == "whisper":
        package = importlib.util.find_spec("faster_whisper") is not None
        if package:
            items.append(_item(
                "asr", "configured", "语音识别",
                "已选择 Whisper，并且本机能导入 faster-whisper。包能导入不等于模型已下载，也没做过转写试跑。",
                "保持 VIDEO_HUB_ASR_ENGINE=whisper。第一次转写才会加载模型。",
            ))
        else:
            items.append(_item(
                "asr", "unconfigured", "语音识别",
                "已选择 Whisper，但本机导入不了 faster-whisper。不能把它当成转写可用。",
                "安装 faster-whisper，或改回默认的 SenseVoice。",
            ))
    elif engine == "sensevoice":
        files = _sensevoice_files()
        if files is True:
            items.append(_item(
                "asr", "configured", "语音识别",
                "默认引擎是 SenseVoice，模型文件在本机。没有加载模型，也没有做转写，不能当成识别效果已验收。",
                "难句仍可能错。需要 Whisper 时设置 VIDEO_HUB_ASR_ENGINE=whisper。",
            ))
        elif files is False:
            items.append(_item(
                "asr", "unconfigured", "语音识别",
                "默认引擎是 SenseVoice，但本机还没有 model.int8.onnx 和 tokens.txt。这次没有下载。",
                "第一次转写才会尝试下载公开 int8 模型。下载失败不会改用 Whisper。",
            ))
        else:
            items.append(_item(
                "asr", "unknown", "语音识别",
                "默认引擎是 SenseVoice，但这次没能判断模型文件在不在。没有下载，也没有试跑。",
                "检查模型目录是否可读。不要把引擎名当成转写成功。",
            ))
    else:
        items.append(_item(
            "asr", "unconfigured", "语音识别",
            f"VIDEO_HUB_ASR_ENGINE={engine or '空'} 不是 sensevoice 或 whisper。",
            "改回 sensevoice，或设为 whisper。",
        ))

    try:
        free = free_mb(DATA_DIR)
    except Exception:
        free = None
    if free is None:
        items.append(_item("disk", "unknown", "磁盘剩余", "没有读到数据目录的剩余空间。", "确认数据目录存在且可统计。"))
    elif free < 512:
        items.append(_item(
            "disk", "limited", "磁盘剩余",
            f"数据目录大约剩余 {int(free)} MB，低于 512 MB。渲染和导出会停下来。",
            "清理临时文件或扩大磁盘。这不是模型效果。",
        ))
    else:
        items.append(_item("disk", "ready", "磁盘剩余", f"数据目录大约剩余 {int(free)} MB。", ""))
    return items
