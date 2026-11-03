"""Video Hub 后端入口：视频库 + 抖音下载 + 直播录制 + 主播监控 + AI 创作。"""
import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import ensure_dirs
from app.db import fail_interrupted_recordings
from app.routers import ai, author, download, edit, events, maintenance, narrate, recordings, stats, system, videos, watchers
from app.services import maintenance as maintenance_service, narrato_import, watcher
from app.services.events import event_hub

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
app.include_router(recordings.router)
app.include_router(stats.router)
app.include_router(watchers.router)
app.include_router(author.router)
app.include_router(events.router)
app.include_router(narrate.router)
app.include_router(system.router)
app.include_router(maintenance.router)
app.include_router(ai.router)


@app.on_event("startup")
async def _startup():
    ensure_dirs()
    event_hub.bind_loop()
    fail_interrupted_recordings()
    asyncio.create_task(watcher.watcher_loop())
    asyncio.create_task(narrato_import.importer_loop())
    asyncio.create_task(maintenance_service.maintenance_loop())


@app.get("/api/health")
def health():
    return {"status": "ok"}
