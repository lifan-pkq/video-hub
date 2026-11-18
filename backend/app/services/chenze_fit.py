"""时长质检。逻辑直接复用知识包 v1 的 fit_qc.py，不在这里改判定。

质检只判断区间，不裁媒体，也不批准发布。
"""

from __future__ import annotations

import importlib.util
from decimal import Decimal

from app.services.chenze_pack import pack_dir


def _load_pack_qc():
    path = pack_dir() / "fit_qc.py"
    spec = importlib.util.spec_from_file_location("chenze_pack_fit_qc_v1", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载时长质检：{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate = _load_pack_qc().evaluate


def fit_label(start, end, limit, **flags) -> str | None:
    """只有质检本身返回 FIT 且时长不超过上限时才给 FIT 标签。"""
    verdict = evaluate(start, end, limit, **flags)
    if verdict.get("status") != "FIT":
        return None
    duration = Decimal(verdict["duration_seconds"])
    cap = Decimal(str(limit))
    if duration > cap:
        return None
    if cap == cap.to_integral_value():
        return f"FIT{int(cap)}"
    return "FIT"
