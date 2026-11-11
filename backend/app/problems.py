"""HTTP 失败体统一成 {code, message, action}，前端可以同时显示原因和下一步。"""

from fastapi import HTTPException


def problem(status: int, code: str, message: str, action: str = "") -> HTTPException:
    return HTTPException(status, detail={"code": code, "message": message, "action": action})
