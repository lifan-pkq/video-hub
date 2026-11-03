"""把新版解说任务导出为剪映草稿（复用旧版 NarratoAI 的 Jianying 能力）。

由后端以 narratoai 虚拟环境调用，不把旧版 Streamlit 变成依赖。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.models.schema import VideoAspect, VideoClipParams
from app.services import jianying_task

ROOT = Path(__file__).resolve().parent.parent
TASKS = ROOT / "storage" / "tasks"


def _aspect(spec: dict) -> str:
    raw = str(spec.get("aspect") or "").strip()
    if raw in {item.value for item in VideoAspect}:
        return raw
    width = int(spec.get("video_width") or 0)
    height = int(spec.get("video_height") or 0)
    return "9:16" if height > width else "16:9"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--draft-name", default="")
    args = parser.parse_args()

    task_id = args.task_id.strip()
    task_dir = TASKS / task_id
    spec_path = task_dir / "spec.json"
    script_path = task_dir / "script.json"
    output_path = task_dir / "jianying_export.json"
    if not task_id or "/" in task_id or not spec_path.is_file() or not script_path.is_file():
        output_path.write_text(
            json.dumps({"ok": False, "error": "任务不存在或脚本尚未生成"}, ensure_ascii=False),
            encoding="utf-8",
        )
        return 2
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        params = VideoClipParams(
            video_clip_json_path=str(script_path),
            video_origin_path=str(spec.get("video_path") or ""),
            video_origin_paths=[str(spec.get("video_path") or "")],
            video_aspect=_aspect(spec),
            video_language="zh-CN",
            voice_name=str(spec.get("voice_name") or "zh-CN-XiaoxiaoNeural"),
            voice_rate=float(spec.get("voice_rate") or 1.0),
            voice_pitch=float(spec.get("voice_pitch") or 1.0),
            tts_engine=str(spec.get("tts_engine") or "edge_tts"),
            subtitle_enabled=bool(spec.get("subtitles", True)),
            font_name=str(spec.get("font_name") or "DroidSansFallbackFull.ttf"),
            font_size=int(spec.get("font_size") or 54),
            text_fore_color=str(spec.get("text_fore_color") or "#FFFFFF"),
            stroke_color=str(spec.get("stroke_color") or "#000000"),
            stroke_width=float(spec.get("stroke_width") or 1.5),
            subtitle_position=str(spec.get("subtitle_position") or "bottom"),
            custom_position=float(spec.get("subtitle_y_percent") or 88),
            original_volume=float(spec.get("original_volume") or 1.0),
            draft_name=args.draft_name.strip(),
        )
        result = jianying_task.start_export_jianying_draft(task_id, params)
        payload = {
            "ok": True,
            "task_id": task_id,
            "draft_name": result.get("draft_name") or args.draft_name,
            "draft_path": result.get("draft_path") or "",
        }
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        payload = {"ok": False, "task_id": task_id, "error": str(exc)}
        try:
            output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
        print(json.dumps(payload, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
