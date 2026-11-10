"""多 Key AI 池：轮询、429 冷却、失败切换，避免单 Key 限额拖死任务。"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

import requests


# 文本优先：解说写稿 / JSON 补全
AI_KEY_ENV_NAMES = (
    "VIDEO_HUB_AI_API_KEYS",
    "AGNES_API_KEYS",
    "VIDEO_HUB_AI_API_KEY",
    "AGNES_API_KEY",
    "VIDEO_HUB_VISION_API_KEYS",
    "VIDEO_HUB_VISION_API_KEY",
)

# 视觉优先：画面说明 / 抽帧 caption
VISION_KEY_ENV_NAMES = (
    "VIDEO_HUB_VISION_API_KEYS",
    "VIDEO_HUB_VISION_API_KEY",
    "VIDEO_HUB_AI_API_KEYS",
    "AGNES_API_KEYS",
    "VIDEO_HUB_AI_API_KEY",
    "AGNES_API_KEY",
)

# 兼容旧常量：合并去重后的全集
KEY_ENV_NAMES = AI_KEY_ENV_NAMES


class AIPoolError(RuntimeError):
    pass


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


def load_keys(env_names: Iterable[str] | None = None) -> list[str]:
    seen: set[str] = set()
    keys: list[str] = []
    for name in env_names or KEY_ENV_NAMES:
        for key in _parse_keys(os.environ.get(name, "")):
            if key not in seen:
                seen.add(key)
                keys.append(key)
    return keys


def _hint(key: str) -> str:
    # 原实现暴露前 6 位：不少厂商 key 前缀含项目/租户标识，属于可枚举情报
    return f"***{key[-4:]}" if len(key) >= 8 else "***"


_SECRET_RE = re.compile(r"[A-Za-z0-9_\-]{16,}")


def _redact(text: str, limit: int = 120) -> str:
    """上游错误体可能回显 key 片段或内网地址，统一打码并截断。"""
    if not text:
        return ""
    return _SECRET_RE.sub("***", str(text))[:limit]


@dataclass
class KeyState:
    failures: int = 0
    successes: int = 0
    last_status: int | None = None
    last_used: float = 0.0
    cooldown_until: float = 0.0
    last_error: str = ""


# 同一 Key 在文本池 / 视觉池之间共享冷却与计数，避免一边 429 另一边还继续打
_SHARED_STATE: dict[str, KeyState] = {}
_SHARED_LOCK = threading.RLock()


def _shared_state(key: str) -> KeyState:
    with _SHARED_LOCK:
        state = _SHARED_STATE.get(key)
        if state is None:
            state = KeyState()
            _SHARED_STATE[key] = state
        return state


class AIPool:
    def __init__(self, keys: list[str] | None = None, name: str = "agnes"):
        self.name = name
        self.keys = keys if keys is not None else load_keys()
        self._lock = threading.RLock()
        self._index = 0
        self._state: dict[str, KeyState] = {key: _shared_state(key) for key in self.keys}

    def has_keys(self) -> bool:
        return bool(self.keys)

    def acquire(self) -> str | None:
        now = time.time()
        with self._lock:
            count = len(self.keys)
            for offset in range(count):
                index = (self._index + offset) % count
                key = self.keys[index]
                state = self._state[key]
                if state.cooldown_until <= now:
                    self._index = (index + 1) % count
                    state.last_used = now
                    return key
        return None

    def mark_success(self, key: str) -> None:
        with self._lock:
            state = self._state.get(key)
            if state:
                state.successes += 1
                state.failures = 0
                state.last_status = 200
                state.cooldown_until = 0.0
                state.last_error = ""

    def mark_failure(
        self,
        key: str,
        status: int | None = None,
        detail: str = "",
        retry_after: float | None = None,
    ) -> None:
        now = time.time()
        with self._lock:
            state = self._state.get(key)
            if not state:
                return
            state.failures += 1
            state.last_status = status
            state.last_error = detail[:160]
            if status in {401, 403}:
                state.cooldown_until = now + 3600
            elif status == 429:
                state.cooldown_until = now + (retry_after or 60)
            elif status and status >= 500:
                state.cooldown_until = now + 30
            else:
                state.cooldown_until = now + 10

    def request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        timeout: float = 120,
        headers: dict[str, str] | None = None,
        busy_markers: tuple[str, ...] = (),
    ) -> requests.Response:
        if not self.keys:
            raise AIPoolError("没有配置 AI API Key")
        attempts = max(1, len(self.keys))
        last_error = "所有 Key 都不可用"
        for _ in range(attempts):
            key = self.acquire()
            if not key:
                break
            request_headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            if headers:
                request_headers.update(headers)
            try:
                response = requests.request(
                    method,
                    url,
                    headers=request_headers,
                    json=json_body,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                self.mark_failure(key, None, str(exc))
                last_error = str(exc)
                continue
            if 200 <= response.status_code < 300:
                self.mark_success(key)
                response._ai_pool_key = key
                return response
            retry_after = None
            try:
                retry_after = float(response.headers.get("Retry-After") or 0) or None
            except (TypeError, ValueError):
                pass
            if response.status_code >= 500 and any(marker in response.text for marker in busy_markers):
                # 服务端队列满不是 Key 的问题，交给调用方退避重试
                return response
            if response.status_code in {401, 403, 408, 409, 425, 429} or response.status_code >= 500:
                self.mark_failure(key, response.status_code, response.text[:160], retry_after)
                last_error = f"HTTP {response.status_code}: {_redact(response.text, 160)}"
                continue
            # 400 等请求参数错误不是 Key 的问题，直接交给调用方处理
            return response
        raise AIPoolError(last_error)

    def status(self) -> dict[str, Any]:
        now = time.time()
        index_of = {key: i for i, key in enumerate(self.keys)}
        with self._lock:
            return {
                "name": self.name,
                "total": len(self.keys),
                "available": sum(1 for state in self._state.values() if state.cooldown_until <= now),
                "keys": [
                    {
                        "hint": f"#{index_of.get(key, 0) + 1}·{_hint(key)}",
                        "available": state.cooldown_until <= now,
                        "cooldown_seconds": max(0, round(state.cooldown_until - now, 1)),
                        "successes": state.successes,
                        "failures": state.failures,
                        "last_status": state.last_status,
                        "last_error": _redact(state.last_error),
                    }
                    for key, state in self._state.items()
                ],
            }


AI_POOL = AIPool(keys=load_keys(AI_KEY_ENV_NAMES), name="agnes")
VISION_POOL = AIPool(keys=load_keys(VISION_KEY_ENV_NAMES), name="vision")


def pool_for(*, vision: bool = False) -> AIPool:
    """画面说明走视觉池，文本 JSON 走 AI 池。"""
    return VISION_POOL if vision else AI_POOL
