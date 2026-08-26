"""主播监控（自动录制）：轮询启用的 watcher，开播即按预设画质/帧率自动录制。

- 每 60 秒检查一次（WATCH_POLL_INTERVAL 环境变量可调）
- 开播 → start_recording（沿用录制服务的全部逻辑）；录制结束 → 自动重新进入监控
- 关播判断：抖音 = 页面解析不出流地址；B站 = room get_info 的 live_status
"""
import asyncio
import json
import logging
import re
import time
import urllib.request

from app.db import (get_recording, list_watchers, update_watcher)
from app.services import recorder

log = logging.getLogger("watcher")

POLL_INTERVAL = 60  # 秒


def _is_live_douyin(url: str) -> bool:
    try:
        return bool(recorder.resolve_stream_url(url, "douyin", "best"))
    except Exception:
        return False


def _is_live_bilibili(url: str) -> bool:
    try:
        room_id = recorder._bilibili_room_id(url)
        text = recorder._http_get(
            f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={room_id}")
        return (json.loads(text).get("data") or {}).get("live_status") == 1
    except Exception:
        return False


def is_live(platform: str, url: str) -> bool:
    if platform == "douyin":
        return _is_live_douyin(url)
    if platform == "bilibili":
        return _is_live_bilibili(url)
    return False


async def check_watcher(w: dict):
    """单个 watcher 的一次轮询。"""
    wid = w["id"]
    live = await asyncio.to_thread(is_live, w["platform"], w["url"])
    update_watcher(wid, live=1 if live else 0,
                   last_checked_at=time.strftime("%Y-%m-%d %H:%M:%S"))

    active_id = w.get("active_recording_id")
    if active_id:
        rec = get_recording(active_id)
        if rec and rec["status"] == "recording":
            return  # 正在录，什么都不做
        # 上一场已结束，重新武装（若还在播，下面会立刻开新一场）
        update_watcher(wid, active_recording_id=None)
        active_id = None

    if live and not active_id:
        title = f"{w['name'] or '主播'} · {time.strftime('%m-%d %H:%M')}场"
        try:
            rec = await asyncio.to_thread(
                recorder.start_recording, w["url"], title, w["quality"], w["fps"])
            update_watcher(wid, active_recording_id=rec["id"])
            log.info("watcher %s 检测到开播，自动开始录制 %s", wid, title)
        except Exception as e:
            log.warning("watcher %s 自动录制启动失败: %s", wid, e)


async def watcher_loop():
    """常驻轮询循环（FastAPI 启动时拉起）。"""
    while True:
        try:
            for w in list_watchers(enabled_only=True):
                try:
                    await check_watcher(w)
                except Exception as e:
                    log.warning("watcher %s 轮询异常: %s", w.get("id"), e)
        except Exception as e:
            log.warning("watcher 轮询循环异常: %s", e)
        await asyncio.sleep(POLL_INTERVAL)
