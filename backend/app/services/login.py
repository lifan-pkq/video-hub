"""扫码登录会话管理：每个会话拉起一个 qr_login.py 子进程。

- 会话状态全部落在 LOGIN_DIR/<session_id>/ 下（status.json + qr.png），后端只读
- 抖音必须 xvfb + 有头（防风控），其余平台无头即可，但统一走 _build_cmd 的 xvfb 逻辑
- 会话 10 分钟过期，过期自动杀进程；成功后 cookie 由引擎写入 cookies/<platform>_<account>.json
"""
import json
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from app.config import (LOGIN_DIR, PLATFORM_NAMES, PLATFORMS, PUBLISHER_DIR,
                        SAU_PYTHON, USE_XVFB)
from app.services.cookies import valid_account_name

SESSION_TTL = 600  # 秒

_lock = threading.Lock()
_sessions: dict[str, dict] = {}


def _session_dir(session_id: str) -> Path:
    return LOGIN_DIR / session_id


def _read_status(session_id: str) -> dict:
    f = _session_dir(session_id) / "status.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {"state": "starting", "message": "正在启动浏览器…"}


def start_login(platform: str, account: str) -> dict:
    if platform not in PLATFORMS:
        raise ValueError("不支持的平台")
    if not valid_account_name(account):
        raise ValueError("账号名只能包含字母、数字、下划线、短横线（1-32 位）")
    if not SAU_PYTHON.exists():
        raise RuntimeError(f"发布引擎 Python 不存在: {SAU_PYTHON}")
    # 同平台同账号只允许一个进行中的会话
    with _lock:
        for s in _sessions.values():
            if (s["platform"], s["account"]) == (platform, account) and not s["done"]:
                raise ValueError(f"{PLATFORM_NAMES[platform]}({account}) 已有进行中的登录会话")
        session_id = uuid.uuid4().hex[:12]
        d = _session_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        cmd = [str(SAU_PYTHON), str(PUBLISHER_DIR / "qr_login.py"),
               "--platform", platform, "--account", account,
               "--status-file", str(d / "status.json"),
               "--qr-path", str(d / "qr.png"),
               "--headed"]
        if USE_XVFB and shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a", *cmd]
        log_f = open(d / "process.log", "wb")
        try:
            proc = subprocess.Popen(cmd, cwd=str(PUBLISHER_DIR),
                                    stdout=log_f, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL)
        except Exception:
            log_f.close()
            raise
        _sessions[session_id] = {
            "platform": platform, "account": account,
            "proc": proc, "log_f": log_f,
            "created": time.time(), "done": False,
        }
        threading.Thread(target=_watch, args=(session_id,), daemon=True).start()
    return {"session_id": session_id, "platform": platform, "account": account}


def _watch(session_id: str):
    s = _sessions.get(session_id)
    if not s:
        return
    proc: subprocess.Popen = s["proc"]
    try:
        proc.wait(timeout=SESSION_TTL)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        # 进程被超时杀掉但引擎没写最终状态时补写
        st = _read_status(session_id)
        if st.get("state") in ("starting", "qrcode_ready"):
            f = _session_dir(session_id) / "status.json"
            f.write_text(json.dumps({"state": "timeout", "message": "扫码超时，请重新发起登录",
                                     "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                                    ensure_ascii=False), encoding="utf-8")
    s["done"] = True
    try:
        s["log_f"].close()
    except Exception:
        pass


def get_login(session_id: str) -> dict | None:
    with _lock:
        s = _sessions.get(session_id)
        if not s:
            return None
        st = _read_status(session_id)
        qr = _session_dir(session_id) / "qr.png"
        return {
            "session_id": session_id,
            "platform": s["platform"],
            "account": s["account"],
            "state": st.get("state", "starting"),
            "message": st.get("message", ""),
            "has_qr": qr.exists(),
            "qr_version": int(qr.stat().st_mtime) if qr.exists() else 0,
            "done": s["done"],
        }


def qr_file(session_id: str) -> Path | None:
    if session_id not in _sessions:
        return None
    qr = _session_dir(session_id) / "qr.png"
    return qr if qr.exists() else None


def cancel_login(session_id: str) -> bool:
    with _lock:
        s = _sessions.get(session_id)
        if not s:
            return False
        proc: subprocess.Popen = s["proc"]
        if proc.poll() is None:
            proc.kill()
        return True
