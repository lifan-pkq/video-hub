"""陈泽剪辑方式的自动路径：事件单元、候选、以及知识包允许的规则。

默认不走这里。身份对不上、规则没打开、或完整单元放不进时长时，都不产出定稿。
"""

from __future__ import annotations

import re

from app.config import douyin_settings
from app.services.chenze_fit import evaluate, fit_label
from app.services.chenze_pack import auto_enabled, load_columns, load_identity, load_rules, pack_version
from app.services.douyin_plan import build_captions, clip_budget, plan_douyin_shorts

COLUMNS = ("综艺", "评审", "游戏", "通用保真")
PROMPT_WORDS = ("规则", "题面", "如果", "要是", "除非", "假如", "只要")
NEGATION_WORDS = ("不是", "不要", "不能", "没有", "别", "并非", "不会", "不行")
REBUTTAL_WORDS = ("但是", "可是", "不过", "然而", "不对", "不同意", "不是这样")
PAYOFF_WORDS = ("所以", "因此", "结果", "最后", "总之", "结论")
OPEN_CONDITION = ("如果", "要是", "除非", "假如")
CONDITION_HELD = ("所以", "那就", "就要", "结果", "因此")


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _roles(text: str) -> set[str]:
    roles: set[str] = set()
    if any(word in text for word in PROMPT_WORDS):
        roles.add("prompt")
    if any(word in text for word in NEGATION_WORDS):
        roles.add("negation")
    if any(word in text for word in REBUTTAL_WORDS):
        roles.add("rebuttal")
    if any(word in text for word in PAYOFF_WORDS):
        roles.add("payoff")
    return roles


def _normalize_homepage(url: str) -> str:
    return str(url or "").strip().split("?")[0].rstrip("/")


def match_identity(observed: dict, anchor: dict | None = None) -> dict:
    """只有展示名、带尾点的抖音号、已核主页三者一致才是本人。"""
    anchor = anchor or load_identity()
    name = str(observed.get("display_name") or "").strip()
    douyin_id = str(observed.get("douyin_id") or "").strip()
    homepage = _normalize_homepage(str(observed.get("homepage_url") or ""))
    verified_name = str(anchor["display_name"])
    verified_id = str(anchor["douyin_id"])
    verified_home = _normalize_homepage(str(anchor["homepage_url"]))
    verified_uid = str(anchor.get("homepage_sec_uid") or "")
    result = {"is_self": False, "display_name": name, "douyin_id": douyin_id, "homepage_url": homepage}

    for other in anchor.get("not_self") or []:
        other_uid = str(other.get("homepage_sec_uid") or "")
        other_home = _normalize_homepage(str(other.get("homepage_url") or ""))
        if homepage and ((other_uid and other_uid in homepage) or (other_home and homepage == other_home)):
            result["reason"] = "主页属于同名另一账号，不是已核本人"
            return result

    if douyin_id != verified_id:
        same_digits = douyin_id.replace(".", "") == verified_id.replace(".", "") and bool(douyin_id)
        if same_digits:
            result["reason"] = f"抖音号必须是 {verified_id}（保留尾点），不能去掉尾点归并"
        elif not douyin_id:
            result["reason"] = "没有抖音号，同名或转载不能当成本人"
        else:
            result["reason"] = f"抖音号不是已核本人 {verified_id}"
        return result
    if name != verified_name:
        result["reason"] = "没有展示名，不能只靠号码当成本人" if not name else f"展示名不是已核本人「{verified_name}」"
        return result
    if not homepage or (verified_uid not in homepage and homepage != verified_home):
        result["reason"] = "主页不是已核本人主页"
        return result
    result["is_self"] = True
    result["reason"] = "展示名、抖音号和主页与已核账号一致"
    return result


def detect_column(text: str, requested: str, navigation: dict | None = None) -> dict:
    navigation = navigation or load_columns()
    requested = str(requested or "").strip()
    if requested in COLUMNS:
        return {"column": requested, "source": "user", "conflict": False, "hits": [requested]}
    hits = []
    for name, words in (navigation.get("detect") or {}).items():
        if name not in COLUMNS:
            continue
        if any(word and word in text for word in words):
            hits.append(name)
    if len(hits) == 1:
        return {"column": hits[0], "source": "auto", "conflict": False, "hits": hits}
    if len(hits) > 1:
        return {"column": "通用保真", "source": "conflict", "conflict": True, "hits": hits}
    return {"column": "通用保真", "source": "auto", "conflict": False, "hits": []}


def partition_rules(text: str, column: str, rules: list[dict], navigation: dict | None = None) -> dict:
    """栏目不对的规则不执行。没把 enabled 写成 true 的规则只记录，不自动打开。"""
    navigation = navigation or load_columns()
    specs = {item["rule_id"]: item for item in navigation.get("assignments") or [] if item.get("rule_id")}
    applied: list[str] = []
    skipped: list[str] = []
    blocked: list[str] = []
    for rule in rules:
        rule_id = str(rule.get("rule_id") or "")
        spec = specs.get(rule_id)
        if not spec:
            continue
        keywords = [word for word in (spec.get("keywords") or []) if word]
        if not any(word in text for word in keywords):
            continue
        kind = str(rule.get("rule_kind") or "")
        allowed = list(spec.get("columns") or [])
        derivative = kind == "derivative_creator_method_candidate" or not allowed
        if derivative:
            blocked.append(rule_id)
            continue
        if column == "通用保真" or column not in allowed:
            blocked.append(rule_id)
            continue
        if rule.get("enabled_for_automatic_editing") is True:
            applied.append(rule_id)
        else:
            skipped.append(rule_id)
    return {
        "applied": applied,
        "skipped_disabled": skipped,
        "blocked_cross_column": blocked,
    }


def detect_event_units(segments: list[dict]) -> list[dict]:
    """把条件、否定、反驳、同伴接话和收束留在同一个事件里，不为了短而拆开。"""
    prepared = []
    for segment in segments:
        start = _finite(segment.get("start"))
        end = _finite(segment.get("end"))
        if start is None or end is None or end <= start:
            continue
        text = str(segment.get("text") or "").strip()
        speaker = str(segment.get("speaker") or "")
        prepared.append({
            "start": start,
            "end": end,
            "text": text,
            "speaker": speaker,
            "roles": _roles(text),
        })
    if not prepared:
        return []
    groups: list[list[dict]] = [[prepared[0]]]
    for segment in prepared[1:]:
        current = groups[-1]
        previous = current[-1]
        gap = segment["start"] - previous["end"]
        join = False
        incoming = dict(segment)
        incoming["roles"] = set(segment["roles"])
        if 0 <= gap <= 3.0:
            if "rebuttal" in incoming["roles"] or "payoff" in incoming["roles"]:
                join = True
            if "prompt" in previous["roles"] or "negation" in previous["roles"]:
                join = True
            previous_speaker = previous["speaker"]
            this_speaker = incoming["speaker"]
            known = (
                previous_speaker
                and this_speaker
                and "unknown" not in previous_speaker
                and "unknown" not in this_speaker
            )
            speakers = {item["speaker"] for item in current if item["speaker"]}
            if known and previous_speaker != this_speaker and len(speakers) < 2:
                join = True
                incoming["roles"].add("cohost")
        if join:
            current.append(incoming)
        else:
            groups.append([incoming])
    return [_finish_unit(group) for group in groups]


def _finish_unit(group: list[dict]) -> dict:
    roles: set[str] = set()
    for item in group:
        roles |= set(item["roles"])
    return {
        "start": group[0]["start"],
        "end": group[-1]["end"],
        "text": "".join(item["text"] for item in group),
        "roles": roles,
        "segments": group,
    }


def choose_span(unit: dict, limit) -> dict:
    """事件单元是一整段。放不进上限就 NO_FIT，不把句尾保护之后再截到上限。"""
    start = unit["start"]
    end = unit["end"]
    verdict = evaluate(start, end, limit)
    chosen = {
        "start": start,
        "end": end,
        "text": unit["text"],
        "automatic_trim": False,
        "publication_approved": False,
        "fit_label": None,
        "duration_seconds": verdict.get("duration_seconds"),
        "limit_seconds": verdict.get("limit_seconds") or str(limit),
        "roles": set(unit.get("roles") or []),
        "segments": unit["segments"],
    }
    if verdict["status"] == "INVALID_INTERVAL":
        return {**chosen, "status": "pending", "reason": verdict["reason"]}
    if verdict["status"] == "REJECT_OVER_LIMIT":
        return {**chosen, "status": "NO_FIT", "reason": verdict["reason"]}
    return {
        **chosen,
        "status": "pending",
        "reason": verdict["reason"],
        "fit_label": fit_label(start, end, limit),
    }


def needs_context(unit: dict) -> str | None:
    text = unit.get("text") or ""
    if "弹幕" in text:
        return "提到弹幕，但没有可核对的弹幕文本"
    open_condition = any(word in text for word in OPEN_CONDITION)
    held = any(word in text for word in CONDITION_HELD)
    if open_condition and not held and "payoff" not in set(unit.get("roles") or []):
        return "规则或条件没有接到后文"
    return None


def rights_notes(text: str, *, width: int | None, height: int | None, low_res: bool) -> list[str]:
    notes = []
    short_side = None
    if width and height:
        short_side = min(int(width), int(height))
    if low_res or (short_side is not None and short_side < 480):
        notes.append("低清不视为已脱敏或已授权")
    if any(word in text for word in ("诊断", "癌症", "患病", "生病")):
        notes.append("疾病措辞不作真人诊断，也不视为隐私已清除")
    if "假设" in text and "老年" in text:
        notes.append("节目里的假设或表演不是真人经历，也不视为授权已清除")
    return notes


def _review_item(item: dict, *, preview: bool) -> dict:
    return {
        "status": item["status"],
        "start": item["start"],
        "end": item["end"],
        "text": str(item.get("text") or "")[:180],
        "reason": item.get("reason") or "",
        "column": item.get("column") or "",
        "fit_label": item.get("fit_label"),
        "duration_seconds": item.get("duration_seconds"),
        "limit_seconds": item.get("limit_seconds"),
        "automatic_trim": False,
        "publication_approved": False,
        "preview": preview,
        "rights_notes": list(item.get("rights_notes") or []),
    }


def _clip_from_candidate(item: dict, segments: list[dict], index: int) -> dict:
    spoken = re.sub(r"\s+", "", item.get("text") or "")
    title = spoken[:22] or f"候选 {index}"
    return {
        "start": item["start"],
        "end": item["end"],
        "score": 3,
        "hook": "",
        "reason": item.get("reason") or "",
        "title": title,
        "hashtags": [],
        "description": ("待人工复核，不是定稿。" + str(item.get("reason") or ""))[:200],
        "captions": build_captions(segments, float(item["start"]), float(item["end"])),
        "review_status": "pending",
        "publication_approved": False,
        "fit_label": item.get("fit_label"),
        "column": item.get("column") or "",
    }


def run_chenze_pipeline(
    segments: list[dict],
    duration: float,
    *,
    instruction: str = "",
    max_clips: int = 0,
    limit: float | None = None,
    identity: dict,
    column: str = "",
    rules: list[dict] | None = None,
    low_res: bool = False,
    width: int | None = None,
    height: int | None = None,
    log=print,
) -> dict:
    rules = load_rules() if rules is None else rules
    navigation = load_columns()
    if limit is None:
        limit_value = float(douyin_settings()["max_seconds"])
        limit_source = "product_cap"
    else:
        limit_value = float(limit)
        limit_source = "request"
    transcript = "\n".join(str(segment.get("text") or "") for segment in segments)
    text = f"{instruction}\n{transcript}"
    column_info = detect_column(text, column, navigation)
    partitions = partition_rules(text, column_info["column"], rules, navigation)
    log(f"陈泽剪辑方式：栏目 {column_info['column']}，时长上限 {limit_value:g} 秒")
    units = detect_event_units(segments)
    candidates = []
    for unit in units:
        chosen = choose_span(unit, limit_value)
        status = chosen["status"]
        reason = chosen["reason"]
        if status != "NO_FIT":
            hold = needs_context(chosen)
            if column_info.get("conflict"):
                status = "context_required"
                reason = "同一条素材同时像多个栏目，只走通用保真，不套栏目规则"
            elif hold:
                status = "context_required"
                reason = hold
            else:
                status = "pending"
                reason = "时间落在上限内，转写和风格仍待人工复核"
        notes = rights_notes(chosen.get("text") or "", width=width, height=height, low_res=low_res)
        blocking = [note for note in notes if not note.startswith("低清")]
        if blocking and status == "pending":
            status = "context_required"
            reason = blocking[0]
        candidates.append({
            **chosen,
            "status": status,
            "reason": reason,
            "column": column_info["column"],
            "rights_notes": notes,
            "publication_approved": False,
            "fit_label": chosen.get("fit_label") if status == "pending" else None,
        })

    budget = clip_budget(float(duration), max_clips or None)
    pending = [item for item in candidates if item["status"] == "pending"]
    previews = pending[:budget]
    preview_ids = {id(item) for item in previews}
    overflow_ids = {id(item) for item in pending[budget:]}
    review_source = []
    for item in candidates:
        if id(item) in preview_ids:
            review_source.append(_review_item(item, preview=True))
        elif id(item) in overflow_ids:
            copied = dict(item)
            copied["reason"] = (str(item["reason"]) + "；超过本次条数，仍待复核").strip("；")
            review_source.append(_review_item(copied, preview=False))
        else:
            review_source.append(_review_item(item, preview=False))

    clips = []
    for index, item in enumerate(previews, start=1):
        clip = _clip_from_candidate(item, segments, index)
        clip["index"] = index
        clip["duration"] = round(float(clip["end"]) - float(clip["start"]), 2)
        clips.append(clip)

    no_fit = sum(1 for item in candidates if item["status"] == "NO_FIT")
    missing_context = sum(1 for item in candidates if item["status"] == "context_required")
    warnings = [
        "已自动跑陈泽剪辑流程：按事件单元挑选候选。个人风格尚未人工验收，这些都不是定稿。",
        "知识包里没有明确开启的规则不会自动执行。",
    ]
    if no_fit:
        warnings.append(f"{no_fit} 条完整单元放不进时长，已标 NO_FIT，没有硬截。")
    if missing_context:
        warnings.append(f"{missing_context} 条缺语境或不能当已授权，已标 context_required。")
    if not partitions["applied"]:
        warnings.append("本次没有自动启用任何风格规则。")
    enabled = [rule["rule_id"] for rule in auto_enabled(rules)]
    return {
        "source": "chenze",
        "budget": budget,
        "min_seconds": 0,
        "max_seconds": limit_value,
        "warnings": warnings,
        "clips": clips,
        "chenze": {
            "requested": True,
            "applied": True,
            "automation": "event_unit",
            "pack_id": "chenze-knowledge-v1",
            "pack_version": pack_version(),
            "identity": identity,
            "column": column_info["column"],
            "column_source": column_info["source"],
            "limit_seconds": str(limit_value),
            "limit_source": limit_source,
            "applied_rule_ids": partitions["applied"],
            "skipped_disabled_rule_ids": partitions["skipped_disabled"],
            "blocked_cross_column_rule_ids": partitions["blocked_cross_column"],
            "enabled_rule_ids_in_pack": enabled,
            "review_candidates": review_source,
            "publication_approved": False,
            "validated_person_style": False,
            "authorization_cleared": False,
        },
    }


def plan_intelligent_edit(
    segments: list[dict],
    duration: float,
    *,
    instruction: str = "",
    max_clips: int = 0,
    min_seconds: float | None = None,
    max_seconds: float | None = None,
    chenze_style: bool = False,
    chenze_options: dict | None = None,
    video: dict | None = None,
    rules: list[dict] | None = None,
    width: int | None = None,
    height: int | None = None,
    request_ai=None,
    log=print,
) -> dict:
    """关闭时原样走现在的抖音选段。打开且身份核上才走陈泽自动路径。"""
    if not chenze_style:
        return plan_douyin_shorts(
            segments,
            duration,
            instruction=instruction,
            max_clips=max_clips,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            request_ai=request_ai,
            log=log,
        )
    options = chenze_options or {}
    homepage = str(options.get("homepage_url") or "").strip()
    if not homepage and video:
        homepage = str(video.get("source_url") or "")
    identity = match_identity({
        "display_name": options.get("display_name") or "",
        "douyin_id": options.get("douyin_id") or "",
        "homepage_url": homepage,
    })
    if not identity["is_self"]:
        plan = plan_douyin_shorts(
            segments,
            duration,
            instruction=instruction,
            max_clips=max_clips,
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            request_ai=request_ai,
            log=log,
        )
        plan["chenze"] = {
            "requested": True,
            "applied": False,
            "automation": None,
            "pack_id": "chenze-knowledge-v1",
            "pack_version": pack_version(),
            "identity": identity,
            "applied_rule_ids": [],
            "skipped_disabled_rule_ids": [],
            "blocked_cross_column_rule_ids": [],
            "review_candidates": [],
            "publication_approved": False,
            "validated_person_style": False,
            "authorization_cleared": False,
        }
        plan["warnings"] = [*(plan.get("warnings") or []), f"未按陈泽本人剪辑：{identity['reason']}"]
        return plan
    requested_limit = options.get("max_seconds")
    if requested_limit is None:
        requested_limit = max_seconds
    return run_chenze_pipeline(
        segments,
        duration,
        instruction=instruction,
        max_clips=max_clips,
        limit=None if requested_limit is None else float(requested_limit),
        identity=identity,
        column=str(options.get("column") or ""),
        rules=rules,
        low_res=bool(options.get("low_res")),
        width=width,
        height=height,
        log=log,
    )
