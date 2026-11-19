"""读取仓库里的陈泽知识包 v1。不会把未开启的规则改成自动生效。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


def pack_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "knowledge" / "chenze" / "v1"


def _read_json(name: str) -> dict:
    return json.loads((pack_dir() / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_status() -> dict:
    return _read_json("current-status.json")


@lru_cache(maxsize=1)
def load_identity() -> dict:
    return _read_json("identity.json")


@lru_cache(maxsize=1)
def load_columns() -> dict:
    return _read_json("columns.json")


@lru_cache(maxsize=1)
def _load_rules() -> tuple[dict, ...]:
    data = _read_json("rules-draft.json")
    rules = data.get("rules") or []
    frozen = []
    for item in rules:
        if isinstance(item, dict):
            frozen.append(json.dumps(item, ensure_ascii=False))
    return tuple(frozen)


def load_rules() -> list[dict]:
    """每次返回新副本，调用方改开关不会写回知识包。"""
    return [json.loads(item) for item in _load_rules()]


def pack_version() -> str:
    return str(load_status().get("version") or "chenze-knowledge-v1")


def auto_enabled(rules: list[dict] | None = None) -> list[dict]:
    chosen = load_rules() if rules is None else rules
    return [rule for rule in chosen if rule.get("enabled_for_automatic_editing") is True]
