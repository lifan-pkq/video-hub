"""Standalone configuration. Do not copy this file over an existing Video Hub config."""

from __future__ import annotations

import os
from pathlib import Path

IS_STANDALONE_CONFIG = True


def data_dir() -> Path:
    configured = os.environ.get("VIDEO_HUB_DATA_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser().resolve()
    else:
        path = Path(__file__).resolve().parents[2] / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def __getattr__(name: str):
    if name == "DATA_DIR":
        return data_dir()
    if name == "VIDEOS_DIR":
        path = data_dir() / "videos"
        path.mkdir(parents=True, exist_ok=True)
        return path
    if name == "COVERS_DIR":
        path = data_dir() / "covers"
        path.mkdir(parents=True, exist_ok=True)
        return path
    raise AttributeError(name)
