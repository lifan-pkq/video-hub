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
