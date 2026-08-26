"""browser-use 0.13 直接控制浏览器演示：打开 Bing -> 搜索 -> 截图。"""
import asyncio
import base64
import pathlib

from browser_use.browser import BrowserSession
from browser_use.browser.profile import BrowserProfile
from browser_use.browser.events import (
    ClickElementEvent,
    TypeTextEvent,
    SendKeysEvent,
)

OUT = pathlib.Path(__file__).parent


async def dispatch(session, event):
    ev = session.event_bus.dispatch(event)
    await ev
    await ev.event_result(raise_if_any=True, raise_if_none=False)


async def main():
    session = BrowserSession(browser_profile=BrowserProfile(
        headless=False,
        executable_path="/usr/bin/google-chrome",
        enable_default_extensions=False,
    ))
    await session.start()
    print("[1] 浏览器已启动")

    await session.navigate_to("https://www.bing.com")
    await asyncio.sleep(2)
    print("[2] 已打开 Bing:", await session.get_current_page_url())

    # 取 DOM 状态，找到搜索框节点
    state = await session.get_browser_state_summary()
    search_node = None
    fallback = None
    for idx, node in state.dom_state.selector_map.items():
        attrs = getattr(node, "attributes", {}) or {}
        tag = (getattr(node, "tag_name", "") or "").lower()
        if attrs.get("id") == "sb_form_q" or attrs.get("name") == "q":
            search_node = node
            break
        if fallback is None and tag in ("input", "textarea"):
            fallback = node
    node = search_node or fallback
    assert node is not None, "没找到搜索框"
    print("[3] 找到搜索框:", getattr(node, "tag_name", "?"), getattr(node, "attributes", {}))

    await dispatch(session, ClickElementEvent(node=node))
    await dispatch(session, TypeTextEvent(node=node, text="browser-use github", clear=True))
    print("[4] 已输入搜索关键词")

    await dispatch(session, SendKeysEvent(keys="Enter"))
    await asyncio.sleep(3)
    print("[5] 已回车，当前页面:", await session.get_current_page_url())
    print("    标题:", await session.get_current_page_title())

    shot = await session.take_screenshot()
    if isinstance(shot, str):
        data = base64.b64decode(shot)
    else:
        data = shot
    path = OUT / "bing_result.png"
    path.write_bytes(data)
    print("[6] 截图已保存:", path)

    await asyncio.sleep(2)
    await session.stop()
    print("[7] 浏览器已关闭，演示结束")


asyncio.run(main())
