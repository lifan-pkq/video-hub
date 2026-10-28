"""平台账号：多账号管理、cookie 校验/上传/删除、扫码登录会话。"""
import json
import os
import tempfile

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import PLATFORMS, PLATFORM_NAMES
from app.services.cookies import cookie_path, delete_cookie, list_accounts, valid_account_name
from app.services.login import cancel_login, get_login, qr_file, start_login
from app.services.publisher import check_platform

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

COOKIE_CHUNK_BYTES = 64 * 1024


def _check_platform_account(platform: str, account: str):
    if platform not in PLATFORMS:
        raise HTTPException(404, "不支持的平台")
    if not valid_account_name(account):
        raise HTTPException(400, "账号名只能包含字母、数字、下划线、短横线（1-32 位）")


@router.get("")
async def accounts(check: bool = False):
    """返回所有账号（平台+账号名）状态。check=true 时逐个真实校验 cookie（较慢）。"""
    result = list_accounts()
    if check:
        for item in result:
            if not item["cookie_exists"]:
                item["valid"] = False
                item["detail"] = "无 cookie"
                continue
            r = await check_platform(item["platform"], item["account"])
            item["valid"] = r["valid"]
            item["detail"] = r["detail"]
    return result


@router.post("/{platform}/{account}/check")
async def check_one(platform: str, account: str):
    _check_platform_account(platform, account)
    return await check_platform(platform, account)


def _replace_cookie(path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = path.parent / tmp_name
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


@router.post("/{platform}/{account}/cookie")
async def upload_cookie(platform: str, account: str, file: UploadFile = File(...)):
    """上传 playwright storage_state JSON 作为该平台该账号的 cookie（B站例外：需 biliup 格式）。"""
    _check_platform_account(platform, account)
    content = bytearray()
    try:
        while chunk := await file.read(COOKIE_CHUNK_BYTES):
            content.extend(chunk)
    finally:
        await file.close()
    try:
        data = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(400, "文件不是合法 JSON") from e
    if not isinstance(data, dict):
        raise HTTPException(400, "文件不是合法 JSON")
    if platform == "bilibili" and "cookie_info" not in data:
        raise HTTPException(400, "哔哩哔哩不支持 playwright 格式的 cookie 文件，请直接用「扫码登录」添加账号")
    if platform != "bilibili" and "cookies" not in data:
        raise HTTPException(400, "不是合法的 playwright storage_state 文件（缺少 cookies 字段）")

    path = cookie_path(platform, account)
    _replace_cookie(path, bytes(content))
    netscape = path.with_suffix(".netscape.txt")
    netscape.unlink(missing_ok=True)
    message = f"{PLATFORM_NAMES[platform]}({account}) cookie 已更新"
    if platform != "bilibili":
        message += "，建议立即校验"
    return {"ok": True, "path": str(path), "message": message}


@router.delete("/{platform}/{account}")
async def remove_account(platform: str, account: str):
    """删除账号（删除其 cookie 文件）。"""
    _check_platform_account(platform, account)
    if not delete_cookie(platform, account):
        raise HTTPException(404, "账号不存在")
    return {"ok": True, "message": f"{PLATFORM_NAMES[platform]}({account}) 已删除"}


# ---------- 扫码登录 ----------

class LoginRequest(BaseModel):
    platform: str
    account: str


@router.post("/login")
async def login(req: LoginRequest):
    """发起扫码登录会话，返回 session_id，前端轮询状态并展示二维码。"""
    _check_platform_account(req.platform, req.account)
    from app.config import QR_LOGIN_PLATFORMS
    if req.platform not in QR_LOGIN_PLATFORMS:
        raise HTTPException(400, f"{PLATFORM_NAMES.get(req.platform, req.platform)}暂不支持网页扫码登录，请改用「上传 cookie」添加账号")
    try:
        return start_login(req.platform, req.account)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))


@router.get("/login/{session_id}")
async def login_status(session_id: str):
    s = get_login(session_id)
    if not s:
        raise HTTPException(404, "登录会话不存在或已过期")
    return s


@router.get("/login/{session_id}/qr.png")
async def login_qr(session_id: str):
    qr = qr_file(session_id)
    if not qr:
        raise HTTPException(404, "二维码还未生成")
    # B站二维码是纯 Python 生成的 SVG，其余平台是 PNG 截图——按内容嗅探
    head = qr.read_bytes()[:128].lstrip()
    media = "image/svg+xml" if head.startswith(b"<") else "image/png"
    return FileResponse(str(qr), media_type=media,
                        headers={"Cache-Control": "no-store"})


@router.post("/login/{session_id}/cancel")
async def login_cancel(session_id: str):
    if not cancel_login(session_id):
        raise HTTPException(404, "登录会话不存在")
    return {"ok": True}
