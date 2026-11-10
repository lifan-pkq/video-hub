"""本地平台 cookie：供下载器 / 作者抓取把 playwright storage_state 转成 yt-dlp 可用格式。

账号管理路由已随账号体系一并删除，这里只保留下载链路实际用到的函数。
"""
import json
import os
import tempfile
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


def _cookie_list_from_state(state: dict) -> list[dict]:
    """兼容 playwright storage_state 和 B站 cookie_info 两种结构。"""
    if isinstance(state.get("cookies"), list):
        return [c for c in state["cookies"] if isinstance(c, dict)]
    info = state.get("cookie_info")
    if isinstance(info, dict) and isinstance(info.get("cookies"), list):
        return [c for c in info["cookies"] if isinstance(c, dict)]
    return []


def _write_cookie_file(path: Path, data: bytes) -> None:
    """原子替换写 cookie 文件，权限 0600（与 accounts 上传路由一致）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp_path = path.parent / tmp_name
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)
