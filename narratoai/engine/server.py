"""NarratoAI 解说引擎的本地 JSON API（标准库实现，避免再引入一套 Web 框架）。

Video Hub 后端是唯一调用方：
    POST   /tasks              创建并启动生成任务
    GET    /tasks/{id}         查询进度/结果
    POST   /tasks/{id}/cancel  取消（杀掉 worker 进程组）
    GET    /tasks              最近任务
    GET    /settings           模型/TTS 设置（Key 脱敏）
    PUT    /settings           保存设置到 config.toml
    GET    /capabilities       内容类型/主播人格/音色/画幅/依赖状态
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.personas import PERSONA_PROFILES, PERSONA_STRENGTHS  # noqa: E402
from engine.profiles import (  # noqa: E402
    ASPECTS,
    CONTENT_PROFILES,
    DEFAULT_TTS_ENGINE,
    DEFAULT_VOICE,
    SCRIPT_MODES,
    TTS_ENGINES,
    VOICES,
)
from engine.settings import ensure_config, settings_payload, update_settings  # noqa: E402
from engine.spec import SpecError, normalize_script_items, normalize_spec  # noqa: E402
from engine.store import TaskStore  # noqa: E402
from engine.storyboard import build_storyboard  # noqa: E402

MAX_BODY = 3 * 1024 * 1024
TASK_RE = re.compile(r"^/tasks/([A-Za-z0-9_-]{1,80})(?:/(cancel|logs|script))?$")
CANCEL_RE = re.compile(r"^/tasks/([A-Za-z0-9_-]{1,80})/cancel$")
APPROVE_RE = re.compile(r"^/tasks/([A-Za-z0-9_-]{1,80})/approve$")
SCRIPT_RE = re.compile(r"^/tasks/([A-Za-z0-9_-]{1,80})/script$")

STORE = TaskStore(ROOT)
SPAWN_LOCK = threading.Lock()
TOKEN = (
    os.environ.get("NARRATO_ENGINE_TOKEN", "").strip()
    or os.environ.get("VIDEO_HUB_NARRATO_ENGINE_TOKEN", "").strip()
)
try:
    MAX_ACTIVE = max(1, int(os.environ.get("NARRATO_MAX_ACTIVE", "2") or 2))
except ValueError:
    MAX_ACTIVE = 2


def _version() -> str:
    try:
        return (ROOT / "project_version").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"


def _fonts() -> list[dict[str, str]]:
    fonts = [{"value": "", "label": "系统默认（自动选择）"}]
    directory = ROOT / "resource" / "fonts"
    if directory.is_dir():
        for item in sorted(directory.iterdir()):
            if item.is_file() and item.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                fonts.append({"value": item.name, "label": item.stem})
    return fonts


def _deps() -> dict[str, bool]:
    return {
        name: importlib.util.find_spec(module) is not None
        for name, module in (
            ("moviepy", "moviepy"),
            ("edge_tts", "edge_tts"),
            ("openai", "openai"),
            ("requests", "requests"),
            ("pillow", "PIL"),
            ("loguru", "loguru"),
        )
    }


def _spawn_worker(task_id: str) -> subprocess.Popen:
    task_dir = STORE.task_dir(task_id)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONUNBUFFERED", "1")
    log_path = task_dir / "worker.log"
    handle = log_path.open("ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "engine.runner", "--task-dir", str(task_dir)],
            cwd=str(ROOT),
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        handle.close()
    STORE.set_pid(task_id, proc.pid)
    threading.Thread(target=_monitor_worker, args=(task_id, proc), daemon=True).start()
    return proc


def _monitor_worker(task_id: str, proc: subprocess.Popen) -> None:
    """worker 在写 state 之前就崩溃（import/依赖错误）时，及时把任务收口。"""
    code = proc.wait()
    state = STORE.read_state(task_id) or {}
    if state.get("status") in {"awaiting_review", "success", "needs_review", "failed", "cancelled"}:
        return
    try:
        log_text = (STORE.task_dir(task_id) / "worker.log").read_text(encoding="utf-8", errors="replace")[-3000:]
    except OSError:
        log_text = ""
    STORE.update_state(
        task_id,
        status="failed",
        phase="failed",
        message=f"生成进程异常退出（exit {code}）",
        error=log_text or f"worker exited with code {code}",
    )


class Handler(BaseHTTPRequestHandler):
    server_version = "NarratoEngine/1.0"

    # ---- 基础响应 ----

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("[engine-http] " + (fmt % args) + "\n")

    def _send(self, status: int, payload: object) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, detail: str = "") -> None:
        payload = {"ok": False, "error": message}
        if detail:
            payload["detail"] = detail
        self._send(status, payload)

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("X-Engine-Token", "") == TOKEN

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise SpecError("请求体过大")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise SpecError("请求体不是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise SpecError("请求体必须是 JSON 对象")
        return payload

    # ---- 路由 ----

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        if path == "/health":
            self._send(200, {
                "ok": True,
                "status": "ok",
                "version": _version(),
                "storage": str(STORE.tasks_dir),
                "dependencies": _deps(),
            })
            return
        if not self._authorized():
            self._error(401, "unauthorized")
            return
        if path == "/capabilities":
            self._capabilities()
            return
        if path == "/settings":
            self._settings()
            return
        if path == "/tasks":
            try:
                limit = int((query.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            self._send(200, {"ok": True, "tasks": STORE.list(limit)})
            return

        match = TASK_RE.match(path)
        if match:
            task_id, action = match.group(1), match.group(2)
            try:
                if action == "logs":
                    self._send(200, {"ok": True, "task_id": task_id, "logs": STORE.public(task_id, include_logs=True)["logs"]})
                elif action == "script":
                    data = STORE._read_json(STORE.task_dir(task_id) / "script.json", None)  # noqa: SLF001
                    if data is None:
                        self._error(404, "脚本尚未生成")
                    else:
                        self._send(200, {"ok": True, "task_id": task_id, "items": data})
                else:
                    include_logs = (query.get("logs") or ["0"])[0] in {"1", "true"}
                    self._send(200, {"ok": True, "task": STORE.public(task_id, include_logs=include_logs)})
            except KeyError:
                self._error(404, "任务不存在")
            except Exception as exc:  # noqa: BLE001
                self._error(500, "读取任务失败", str(exc))
            return
        self._error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._error(401, "unauthorized")
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/tts/preview":
            try:
                from engine.voice_preview import create_preview

                self._send(200, {"ok": True, "preview": create_preview(ROOT, self._body())})
            except (ValueError, RuntimeError) as exc:
                self._error(400, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._error(500, "试听生成失败", str(exc))
            return
        if path == "/tasks":
            self._create_task()
            return
        match = CANCEL_RE.match(path)
        if match:
            task_id = match.group(1)
            try:
                task = STORE.cancel(task_id)
                self._send(200, {"ok": True, "task": task})
            except KeyError:
                self._error(404, "任务不存在")
            except Exception as exc:  # noqa: BLE001
                self._error(500, "取消失败", str(exc))
            return
        match = APPROVE_RE.match(path)
        if match:
            task_id = match.group(1)
            try:
                state = STORE.read_state(task_id) or {}
                if state.get("status") != "awaiting_review":
                    self._error(409, "任务不在待审片状态")
                    return
                active = [item for item in STORE.list(200) if item.get("status") == "running"]
                if len(active) >= MAX_ACTIVE:
                    self._error(429, f"同时最多运行 {MAX_ACTIVE} 个任务，请稍后再确认")
                    return
                STORE.update_state(
                    task_id,
                    status="queued",
                    phase="queued",
                    progress=30,
                    message="审片已确认，等待渲染",
                    resume_stage="render",
                    error="",
                )
                _spawn_worker(task_id)
                self._send(200, {"ok": True, "task": STORE.public(task_id)})
            except KeyError:
                self._error(404, "任务不存在")
            except Exception as exc:  # noqa: BLE001
                self._error(500, "确认渲染失败", str(exc))
            return
        self._error(404, "not found")

    def do_PUT(self) -> None:  # noqa: N802
        if not self._authorized():
            self._error(401, "unauthorized")
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        match = SCRIPT_RE.match(path)
        if match:
            self._update_script(match.group(1))
            return
        if path == "/settings":
            try:
                payload = self._body()
                result = update_settings(ROOT, payload)
                self._send(200, {"ok": True, "settings": result})
            except (SpecError, ValueError) as exc:
                self._error(400, str(exc))
            except Exception as exc:  # noqa: BLE001
                self._error(500, "保存设置失败", str(exc))
            return
        self._error(404, "not found")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Engine-Token")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- 业务 ----

    def _update_script(self, task_id: str) -> None:
        """待审片阶段保存完整结构：排序、删章、文案和故事角色。"""
        try:
            state = STORE.read_state(task_id) or {}
            if state.get("status") != "awaiting_review":
                self._error(409, "任务不在待审片状态，无法编辑脚本")
                return
            payload = self._body()
            submitted = payload.get("items")
            if not isinstance(submitted, list) or not submitted:
                self._error(400, "items 必须是非空数组，至少保留一段")
                return
            if len(submitted) > 200:
                self._error(400, "脚本片段过多")
                return
            existing = STORE.read_script(task_id)
            if not isinstance(existing, list) or not existing:
                self._error(404, "脚本尚未生成")
                return
            task_spec = STORE.read_spec(task_id) or {}
            normalized = normalize_script_items(
                submitted, float(task_spec.get("video_duration") or task_spec.get("target_duration") or 60)
            )
            metadata = (
                "segment_id", "story_role", "reason", "evidence", "estimated_duration",
                "emotion", "voice_name", "voice_rate", "voice_pitch", "quality_flags",
                "score", "review_reason", "clip_duration", "tts_text",
            )
            updated: list[dict] = []
            segment_ids: set[str] = set()
            for index, (source, item) in enumerate(zip(submitted, normalized)):
                for key in metadata:
                    if key in source:
                        item[key] = source[key]
                segment_id = str(item.get("segment_id") or f"segment-{index + 1}")
                if segment_id in segment_ids:
                    raise SpecError(f"segment_id 重复：{segment_id}")
                segment_ids.add(segment_id)
                item["segment_id"] = segment_id
                updated.append(item)
            task_dir = STORE.task_dir(task_id)
            (task_dir / "analysis").mkdir(parents=True, exist_ok=True)
            (task_dir / "analysis" / "script_before_edit.json").write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            STORE.write_script(task_id, updated)
            plan_path = task_dir / "analysis" / "plan.json"
            plan = STORE._read_json(plan_path, {}) or {}  # noqa: SLF001
            plan["items"] = updated
            plan["total_estimated_duration"] = round(sum(float(item.get("estimated_duration") or 0) for item in updated), 2)
            plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
            build_storyboard(task_dir, updated, str(task_spec.get("video_path") or ""), extract=True)
            STORE.update_state(
                task_id,
                message="审片结构已保存，等待确认渲染",
                script_edited=True,
            )
            self._send(200, {"ok": True, "task_id": task_id, "items": updated})
        except KeyError:
            self._error(404, "任务不存在")
        except SpecError as exc:
            self._error(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._error(500, "保存脚本失败", str(exc))

    def _capabilities(self) -> None:
        try:
            settings = settings_payload(ROOT)
        except Exception as exc:  # noqa: BLE001
            settings = {"ready": False, "error": str(exc)}
        deps = _deps()
        self._send(200, {
            "ok": True,
            "version": _version(),
            "content_types": [
                {"value": key, "label": value["label"], "summary": value["summary"], "original_volume": value["original_volume"]}
                for key, value in CONTENT_PROFILES.items()
            ],
            "personas": [
                {"value": key, "label": value["label"], "summary": value["summary"]}
                for key, value in PERSONA_PROFILES.items()
            ],
            "persona_strengths": [
                {"value": key, "label": value["label"]}
                for key, value in PERSONA_STRENGTHS.items()
            ],
            "voices": VOICES,
            "fonts": _fonts(),
            "tts_engines": TTS_ENGINES,
            "aspects": ASPECTS,
            "script_modes": SCRIPT_MODES,
            "defaults": {
                "content_type": "auto",
                "persona": "auto",
                "persona_strength": "natural",
                "voice_name": DEFAULT_VOICE,
                "tts_engine": DEFAULT_TTS_ENGINE,
                "aspect": "auto",
                "target_duration": 60,
                "original_volume": 0.15,
                "subtitles": True,
                "font_name": "Noto Sans CJK SC",
                "font_size": 40,
                "text_fore_color": "#FFFFFF",
                "stroke_color": "#000000",
                "stroke_width": 0.8,
                "subtitle_max_chars": 16,
                "voice_rate": 1.05,
                "subtitle_position": "bottom",
                "subtitle_y_percent": 88,
                "subtitle_mask_enabled": False,
                "subtitle_mask_y": 78,
                "subtitle_mask_height": 14,
                "subtitle_mask_blur": 18,
                "subtitle_mask_opacity": 82,
                "clip_mode": "timeline",
                "coverage": "highlights",
                "analysis_mode": "deep",
                "output_mode": "narration",
                "pause_between": True,
                "pause_seconds": 0.4,
                "review_mode": True,
                "loudness_normalize": True,
                "tts_pronunciation": True,
                "deep_segment_seconds": 30,
                "deep_frame_interval": 1.5,
                "deep_scene_detection": True,
            },
            "dependencies": deps,
            "settings": settings,
            "ready": bool(settings.get("ready")) and deps.get("moviepy") and deps.get("edge_tts"),
            "storage": str(STORE.tasks_dir),
        })

    def _settings(self) -> None:
        try:
            self._send(200, {"ok": True, "settings": settings_payload(ROOT)})
        except Exception as exc:  # noqa: BLE001
            self._error(500, "读取设置失败", str(exc))

    def _create_task(self) -> None:
        try:
            payload = self._body()
            spec = normalize_spec(payload)
        except SpecError as exc:
            self._error(400, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self._error(400, "参数不合法", str(exc))
            return

        task_id = spec["task_id"]
        if STORE.exists(task_id):
            self._error(409, "任务已存在")
            return
        active = [item for item in STORE.list(200) if item.get("status") in {"queued", "running"}]
        if len(active) >= MAX_ACTIVE:
            self._error(429, f"同时最多运行 {MAX_ACTIVE} 个解说任务，请等待当前任务完成或取消")
            return
        try:
            ensure_config(ROOT)
            task = STORE.create(spec)
        except Exception as exc:  # noqa: BLE001
            self._error(500, "创建任务失败", str(exc))
            return

        with SPAWN_LOCK:
            try:
                _spawn_worker(task_id)
            except Exception as exc:  # noqa: BLE001
                STORE.update_state(
                    task_id,
                    status="failed",
                    phase="failed",
                    message="无法启动生成进程",
                    error=str(exc),
                )
                self._error(500, "无法启动生成进程", str(exc))
                return
        self._send(202, {"ok": True, "task": STORE.public(task_id)})


def main() -> int:
    parser = argparse.ArgumentParser(description="NarratoAI narration engine API")
    parser.add_argument("--host", default=os.environ.get("NARRATO_ENGINE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NARRATO_ENGINE_PORT", "8601")))
    args = parser.parse_args()

    ensure_config(ROOT)
    recovered = STORE.recover()
    if recovered:
        print(f"[engine] 收口中断任务：{', '.join(recovered)}", flush=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"[engine] listening on http://{args.host}:{args.port} (root={ROOT})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[engine] shutting down", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
