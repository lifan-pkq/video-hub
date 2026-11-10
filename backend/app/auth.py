"""统一鉴权：/api/* 共享口令（Header / Cookie），管理类接口叠加二级口令。

个人自用部署，不做灰度：默认 enforce，一次性生效。
环境变量（写进 /opt/video-hub/.env，不改 systemd 单元）：
  VIDEO_HUB_AUTH_MODE    enforce（默认）| off（仅应急回滚用：临时彻底关闭鉴权）
  VIDEO_HUB_API_TOKEN    普通接口口令；enforce 下为空则拒绝启动（fail-closed）
  VIDEO_HUB_ADMIN_TOKEN  管理类口令；留空则沿用 API_TOKEN
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Final

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

MODE: Final = os.environ.get("VIDEO_HUB_AUTH_MODE", "enforce").strip().lower()
API_TOKEN: Final = os.environ.get("VIDEO_HUB_API_TOKEN", "").strip()
ADMIN_TOKEN: Final = os.environ.get("VIDEO_HUB_ADMIN_TOKEN", "").strip() or API_TOKEN

COOKIE_NAME: Final = "vh_token"
HEADER_NAME: Final = "x-api-token"

# 放行清单：健康检查（deploy.sh 依赖）+ 登录入口 + 外部 AI 服务回拉参考图（带不上 Cookie）
EXEMPT_PREFIXES: Final = (
    "/api/health",
    "/api/auth/login",
    "/api/ai/media/references/",  # Agnes 从公网拉 ref_<uuid32>，不可猜
)
# 管理类：清库/清日志/改维护配置/服务器状态/Key 池
ADMIN_PREFIXES: Final = ("/api/maintenance", "/api/system", "/api/ai/pool")

log = logging.getLogger("video_hub.auth")

# fail-closed：缺口令宁可起不来，也不静默放行整个 /api/*
if MODE != "off" and not API_TOKEN:
    raise RuntimeError(
        "VIDEO_HUB_API_TOKEN 未配置：鉴权默认 enforce，拒绝以“无口令放行”状态启动。"
        " 请在 .env 设置 VIDEO_HUB_API_TOKEN（openssl rand -hex 24 生成）；"
        " 确实要临时关闭鉴权，设 VIDEO_HUB_AUTH_MODE=off 后重启。"
    )


def _match(value: str, expected: str) -> bool:
    return bool(value) and bool(expected) and hmac.compare_digest(value, expected)


def _expected_for(path: str) -> str:
    return ADMIN_TOKEN if path.startswith(ADMIN_PREFIXES) else API_TOKEN


def _presented(headers, cookies) -> str:
    """只认 Header 与 Cookie：?token= 会进 access log / referrer，不提供。"""
    return (headers.get(HEADER_NAME) or "").strip() or (cookies.get(COOKIE_NAME) or "").strip()


def authorized(request: Request) -> bool:
    if MODE == "off":
        return True
    path = request.url.path
    if not path.startswith("/api/") or path.startswith(EXEMPT_PREFIXES):
        return True
    return _match(_presented(request.headers, request.cookies), _expected_for(path))


class AuthMiddleware(BaseHTTPMiddleware):
    """/api/* 全局闸门。BaseHTTPMiddleware 不覆盖 websocket，WS 由 events.py 单独校验。"""

    async def dispatch(self, request: Request, call_next):
        if authorized(request):
            return await call_next(request)
        log.warning("auth(deny): %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "未授权：口令缺失或错误"}, status_code=401,
                            headers={"WWW-Authenticate": "Bearer"})


def ws_authorized(ws) -> bool:
    """WS 握手进不了中间件，这里显式校验；admin 口令同样可用（避免 admin 被 1008 拒）。"""
    if MODE == "off":
        return True
    presented = _presented(ws.headers, ws.cookies)
    return _match(presented, API_TOKEN) or _match(presented, ADMIN_TOKEN)


def login(token: str) -> bool:
    """校验口令；Cookie 由路由写入（HttpOnly，前端另存 Header 副本）。"""
    return _match(token.strip(), API_TOKEN) or _match(token.strip(), ADMIN_TOKEN)
