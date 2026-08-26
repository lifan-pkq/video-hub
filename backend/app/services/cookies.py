"""平台 cookie 文件管理 + playwright storage_state 转 Netscape cookiefile（供 yt-dlp 使用）。"""
import json
import re
import time
from pathlib import Path

from app.config import ACCOUNT_NAME_RE, PLATFORMS, PUBLISHER_COOKIES_DIR, PUBLISH_ACCOUNT


def valid_account_name(account: str) -> bool:
    return bool(re.match(ACCOUNT_NAME_RE, account or ""))


def cookie_path(platform: str, account: str = PUBLISH_ACCOUNT) -> Path:
    return PUBLISHER_COOKIES_DIR / f"{platform}_{account}.json"


def cookie_exists(platform: str, account: str = PUBLISH_ACCOUNT) -> bool:
    return cookie_path(platform, account).exists()


def storage_state_to_netscape(state_path: Path, out_path: Path) -> int:
    """把 playwright storage_state JSON 转成 yt-dlp 可用的 Netscape cookie 文件，返回条数。"""
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    lines = ["# Netscape HTTP Cookie File"]
    count = 0
    for c in state.get("cookies", []):
        domain = c.get("domain", "")
        if not domain:
            continue
        sub = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        try:
            expires = int(c.get("expires", 0) or 0)
        except (TypeError, ValueError):
            expires = 0
        lines.append("\t".join([domain, sub, c.get("path", "/"), secure, str(max(expires, 0)),
                                c.get("name", ""), c.get("value", "")]))
        count += 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return count


def make_ytdlp_cookiefile(platform: str, account: str = PUBLISH_ACCOUNT) -> Path | None:
    """为 yt-dlp 生成指定平台的 Netscape cookiefile；源文件不存在时返回 None。"""
    src = cookie_path(platform, account)
    if not src.exists():
        return None
    out = src.with_suffix(".netscape.txt")
    # 源文件更新时才重新生成
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    storage_state_to_netscape(src, out)
    return out


def storage_state_to_requests_cookies(platform: str, account: str = PUBLISH_ACCOUNT) -> dict:
    """把 storage_state 的 cookies 转成 requests 可用的 {name: value} dict；源文件不存在返回空 dict。"""
    src = cookie_path(platform, account)
    if not src.exists():
        return {}
    with open(src, encoding="utf-8") as f:
        state = json.load(f)
    return {c["name"]: c["value"] for c in state.get("cookies", []) if c.get("name")}


def save_cookie(platform: str, content: bytes, account: str = PUBLISH_ACCOUNT) -> Path:
    """保存用户上传的 storage_state JSON，并使之对应的 netscape 缓存失效。"""
    if not valid_account_name(account):
        raise ValueError("账号名只能包含字母、数字、下划线、短横线（1-32 位）")
    data = json.loads(content.decode("utf-8"))  # 校验是合法 JSON
    if "cookies" not in data:
        raise ValueError("不是合法的 playwright storage_state 文件（缺少 cookies 字段）")
    path = cookie_path(platform, account)
    path.write_bytes(content)
    netscape = path.with_suffix(".netscape.txt")
    if netscape.exists():
        netscape.unlink()
    return path


# ================= cookie 字符串导入/导出（免上传文件） =================

# 平台 → cookie 默认主域名：粘贴请求头格式 cookie 时无法得知域名，按平台补默认
PLATFORM_COOKIE_DOMAINS = {
    "douyin": ".douyin.com",
    "xiaohongshu": ".xiaohongshu.com",
    "kuaishou": ".kuaishou.com",
    "bilibili": ".bilibili.com",
}

_COOKIE_FORMAT_ERROR = "无法识别的 cookie 格式，支持：请求头字符串(name=val; …)、Netscape 文本、storage_state JSON"


def _parse_netscape_cookies(text: str) -> list[dict] | None:
    """解析 Netscape cookie 文本（domain\\tflag\\tpath\\tsecure\\texpires\\tname\\tvalue），
    返回 playwright cookie 列表；不是该格式时返回 None。"""
    cookies = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        http_only = False
        if line.startswith("#HttpOnly_"):
            # HttpOnly  cookie 在 Netscape 文件里以 #HttpOnly_ 前缀标记，仍是有效数据行
            http_only = True
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            # 数据行不足 7 列：整体不是 Netscape 格式，交给下一种格式识别
            return None
        domain, _flag, path, secure, expires, name, value = parts[:7]
        if not name:
            return None
        try:
            expires_i = int(expires)
        except ValueError:
            expires_i = 0
        cookies.append({
            "name": name, "value": value,
            "domain": domain, "path": path or "/",
            # playwright 会话 cookie 用 -1 表示
            "expires": expires_i if expires_i > 0 else -1,
            "httpOnly": http_only,
            "secure": secure.upper() == "TRUE",
            "sameSite": "Lax",
        })
    return cookies or None


def _parse_header_cookies(platform: str, text: str) -> list[dict]:
    """解析浏览器请求头格式 "name1=val1; name2=val2"（F12 复制），domain 用平台默认主域名。"""
    if "=" not in text:
        raise ValueError(_COOKIE_FORMAT_ERROR)
    domain = PLATFORM_COOKIE_DOMAINS.get(platform, "")
    cookies = []
    # 复制时可能带换行，统一按分号切分
    for pair in text.replace("\r", ";").replace("\n", ";").split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        name, _, value = pair.partition("=")
        name = name.strip()
        if not name:
            continue
        cookies.append({
            "name": name, "value": value.strip(),
            "domain": domain, "path": "/",
            # 请求头格式拿不到过期时间，默认给一年，避免落地即成会话 cookie
            "expires": int(time.time()) + 365 * 24 * 3600,
            "httpOnly": False, "secure": False, "sameSite": "Lax",
        })
    if not cookies:
        raise ValueError(_COOKIE_FORMAT_ERROR)
    return cookies


def import_cookie_string(platform: str, account: str, content: str) -> Path:
    """粘贴字符串导入 cookie：自动识别 storage_state JSON / Netscape 文本 / 请求头字符串，
    统一转成 playwright storage_state JSON 保存（复用 save_cookie 的校验逻辑）。"""
    text = (content or "").strip()
    if not text:
        raise ValueError(_COOKIE_FORMAT_ERROR)
    # ① storage_state JSON（含 cookies 字段）→ 直接用
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(_COOKIE_FORMAT_ERROR)
        if not isinstance(data, dict) or "cookies" not in data:
            raise ValueError(_COOKIE_FORMAT_ERROR)
        return save_cookie(platform, json.dumps(data, ensure_ascii=False).encode("utf-8"), account)
    # ② Netscape cookie 文件文本 → 转 storage_state
    cookies = _parse_netscape_cookies(text)
    if cookies is None:
        # ③ 浏览器请求头格式 "name=val; …"（最常见）→ 转 storage_state
        cookies = _parse_header_cookies(platform, text)
    state = {"cookies": cookies, "origins": []}
    return save_cookie(platform, json.dumps(state, ensure_ascii=False).encode("utf-8"), account)


def export_cookie_string(platform: str, account: str = PUBLISH_ACCOUNT) -> str:
    """读 storage_state JSON → 输出请求头格式 "name=value; …"（只导出该平台主域名的 cookie）。"""
    src = cookie_path(platform, account)
    if not src.exists():
        raise FileNotFoundError(f"{platform}({account}) 还没有 cookie")
    with open(src, encoding="utf-8") as f:
        state = json.load(f)
    cookies = state.get("cookies")
    if cookies is None:
        # B站扫码登录存的是 biliup 格式（cookie_info.cookies），同样支持导出
        cookies = (state.get("cookie_info") or {}).get("cookies") or []
    suffix = PLATFORM_COOKIE_DOMAINS.get(platform, "").lstrip(".")
    pairs = []
    for c in cookies:
        name = c.get("name")
        if not name:
            continue
        d = (c.get("domain") or "").lstrip(".")
        # 只导出平台主域名的 cookie（无 domain 字段的 biliup 条目不过滤）
        if suffix and d and d != suffix and not d.endswith("." + suffix):
            continue
        pairs.append(f"{name}={c.get('value', '')}")
    return "; ".join(pairs)


def delete_cookie(platform: str, account: str) -> bool:
    """删除指定账号的 cookie 文件（含 netscape 缓存），返回是否删到了文件。"""
    path = cookie_path(platform, account)
    existed = path.exists()
    if existed:
        path.unlink()
    netscape = path.with_suffix(".netscape.txt")
    if netscape.exists():
        netscape.unlink()
    return existed


def list_accounts() -> list[dict]:
    """扫描 cookies 目录，列出所有 平台_账号.json（多账号）。"""
    result = []
    for p in PLATFORMS:
        found: dict[str, Path] = {}
        for f in sorted(PUBLISHER_COOKIES_DIR.glob(f"{p}_*.json")):
            account = f.stem[len(p) + 1:]
            if valid_account_name(account):
                found[account] = f
        # 没有任何 cookie 时也显示默认账号占位，方便前端引导
        if not found:
            result.append({"platform": p, "account": PUBLISH_ACCOUNT,
                           "cookie_exists": False, "updated_at": None})
        for account, path in found.items():
            result.append({
                "platform": p,
                "account": account,
                "cookie_exists": True,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(path.stat().st_mtime)),
            })
    return result
