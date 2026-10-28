"""子进程树终止工具：进程组 kill + 孤儿浏览器收割。

chromium 会自行脱离原进程组（zygote 机制），killpg 杀不到它；
但它被 init（PID 1）收养（ppid=1），据此可安全识别并清理孤儿浏览器进程。
容器内只有本应用会起 chrome，收割范围是安全的。
"""
import os
import signal
import subprocess
from pathlib import Path

_BROWSER_NAMES = {"chrome", "chrome_crashpad", "headless_shell"}


def kill_tree(proc: subprocess.Popen):
    """先 SIGTERM 进程组（给优雅退出机会），5 秒后 SIGKILL，最后收割孤儿浏览器。"""
    if proc.poll() is not None:
        reap_orphan_browsers()
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
    reap_orphan_browsers()


def reap_orphan_browsers():
    """收割被 init 收养（ppid=1）的孤儿浏览器进程——它们是失控子进程的残留。"""
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
            if comm not in _BROWSER_NAMES:
                continue
            stat = (entry / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[-1].split()[1])  # comm 可能含空格，取最后一个 ) 之后
            if ppid == 1:
                os.kill(int(entry.name), signal.SIGKILL)
        except Exception:
            continue
