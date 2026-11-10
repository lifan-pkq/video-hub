"""Shared contracts for edit suggestions and narration beats."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class TranscriptSegment(BaseModel):
    id: str = ""
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = ""


class Suggestion(BaseModel):
    id: str
    video_id: int
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    kind: Literal["silence", "black", "pause", "filler", "scene"]
    action: Literal["drop", "split"]
    score: float = Field(ge=0, le=1)
    reason: str
    evidence: str = ""
    recommended: bool = False
    llm_action: Literal["drop", "keep"] | None = None
    text: str = ""

    model_config = {"extra": "ignore"}


class Beat(BaseModel):
    id: str
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    narration: str = Field(min_length=1, max_length=200)
    purpose: Literal["hook", "explain", "evidence", "turn", "close"] = "explain"

    model_config = {"extra": "ignore"}


class VoiceSettings(BaseModel):
    engine: Literal["auto", "edge", "openai", "espeak", "tone"] = "auto"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    original_volume: float = Field(default=0.18, ge=0, le=1)
    subtitles: bool = True
    font_size: int = Field(default=46, ge=18, le=96)
    margin_v: int = Field(default=48, ge=0, le=400)

    model_config = {"extra": "ignore"}


class RankDecision(BaseModel):
    id: str
    action: Literal["drop", "keep"]
    reason: str = Field(default="", max_length=80)

    model_config = {"extra": "ignore"}


class RankResponse(BaseModel):
    decisions: list[RankDecision]

    model_config = {"extra": "ignore"}


class ScriptResponse(BaseModel):
    beats: list[Beat]

    model_config = {"extra": "ignore"}


class CaptionResponse(BaseModel):
    caption: str = Field(default="", max_length=40)

    model_config = {"extra": "ignore"}
