"""OpenAI-compatible chat client. Strict JSON, short context, loud failures."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from engine.errors import EngineError
from engine.settings import ai_api_key, ai_api_keys, ai_base_url, ai_model, llm_configured


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


def _get_pool(*, vision: bool):
    """优先复用 backend AI 池（带冷却状态）；独立跑 engine 时回退本地轮换。"""
    try:
        from app.services.ai_pool import pool_for

        return pool_for(vision=vision)
    except Exception:
        return None


def _post_single(
    url: str,
    payload: dict[str, Any],
    key: str,
    timeout: float,
):
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
    return response


def _post_chat(
    url: str,
    payload: dict[str, Any],
    *,
    api_key: str | None,
    vision: bool,
    timeout: float,
):
    """有池则轮换；显式 api_key 且无池时走单 Key。"""
    pool = _get_pool(vision=vision)
    if pool is not None and pool.has_keys() and api_key is None:
        try:
            from app.services.ai_pool import AIPoolError
        except Exception:  # pragma: no cover
            AIPoolError = RuntimeError  # type: ignore
        try:
            response = pool.request("POST", url, json_body=payload, timeout=timeout)
        except AIPoolError as exc:
            raise EngineError(
                f"模型接口全部 Key 失败：{exc}",
                code="llm_http",
                action="稍后重试，或检查 VIDEO_HUB_AI_API_KEYS / VIDEO_HUB_VISION_API_KEYS。",
                status=502,
            ) from exc
        except Exception as exc:
            raise EngineError(
                f"模型接口连不上：{exc}",
                code="llm_http",
                action=f"检查 {url} 是否可从这台机器访问。",
                status=502,
            ) from exc
        return response, None

    key = api_key if api_key is not None else ai_api_key()
    if not key:
        raise EngineError(
            "没有配置文本模型",
            code="llm_unconfigured",
            action="设置 VIDEO_HUB_AI_BASE_URL、VIDEO_HUB_AI_API_KEYS、VIDEO_HUB_AI_MODEL 后重试。",
            status=409,
        )
    return _post_single(url, payload, key, timeout), key


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
    name = model_name or ai_model()
    use_vision = bool(images)
    has_keys = bool(ai_api_keys()) if not use_vision else bool(ai_api_keys())
    # 视觉路径也接受 AI/VISION 任一池
    if use_vision:
        try:
            from engine.settings import vision_api_keys

            has_keys = bool(vision_api_keys())
        except Exception:
            has_keys = bool(ai_api_keys())
    if not base or not name or (api_key is None and not has_keys and not ai_api_key()):
        if api_key is None and not has_keys:
            raise EngineError(
                "没有配置文本模型",
                code="llm_unconfigured",
                action="设置 VIDEO_HUB_AI_BASE_URL、VIDEO_HUB_AI_API_KEYS、VIDEO_HUB_AI_MODEL 后重试。剪辑可以继续用信号建议。",
                status=409,
            )
    if not base or not name:
        raise EngineError(
            "没有配置文本模型",
            code="llm_unconfigured",
            action="设置 VIDEO_HUB_AI_BASE_URL、VIDEO_HUB_AI_API_KEYS、VIDEO_HUB_AI_MODEL 后重试。剪辑可以继续用信号建议。",
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
    response, used_key = _post_chat(
        url, payload, api_key=api_key, vision=use_vision, timeout=timeout
    )
    if response.status_code == 400 and "response_format" in payload:
        payload.pop("response_format", None)
        response, used_key = _post_chat(
            url, payload, api_key=api_key, vision=use_vision, timeout=timeout
        )
    if response.status_code >= 400:
        redact_key = used_key or ""
        body = response.text[:300]
        if redact_key:
            body = body.replace(redact_key, "***")
        body = re.sub(r"[A-Za-z0-9_\-]{16,}", "***", body)
        raise EngineError(
            f"模型接口返回 HTTP {response.status_code}：{body}",
            code="llm_http",
            action="核对模型名和 base URL。有些服务不接受 response_format，已自动重试过一次。若 429，系统会切换 Key。",
            status=502,
        )
    try:
        data = response.json()
        content = _message_text(data["choices"][0]["message"]["content"])
        parsed = json.loads(_strip_fence(content))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raw = re.sub(r"[A-Za-z0-9_\-]{16,}", "***", response.text[:400])
        raise EngineError(
            f"模型没有返回合法 JSON（{exc}）。原文摘录：{raw}",
            code="llm_parse",
            action="重试这一步。若连续失败，换模型，或改用逐句骨架 / 信号方案。",
            status=422,
        ) from exc
    if isinstance(parsed, dict):
        # 画面说明偶发超长：校验前截断，避免整帧失败并露出英文 pydantic 报错
        caption = parsed.get("caption")
        if isinstance(caption, str) and len(caption) > 200:
            parsed["caption"] = caption.strip()[:200]
        for beat in parsed.get("beats") or []:
            if isinstance(beat, dict) and isinstance(beat.get("narration"), str):
                beat["narration"] = beat["narration"].strip()[:200]

    def _schema_message(exc: ValidationError) -> str:
        err = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in (err.get("loc") or ()))
        msg = str(err.get("msg") or "字段无效")
        if "at most" in msg:
            import re as _re
            match = _re.search(r"at most (\d+)", msg)
            limit = match.group(1) if match else "?"
            field = loc or "内容"
            return f"字段「{field}」超长（上限 {limit} 字）"
        if "at least" in msg:
            field = loc or "内容"
            return f"字段「{field}」为空或过短"
        if loc:
            return f"字段「{loc}」不合约定：{msg}"
        return f"模型 JSON 不合约定：{msg}"

    try:
        return model.model_validate(parsed)
    except ValidationError as exc:
        raise EngineError(
            _schema_message(exc),
            code="llm_schema",
            action="可重试这一步；画面说明过长时会自动截断，仍失败再换提示词。",
            status=422,
        ) from exc


def configured() -> bool:
    return llm_configured()
