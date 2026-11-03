"""读写 NarratoAI config.toml，并提供环境变量兜底。

只管理"生成解说"需要的字段；不碰其他模块的配置段。
HTTP 永远不回传 API Key 明文，只返回是否已配置和尾号。
"""

from __future__ import annotations

import os
import re
import shutil
import threading
import tomllib
from pathlib import Path
from typing import Any

try:  # 写入时优先用 tomli_w，保留类型与格式
    import tomli_w  # type: ignore
except Exception:  # pragma: no cover - narratoai 环境正常都有
    tomli_w = None

from engine.profiles import DEFAULT_TTS_ENGINE, DEFAULT_VOICE, TTS_ENGINES

_lock = threading.Lock()
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/@+\-]{1,120}$")
_ENGINES = {item["value"] for item in TTS_ENGINES}


def config_path(root: Path | str) -> Path:
    override = os.environ.get("NARRATOAI_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path(root).resolve() / "config.toml")


def ensure_config(root: Path | str) -> Path:
    path = config_path(root)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    example = Path(root).resolve() / "config.example.toml"
    if example.is_file():
        shutil.copy2(example, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path


def load_config(root: Path | str) -> dict[str, Any]:
    path = ensure_config(root)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        data = {}
    return data


def _get(data: dict[str, Any], section: str, key: str, default: Any = "") -> Any:
    block = data.get(section)
    if isinstance(block, dict):
        return block.get(key, default)
    return default


def _hint(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 6:
        return "*" * len(key)
    return f"{key[:3]}…{key[-4:]}"


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def env_llm_overrides() -> dict[str, str]:
    """Video Hub 的 .env 直接驱动解说引擎，避免两套模型配置。"""
    base = _env("VIDEO_HUB_AI_BASE_URL")
    key = _env("VIDEO_HUB_AI_API_KEY")
    model = _env("VIDEO_HUB_AI_MODEL")

    vision_base = _env("VIDEO_HUB_VISION_BASE_URL") or base
    vision_key = _env("VIDEO_HUB_VISION_API_KEY") or key
    vision_model = _env("VIDEO_HUB_VISION_MODEL") or _env("VIDEO_HUB_AI_VISION_MODEL")
    if not vision_model and model and any(token in model.lower() for token in ("vl", "vision", "gpt-4o", "gemini")):
        vision_model = model

    return {
        "text_base_url": base,
        "text_api_key": key,
        "text_model": model,
        "vision_base_url": vision_base,
        "vision_api_key": vision_key,
        "vision_model": vision_model,
    }


def settings_payload(root: Path | str) -> dict[str, Any]:
    data = load_config(root)
    overrides = env_llm_overrides()
    text_key = str(_get(data, "app", "text_openai_api_key", "") or "")
    vision_key = str(_get(data, "app", "vision_openai_api_key", "") or "")
    text = {
        "provider": "openai",
        "base_url": overrides["text_base_url"] or str(_get(data, "app", "text_openai_base_url", "") or ""),
        "model": overrides["text_model"] or str(_get(data, "app", "text_openai_model_name", "") or ""),
        "fast_model": str(_get(data, "app", "text_openai_fast_model_name", "") or ""),
        "configured": bool(overrides["text_api_key"] or text_key),
        "api_key_hint": _hint(overrides["text_api_key"] or text_key),
        "source": "env" if overrides["text_api_key"] else ("config" if text_key else "none"),
    }
    vision = {
        "provider": "openai",
        "base_url": overrides["vision_base_url"] or str(_get(data, "app", "vision_openai_base_url", "") or ""),
        "model": overrides["vision_model"] or str(_get(data, "app", "vision_openai_model_name", "") or ""),
        "configured": bool(overrides["vision_api_key"] or vision_key),
        "api_key_hint": _hint(overrides["vision_api_key"] or vision_key),
        "source": "env" if overrides["vision_api_key"] else ("config" if vision_key else "none"),
    }
    tts = {
        "engine": str(_get(data, "ui", "tts_engine", "") or DEFAULT_TTS_ENGINE),
        "voice_name": str(_get(data, "ui", "edge_voice_name", "") or DEFAULT_VOICE),
    }
    return {
        "config_file": str(config_path(root)),
        "text": text,
        "vision": vision,
        "tts": tts,
        "ready": bool(text["configured"] and text["model"] and vision["configured"] and vision["model"]),
    }


def _validate_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if not re.match(r"^https?://[^\s]+$", value):
        raise ValueError("Base URL 必须是 http(s) 地址")
    return value


def _validate_model(value: str) -> str:
    value = value.strip()
    if value and not _MODEL_RE.match(value):
        raise ValueError("模型名含有非法字符")
    return value


def _merge_api_key(block: dict[str, Any], prefix: str, incoming: dict[str, Any]) -> None:
    if incoming.get("clear_api_key"):
        block[f"{prefix}_api_key"] = ""
    key = str(incoming.get("api_key") or "").strip()
    if key:
        if len(key) > 500 or "\n" in key or "\r" in key:
            raise ValueError("API Key 格式不合法")
        block[f"{prefix}_api_key"] = key


def update_settings(root: Path | str, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    with _lock:
        data = load_config(root)
        app = data.setdefault("app", {})
        ui = data.setdefault("ui", {})

        text = payload.get("text") or {}
        if not isinstance(text, dict):
            raise ValueError("text 必须是对象")
        if "base_url" in text:
            app["text_openai_base_url"] = _validate_url(str(text.get("base_url") or ""))
        if "model" in text:
            app["text_openai_model_name"] = _validate_model(str(text.get("model") or ""))
        if "fast_model" in text:
            app["text_openai_fast_model_name"] = _validate_model(str(text.get("fast_model") or ""))
        _merge_api_key(app, "text_openai", text)

        vision = payload.get("vision") or {}
        if not isinstance(vision, dict):
            raise ValueError("vision 必须是对象")
        if "base_url" in vision:
            app["vision_openai_base_url"] = _validate_url(str(vision.get("base_url") or ""))
        if "model" in vision:
            app["vision_openai_model_name"] = _validate_model(str(vision.get("model") or ""))
        _merge_api_key(app, "vision_openai", vision)

        app["text_llm_provider"] = "openai"
        app["vision_llm_provider"] = "openai"

        tts = payload.get("tts") or {}
        if isinstance(tts, dict):
            engine = str(tts.get("engine") or "").strip()
            if engine:
                if engine not in _ENGINES:
                    raise ValueError("不支持的 TTS 引擎")
                ui["tts_engine"] = engine
            voice = str(tts.get("voice_name") or "").strip()
            if voice:
                if not re.match(r"^[A-Za-z0-9._:\-]{1,80}$", voice):
                    raise ValueError("音色名含有非法字符")
                ui["edge_voice_name"] = voice

        path = config_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".toml.tmp")
        if tomli_w is not None:
            with tmp.open("wb") as handle:
                tomli_w.dump(data, handle)
        else:
            tmp.write_text(_dump_toml(data), encoding="utf-8")
        os.replace(tmp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return settings_payload(root)


def _dump_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_dump_scalar(item) for item in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def _dump_toml(data: dict[str, Any]) -> str:
    """极简 TOML 写入兜底（只有 tomli_w 缺失时使用，会丢注释）。"""
    lines: list[str] = []
    for section, block in data.items():
        if not isinstance(block, dict):
            lines.append(f"{section} = {_dump_scalar(block)}")
            continue
        lines.append(f"[{section}]")
        for key, value in block.items():
            if isinstance(value, dict):
                continue
            lines.append(f"{key} = {_dump_scalar(value)}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"
