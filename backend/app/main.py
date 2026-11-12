"""Video Hub：素材、剪辑、AI 创作与成品导出。"""
import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import auth
from app.config import CORS_ORIGINS, ensure_dirs
from app.db import fail_interrupted_recordings
from app.jobs import fail_interrupted_jobs
from app.services.understanding import fail_interrupted_analyses
from app.routers import (
    ai, download, drama, edit, events, exports, inspirations, jobs, maintenance, profiles,
    recordings, stats, system, understanding, videos,
)
from app.services import drama_store, maintenance as maintenance_service, narrato_import
from app.services.events import event_hub

app = FastAPI(title="Video Hub", version="2.0.0")

app.add_middleware(auth.AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


class LoginBody(BaseModel):
    token: str


@app.post("/api/auth/login")
def login(body: LoginBody, response: Response):
    """口令换 Cookie：一次登录即可覆盖所有 <img>/<video>/WS 子请求。"""
    if auth.MODE == "off":
        return {"ok": True, "auth": "off"}
    if not auth.login(body.token):
        raise HTTPException(401, "口令错误")
    response.set_cookie(
        auth.COOKIE_NAME, body.token.strip(),
        max_age=30 * 24 * 3600, httponly=True, samesite="lax", path="/",
        secure=False,  # TLS 上线后改 True
    )
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


from engine.errors import EngineError


@app.exception_handler(EngineError)
def engine_error(_request, exc: EngineError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.as_dict()})


app.include_router(videos.router)
app.include_router(edit.router)
app.include_router(jobs.router)
app.include_router(download.router)
app.include_router(recordings.router)
app.include_router(stats.router)
app.include_router(events.router)
app.include_router(system.router)
app.include_router(maintenance.router)
app.include_router(ai.router)
app.include_router(drama.router)
app.include_router(understanding.router)
app.include_router(profiles.router)
app.include_router(exports.router)
app.include_router(inspirations.router)


@app.on_event("startup")
async def _startup():
    ensure_dirs()
    event_hub.bind_loop()
    fail_interrupted_recordings()
    fail_interrupted_analyses()
    fail_interrupted_jobs()
    drama_store.interrupt_active_projects()
    maintenance_service.sweep_temp()
    asyncio.create_task(narrato_import.importer_loop())
    asyncio.create_task(maintenance_service.maintenance_loop())


@app.get("/api/health")
def health():
    import shutil

    from engine.llm import configured
    from engine.settings import asr_url, vision_configured

    asr = "remote" if asr_url() else "unconfigured"
    try:
        import importlib.util

        if not asr_url() and importlib.util.find_spec("faster_whisper") is not None:
            asr = "whisper"
    except Exception:
        pass
    from app.config import DATA_DIR
    from app.services.media import free_mb

    return {
        "status": "ok",
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "llm": configured(),
        "vision": vision_configured(),
        "asr": asr,
        "disk_free_mb": free_mb(DATA_DIR),
    }
