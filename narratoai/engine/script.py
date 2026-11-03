"""通用解说脚本生成：内容 Profile 规划中立的解说，主播人格负责二次改写。

NarratoAI 自带的解说提示词偏"病毒式影视解说"。深度融合后，这里提供一套
面向任意视频的脚本生成：先按素材生成事实与时间轴，再按可选人格调整表达，
人格改写不改变片段结构。
"""

from __future__ import annotations

import json
import re
from typing import Any

from engine.personas import (
    PERSONA_PROFILES,
    PERSONA_STRENGTHS,
    build_persona_instruction,
    resolve_persona,
    resolve_persona_strength,
)
from engine.profiles import build_creative_context

SYSTEM_PROMPT = (
    "你是一名中文短视频解说编导。你擅长从画面、口播或操作过程中提炼亮点和叙事，"
    "让观众在短时间内理解发生了什么、为什么值得看。你不编造事实，也不写空洞套话或看图说话。"
)

TEMPLATE = """请根据下面的素材，写一条可直接配音的中文解说脚本。

## 创作设定
- 内容类型：{label}
- 视频主题：{theme}
- 目标时长：约 {duration} 秒
- 使用语言：{language}
{instruction}

## 写作规则
1. 只使用素材中确实出现的信息；不确定的内容不要补充，不要虚构人物、数字或因果。
2. 按时间顺序分段，每段对应素材里的一个时间范围；段与段自然衔接。
3. 每段 1-2 句、约 15-45 字，口语化，适合直接朗读；使用自然句末标点，复合句用逗号划分意群，每个意群尽量不超过 16 字。
4. 不要写"大家好""欢迎来到我的频道""记得点赞关注"之类的开场/结尾套话。
5. 不要逐镜头流水账，也不要重复观众已经看到的性别、动作、滤镜等表面细节；优先提炼亮点、反差、上下文和结果。表面动作只在推动叙事时提及，没有依据时不要猜测原因。
6. 第一段直接进入内容，不要铺垫。
7. 每段的 timestamp 必须从素材中原样选取，格式为 "HH:MM:SS,mmm-HH:MM:SS,mmm"。

## 素材
{material}

## 输出
只输出 JSON，不要代码块或解释：
{{
  "items": [
    {{
      "_id": 1,
      "timestamp": "00:00:00,000-00:00:05,000",
      "picture": "这一段的画面/内容摘要",
      "narration": "这一段的解说词"
    }}
  ]
}}
"""

PERSONA_TEMPLATE = """下面是一条已经按画面和时间轴写好的中立解说稿。请只改写法，不改内容。

## 主播人格与强度
{instruction}

## 硬性规则
1. 保留每段原有事实、人物、动作、数字和因果关系，不新增素材里没有的信息。
2. 不改变片段数量与 _id；每段仍保持 1-2 句，长度与原段接近，适合直接配音。
3. 不输出 timestamp 或 picture，不讨论人设，不写“以下是改写”等解释。
4. 不要每段都使用口头禅，不要重复语录，不要用粗口、人身攻击或真人自我介绍。

## 中立解说稿
{items}

## 输出
只输出 JSON，不要代码块：
{{
  "items": [
    {{"_id": 1, "narration": "改写后的解说词"}}
  ]
}}
"""


class ScriptGenerationError(RuntimeError):
    pass


def build_markdown_from_transcript(transcript: str) -> str:
    """把用户文稿/字幕整理成按时间分段的素材。"""
    text = str(transcript or "").strip()
    if not text:
        raise ScriptGenerationError("文稿为空")
    # 已经带时间轴的 SRT/时间戳文本直接使用；纯文本按行编号。
    if re.search(r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->", text):
        blocks = []
        for block in re.split(r"\n\s*\n", text):
            lines = [line.strip() for line in block.splitlines() if line.strip()]
            if len(lines) >= 2 and "-->" in lines[1]:
                blocks.append(f"- {lines[1].replace(' --> ', '-')}：{' '.join(lines[2:])}")
        if blocks:
            return "# 视频口播字幕\n" + "\n".join(blocks)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "# 视频口播文稿\n" + "\n".join(f"- 第 {index + 1} 段：{line}" for index, line in enumerate(lines))


def _text_settings() -> tuple[str, str, list[str]]:
    from app.config import config
    from app.services.llm.key_pool import TEXT_POOL

    provider = str(config.app.get("text_llm_provider", "openai") or "openai").lower()
    api_key = str(config.app.get(f"text_{provider}_api_key") or "").strip()
    model = str(config.app.get(f"text_{provider}_model_name") or "").strip()
    base_url = str(config.app.get(f"text_{provider}_base_url") or "").strip()
    if not model:
        raise ScriptGenerationError("未配置文本模型名，请先在“AI 模型设置”中填写")
    keys = TEXT_POOL.available() or ([api_key] if api_key else [])
    if not keys:
        raise ScriptGenerationError("未配置文本模型 Key，请先在“AI 模型设置”中填写")
    return model, base_url, keys


def _chat_text(model: str, base_url: str, keys: list[str], messages: list[dict], log) -> str:
    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover
        raise ScriptGenerationError("缺少 openai 依赖，无法生成解说脚本") from exc
    from app.services.llm.key_pool import TEXT_POOL

    last_error: Exception | None = None
    for key in keys:
        client = OpenAI(api_key=key, base_url=base_url or None, timeout=180, max_retries=0)
        try:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.8,
                    response_format={"type": "json_object"},
                )
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if status in (401, 403, 429) or (status and status >= 500):
                    TEXT_POOL.failure(key, status)
                    last_error = exc
                    log(f"文本 Key 不可用（HTTP {status}），切换下一个")
                    continue
                log(f"JSON 模式不可用（{exc}），改用普通模式重试")
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.8,
                )
            content = response.choices[0].message.content if response.choices else ""
            if not content:
                raise ScriptGenerationError("文本模型返回空内容")
            TEXT_POOL.success(key)
            return content
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in (401, 403, 429) or (status and status >= 500):
                TEXT_POOL.failure(key, status)
                last_error = exc
                continue
            raise
    raise ScriptGenerationError(f"所有文本 Key 都不可用：{last_error or '未知错误'}")


def _extract_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    candidates = [text]
    fence = chr(96) * 3
    block = re.search(re.escape(fence) + r"(?:json)?\s*(.*?)\s*" + re.escape(fence), text, re.DOTALL)
    if block:
        candidates.append(block.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            continue
    raise ScriptGenerationError("模型返回的不是合法 JSON")


def _clock(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole = int(secs)
    millis = int(round((secs - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def _parse_clock(value: str) -> float:
    value = str(value or "").strip().replace(",", ".")
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(value)
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def _timestamp_range(value: str, fallback_start: float, fallback_end: float) -> str:
    text = str(value or "").strip()
    parts = re.split(r"\s*[-~—]\s*", text, maxsplit=1)
    if len(parts) == 2:
        try:
            start, end = _parse_clock(parts[0]), _parse_clock(parts[1])
            if end > start:
                return f"{_clock(start)}-{_clock(end)}"
        except (ValueError, IndexError):
            pass
    if len(parts) == 1:
        try:
            start = _parse_clock(parts[0])
            return f"{_clock(start)}-{_clock(start + 5)}"
        except (ValueError, IndexError):
            pass
    return f"{_clock(fallback_start)}-{_clock(fallback_end)}"


def _normalize_items(payload: dict[str, Any], duration: float) -> list[dict[str, Any]]:
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list) or not raw_items:
        raise ScriptGenerationError("模型没有返回 items 数组")
    total = max(5.0, float(duration or 60.0))
    step = total / max(1, len(raw_items))
    items: list[dict[str, Any]] = []
    used_timestamps: set[str] = set()
    for index, raw in enumerate(raw_items[:200]):
        if not isinstance(raw, dict):
            continue
        narration = str(raw.get("narration") or "").strip()
        if not narration:
            continue
        timestamp = _timestamp_range(raw.get("timestamp"), index * step, min(total, (index + 1) * step))
        if timestamp in used_timestamps:
            timestamp = _timestamp_range("", index * step, min(total, (index + 1) * step))
        # TTS 音频文件按时间戳命名，必须保证唯一。
        while timestamp in used_timestamps:
            step += 0.1
            timestamp = _timestamp_range("", index * step, min(total, (index + 1) * step))
        used_timestamps.add(timestamp)
        items.append(
            {
                "_id": len(items) + 1,
                "timestamp": timestamp,
                "picture": str(raw.get("picture") or narration).strip()[:500],
                "narration": narration[:500],
                "OST": 2,
            }
        )
    if not items:
        raise ScriptGenerationError("模型返回的脚本为空")
    return items


def _enforce_persona_limits(
    items: list[dict[str, Any]],
    persona: str,
    strength: str,
) -> list[dict[str, Any]]:
    persona_key = resolve_persona(persona)
    if persona_key == "auto":
        return items
    strength_key = resolve_persona_strength(strength)
    ratio = float(PERSONA_STRENGTHS[strength_key]["catchphrase_ratio"])
    output = [dict(item) for item in items]
    allowed = max(1, int(len(output) * ratio)) if output else 0
    for phrase in PERSONA_PROFILES[persona_key]["catchphrases"]:
        indexes = [index for index, item in enumerate(output) if phrase in str(item.get("narration") or "")]
        for index in indexes[allowed:]:
            text = str(output[index].get("narration") or "").replace(phrase, "")
            text = re.sub(r"^[，,。！？!?；;：:、\s]+", "", text)
            text = re.sub(r"([，,。！？!?；;])\1+", r"\1", text)
            output[index]["narration"] = re.sub(r"\s{2,}", " ", text).strip() or "继续。"
    return output


def _apply_persona(
    items: list[dict[str, Any]],
    spec: dict[str, Any],
    model: str,
    base_url: str,
    keys: list[str],
    log,
) -> list[dict[str, Any]]:
    persona = resolve_persona(spec.get("persona"))
    strength = resolve_persona_strength(spec.get("persona_strength"))
    if persona == "auto" or spec.get("output_mode") == "edit":
        return items

    profile = PERSONA_PROFILES[persona]
    log(f"应用{profile['label']}主播人格（{PERSONA_STRENGTHS[strength]['label']}风格）…")
    neutral = [{"_id": item["_id"], "narration": item["narration"]} for item in items]
    prompt = PERSONA_TEMPLATE.format(
        instruction=build_persona_instruction(persona, strength),
        items=json.dumps(neutral, ensure_ascii=False, indent=2),
    )
    content = _chat_text(
        model,
        base_url,
        keys,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        log,
    )
    payload = _extract_json(content)
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ScriptGenerationError("人格改写没有返回 items 数组")
    by_id: dict[int, str] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        try:
            item_id = int(raw.get("_id"))
        except (TypeError, ValueError):
            continue
        narration = str(raw.get("narration") or "").strip()
        if narration:
            by_id[item_id] = narration[:500]

    styled = []
    for item in items:
        narration = by_id.get(int(item["_id"]))
        if not narration:
            raise ScriptGenerationError(f"人格改写缺少第 {item['_id']} 段或返回空解说")
        styled.append({**item, "narration": narration})
    return _enforce_persona_limits(styled, persona, strength)


def generate_items(material_markdown: str, spec: dict[str, Any], log=print) -> list[dict[str, Any]]:
    context = build_creative_context(
        content_type=spec.get("content_type", "auto"),
        topic=spec.get("topic", ""),
        instruction=spec.get("instruction", ""),
        target_duration=spec.get("target_duration", 60),
        language=spec.get("language", "中文"),
        tone=spec.get("tone", ""),
        coverage=spec.get("coverage", "highlights"),
        video_duration=spec.get("video_duration", 0),
    )
    material_limit = 240000 if spec.get("analysis_mode") == "deep" else 120000
    prompt = TEMPLATE.format(
        label=context["label"],
        theme=context["theme"] or "由素材决定",
        duration=int(spec.get("target_duration") or 60),
        language=spec.get("language", "中文"),
        instruction=context["prompt"],
        material=material_markdown[:material_limit],
    )
    model, base_url, keys = _text_settings()
    log(f"调用文本模型 {model} 生成解说脚本（{len(keys)} 个 Key 可轮换）…")
    content = _chat_text(
        model,
        base_url,
        keys,
        [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        log,
    )
    payload = _extract_json(content)
    items = _normalize_items(payload, spec.get("video_duration") or spec.get("target_duration") or 60)
    return _apply_persona(items, spec, model, base_url, keys, log)
