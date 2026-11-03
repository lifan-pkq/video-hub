"""单任务 worker：脚本生成 + TTS/剪辑/字幕/合成，全程写文件态进度。

由 engine.server 以子进程方式拉起，因此可以独立取消、崩溃隔离，也不会
把 Streamlit 的 session_state 带进生成流程。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.profiles import build_creative_context  # noqa: E402
from engine.script import (  # noqa: E402
    build_markdown_from_transcript,
    generate_items,
)
from engine.settings import ensure_config, env_llm_overrides  # noqa: E402
from engine.spec import parse_timestamp_range  # noqa: E402
from engine.pronunciation import apply as apply_pronunciation  # noqa: E402
from engine.store import TaskStore  # noqa: E402

SCRIPT_PROGRESS_END = 30
GENERATION_OFFSET = 30
GENERATION_SCALE = 0.7


def log(message: str) -> None:
    print(f"[engine] {message}", flush=True)


def _apply_runtime_config() -> None:
    ensure_config(ROOT)
    from app.config import config

    app = config.app
    overrides = env_llm_overrides()
    # 环境变量是部署级显式配置，优先级高于 config.toml 里的示例/旧值。
    for field, env_key in (
        ("text_openai_base_url", "text_base_url"),
        ("text_openai_api_key", "text_api_key"),
        ("text_openai_model_name", "text_model"),
        ("vision_openai_base_url", "vision_base_url"),
        ("vision_openai_api_key", "vision_api_key"),
        ("vision_openai_model_name", "vision_model"),
    ):
        if overrides.get(env_key):
            app[field] = overrides[env_key]
    if app.get("text_openai_api_key"):
        app["text_llm_provider"] = "openai"
    if app.get("vision_openai_api_key"):
        app["vision_llm_provider"] = "openai"
    config.llm_text_timeout = int(app.get("llm_text_timeout", 180) or 180)


def _probe(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            return {}
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams") or []
        video = next((item for item in streams if item.get("width")), {})
        duration = float((payload.get("format") or {}).get("duration") or 0)
        return {
            "duration": round(duration, 2),
            "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0),
        }
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


class PipelineState:
    """把 task.py 内部的进度写入任务目录，供引擎 HTTP 层读取。"""

    def __init__(self, store: TaskStore, task_id: str):
        self.store = store
        self.task_id = task_id

    def update_task(self, task_id: str, state: int = 4, progress: int = 0, **kwargs: Any) -> None:
        current = self.store.read_state(self.task_id) or {}
        if current.get("status") == "cancelled":
            return
        try:
            raw_progress = float(progress)
        except (TypeError, ValueError):
            raw_progress = 0.0
        mapped = GENERATION_OFFSET + max(0.0, min(100.0, raw_progress)) * GENERATION_SCALE
        if int(state) == 1:
            mapped = 99.0
        status = "failed" if int(state) == -1 else "running"
        fields = {
            "status": status,
            "progress": round(mapped, 1),
            "phase": "generating",
            "message": str(kwargs.get("message") or current.get("message") or "正在生成视频"),
            "step_current": kwargs.get("step_current", 0),
            "step_total": kwargs.get("step_total", 6),
        }
        if kwargs.get("ffmpeg_progress") is not None:
            fields["ffmpeg_progress"] = kwargs.get("ffmpeg_progress")
        self.store.update_state(self.task_id, **fields)

    def get_task(self, task_id: str):
        return self.store.read_state(self.task_id)


def _srt_time(seconds: float) -> str:
    millis = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _asr_markdown(spec: dict[str, Any], store: TaskStore) -> str:
    """调用共享 faster-whisper ASR（8600）识别口播，生成带时间轴的素材。"""
    import requests

    url = (
        os.environ.get("VIDEO_HUB_ASR_URL", "").strip()
        or os.environ.get("NARRATO_ASR_URL", "").strip()
        or "http://127.0.0.1:8600/v1/audio/transcriptions"
    )
    language = "en" if spec.get("language") == "英文" else "zh"
    store.update_state(
        spec["task_id"],
        status="running",
        phase="transcribing",
        progress=8,
        message="正在调用共享 ASR 识别口播",
        step_current=1,
        step_total=6,
    )
    log(f"调用 ASR：{url}")
    try:
        with open(spec["video_path"], "rb") as media:
            response = requests.post(
                url,
                files={"file": (os.path.basename(spec["video_path"]), media, "video/mp4")},
                data={"model": "whisper-1", "language": language, "response_format": "verbose_json"},
                timeout=(10, 1800),
            )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"ASR 口播识别失败：{exc}") from exc

    segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("ASR 没有返回有效分段，请检查共享 ASR 服务")
    lines = []
    for segment in segments:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0)
        end = float(segment.get("end") or start + 2)
        lines.append(f"{len(lines) + 1}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}")
    if not lines:
        raise RuntimeError("ASR 分段全部为空")
    log(f"ASR 完成，共 {len(lines)} 段")
    return build_markdown_from_transcript("\n\n".join(lines))


def _material_markdown(spec: dict[str, Any], store: TaskStore) -> str:
    if spec.get("mode") in {"transcript", "asr"}:
        text = spec.get("transcript_text", "")
        if text:
            return build_markdown_from_transcript(text)
        return _asr_markdown(spec, store)

    if spec.get("analysis_mode") == "deep":
        from engine.deep_analysis import analyze_deep

        log("使用深度模式：整片分段解析")
        return analyze_deep(store, spec, log=log)

    from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService
    from app.services.generate_narration_script import parse_frame_analysis_to_markdown

    service = DocumentaryFrameAnalysisService()

    def progress(value: float, message: str = "") -> None:
        mapped = 6 + max(0.0, min(100.0, float(value))) * 0.18
        store.update_state(
            spec["task_id"],
            status="running",
            phase="analyzing",
            progress=round(mapped, 1),
            message=message or "正在分析画面",
            step_current=1,
            step_total=6,
        )
        if message:
            log(message)

    log("开始逐帧画面分析")
    analysis = asyncio.run(
        service.analyze_video(
            video_path=spec["video_path"],
            video_theme=build_creative_context(
                spec.get("content_type", "auto"),
                spec.get("topic", ""),
                spec.get("instruction", ""),
                spec.get("target_duration", 60),
                spec.get("language", "中文"),
                spec.get("tone", ""),
                spec.get("coverage", "highlights"),
                spec.get("video_duration", 0),
            )["theme"],
            custom_prompt=spec.get("instruction", ""),
            frame_interval_input=spec.get("frame_interval", 3),
            vision_batch_size=spec.get("vision_batch_size", 10),
            max_concurrency=spec.get("vision_max_concurrency", 2),
            progress_callback=progress,
        )
    )
    markdown = parse_frame_analysis_to_markdown(analysis["analysis_json_path"])
    if not str(markdown or "").strip():
        raise RuntimeError("画面分析结果为空")
    return markdown


def _apply_clip_mode(items: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """按原片时间轴剪辑：给每段写入 clip_duration，合成时按原片区间保留时长。"""
    if spec.get("clip_mode") != "timeline":
        return items
    for item in items:
        try:
            start, end = parse_timestamp_range(str(item.get("timestamp") or ""))
            item["clip_duration"] = round(max(0.1, end - start), 3)
        except (ValueError, TypeError):
            continue
    return items


def _normalize_loudness(path: Path, log=print) -> bool:
    """对成片做一次 EBU R128 响度统一（视频流直接复制，只重编码音频）。"""
    if not path.is_file():
        return False
    temp = path.with_name("combined_loudnorm.mp4")
    command = [
        "ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(path),
        "-c:v", "copy", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(temp),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"响度统一失败：{exc}")
        return False
    if result.returncode != 0 or not temp.is_file() or temp.stat().st_size <= 0:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    try:
        temp.replace(path)
        return True
    except OSError:
        return False


def _apply_pacing(items: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """在相邻解说片段之间插入原声停顿段（OST=1），实现“暂停解说”。"""
    if not spec.get("pause_between") or spec.get("clip_mode") != "timeline" or spec.get("output_mode") == "edit":
        return items
    try:
        pause_seconds = max(0.0, min(float(spec.get("pause_seconds") or 0.4), 6.0))
    except (TypeError, ValueError):
        pause_seconds = 0.4
    output: list[dict[str, Any]] = []
    for item in items:
        if output:
            try:
                _, previous_end = parse_timestamp_range(str(output[-1].get("timestamp") or ""))
                next_start, _ = parse_timestamp_range(str(item.get("timestamp") or ""))
                gap = next_start - previous_end
                if gap >= 0.8:
                    pause = min(gap, pause_seconds)
                    output.append(
                        {
                            "_id": len(output) + 1,
                            "timestamp": f"{_srt_time(previous_end)}-{_srt_time(previous_end + pause)}",
                            "clip_duration": round(pause, 3),
                            "narration": "",
                            "picture": "原声停顿",
                            "OST": 1,
                        }
                    )
            except (ValueError, TypeError):
                pass
        item["_id"] = len(output) + 1
        output.append(item)
    return output


def _build_script(spec: dict[str, Any], store: TaskStore) -> list[dict[str, Any]]:
    if spec.get("mode") == "script":
        items = spec.get("script_items") or []
        if not items:
            raise RuntimeError("文稿脚本为空")
        log(f"使用用户文稿，共 {len(items)} 段")
        return apply_pronunciation(_apply_pacing(_apply_clip_mode(items, spec), spec), bool(spec.get("tts_pronunciation", True)))

    store.update_state(
        spec["task_id"],
        status="running",
        phase="analyzing",
        progress=5,
        message="正在准备素材",
        step_current=1,
        step_total=6,
    )
    markdown = _material_markdown(spec, store)
    store.update_state(
        spec["task_id"],
        status="running",
        phase="writing",
        progress=SCRIPT_PROGRESS_END - 4,
        message="正在撰写解说脚本",
        step_current=2,
        step_total=6,
    )
    items = generate_items(markdown, spec, log=log)
    log(f"脚本生成完成，共 {len(items)} 段")
    return apply_pronunciation(_apply_pacing(_apply_clip_mode(items, spec), spec), bool(spec.get("tts_pronunciation", True)))


def _build_params(spec: dict[str, Any], script_path: Path):
    from app.models.schema import VideoAspect, VideoClipParams

    return VideoClipParams(
        video_clip_json_path=str(script_path),
        video_origin_path=spec["video_path"],
        video_origin_paths=[],
        video_aspect=VideoAspect(spec.get("aspect") or "9:16").value,
        video_language="en-US" if spec.get("language") == "英文" else "zh-CN",
        voice_name=spec.get("voice_name") or "zh-CN-YunxiNeural",
        voice_volume=1.0,
        voice_rate=float(spec.get("voice_rate") or 1.05),
        voice_pitch=float(spec.get("voice_pitch") or 1.0),
        tts_engine=spec.get("tts_engine") or "edge_tts",
        bgm_name="",
        bgm_type="",
        bgm_file="",
        bgm_volume=0.0,
        sonilo_sfx_enabled=False,
        subtitle_enabled=bool(spec.get("subtitles", True)) and spec.get("output_mode") != "edit",
        subtitle_mask_enabled=bool(spec.get("subtitle_mask_enabled", False)) and spec.get("output_mode") != "edit",
        subtitle_mask_landscape_x_percent=10.0,
        subtitle_mask_landscape_y_percent=float(spec.get("subtitle_mask_y") or 78),
        subtitle_mask_landscape_width_percent=80.0,
        subtitle_mask_landscape_height_percent=float(spec.get("subtitle_mask_height") or 14),
        subtitle_mask_landscape_blur_radius=int(spec.get("subtitle_mask_blur") or 18),
        subtitle_mask_landscape_opacity_percent=int(spec.get("subtitle_mask_opacity") or 82),
        subtitle_mask_portrait_x_percent=8.0,
        subtitle_mask_portrait_y_percent=float(spec.get("subtitle_mask_y") or 79),
        subtitle_mask_portrait_width_percent=84.0,
        subtitle_mask_portrait_height_percent=float(spec.get("subtitle_mask_height") or 16),
        subtitle_mask_portrait_blur_radius=int(spec.get("subtitle_mask_blur") or 26),
        subtitle_mask_portrait_opacity_percent=int(spec.get("subtitle_mask_opacity") or 84),
        subtitle_auto_transcribe_enabled=False,
        font_name=spec.get("font_name") if spec.get("font_name") is not None else "Noto Sans CJK SC",
        font_size=int(spec.get("font_size") or 40),
        text_fore_color=spec.get("text_fore_color") or "#FFFFFF",
        text_back_color=None,
        stroke_color=spec.get("stroke_color") or "#000000",
        stroke_width=float(spec.get("stroke_width") if spec.get("stroke_width") is not None else 0.8),
        subtitle_max_chars=int(spec.get("subtitle_max_chars") or 16),
        subtitle_position=spec.get("subtitle_position") or "bottom",
        custom_position=float(spec.get("custom_position") or 70),
        subtitle_position_landscape_y_percent=float(spec.get("subtitle_y_percent") or 88),
        subtitle_position_portrait_y_percent=float(spec.get("subtitle_y_percent") or 88),
        n_threads=4,
        tts_volume=float(spec.get("tts_volume") or 1.0),
        original_volume=1.0 if spec.get("output_mode") == "edit" else float(spec.get("original_volume") if spec.get("original_volume") is not None else 0.15),
    )


def _mark_failed(store: TaskStore, task_id: str, exc: BaseException) -> None:
    detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    full = traceback.format_exc()[-6000:]
    log(f"任务失败：{detail}")
    log(full)
    current = store.read_state(task_id) or {}
    if current.get("status") == "cancelled":
        return
    store.update_state(
        task_id,
        status="failed",
        phase="failed",
        progress=float(current.get("progress") or 0),
        message=detail[:500] or "任务失败",
        error=full,
        finished_at=_now(),
    )


def _now() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def run(task_dir: Path) -> int:
    task_id = task_dir.name
    store = TaskStore(ROOT)
    spec = store.read_spec(task_id)
    if not spec:
        log(f"找不到任务 spec：{task_dir}")
        return 2

    try:
        _apply_runtime_config()
        store.update_state(
            task_id,
            status="running",
            phase="preparing",
            progress=2,
            message="正在初始化生成环境",
            started_at=_now(),
        )
        current_state = store.read_state(task_id) or {}
        resume_render = current_state.get("resume_stage") == "render"
        if resume_render:
            items = store.read_script(task_id)
            if not items:
                raise RuntimeError("审片确认后找不到脚本")
            script_path = store.write_script(task_id, items)
            store.update_state(
                task_id,
                status="running",
                phase="generating",
                progress=SCRIPT_PROGRESS_END,
                message="审片已确认，开始配音与剪辑",
                step_current=2,
                step_total=6,
            )
        else:
            items = _build_script(spec, store)
            if spec.get("output_mode") == "edit":
                # AI 剪辑：复用选段结果，全部走原声，不生成 TTS/字幕
                for item in items:
                    item["OST"] = 1
            script_path = store.write_script(task_id, items)
            try:
                plan_dir = task_dir / "analysis"
                plan_dir.mkdir(parents=True, exist_ok=True)
                (plan_dir / "plan.json").write_text(
                    json.dumps(
                        {
                            "coverage": spec.get("coverage"),
                            "clip_mode": spec.get("clip_mode"),
                            "analysis_mode": spec.get("analysis_mode"),
                            "output_mode": spec.get("output_mode"),
                            "items": [
                                {
                                    "timestamp": item.get("timestamp"),
                                    "clip_duration": item.get("clip_duration"),
                                    "narration": item.get("narration"),
                                    "OST": item.get("OST"),
                                }
                                for item in items
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except OSError:
                pass
            if spec.get("review_mode", True):
                store.update_state(
                    task_id,
                    status="awaiting_review",
                    phase="review",
                    progress=SCRIPT_PROGRESS_END,
                    message="规划完成，等待审片确认后开始渲染",
                    step_current=1,
                    step_total=6,
                    finished_at="",
                )
                log("规划完成，已暂停等待审片")
                return 0
            store.update_state(
                task_id,
                status="running",
                phase="generating",
                progress=SCRIPT_PROGRESS_END,
                message="脚本就绪，开始配音与剪辑",
                step_current=2,
                step_total=6,
            )

        from app.services import state as sm
        from app.services import task as task_service

        sm.state = PipelineState(store, task_id)
        params = _build_params(spec, script_path)
        log("开始 TTS、裁剪、字幕与合成")
        task_service.start_subclip_unified(task_id=task_id, params=params)

        output_path = task_dir / "combined.mp4"
        if spec.get("loudness_normalize", True) and output_path.is_file():
            log("正在统一成片响度")
            _normalize_loudness(output_path, log)
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError("生成结束但未找到 combined.mp4")
        meta = _probe(output_path)
        store.write_result(
            task_id,
            {
                "output_path": str(output_path),
                "filename": output_path.name,
                "size": output_path.stat().st_size,
                "duration": meta.get("duration"),
                "width": meta.get("width"),
                "height": meta.get("height"),
                "script_path": str(script_path),
                "aspect": spec.get("aspect"),
                "completed_at": _now(),
            },
        )
        current = store.read_state(task_id) or {}
        if current.get("status") != "cancelled":
            store.update_state(
                task_id,
                status="success",
                phase="done",
                progress=100,
                message="视频生成完成",
                step_current=6,
                step_total=6,
                finished_at=_now(),
            )
        log(f"完成：{output_path}")
        return 0
    except BaseException as exc:  # noqa: BLE001 - 子进程必须把失败写回任务目录
        _mark_failed(store, task_id, exc)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="NarratoAI narration engine worker")
    parser.add_argument("--task-dir", required=True, help="storage/tasks/<task_id>")
    args = parser.parse_args()
    return run(Path(args.task_dir).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
