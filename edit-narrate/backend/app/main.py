"""Standalone Video Hub edit + narration server.

Drop-in deploys should include the routers from an existing app, not replace this file.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from engine import __version__
from engine.errors import EngineError
from engine.llm import configured
from engine.settings import asr_url, vision_configured
from engine.tts import engine_status
from app.routers import edit, jobs, library, narrate

app = FastAPI(title="Video Hub 剪辑与解说", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(EngineError)
def engine_error(_request, exc: EngineError):
    return JSONResponse(status_code=exc.status, content={"detail": exc.as_dict()})


@app.get("/api/health")
def health():
    engines = engine_status()
    asr = "remote" if asr_url() else "whisper" if _whisper() else "unconfigured"
    return {
        "ok": True,
        "version": __version__,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "llm": configured(),
        "vision": vision_configured(),
        "asr": asr,
        "tts": engines,
    }


def _whisper() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


app.include_router(library.router)
app.include_router(edit.router)
app.include_router(narrate.router)
app.include_router(jobs.router)

_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    from fastapi.responses import FileResponse

    @app.get("/edit")
    @app.get("/edit/{path:path}")
    @app.get("/narrate")
    @app.get("/narrate/{path:path}")
    def spa_pages(path: str = ""):
        return FileResponse(_dist / "index.html")

    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
