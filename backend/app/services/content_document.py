"""整段录制的可读内容分析文档。

文本模型和视觉模型都没配时，仍然用本机转写、镜头和静音写出文档。
口播只是转写草稿，不把难句说成已经听对。陈泽开关只在身份核上时
给文档加风格参考，不会打开知识包里那 28 条个人风格规则。
"""

from __future__ import annotations

from typing import Any

from app.services.chenze_pack import auto_enabled, load_rules
from app.services.chenze_pipeline import detect_event_units, match_identity

ASR_LIMIT = "口播是本机转写草稿。难句、专名和口误仍可能错，这里没有解决，也没有人工听审。"
STYLE_LABEL = "风格参考（按知识包的事件单元整理，不是听记结论，也不是已经人工接受的个人风格）"
NOT_SELF_LABEL = "已勾选陈泽剪辑方式，但账号与已核本人不一致，因此没有套用陈泽风格。"


def _clock(seconds: float | None) -> str:
    try:
        total = max(0, int(round(float(seconds or 0))))
    except (TypeError, ValueError):
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _range_line(start: float, end: float, text: str) -> str:
    return f"- {_clock(start)}–{_clock(end)} {text}".rstrip()


def local_structure(
    duration: float,
    speech: list[dict],
    cuts: list[float],
    silence: list[dict] | None = None,
) -> dict[str, Any]:
    """没有文本模型时也能给出结构、人物/场景说明和建议切点。"""
    duration = max(0.0, float(duration or 0))
    silence = silence or []
    sections = _sections(duration, speech, cuts)
    cuts_out = _suggested_cuts(duration, speech, cuts, silence)
    sentences = len(speech)
    summary = (
        f"本机结构分析：约 {_clock(duration)}，口播 {sentences} 句，"
        f"镜头切换 {len(cuts)} 处。完整转写和切点在这份文档里。"
        "没有使用文本模型，也没有人工听审。"
    )
    return {
        "summary": summary,
        "people": [],
        "scenes": [],
        "emotion_tags": [],
        "best_segments": [item for item in cuts_out if item.get("kind") == "keep"],
        "structure": sections,
        "emotion_curve": [],
        "suggested_cuts": cuts_out,
        "source": "local",
    }


def chenze_notice(video: dict | None, speech: list[dict]) -> dict[str, Any]:
    """身份对不上就不套用风格。对上了也只标成风格参考，不改规则开关。"""
    video = video or {}
    requested = bool(video.get("chenze_style"))
    rules = load_rules()
    enabled = [str(rule.get("rule_id") or "") for rule in auto_enabled(rules) if rule.get("rule_id")]
    base = {
        "requested": requested,
        "style_informed": False,
        "treated_as_self": False,
        "validated_person_style": False,
        "listening_conclusion": False,
        "human_accepted_style": False,
        "applied_rule_ids": [],
        "enabled_rule_ids": enabled,
        "rule_count": len(rules),
        "units": [],
        "reason": "",
        "label": "未选择陈泽剪辑方式。",
    }
    if not requested:
        return base
    options = video.get("chenze") if isinstance(video.get("chenze"), dict) else {}
    identity = match_identity({
        "display_name": options.get("display_name") or "",
        "douyin_id": options.get("douyin_id") or "",
        "homepage_url": options.get("homepage_url") or "",
    })
    base["identity"] = {
        "is_self": bool(identity.get("is_self")),
        "reason": identity.get("reason") or "",
        "display_name": identity.get("display_name") or "",
        "douyin_id": identity.get("douyin_id") or "",
    }
    if not identity.get("is_self"):
        base["reason"] = str(identity.get("reason") or "身份未核验")
        base["label"] = f"{NOT_SELF_LABEL}{base['reason']}。"
        return base
    units = []
    for unit in detect_event_units(speech):
        start = _num(unit.get("start"))
        end = _num(unit.get("end"))
        if start is None or end is None or end <= start:
            continue
        roles = sorted(str(role) for role in (unit.get("roles") or []) if role)
        units.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "text": str(unit.get("text") or "")[:240],
            "roles": roles,
            "label": STYLE_LABEL,
        })
    base.update({
        "style_informed": True,
        "treated_as_self": True,
        "reason": str(identity.get("reason") or ""),
        "label": STYLE_LABEL,
        "units": units,
        "applied_rule_ids": [],
    })
    return base


def compose_document(video: dict | None, analysis: dict) -> tuple[str, dict[str, Any]]:
    video = video or {}
    speech = [item for item in (analysis.get("tracks") or {}).get("speech") or [] if isinstance(item, dict)]
    duration = _num(analysis.get("duration")) or 0.0
    summary_source = str((analysis.get("sources") or {}).get("summary") or "local")
    visual_source = str((analysis.get("sources") or {}).get("visual") or "skipped")
    chenze = analysis.get("chenze_notice")
    if not isinstance(chenze, dict):
        chenze = chenze_notice(video, speech)
    suggested = analysis.get("suggested_cuts") or []
    limitations = _limitations(summary_source, visual_source)
    lines = [
        f"# 内容分析：{video.get('title') or '未命名录制'}",
        "",
        "这是整段录制的阅读稿，覆盖这一条视频，不是其中一小段 JSON，也不是人工听审结论。",
        "",
        f"- 时长：{_clock(duration)}",
    ]
    if video.get("source_url"):
        lines.append(f"- 来源：{video.get('source_url')}")
    lines.append(f"- 文本：{'已使用文本模型，仍需人工核对' if summary_source == 'model' else '未使用文本模型，结构来自本机转写、镜头和静音'}")
    lines.append(f"- 画面：{'已使用视觉模型，仍需人工核对' if visual_source == 'model' else '未使用视觉模型，不能从画面识别人物和场景'}")
    lines.extend(["", "## 说明", ""])
    for item in limitations:
        lines.append(f"- {item}")
    lines.extend(["", "## 口播转写", ""])
    if speech:
        for item in speech:
            start = _num(item.get("start")) or 0
            end = _num(item.get("end")) or start
            text = str(item.get("text") or "").strip() or "（空）"
            lines.append(_range_line(start, end, text))
    else:
        lines.append("本机转写没有得到可用句子。没有句子时，下面的结构只依据镜头和静音，不编造口播。")
    lines.extend(["", "## 结构", ""])
    structure = [item for item in (analysis.get("structure") or []) if isinstance(item, dict)]
    if structure:
        source_note = "文本模型给出的结构，需要人工核对，不是听记结论。" if summary_source == "model" else "按镜头切换和口播空隙整理的结构，不是听记结论。"
        lines.append(source_note)
        lines.append("")
        for item in structure:
            start = _num(item.get("start")) or 0
            end = _num(item.get("end")) or start
            role = str(item.get("role") or "段落")
            reason = str(item.get("reason") or "").strip()
            lines.append(_range_line(start, end, f"{role} {reason}".strip()))
    else:
        lines.append("没有可靠的段落边界。整段先当作一条。")
    lines.extend(["", "## 人物与场景", ""])
    lines.extend(_people_scene_lines(analysis, summary_source, visual_source))
    lines.extend(["", "## 建议切点", ""])
    lines.append("这些范围用来之后剪辑时参考。它们是结构建议，不是听记结论，也没有被人验收。")
    lines.append("")
    if suggested:
        for item in suggested:
            if not isinstance(item, dict):
                continue
            start = _num(item.get("start")) or 0
            end = _num(item.get("end")) or start
            kind = "建议保留" if item.get("kind") == "keep" else "可考虑切开"
            reason = str(item.get("reason") or "").strip()
            lines.append(_range_line(start, end, f"{kind}。{reason}".strip()))
    else:
        lines.append("没有足够的口播、静音或镜头边界来建议切点。")
    model_segments = [item for item in (analysis.get("best_segments") or []) if isinstance(item, dict)]
    if summary_source == "model" and model_segments:
        lines.extend(["", "文本模型另外标了这些片段，同样需要人工核对，不是听记结论。", ""])
        for item in model_segments:
            start = _num(item.get("start")) or 0
            end = _num(item.get("end")) or start
            reason = str(item.get("reason") or "").strip()
            lines.append(_range_line(start, end, reason or "模型建议保留"))
    lines.extend(["", "## 陈泽剪辑方式", ""])
    lines.extend(_chenze_lines(chenze))
    if analysis.get("warnings"):
        lines.extend(["", "## 生成时的限制", ""])
        for item in analysis.get("warnings") or []:
            lines.append(f"- {item}")
    lines.append("")
    meta = {
        "filename": f"{int(video.get('id') or analysis.get('video_id') or 0)}.md",
        "chenze": chenze,
        "suggested_cuts": suggested,
        "limitations": limitations,
        "summary_source": summary_source,
        "visual_source": visual_source,
    }
    return "\n".join(lines), meta


def _limitations(summary_source: str, visual_source: str) -> list[str]:
    notes = [ASR_LIMIT]
    if summary_source != "model":
        notes.append("未配置或未调用文本模型。结构和切点来自本机转写、镜头切换和静音，不是模型结论。")
    else:
        notes.append("文本模型的摘要和结构仍要人工核对，不能当成听记结论。")
    if visual_source != "model":
        notes.append("未配置或未调用视觉模型。不能从画面判断人物和场景。")
    else:
        notes.append("视觉模型看到的人物和场景仍要人工核对。")
    return notes


def _people_scene_lines(analysis: dict, summary_source: str, visual_source: str) -> list[str]:
    people = [str(item) for item in (analysis.get("people") or []) if str(item).strip()]
    scenes = [str(item) for item in (analysis.get("scenes") or []) if str(item).strip()]
    lines = []
    if people and (summary_source == "model" or visual_source == "model"):
        origin = "视觉模型" if visual_source == "model" and summary_source != "model" else "模型"
        lines.append(f"人物：{'、'.join(people)}。来自{origin}，需要人工核对，不是听记结论。")
    else:
        lines.append("人物：未识别。没有可用的视觉或文本模型时，不能从画面判断谁在场，转写里的称呼也不当成在场的人。")
    if scenes and (summary_source == "model" or visual_source == "model"):
        origin = "视觉模型" if visual_source == "model" and summary_source != "model" else "模型"
        lines.append(f"场景：{'、'.join(scenes)}。来自{origin}，需要人工核对，不是听记结论。")
    else:
        lines.append("场景：未识别。没有可用的视觉模型时，不能从画面判断场景。")
    return lines


def _chenze_lines(notice: dict) -> list[str]:
    lines = [str(notice.get("label") or "未选择陈泽剪辑方式。")]
    rule_count = int(notice.get("rule_count") or 0)
    enabled = notice.get("enabled_rule_ids") or []
    lines.append(
        f"知识包里的个人风格规则共 {rule_count} 条，"
        f"其中已标记为自动剪辑的有 {len(enabled)} 条。"
        "这次没有把未开启的规则改成自动生效。"
    )
    if not notice.get("requested"):
        return lines
    if not notice.get("style_informed"):
        lines.append("身份未核验为已核本人时，文档不按陈泽风格给切点。")
        return lines
    lines.append("下面的时间范围只在身份与已核账号一致时列出，供之后剪辑参考。")
    units = notice.get("units") or []
    if not units:
        lines.append("转写里没有能收成事件单元的句子，因此没有风格参考切点。")
        return lines
    for unit in units:
        roles = "、".join(unit.get("roles") or []) or "口播"
        text = str(unit.get("text") or "").strip()
        lines.append(_range_line(unit.get("start") or 0, unit.get("end") or 0, f"{STYLE_LABEL}。{roles}。{text}"))
    return lines


def _sections(duration: float, speech: list[dict], cuts: list[float]) -> list[dict]:
    points = [0.0]
    for cut in cuts:
        value = _num(cut)
        if value is None or value <= 0.3 or (duration and value >= duration - 0.3):
            continue
        points.append(round(value, 2))
    if len(points) == 1 and speech:
        points.extend(_speech_boundaries(speech, min_gap=2.5))
    if duration > 0:
        points.append(round(duration, 2))
    ordered = sorted({point for point in points if point >= 0})
    if len(ordered) < 2:
        if duration <= 0:
            return []
        return [{"start": 0, "end": round(duration, 2), "role": "整段", "reason": "没有可靠的镜头或口播边界"}]
    sections = []
    spans = list(zip(ordered, ordered[1:]))
    last = len(spans) - 1
    for index, (start, end) in enumerate(spans):
        if end - start < 0.2:
            continue
        if index == 0:
            role, reason = "开场", "这一段前面没有更早的镜头或口播空隙"
        elif index == last:
            role, reason = "收束", "这一段走到录制结束"
        else:
            role, reason = "展开", "落在两次边界之间"
        sections.append({"start": round(start, 2), "end": round(end, 2), "role": role, "reason": reason})
    return sections


def _speech_boundaries(speech: list[dict], min_gap: float) -> list[float]:
    points = []
    previous_end = None
    for item in speech:
        start = _num(item.get("start"))
        end = _num(item.get("end"))
        if start is None or end is None:
            continue
        if previous_end is not None and start - previous_end >= min_gap:
            points.append(round(previous_end, 2))
            points.append(round(start, 2))
        previous_end = end
    return points


def _suggested_cuts(duration: float, speech: list[dict], cuts: list[float], silence: list[dict]) -> list[dict]:
    usable = []
    for item in speech:
        start = _num(item.get("start"))
        end = _num(item.get("end"))
        if start is None or end is None or end <= start:
            continue
        usable.append({"start": start, "end": end, "text": str(item.get("text") or "").strip()})
    suggestions: list[dict] = []
    if usable:
        cluster = [usable[0]]
        for item in usable[1:]:
            gap_start = cluster[-1]["end"]
            gap = item["start"] - gap_start
            if gap >= 0.8:
                suggestions.append(_keep_span(cluster))
                suggestions.append({
                    "start": round(gap_start, 2),
                    "end": round(item["start"], 2),
                    "kind": "cut",
                    "reason": "口播之间有空隙，可以考虑在这里切开。这是结构建议，不是听记结论。",
                })
                cluster = [item]
            else:
                cluster.append(item)
        suggestions.append(_keep_span(cluster))
    elif silence:
        for item in silence:
            start = _num(item.get("start"))
            end = _num(item.get("end"))
            if start is None or end is None or end - start < 0.4:
                continue
            suggestions.append({
                "start": round(start, 2),
                "end": round(end, 2),
                "kind": "cut",
                "reason": "检测到一段静音，可以考虑在这里切开。这是结构建议，不是听记结论。",
            })
    elif cuts and duration > 0:
        boundaries = [0.0, *[value for value in cuts if isinstance(value, (int, float)) and 0 < value < duration], duration]
        for start, end in zip(boundaries, boundaries[1:]):
            if end - start < 0.4:
                continue
            suggestions.append({
                "start": round(float(start), 2),
                "end": round(float(end), 2),
                "kind": "keep",
                "reason": "按镜头切换分成的一段。没有口播时不能判断这句话值不值得留。",
            })
    elif duration > 0:
        suggestions.append({
            "start": 0,
            "end": round(duration, 2),
            "kind": "keep",
            "reason": "没有可靠的口播、静音或镜头边界，先把整段当作一条。",
        })
    return suggestions


def _keep_span(cluster: list[dict]) -> dict:
    text = "".join(item["text"] for item in cluster).strip()
    preview = text[:48] + ("…" if len(text) > 48 else "")
    reason = "连续口播，建议先留着。"
    if preview:
        reason = f"连续口播，建议先留着。草稿：{preview}"
    return {
        "start": round(cluster[0]["start"], 2),
        "end": round(cluster[-1]["end"], 2),
        "kind": "keep",
        "reason": reason + " 这是结构建议，不是听记结论。",
    }
