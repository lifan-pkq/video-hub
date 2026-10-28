"""手机短信自动回填：接收 SmsForwarder 转发的抖音验证码短信。

手机端 SmsForwarder 配置 webhook 通道 POST 本接口，收到短信后自动提交，
写入引擎读取的 verify_code.txt —— 发布全程无需手动输入验证码。
"""
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import PUBLISHER_DIR
from app.services.tasks import task_manager

router = APIRouter(prefix="/api", tags=["sms"])


class SmsPayload(BaseModel):
    token: str = ""
    content: str = ""   # 短信全文（自动从中提取验证码）
    code: str = ""      # 或直接给验证码


def extract_code(text: str) -> str:
    """从短信全文提取 4-8 位数字验证码，优先取「验证码」后面的那串。"""
    m = re.search(r"验证码[^0-9]{0,6}(\d{4,8})", text)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6})\b", text)
    return m.group(1) if m else ""


@router.post("/sms-code")
def receive_sms(payload: SmsPayload):
    code = payload.code.strip() or extract_code(payload.content)
    if not re.fullmatch(r"\d{4,8}", code):
        raise HTTPException(400, "未能从短信内容中解析出验证码")
    (PUBLISHER_DIR / "verify_code.txt").write_text(code, encoding="utf-8")
    # 记到正在等验证码的任务日志里，前端可见
    for t in task_manager.list():
        if t.status == "running" and t.needs_verify:
            t.log("[系统] 手机短信已自动转发，验证码已写入，等待引擎填入…")
    return {"ok": True, "received_at": time.strftime("%Y-%m-%d %H:%M:%S")}
