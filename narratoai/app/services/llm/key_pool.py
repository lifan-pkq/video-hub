"""NarratoAI 内部多 Key 池：文本/视觉请求遇到 429 或鉴权失败时轮换。"""

from __future__ import annotations

import json
import os
import threading
import time


def _keys(*names: str) -> list[str]:
    found: list[str] = []
    for name in names:
        raw = (os.environ.get(name) or "").strip()
        if not raw:
            continue
        if raw.startswith("["):
            try:
                values = json.loads(raw)
            except ValueError:
                values = []
            if isinstance(values, list):
                for value in values:
                    text = str(value).strip()
                    if text and text not in found:
                        found.append(text)
                continue
        for value in raw.replace("\n", ",").split(","):
            text = value.strip()
            if text and text not in found:
                found.append(text)
    return found


class KeyPool:
    def __init__(self, *env_names: str):
        self.keys = _keys(*env_names)
        self._lock = threading.RLock()
        self._index = 0
        self._cooldown: dict[str, float] = {}

    def available(self) -> list[str]:
        now = time.time()
        with self._lock:
            return [key for key in self.keys if self._cooldown.get(key, 0) <= now]

    def acquire(self, fallback: str | None = None) -> str | None:
        now = time.time()
        with self._lock:
            if not self.keys:
                return fallback or None
            for offset in range(len(self.keys)):
                index = (self._index + offset) % len(self.keys)
                key = self.keys[index]
                if self._cooldown.get(key, 0) <= now:
                    self._index = (index + 1) % len(self.keys)
                    return key
        return fallback or None

    def success(self, key: str | None) -> None:
        if not key:
            return
        with self._lock:
            self._cooldown.pop(key, None)

    def failure(self, key: str | None, status: int | None = None, retry_after: float | None = None) -> None:
        if not key:
            return
        now = time.time()
        if status in (401, 403):
            delay = 3600
        elif status == 429:
            delay = retry_after or 60
        elif status and status >= 500:
            delay = 30
        else:
            delay = 10
        with self._lock:
            self._cooldown[key] = now + delay


VISION_POOL = KeyPool("VIDEO_HUB_VISION_API_KEYS", "AGNES_API_KEYS", "VIDEO_HUB_AI_API_KEYS")
TEXT_POOL = KeyPool("VIDEO_HUB_AI_API_KEYS", "AGNES_API_KEYS", "VIDEO_HUB_VISION_API_KEYS")
