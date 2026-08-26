"""平台账号：多账号管理、cookie 校验/上传/删除、cookie 字符串导入导出、扫码登录会话。"""
import json

from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.config import PLATFORMS, PLATFORM_NAMES
from app.services.cookies import (cookie_path, delete_cookie, export_cookie_string,
                                  import_cookie_string, list_accounts, save_cookie,
                                  valid_account_name)
from app.services.login import cancel_login, get_login, qr_file, start_login
from app.services.publisher import check_platform

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


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


@router.post("/{platform}/{account}/cookie")
async def upload_cookie(platform: str, account: str, file: UploadFile = File(...)):
    """上传 playwright storage_state JSON 作为该平台该账号的 cookie（B站例外：需 biliup 格式）。"""
    _check_platform_account(platform, account)
    content = await file.read()
    if platform == "bilibili":
        # biliup 上传依赖 cookie_info + token_info（含 access_token），
        # playwright storage_state 格式用不了，引导用户扫码登录
        try:
            import json as _json
            data = _json.loads(content.decode("utf-8"))
        except Exception:
            raise HTTPException(400, "文件不是合法 JSON")
        if "cookie_info" not in data:
            raise HTTPException(400, "哔哩哔哩不支持 playwright 格式的 cookie 文件，请直接用「扫码登录」添加账号")
        path = cookie_path(platform, account)
        path.write_bytes(content)
        return {"ok": True, "path": str(path),
                "message": f"{PLATFORM_NAMES[platform]}({account}) cookie 已更新"}
    try:
        path = save_cookie(platform, content, account)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "文件不是合法 JSON")
    return {"ok": True, "path": str(path),
            "message": f"{PLATFORM_NAMES[platform]}({account}) cookie 已更新，建议立即校验"}


@router.delete("/{platform}/{account}")
async def remove_account(platform: str, account: str):
    """删除账号（删除其 cookie 文件）。"""
    _check_platform_account(platform, account)
    if not delete_cookie(platform, account):
        raise HTTPException(404, "账号不存在")
    return {"ok": True, "message": f"{PLATFORM_NAMES[platform]}({account}) 已删除"}


# ---------- cookie 字符串导入/导出（免上传文件） ----------

class CookieImportRequest(BaseModel):
    platform: str
    account: str
    content: str


@router.post("/cookie/import")
async def import_cookie(req: CookieImportRequest):
    """直接粘贴 cookie 字符串导入（自动识别 请求头字符串/Netscape 文本/storage_state JSON）。"""
    _check_platform_account(req.platform, req.account)
    try:
        path = import_cookie_string(req.platform, req.account, req.content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    count = len(json.loads(path.read_text(encoding="utf-8")).get("cookies", []))
    return {"ok": True, "count": count,
            "message": f"{PLATFORM_NAMES[req.platform]}({req.account}) cookie 已导入（{count} 条），建议立即校验"}


@router.get("/{platform}/{account}/cookie/export")
async def export_cookie(platform: str, account: str):
    """导出该账号 cookie 为请求头字符串，方便复制到别的设备/工具使用。"""
    _check_platform_account(platform, account)
    try:
        cookie_string = export_cookie_string(platform, account)
    except FileNotFoundError:
        raise HTTPException(404, "该账号还没有 cookie")
    return {"platform": platform, "account": account, "cookie_string": cookie_string}


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
