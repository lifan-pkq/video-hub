"""Video Hub 后端入口：视频库 + 抖音下载 + 多平台发布 + 直播录制 + 主播监控。"""
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import NARRATO_TASKS_DIR, ensure_dirs
from app.db import fail_interrupted_recordings
from app.routers import accounts, download, edit, publish, recordings, sms, stats, videos, watchers
from app.services import narrato_import, watcher

app = FastAPI(title="Video Hub", version="1.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(videos.router)
app.include_router(edit.router)
app.include_router(download.router)
app.include_router(publish.router)
app.include_router(accounts.router)
app.include_router(recordings.router)
app.include_router(sms.router)
app.include_router(stats.router)
app.include_router(watchers.router)


@app.on_event("startup")
async def _startup():
    ensure_dirs()
    fail_interrupted_recordings()
    asyncio.create_task(watcher.watcher_loop())
    if NARRATO_TASKS_DIR.is_dir():
        asyncio.create_task(narrato_import.importer_loop())


@app.get("/api/health")
def health():
    return {"status": "ok"}
