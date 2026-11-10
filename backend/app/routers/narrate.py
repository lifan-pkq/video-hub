"""AI 解说：理解、文稿、审阅、渲染，每一步都留下可编辑产物。"""

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from engine.errors import EngineError
from engine.llm import configured
from engine.schemas import TranscriptSegment, VoiceSettings
from engine.tts import VOICES, engine_status, resolve_engine, synthesize
from app.jobs import create, submit
from app.services.videos import fetch
from app.services.narrate_flow import (
    import_output,
    list_tasks,
    load_task,
    render,
    save_script,
    task_dir,
    understand,
    write_script,
    write_transcript,
)

router = APIRouter(prefix="/api/narrate", tags=["narrate"])


class CreateBody(BaseModel):
    video_id: int
    # 默认每秒两帧；UI 不再展示抽帧数。max_frames 仅兼容旧客户端/测试。
    frames_per_second: float = Field(default=2.0, ge=0.5, le=4.0)
    max_frames: int | None = Field(default=None, ge=1, le=60)
    instruction: str = ""
    tone: str = ""


class TranscriptBody(BaseModel):
    segments: list[TranscriptSegment]


class ScriptBody(BaseModel):
    mode: str = "llm"
    instruction: str = ""
    tone: str = "清楚、具体，不说空话"
    target_duration: float = Field(default=30, ge=5, le=600)
    language: str = "中文"


class SaveScriptBody(BaseModel):
    beats: list[dict]
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


class PreviewBody(BaseModel):
    text: str = Field(min_length=1, max_length=80)
    engine: str = "auto"
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: float = Field(default=1, ge=0.5, le=2)


def _with_task(job: dict) -> dict:
    task = None
    task_id = (job.get("result") or {}).get("task_id")
    if task_id:
        task = load_task(task_id)
    return {"job": job, "task": task}


@router.get("/health")
def health():
    return {"ok": True, "llm": configured(), "tts": engine_status()}


@router.get("/voices")
def voices():
    return {"engines": engine_status(), "voices": VOICES}


@router.get("/tasks")
def tasks(limit: int = 20):
    return list_tasks(limit)


@router.post("/tasks")
def create_task(body: CreateBody):
    fetch(body.video_id)
    instruction = body.instruction.strip()[:800]
    tone = body.tone.strip()[:80]
    fps = float(body.frames_per_second or 2.0)
    job = create(
        "narrate.understand",
        "理解素材",
        {
            "video_id": body.video_id,
            "frames_per_second": fps,
            "max_frames": body.max_frames,
            "instruction": instruction,
            "tone": tone,
        },
    )
    finished = submit(
        job,
        lambda job_id: understand(
            job_id,
            body.video_id,
            body.max_frames,
            frames_per_second=fps,
            instruction=instruction,
            tone=tone,
        ),
    )
    return _with_task(finished)


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    return load_task(task_id)


@router.put("/tasks/{task_id}/transcript")
def put_transcript(task_id: str, body: TranscriptBody):
    return write_transcript(task_id, [item.model_dump() for item in body.segments])


@router.post("/tasks/{task_id}/script")
def post_script(task_id: str, body: ScriptBody):
    task = load_task(task_id)
    mode = body.mode.strip().lower()
    if mode == "llm" and not configured():
        raise EngineError(
            "没有配置文本模型，不能写解说稿",
            code="llm_unconfigured",
            action="设置 VIDEO_HUB_AI_BASE_URL、VIDEO_HUB_AI_API_KEY、VIDEO_HUB_AI_MODEL，或改用逐句骨架 / 自己填写。",
            status=409,
        )
    if mode == "skeleton" and not (task.get("transcript") or {}).get("segments"):
        raise EngineError(
            "没有转写，无法生成逐句骨架",
            code="script_empty",
            action="先让理解步骤产出转写，或在转写表里粘贴至少一句；也可以选「自己填写解说」。",
            status=422,
        )
    if mode == "manual":
        pass
    instruction = body.instruction.strip()[:800] or str(task.get("instruction") or "").strip()[:800]
    tone = body.tone.strip()[:80] or str(task.get("tone") or "清楚、具体，不说空话").strip()[:80]
    job = create("narrate.script", "生成解说稿", {"task_id": task_id, "mode": mode})
    finished = submit(
        job,
        lambda job_id: write_script(
            job_id,
            task_id,
            mode=mode,
            instruction=instruction,
            tone=tone,
            target_duration=body.target_duration,
            language=body.language.strip()[:20] or "中文",
        ),
    )
    return _with_task(finished)


@router.put("/tasks/{task_id}/script")
def put_script(task_id: str, body: SaveScriptBody):
    return save_script(task_id, body.beats, body.voice.model_dump())


@router.post("/tasks/{task_id}/render")
def post_render(task_id: str):
    load_task(task_id)
    job = create("narrate.render", "渲染成片", {"task_id": task_id})
    finished = submit(job, lambda job_id: render(job_id, task_id))
    return _with_task(finished)


@router.post("/tasks/{task_id}/import")
def post_import(task_id: str):
    return import_output(task_id)


@router.get("/tasks/{task_id}/frames/{name}")
def frame(task_id: str, name: str):
    if not name.startswith("frame_") or not name.endswith(".jpg") or "/" in name:
        raise EngineError("画面不存在", code="missing_file", status=404)
    path = task_dir(task_id) / "frames" / name
    if not path.is_file():
        raise EngineError("画面不存在", code="missing_file", action="重新跑理解。", status=404)
    return FileResponse(path, media_type="image/jpeg")


@router.get("/tasks/{task_id}/output")
def output(task_id: str):
    task = load_task(task_id)
    filename = ((task.get("output") or {}).get("file")) or ""
    path = task_dir(task_id) / filename
    if not filename or not path.is_file():
        raise EngineError("成片还没有", code="missing_file", action="先渲染。", status=404)
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.post("/tts-preview")
def tts_preview(body: PreviewBody):
    chosen = resolve_engine(body.engine)
    from app.services.videos import data_dir

    dest_dir = data_dir() / "previews"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "preview.wav"
    synthesize(body.text.strip(), dest, engine=chosen, voice=body.voice, rate=body.rate)
    return FileResponse(dest, media_type="audio/wav", filename="preview.wav")
