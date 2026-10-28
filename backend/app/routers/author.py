"""作者作品：解析作者主页分享链接，返回该作者的全部作品列表。"""
import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.author_works import AuthorBusyError, parse_author_works

router = APIRouter(prefix="/api/author", tags=["author"])

# 整体超时：短链跳转 + 浏览器抓取最长 90s，留些余量
PARSE_TIMEOUT = 120


class AuthorParseRequest(BaseModel):
    text: str


@router.post("/parse")
async def parse(req: AuthorParseRequest):
    try:
        return await asyncio.wait_for(parse_author_works(req.text), timeout=PARSE_TIMEOUT)
    except AuthorBusyError as e:
        raise HTTPException(429, str(e))
    except asyncio.TimeoutError:
        raise HTTPException(400, "抓取超时，请稍后重试")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        msg = str(e)
        if "cookie" in msg.lower():
            msg += "（提示：请先到「平台账号」页扫码登录该平台）"
        raise HTTPException(400, msg)
