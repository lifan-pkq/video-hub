"""Optional LLM keep/drop ranking over an already built candidate list."""

from __future__ import annotations

import json

from engine.errors import EngineError
from engine.llm import complete_json
from engine.schemas import RankResponse
from engine.signals import DROP_THRESHOLD, apply_signal_plan

SYSTEM = """你是剪辑师。你只能裁决给定的删除候选，不能发明新的时间段。
输出严格 JSON：{"decisions":[{"id":"候选id","action":"drop","reason":"不超过40字"}]}
action 只能是 drop 或 keep。
原则：黑场、明显静音、与口播无关的长停顿标 drop；删掉会伤到句子的口头禅标 keep。
理由必须来自候选证据，不要编造画面。每个候选恰好一条。"""


def rank(suggestions: list[dict], *, instruction: str, duration: float, use_llm: bool) -> dict:
    drops = [item for item in suggestions if item.get("action") == "drop"]
    if not use_llm:
        return {
            "source": "signal",
            "drop_threshold": DROP_THRESHOLD,
            "suggestions": apply_signal_plan(suggestions),
            "warnings": ["未调用模型。删除建议只按静音、黑场、停顿和口头禅的测量值排序。"],
        }
    if not drops:
        return {
            "source": "llm",
            "drop_threshold": DROP_THRESHOLD,
            "suggestions": apply_signal_plan(suggestions),
            "warnings": ["没有可裁决的删除候选。镜头切点仍只作标记。"],
        }
    compact = [
        {
            "id": item["id"],
            "start": item["start"],
            "end": item["end"],
            "kind": item["kind"],
            "score": item["score"],
            "evidence": item.get("evidence") or item.get("reason"),
            "text": (item.get("text") or "")[:80],
        }
        for item in drops[:40]
    ]
    payload = json.dumps({
        "duration": round(duration, 2),
        "instruction": (instruction or "")[:400],
        "candidates": compact,
    }, ensure_ascii=False)
    parsed = complete_json(system=SYSTEM, user=payload, model=RankResponse, timeout=60)
    by_id = {item.id: item for item in parsed.decisions}
    warnings: list[str] = []
    known = {item["id"] for item in drops}
    for decision in parsed.decisions:
        if decision.id not in known:
            warnings.append(f"模型返回了未知候选 {decision.id}，已忽略")
    updated = []
    for item in suggestions:
        copy = dict(item)
        if copy.get("action") != "drop":
            copy["recommended"] = False
            updated.append(copy)
            continue
        decision = by_id.get(copy["id"])
        if decision is None:
            copy["recommended"] = float(copy.get("score") or 0) >= DROP_THRESHOLD
            copy["llm_action"] = None
            warnings.append(f"{copy['id']} 模型没有裁决，仍按信号分数 {copy['score']}")
        else:
            copy["llm_action"] = decision.action
            copy["recommended"] = decision.action == "drop"
            if decision.reason:
                copy["reason"] = decision.reason[:80]
        updated.append(copy)
    if not any(item.get("llm_action") for item in updated):
        raise EngineError(
            "模型没有裁决任何已知候选",
            code="llm_schema",
            action="重试精排。时间线仍保留刚才的信号建议。",
            status=422,
        )
    return {
        "source": "llm",
        "drop_threshold": DROP_THRESHOLD,
        "suggestions": updated,
        "warnings": warnings,
    }
