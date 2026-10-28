"""WebSocket 实时事件中心：订阅者管理 + 线程安全广播。

任务在后台同步线程里更新进度，broadcast() 可从任意线程调用：
通过 loop.call_soon_threadsafe 把事件投回 asyncio 事件循环，再在循环内
分发到各连接的队列。每个 WS 连接独占一个队列和自己的发送协程，
慢消费者队列满了直接丢事件（进度类事件可丢，终态事件另有必发保证）。
"""
import asyncio
import itertools
import logging

log = logging.getLogger(__name__)


class EventHub:
    # 每个订阅连接的缓冲上限；1.9G 内存机器上保持轻量
    QUEUE_MAX = 200

    def __init__(self):
        self._subs: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seq = itertools.count(1)  # GIL 下 next() 原子，跨线程安全

    def bind_loop(self):
        """服务启动时在事件循环协程内调用一次，之后 broadcast 才生效。"""
        self._loop = asyncio.get_running_loop()

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def subscribe(self) -> asyncio.Queue:
        """在事件循环协程内调用，返回该连接专属的事件队列。"""
        q: asyncio.Queue = asyncio.Queue(maxsize=self.QUEUE_MAX)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._subs.discard(q)

    def broadcast(self, event: dict):
        """线程安全广播：任意线程（含下载/发布的同步线程）均可调用。

        事件附带单调递增 seq；事件循环未启动/已关闭时静默丢弃。
        """
        event = {**event, "seq": next(self._seq)}
        loop = self._loop
        if loop is None:
            return
        try:
            loop.call_soon_threadsafe(self._fanout, event)
        except RuntimeError:
            pass  # 事件循环已关闭（服务退出中），直接丢弃

    def _fanout(self, event: dict):
        """仅在事件循环线程内执行：投递到每个订阅者队列，满了就丢，绝不阻塞。"""
        for q in tuple(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                log.warning("event hub: 慢消费者，丢弃事件 seq=%s", event.get("seq"))


event_hub = EventHub()
