"""TTS 读音校正：只改送给 TTS 的文本，字幕仍使用原始解说词。"""

from __future__ import annotations

from typing import Any

# 常见多音字/专名在 Edge TTS 里容易读错的词。
# 映射到同音词，让 TTS 按目标读音朗读；字幕仍保留原词。
HINTS: dict[str, str] = {
    "重庆": "崇庆",
    "重新": "崇新",
    "重复": "崇复",
    "重逢": "崇逢",
    "重叠": "崇叠",
    "银行": "银航",
    "行业": "航业",
    "行长": "航掌",
    "睡觉": "睡叫",
    "长大": "掌大",
}


def apply(
    items: list[dict[str, Any]],
    enabled: bool = True,
    custom: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    if not enabled:
        return items
    hints = {**HINTS, **(custom or {})}
    ordered = sorted(hints.items(), key=lambda pair: len(pair[0]), reverse=True)
    for item in items:
        text = str(item.get("narration") or "")
        if not text:
            continue
        tts_text = text
        for source, target in ordered:
            tts_text = tts_text.replace(source, target)
        if tts_text != text:
            item["tts_text"] = tts_text
    return items
