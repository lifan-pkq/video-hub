"""灵感库：只保存链接和笔记，不下载作者作品。"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import add_inspiration, delete_inspiration, list_inspirations

router = APIRouter(prefix="/api/inspirations", tags=["inspirations"])


class InspirationBody(BaseModel):
    url: str
    note: str = ""


@router.get("")
def get_inspirations():
    return list_inspirations()


@router.post("")
def create_inspiration(body: InspirationBody):
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "请输入有效的 http(s) 链接")
    return add_inspiration(url, body.note.strip()[:2000])


@router.delete("/{inspiration_id}")
def remove_inspiration(inspiration_id: int):
    delete_inspiration(inspiration_id)
    return {"ok": True}
