"""三平台发布：封装内置发布引擎的 sau CLI（subprocess 调用，逐平台串行执行）。

- sau 命令的退出码为 0 视为成功，stdout/stderr 全部进任务日志
- 无显示环境自动用 xvfb-run 包裹（抖音 cookie 校验需要有头浏览器）
- 每次发布同时落库 publish_records，供历史查询
"""
import asyncio
import os
import re
import shutil
import signal

from app.config import (BILI_TID, PLATFORM_NAMES, PLATFORMS, PUBLISH_ACCOUNT, PUBLISH_TIMEOUT,
                        PUBLISHER_DIR, SAU_BIN, USE_XVFB)
from app.db import add_publish_record, finish_publish_record


def sau_available() -> bool:
    return SAU_BIN.exists()


# 全局发布锁：同时只跑一个发布任务（多个并发任务会共用 verify_code.txt、
# 互发短信验证码导致彼此作废；服务器内存也扛不住多个浏览器）
publish_lock = asyncio.Lock()


async def run_publish_guarded(task, *args, **kwargs):
    """带全局锁的发布入口：有任务在跑时排队等待。"""
    if publish_lock.locked():
        task.log("前面有发布任务正在进行，本任务排队等待…")
    async with publish_lock:
        await run_publish(task, *args, **kwargs)


def _build_cmd(platform: str, args: list[str]) -> list[str]:
    cmd = [str(SAU_BIN), platform, *args]
    if USE_XVFB and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", *cmd]
    return cmd


def _sanitize_log(text: str) -> str:
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]*", r"\1?<redacted>", text)
    text = re.sub(r"(验证码(?:是|为|[:：])?\s*)[A-Za-z0-9-]{4,}", r"\1<redacted>", text)
    return re.sub(r"(?i)(--(?:verify[-_]?code|code)(?:=|\s+))\S+", r"\1<redacted>", text)


async def _terminate_process_group(proc):
    if proc.returncode is None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            proc.kill()
    await proc.wait()


async def _run_sau(task, platform: str, args: list[str]) -> tuple[int, str]:
    """运行 sau 子进程，实时把输出写入任务日志，返回 (退出码, 全部输出)。"""
    cmd = _build_cmd(platform, args)
    task.log(f"$ {_sanitize_log(' '.join(cmd))}")
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(PUBLISHER_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    output: list[str] = []

    async def consume() -> int:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            line = re.sub(r"\x1b\[[0-9;]*m", "", line)
            if line.strip():
                output.append(_sanitize_log(line))
                # 抖音短信验证状态跟踪：引擎弹出验证 → 通知前端弹窗；填完 → 关闭弹窗
                if "等待验证码输入" in line or "已点击「获取验证码」" in line:
                    task.needs_verify = True
                elif "验证码未通过" in line:
                    # 上一个码被抖音拒绝，引擎已自动重发短信，提示用户输入新码
                    task.needs_verify = True
                elif "验证码已填入输入框" in line or "验证码处理完成" in line:
                    task.needs_verify = False
                task.log(f"[{PLATFORM_NAMES.get(platform, platform)}] {_sanitize_log(line)}")
        return await proc.wait()

    try:
        code = await asyncio.wait_for(consume(), timeout=PUBLISH_TIMEOUT)
    except asyncio.TimeoutError:
        await _terminate_process_group(proc)
        task.log(f"[{PLATFORM_NAMES.get(platform, platform)}] 执行超时，已终止")
        return -1, "\n".join(output)
    return code, "\n".join(output)


async def check_platform(platform: str, account: str = PUBLISH_ACCOUNT) -> dict:
    """校验某平台某账号 cookie 是否有效（调用 sau <platform> check）。"""
    if platform not in PLATFORMS:
        return {"platform": platform, "account": account, "valid": False, "detail": "不支持的平台"}
    if not sau_available():
        return {"platform": platform, "account": account, "valid": False, "detail": f"发布引擎未安装: {SAU_BIN}"}
    cmd = _build_cmd(platform, ["check", "--account", account])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(PUBLISHER_DIR),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        text = out.decode("utf-8", errors="replace")
        valid = proc.returncode == 0 and "valid" in text.lower()
        return {"platform": platform, "account": account, "valid": valid,
                "detail": "cookie 有效" if valid else "cookie 无效或已失效"}
    except asyncio.TimeoutError:
        await _terminate_process_group(proc)
        return {"platform": platform, "account": account, "valid": False, "detail": "校验超时"}
    except Exception as e:
        return {"platform": platform, "account": account, "valid": False, "detail": _sanitize_log(str(e))}


async def run_publish(task, video: dict, video_path, targets: list[dict],
                      title: str, desc: str, tags: list[str], schedule: str | None):
    """targets: [{"platform": "douyin", "account": "default"}, ...]，逐个串行发布。"""
    task.status = "running"
    # 清掉可能残留的过期验证码，避免新任务误读
    stale_code = PUBLISHER_DIR / "verify_code.txt"
    if stale_code.exists():
        stale_code.unlink()
    results: dict[str, dict] = {}
    for t in targets:
        platform, account = t["platform"], t.get("account") or PUBLISH_ACCOUNT
        key = f"{platform}:{account}"
        if platform not in PLATFORMS:
            results[key] = {"status": "failed", "message": "不支持的平台"}
            continue
        name = f"{PLATFORM_NAMES[platform]}({account})"
        task.log(f"—— 开始发布到 {name} ——")
        record_id = add_publish_record(task.id, video.get("id"), video.get("title", ""),
                                       platform, title, desc, tags, account=account, schedule=schedule)
        args = ["upload-video", "--account", account,
                "--file", str(video_path), "--title", title]
        # B站的 sau 子命令要求 desc/tid 必填
        if desc or platform == "bilibili":
            args += ["--desc", desc or title]
        if tags:
            args += ["--tags", ",".join(tags)]
        if platform == "bilibili":
            args += ["--tid", str(BILI_TID)]
        if schedule:
            args += ["--schedule", schedule]
        try:
            code, output = await _run_sau(task, platform, args)
        except Exception as e:
            code, output = -1, str(e)
        ok = code == 0
        results[key] = {"status": "success" if ok else "failed",
                        "message": "发布成功" if ok else "发布失败，详见日志"}
        finish_publish_record(record_id, results[key]["status"], output[-8000:])
        task.log(f"—— {name} {'✅ 发布成功' if ok else '❌ 发布失败'} ——")

    task.result = {"targets": results, "video_id": video.get("id")}
    succeeded = sum(1 for r in results.values() if r["status"] == "success")
    if succeeded == len(targets):
        task.finish("success")
    elif succeeded == 0:
        task.finish("failed")
    else:
        task.finish("partial")
