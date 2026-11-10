"""Environment configuration. Keys are never read from the repo."""

from __future__ import annotations

import os


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def ai_base_url() -> str:
    return _env("VIDEO_HUB_AI_BASE_URL").rstrip("/")


def ai_api_key() -> str:
    return _env("VIDEO_HUB_AI_API_KEY")


def ai_model() -> str:
    return _env("VIDEO_HUB_AI_MODEL", "gpt-4o-mini")


def vision_base_url() -> str:
    return (_env("VIDEO_HUB_VISION_BASE_URL") or ai_base_url()).rstrip("/")


def vision_api_key() -> str:
    return _env("VIDEO_HUB_VISION_API_KEY") or ai_api_key()


def vision_model() -> str:
    return _env("VIDEO_HUB_VISION_MODEL")


def asr_url() -> str:
    return _env("VIDEO_HUB_ASR_URL")


def asr_sidecar_enabled() -> bool:
    return _env("VIDEO_HUB_ASR_SIDECAR", "0") in {"1", "true", "yes"}


def tts_base_url() -> str:
    return (_env("VIDEO_HUB_TTS_BASE_URL") or ai_base_url()).rstrip("/")


def tts_api_key() -> str:
    return _env("VIDEO_HUB_TTS_API_KEY") or ai_api_key()


def tts_model() -> str:
    return _env("VIDEO_HUB_TTS_MODEL", "tts-1")


def tts_voice() -> str:
    return _env("VIDEO_HUB_TTS_VOICE", "alloy")


def jobs_inline() -> bool:
    return _env("VIDEO_HUB_JOBS_INLINE", "0") in {"1", "true", "yes"}


def llm_configured() -> bool:
    return bool(ai_base_url() and ai_api_key() and ai_model())


def vision_configured() -> bool:
    return bool(vision_base_url() and vision_api_key() and vision_model())
