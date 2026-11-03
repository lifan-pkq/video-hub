"""面向通用视频的解说内容档案（不绑定影视/短剧）。

内容档案只描述"这条视频该怎么讲"，与具体模型、TTS、剪辑实现解耦。
引擎与前端共用同一份定义，通过 /capabilities 下发。
"""

from __future__ import annotations

from typing import Any

# 内容类型：默认 auto 走通用叙述，避免把任何视频都当影视解说处理。
CONTENT_PROFILES: dict[str, dict[str, Any]] = {
    "auto": {
        "label": "通用",
        "summary": "不预设题材，按画面与主题自然讲述",
        "theme": "",
        "style": "自然、具体、信息密度高；先讲发生了什么，再补充背景和意义；不堆砌形容词，不虚构画面里没有的信息",
        "original_volume": 0.15,
    },
    "knowledge": {
        "label": "知识科普",
        "summary": "解释原理、概念与因果关系",
        "theme": "知识科普",
        "style": "像一位耐心的老师：先给结论，再用画面中的细节解释原因；必要时用类比，但不要编造数据",
        "original_volume": 0.25,
    },
    "documentary": {
        "label": "纪录片",
        "summary": "沉稳叙述，强调场景与过程",
        "theme": "纪录片",
        "style": "克制、有画面感，按时间与空间顺序推进；用短句承接镜头，避免连续感叹",
        "original_volume": 0.65,
    },
    "vlog": {
        "label": "生活记录",
        "summary": "轻松口语，像朋友在身边",
        "theme": "生活记录",
        "style": "第一人称口语，轻松但不油腻；讲清楚正在做什么、为什么这样做，允许一点情绪和玩笑",
        "original_volume": 0.4,
    },
    "product": {
        "label": "产品体验",
        "summary": "讲清亮点、使用场景与取舍",
        "theme": "产品体验",
        "style": "先给使用场景，再讲画面中能看到的功能与细节；说优点也说限制，不喊口号、不虚构参数",
        "original_volume": 0.3,
    },
    "tutorial": {
        "label": "教程步骤",
        "summary": "按步骤讲操作与要点",
        "theme": "教程",
        "style": "按步骤讲：现在做什么、注意什么、做完会得到什么；用第二人称，短句，一步一句",
        "original_volume": 0.2,
    },
    "interview": {
        "label": "访谈口播",
        "summary": "保留原声，补充背景与承接",
        "theme": "访谈口播",
        "style": "尊重原声：只在需要时补充人物、背景和上下文；不替受访者下结论，不盖过正在说话的人",
        "original_volume": 0.8,
    },
    "film": {
        "label": "影视剧情",
        "summary": "梳理人物、冲突与剧情钩子",
        "theme": "影视剧情",
        "style": "按剧情推进讲人物动机与冲突，制造悬念但不剧透关键反转；区分画面事实与合理推测",
        "original_volume": 0.25,
    },
    "custom": {
        "label": "自定义",
        "summary": "完全按你的指令创作",
        "theme": "",
        "style": "严格遵循用户给出的创作要求；没有要求时保持中性、具体、不虚构",
        "original_volume": 0.15,
    },
}

# 输出画幅。auto 由后端按源视频尺寸解析后再传给引擎。
ASPECTS: list[dict[str, str]] = [
    {"value": "auto", "label": "跟随原片"},
    {"value": "9:16", "label": "竖屏 9:16"},
    {"value": "16:9", "label": "横屏 16:9"},
    {"value": "1:1", "label": "方形 1:1"},
    {"value": "3:4", "label": "竖屏 3:4"},
    {"value": "4:3", "label": "横屏 4:3"},
]

# Edge TTS 声音（免密钥、开箱可用）。其他引擎可在设置中切换。
VOICES: list[dict[str, str]] = [
    {"value": "zh-CN-XiaoxiaoNeural", "label": "晓晓 · 女声 · 温暖自然", "engine": "edge_tts"},
    {"value": "zh-CN-XiaoyiNeural", "label": "晓伊 · 女声 · 活泼", "engine": "edge_tts"},
    {"value": "zh-CN-YunxiNeural", "label": "云希 · 男声 · 清爽", "engine": "edge_tts"},
    {"value": "zh-CN-YunjianNeural", "label": "云健 · 男声 · 沉稳", "engine": "edge_tts"},
    {"value": "zh-CN-YunyangNeural", "label": "云扬 · 男声 · 播报", "engine": "edge_tts"},
    {"value": "zh-CN-liaoning-XiaobeiNeural", "label": "晓北 · 女声 · 东北", "engine": "edge_tts"},
    {"value": "zh-CN-shaanxi-XiaoniNeural", "label": "晓妮 · 女声 · 陕西", "engine": "edge_tts"},
]

TTS_ENGINES: list[dict[str, str]] = [
    {"value": "edge_tts", "label": "Edge TTS（免密钥）"},
    {"value": "azure_speech", "label": "Azure 语音（需配置）"},
    {"value": "tencent_tts", "label": "腾讯云语音（需配置）"},
]

SCRIPT_MODES: list[dict[str, str]] = [
    {"value": "auto", "label": "自动（有文稿用文稿，否则看画面）"},
    {"value": "visual", "label": "按画面理解生成"},
    {"value": "transcript", "label": "按口播文稿生成"},
    {"value": "asr", "label": "自动识别口播（ASR）"},
    {"value": "script", "label": "直接使用解说稿"},
]

DEFAULT_VOICE = "zh-CN-YunxiNeural"
DEFAULT_TTS_ENGINE = "edge_tts"


def _clean(value: Any, limit: int = 2000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def profile_for(key: str) -> dict[str, Any]:
    return CONTENT_PROFILES.get(str(key or "").strip().lower(), CONTENT_PROFILES["auto"])


def resolve_content_type(key: str) -> str:
    normalized = str(key or "").strip().lower()
    return normalized if normalized in CONTENT_PROFILES else "auto"


def build_creative_context(
    content_type: str,
    topic: str = "",
    instruction: str = "",
    target_duration: int = 60,
    language: str = "中文",
    tone: str = "",
    coverage: str = "highlights",
    video_duration: float = 0,
) -> dict[str, str]:
    """把内容类型 + 用户输入拼成 NarratoAI 脚本生成器可直接使用的上下文。

    返回 theme（视频主题）与 prompt（补充创作要求）。
    """
    key = resolve_content_type(content_type)
    profile = profile_for(key)
    topic = _clean(topic, 500)
    instruction = _clean(instruction, 1500)
    tone = _clean(tone, 200)

    theme_parts = [profile["theme"]] if profile.get("theme") else []
    if topic:
        theme_parts.append(topic)
    theme = " · ".join(theme_parts)

    source_duration = max(0.0, float(video_duration or 0))
    coverage = "full" if str(coverage or "").lower() == "full" else "highlights"
    if coverage == "full" and source_duration > 0:
        duration = max(5, min(int(round(source_duration)), 7200))
    else:
        duration = max(5, min(int(target_duration or 60), 7200))
    chars = max(60, round(duration * 4.2))
    lines = [
        f"内容类型：{profile['label']}（{profile['summary']}）。",
        f"讲述风格：{profile['style']}。",
        f"目标成片时长约 {duration} 秒，解说口播总字数控制在 {chars} 字左右。",
        f"使用语言：{language}。",
    ]
    if coverage == "full" and source_duration > 0:
        lines.append(
            f"覆盖范围：从 0 秒到视频结尾（约 {source_duration:.1f} 秒）完整覆盖；"
            "timestamp 必须从开头连续推进到结尾，不能只写前几十秒，也不能跳过中间大段。"
        )
    elif source_duration > 0:
        lines.append(
            f"素材覆盖整条视频 0–{source_duration:.1f} 秒。请从全片挑选代表性片段，"
            "timestamp 必须分散在整条视频上，不能只从开头连续取；"
            f"所有片段的原片时长合计控制在约 {duration} 秒。"
        )
    lines.extend([
        "画面优先：只描述画面、字幕或用户文稿中确实存在的信息；不确定的内容不要编造。",
        "只讲关键内容，跳过无关过程；开头用一句话点题或设悬念，结尾回扣开头/题眼，形成前后呼应。",
        "关键画面可以少讲或留白，让原声和画面自己表达；不要把每一段都写满。",
        "先通读全部素材，规划要使用哪些片段、按什么顺序讲，再逐段写解说；不要机械复述素材。",
        "按镜头顺序分段，每段 1-2 句，段与段之间自然承接；不要写开场白套话、不要重复上一段。",
        "输出必须遵守脚本生成器要求的 JSON 结构，每个片段都要有 narration、timestamp、OST。",
    ])
    if tone:
        lines.append(f"语气要求：{tone}。")
    if instruction:
        lines.append(f"用户补充要求：{instruction}")

    return {"theme": theme, "prompt": "\n".join(lines), "label": profile["label"]}
