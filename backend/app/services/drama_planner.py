from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.understanding import _request_json

ANALYZE_SYSTEM = (
    "你是资深短剧编剧与制片人，擅长把小说改编成适合 AI 视频生成的竖屏短剧。"
    "只输出严格 JSON，不要输出任何解释。"
)
STORYBOARD_SYSTEM = (
    "你是短剧分镜师，为 AI 图生视频设计分镜。每个镜头只有 4–12 秒，"
    "只能表现一个缓慢、连续、简单的动作。只输出严格 JSON。"
)


class PlannerError(RuntimeError):
    pass


class PlannedCharacter(BaseModel):
    name: str = Field(min_length=1)
    age: int | str | None = None
    gender: str = ""
    appearance: str = ""
    clothing: str = ""
    personality: str = ""

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("角色名不能为空")
        return value


class PlannedEpisode(BaseModel):
    index: int = Field(ge=1)
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    source_excerpt: str = ""

    @field_validator("title", "summary")
    @classmethod
    def content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("分集内容不能为空")
        return value


class PlannedShot(BaseModel):
    scene: str = Field(min_length=1)
    action: str = Field(min_length=1)
    expression: str = ""
    camera: str = "固定"
    seconds: int = Field(ge=4, le=12)
    dialogue: str = ""
    characters: list[str] = Field(default_factory=list)


def validate_shot(value: dict) -> PlannedShot:
    try:
        return PlannedShot.model_validate(value)
    except ValueError as exc:
        raise PlannerError("AI 分镜字段或时长不合法") from exc


def plan_novel(novel: str, episodes_count: int | None = None) -> dict[str, Any]:
    """分析小说并分集。episodes_count 为 None 时由 AI 根据篇幅和剧情节奏自主决定集数（1–4 集）。"""
    if episodes_count is not None:
        count_line = f"把下面这部小说改编为 {episodes_count} 集 AI 短剧。\n"
    else:
        count_line = "把下面这部小说改编为 AI 短剧，根据小说篇幅和剧情节奏自主决定集数（1–4 集）。\n"
    user = (
        count_line +
        "要求：\n"
        "1. title：短剧名；logline：一句话卖点。\n"
        "2. characters：主要角色（不超过 4 个），每个角色含 name、age（具体数字）、gender、"
        "appearance（固定外貌特征：脸型、眼型、发型发色、体型、标志物，80 字内，全剧不变）、"
        "clothing（固定服装：款式颜色材质，50 字内）、personality（气质，20 字内）。\n"
        "3. episodes：每集含 index、title、summary（150 字内，开头 3 秒必须有强冲突钩子，"
        "结尾留悬念）、source_excerpt（该集对应的小说原文关键片段，800 字内，供分镜使用）。\n"
        "4. 角色外貌和服装描述要具体、可视化，禁止「年轻」「漂亮」这类模糊词。\n\n"
        f"小说全文：\n{novel[:80000]}"
    )
    result = _request_json([
        {"role": "system", "content": ANALYZE_SYSTEM},
        {"role": "user", "content": user},
    ])
    characters = result.get("characters") if isinstance(result, dict) else None
    episodes = result.get("episodes") if isinstance(result, dict) else None
    if not isinstance(characters, list) or not characters:
        raise PlannerError("小说分析没有返回有效角色列表")
    if not isinstance(episodes, list) or not 1 <= len(episodes) <= 4:
        raise PlannerError("小说分析必须返回 1–4 集完整大纲")
    if episodes_count is not None and len(episodes) != episodes_count:
        raise PlannerError(f"小说分析必须返回 {episodes_count} 集完整大纲")
    try:
        [PlannedCharacter.model_validate(item) for item in characters]
        planned_episodes = [PlannedEpisode.model_validate(item) for item in episodes]
    except ValueError as exc:
        raise PlannerError("小说分析角色或分集字段不完整") from exc
    if [episode.index for episode in planned_episodes] != list(range(1, len(episodes) + 1)):
        raise PlannerError("小说分析分集编号必须按 1 到集数连续排列")
    return result


def plan_episode(
    episode: dict[str, Any],
    characters: list[dict[str, Any]],
    *,
    title: str = "AI 短剧",
    style: str = "",
) -> list[dict[str, Any]]:
    episode_index = int(episode["index"])
    user = (
        f"为短剧《{title}》第 {episode_index} 集「{episode.get('title') or f'第 {episode_index} 集'}」设计分镜。\n"
        f"本集剧情：{episode.get('summary') or ''}\n"
        f"原文片段：{episode.get('source_excerpt') or '（无，按剧情创作）'}\n"
        f"角色表：{json.dumps(characters, ensure_ascii=False)}\n"
        f"整体画风：{style}\n"
        "要求：\n"
        "1. 根据剧情节奏自主决定镜头数量，至少 1 个，不设置固定目标，输出 shots 数组。\n"
        "2. 根据每个镜头的动作和叙事需要自主决定 seconds，每镜必须为 4–12 秒整数。\n"
        "3. 每个镜头含：index、scene（场景，含时间光线）、action（一个缓慢连续的简单动作，"
        "避免打斗快跑等剧烈运动）、expression（情绪表情）、camera（固定/缓慢推近/缓慢拉远/缓慢横移 之一）、"
        "seconds、dialogue（该镜台词或旁白，30 字内，可为空）、"
        "characters（出镜角色 id 数组，尽量单人出镜，最多 2 人）。\n"
        "4. 第 1 镜必须是强冲突钩子画面，镜头之间剧情连贯。\n"
    )
    result = _request_json([
        {"role": "system", "content": STORYBOARD_SYSTEM},
        {"role": "user", "content": user},
    ])
    raw_shots = result.get("shots") if isinstance(result, dict) else None
    if not isinstance(raw_shots, list) or not raw_shots:
        raise PlannerError(f"第 {episode_index} 集分镜为空或格式不合法")

    shots = []
    for index, item in enumerate(raw_shots, start=1):
        planned = validate_shot(item)
        shot = planned.model_dump()
        shot.update({
            "id": f"e{episode_index}s{index}",
            "index": index,
            "prompt": (
                f"场景：{planned.scene}。动作：{planned.action}。"
                f"表情：{planned.expression}。运镜：{planned.camera}。"
            ),
            "characters": planned.characters[:2],
            "revision": 1,
            "status": "pending",
            "image": {
                "name": "", "url": "", "status": "pending", "attempts": 0,
                "quality": None, "revision": 1, "legacy_accepted": False,
            },
            "video": {
                "name": "", "url": "", "status": "pending", "attempts": 0,
                "quality": None, "revision": 1, "legacy_accepted": False,
            },
            "quality_history": [],
            "attempts": 0,
        })
        shots.append(shot)
    return shots
