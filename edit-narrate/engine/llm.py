"""OpenAI-compatible chat client. Strict JSON, short context, loud failures."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from engine.errors import EngineError
from engine.settings import ai_api_key, ai_base_url, ai_model, llm_configured


def _strip_fence(text: str) -> str:
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(.+?)```", text, flags=re.S)
    if match:
        return match.group(1).strip()
    return text


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(content or "")


def complete_json(
    *,
    system: str,
    user: str,
    model: type[BaseModel],
    images: list[tuple[str, bytes]] | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    model_name: str | None = None,
    timeout: float = 90,
) -> BaseModel:
    base = (base_url or ai_base_url()).rstrip("/")
    key = api_key if api_key is not None else ai_api_key()
    name = model_name or ai_model()
    if not base or not key or not name:
        raise EngineError(
            "没有配置文本模型",
            code="llm_unconfigured",
            action="设置 VIDEO_HUB_AI_BASE_URL、VIDEO_HUB_AI_API_KEY、VIDEO_HUB_AI_MODEL 后重试。剪辑可以继续用信号建议；解说可以改用逐句骨架。",
            status=409,
        )
    url = base if base.endswith("/chat/completions") else base + "/chat/completions"
    if images:
        user_content: Any = [{"type": "text", "text": user}]
        for mime, blob in images:
            import base64

            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{base64.b64encode(blob).decode()}"},
            })
    else:
        user_content = user
    payload = {
        "model": name,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise EngineError(
            f"模型接口连不上：{exc}",
            code="llm_http",
            action=f"检查 {url} 是否可从这台机器访问，以及密钥是否有效。",
            status=502,
        ) from exc
    if response.status_code == 400 and "response_format" in payload:
        payload.pop("response_format", None)
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.HTTPError as exc:
            raise EngineError(f"模型接口连不上：{exc}", code="llm_http", action="稍后重试。", status=502) from exc
    if response.status_code >= 400:
        body = response.text[:300].replace(key, "***")
        raise EngineError(
            f"模型接口返回 HTTP {response.status_code}：{body}",
            code="llm_http",
            action="核对模型名和 base URL。有些服务不接受 response_format，已自动重试过一次。",
            status=502,
        )
    try:
        data = response.json()
        content = _message_text(data["choices"][0]["message"]["content"])
        parsed = json.loads(_strip_fence(content))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raw = response.text[:400]
        raise EngineError(
            f"模型没有返回合法 JSON（{exc}）。原文摘录：{raw}",
            code="llm_parse",
            action="重试这一步。若连续失败，换模型，或改用逐句骨架 / 信号方案。",
            status=422,
        ) from exc
    try:
        return model.model_validate(parsed)
    except ValidationError as exc:
        raise EngineError(
            "模型 JSON 不符合约定：" + exc.errors()[0].get("msg", "字段无效"),
            code="llm_schema",
            action="重试。不要放宽校验去迁就模型，空结果比坏结果更难发现。",
            status=422,
        ) from exc


def configured() -> bool:
    return llm_configured()
