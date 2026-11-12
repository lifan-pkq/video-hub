"""Environment configuration. Keys are never read from the repo."""

from __future__ import annotations

import json
import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _parse_keys(raw: str) -> list[str]:
    value = (raw or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = []
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


def _keys_from(*names: str) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for name in names:
        for key in _parse_keys(_env(name)):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def ai_base_url() -> str:
    return _env("VIDEO_HUB_AI_BASE_URL").rstrip("/")


def ai_api_key() -> str:
    keys = ai_api_keys()
    return keys[0] if keys else ""


def ai_api_keys() -> list[str]:
    return _keys_from(
        "VIDEO_HUB_AI_API_KEYS",
        "AGNES_API_KEYS",
        "VIDEO_HUB_AI_API_KEY",
        "AGNES_API_KEY",
        "VIDEO_HUB_VISION_API_KEYS",
        "VIDEO_HUB_VISION_API_KEY",
    )


def ai_model() -> str:
    return _env("VIDEO_HUB_AI_MODEL", "gpt-4o-mini")


def vision_base_url() -> str:
    return (_env("VIDEO_HUB_VISION_BASE_URL") or ai_base_url()).rstrip("/")


def vision_api_key() -> str:
    keys = vision_api_keys()
    return keys[0] if keys else ""


def vision_api_keys() -> list[str]:
    return _keys_from(
        "VIDEO_HUB_VISION_API_KEYS",
        "VIDEO_HUB_VISION_API_KEY",
        "VIDEO_HUB_AI_API_KEYS",
        "AGNES_API_KEYS",
        "VIDEO_HUB_AI_API_KEY",
        "AGNES_API_KEY",
    )


def vision_model() -> str:
    return _env("VIDEO_HUB_VISION_MODEL")


def asr_url() -> str:
    return _env("VIDEO_HUB_ASR_URL")


def asr_sidecar_enabled() -> bool:
    return _env("VIDEO_HUB_ASR_SIDECAR", "0") in {"1", "true", "yes"}


def jobs_inline() -> bool:
    return _env("VIDEO_HUB_JOBS_INLINE", "0") in {"1", "true", "yes"}


def llm_configured() -> bool:
    return bool(ai_base_url() and ai_api_keys() and ai_model())


def vision_configured() -> bool:
    return bool(vision_base_url() and vision_api_keys() and vision_model())
