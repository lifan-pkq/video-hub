"""读取本地平台 cookie（仅供下载器 / 作者抓取使用，不再提供账号登录管理）。"""
import json
from pathlib import Path

from app.config import COOKIES_DIR, DEFAULT_COOKIE_NAME


def cookie_path(platform: str, name: str = DEFAULT_COOKIE_NAME) -> Path:
    return COOKIES_DIR / f"{platform}_{name}.json"


def cookie_exists(platform: str, name: str = DEFAULT_COOKIE_NAME) -> bool:
    return cookie_path(platform, name).exists()


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


def make_ytdlp_cookiefile(platform: str, name: str = DEFAULT_COOKIE_NAME) -> Path | None:
    src = cookie_path(platform, name)
    if not src.exists():
        return None
    out = src.with_suffix(".netscape.txt")
    if out.exists() and out.stat().st_mtime >= src.stat().st_mtime:
        return out
    storage_state_to_netscape(src, out)
    return out


def storage_state_to_requests_cookies(platform: str, name: str = DEFAULT_COOKIE_NAME) -> dict:
    src = cookie_path(platform, name)
    if not src.exists():
        return {}
    with open(src, encoding="utf-8") as f:
        state = json.load(f)
    return {c["name"]: c["value"] for c in state.get("cookies", []) if c.get("name")}
