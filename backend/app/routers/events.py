"""实时事件中心：/api/ws WebSocket，连接即发任务快照，之后转发广播事件并定期保活。"""
import asyncio
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.events import event_hub
from app.services.tasks import task_manager

router = APIRouter(prefix="/api", tags=["events"])

PING_INTERVAL = 25  # 秒；应用层保活，避免反代空闲断连


@router.websocket("/ws")
async def ws_events(ws: WebSocket):
    await ws.accept()
    queue = event_hub.subscribe()
    try:
        # 先订阅再发快照：期间到达的事件进入队列，随后由 writer 按序补发，不丢不乱
        await ws.send_json({
            "type": "snapshot",
            "tasks": [t.to_dict(with_logs=False) for t in task_manager.list(limit=20)],
        })
        writer = asyncio.create_task(_writer(ws, queue))
        try:
            while True:
                # 客户端无上行消息，receive 仅为及时感知断开
                await ws.receive_text()
        finally:
            writer.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        event_hub.unsubscribe(queue)  # 断开必须清理，防止泄漏


async def _writer(ws: WebSocket, queue: asyncio.Queue):
    """每条连接唯一的发送协程：转发队列事件，空闲 25s 发 ping 保活。"""
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
            except asyncio.TimeoutError:
                event = {"type": "ping"}
            await ws.send_text(json.dumps(event, ensure_ascii=False))
    except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
        pass
