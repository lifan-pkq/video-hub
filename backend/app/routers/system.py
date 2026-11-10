"""服务器运行状态：内存/交换/磁盘/负载 + 关联服务健康度与进程内存。"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from fastapi import APIRouter

from app.config import DATA_DIR

router = APIRouter(prefix="/api/system", tags=["system"])

SERVICES = [
    ("backend", "Video Hub 后端", "", "video-hub-backend"),
]


def _read_meminfo() -> dict:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if key in {"MemTotal", "MemAvailable", "MemFree", "SwapTotal", "SwapFree"}:
                    values[key] = int(rest.strip().split()[0])
    except (OSError, ValueError, IndexError):
        return {}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(0, total - available)
    swap_total = values.get("SwapTotal", 0)
    swap_free = values.get("SwapFree", 0)
    return {
        "total_mb": round(total / 1024, 1),
        "used_mb": round(used / 1024, 1),
        "available_mb": round(available / 1024, 1),
        "percent": round(used / total * 100, 1) if total else 0,
        "swap_total_mb": round(swap_total / 1024, 1),
        "swap_used_mb": round(max(0, swap_total - swap_free) / 1024, 1),
    }


def _process_memory_mb(pid: int | None) -> float | None:
    if not pid or pid <= 0:
        return None
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def _main_pid(unit: str) -> int | None:
    try:
        result = subprocess.run(
            ["systemctl", "show", "-p", "MainPID", "--value", unit],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return int(result.stdout.strip() or 0) or None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _probe_service(item: tuple[str, str, str, str]) -> dict:
    key, name, url, unit = item
    started = time.monotonic()
    active = True
    latency = 0.0
    detail = ""
    if url:
        try:
            response = requests.get(url, timeout=2)
            latency = round((time.monotonic() - started) * 1000, 0)
            active = response.status_code < 400
            detail = f"HTTP {response.status_code}"
        except requests.RequestException as exc:
            latency = round((time.monotonic() - started) * 1000, 0)
            active = False
            detail = str(exc)[:120]
    pid = _main_pid(unit) if unit else os.getpid()
    return {
        "key": key,
        "name": name,
        "active": active,
        "latency_ms": latency,
        "memory_mb": _process_memory_mb(pid),
        # PID 与上游异常文本（含内网 URL/端口）属于内部拓扑信息，不再对外返回
        "pid": None,
        "detail": "",
    }


@router.get("/status")
def system_status():
    memory = _read_meminfo()
    try:
        disk = shutil.disk_usage(str(DATA_DIR))
        disk_info = {
            "total_gb": round(disk.total / 1024 ** 3, 1),
            "used_gb": round(disk.used / 1024 ** 3, 1),
            "free_gb": round(disk.free / 1024 ** 3, 1),
            "percent": round(disk.used / disk.total * 100, 1) if disk.total else 0,
        }
    except OSError:
        disk_info = {}
    try:
        load = [round(value, 2) for value in os.getloadavg()]
    except OSError:
        load = []
    try:
        uptime = round(float(Path("/proc/uptime").read_text(encoding="ascii").split()[0]), 0)
    except (OSError, ValueError, IndexError):
        uptime = None
    with ThreadPoolExecutor(max_workers=len(SERVICES)) as pool:
        services = list(pool.map(_probe_service, SERVICES))
    return {
        "memory": memory,
        "disk": disk_info,
        "load": load,
        "uptime_seconds": uptime,
        "services": services,
        "checked_at": int(time.time()),
    }
