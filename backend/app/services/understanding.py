"""视频素材理解与素材卡：口播、镜头、声音、结构和情绪共用一份 JSON。"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import requests

from app.config import (
    AI_BASE_URL,
    AI_MODEL,
    DATA_DIR,
    TMP_DIR,
    VIDEOS_DIR,
    VISION_BASE_URL,
    VISION_API_KEY,
    VISION_MODEL,
)
from app.db import get_video, list_videos, update_video
from app.services.ai_pool import AI_POOL, AIPoolError
from app.services.audio_analysis import analyze_loudness, detect_beats, detect_silence
from app.services.media import probe_video
from app.services.video_analysis import detect_scene_cuts

UNDERSTANDING_DIR = DATA_DIR / "understanding"

# 单个汇总提示词里时间线条目的 JSON 字符预算，超长素材按时间分段后再汇总。
SUMMARY_BUDGET_CHARS = 6000
# 模型时间戳与最近抽帧的允许差；超出只记 warning 并按最近镜头对齐，不再丢弃。
VISUAL_ALIGN_WARN_SECONDS = 1.5


class UnderstandingError(RuntimeError):
    pass


def _extract_json(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    candidates = [text]
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if match:
        candidates.append(match.group(1))
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise UnderstandingError("理解模型没有返回合法 JSON")


def _request_json(messages: list[dict], *, vision: bool = False) -> dict[str, Any]:
    base_url = VISION_BASE_URL if vision else AI_BASE_URL.rstrip("/")
    model = VISION_MODEL if vision else AI_MODEL
    if not model:
        raise UnderstandingError("未配置视觉模型" if vision else "未配置文本模型")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    try:
        if AI_POOL.has_keys():
            response = AI_POOL.request(
                "POST", f"{base_url}/chat/completions", json_body=payload, timeout=240
            )
        else:
            if not VISION_API_KEY:
                raise UnderstandingError("未配置理解模型 Key")
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {VISION_API_KEY}"},
                json=payload,
                timeout=240,
            )
    except (requests.RequestException, AIPoolError) as exc:
        raise UnderstandingError(f"理解模型请求失败：{exc}") from exc
    if response.status_code != 200:
        raise UnderstandingError(f"理解模型 HTTP {response.status_code}：{response.text[:200]}")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise UnderstandingError("理解模型响应缺少文本内容") from exc
    return _extract_json(content)


def _frame_at(path: Path, timestamp: float, target: Path) -> bool:
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{timestamp:.3f}", "-i", str(path),
         "-frames:v", "1", "-vf", "scale='min(960,iw)':-2", "-q:v", "4", str(target)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    return result.returncode == 0 and target.is_file() and target.stat().st_size > 0


def _visual_understanding(path: Path, duration: float, cuts: list[float]) -> tuple[list[dict], dict]:
    if not VISION_MODEL:
        return [], {"status": "skipped", "reason": "未配置 VIDEO_HUB_VISION_MODEL"}
    points = sorted({0.0, duration, *[value for value in cuts if 0 < value < duration]})
    ranges = list(zip(points, points[1:])) or [(0.0, duration)]
    if len(ranges) > 8:
        step = len(ranges) / 8
        ranges = [ranges[min(len(ranges) - 1, int(index * step))] for index in range(8)]
    content: list[dict[str, Any]] = [{
        "type": "text",
        "text": (
            "这些关键帧按给定原片时间排列。逐帧输出谁、在哪、动作、情绪、OCR 文字和主体框"
            "（0-1000 的 x/y/width/height）。再输出全片一句话摘要、人物、场景、情绪标签、"
            "最佳片段和故事结构。只输出 JSON："
            '{"frames":[{"timestamp":0,"description":"","ocr":[],"emotion":"","subject_box":null}],'
            '"summary":"","people":[],"scenes":[],"emotion_tags":[],"best_segments":[],'
            '"structure":[],"emotion_curve":[]}'
        ),
    }]
    frames: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="understanding-", dir=TMP_DIR) as temp:
        for index, (start, end) in enumerate(ranges):
            timestamp = max(0.0, (start + min(end, start + 2)) / 2)
            target = Path(temp) / f"frame-{index}.jpg"
            if not _frame_at(path, timestamp, target):
                continue
            content.append({"type": "text", "text": f"原片时间 {timestamp:.3f} 秒"})
            encoded = base64.b64encode(target.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}})
            frames.append({"timestamp": timestamp, "start": start, "end": end})
    if not frames:
        return [], {"status": "unknown", "reason": "无法抽取关键帧"}
    result = _request_json([{"role": "user", "content": content}], vision=True)
    # 按最近镜头配对，而不是按时间戳精确命中：模型四舍五入过的时钟不再导致整条丢失。
    matched: dict[int, tuple[float, dict]] = {}
    warnings: list[str] = []
    misaligned: list[float] = []
    unparsable = 0
    for item in result.get("frames") or []:
        if not isinstance(item, dict):
            unparsable += 1
            continue
        try:
            stamp = float(item.get("timestamp"))
        except (TypeError, ValueError):
            unparsable += 1
            continue
        index = min(range(len(frames)), key=lambda value: abs(frames[value]["timestamp"] - stamp))
        gap = abs(frames[index]["timestamp"] - stamp)
        if gap > VISUAL_ALIGN_WARN_SECONDS:
            misaligned.append(gap)
        if index not in matched or gap < matched[index][0]:
            matched[index] = (gap, item)
    if misaligned:
        warnings.append(
            f"{len(misaligned)} 条帧描述与最近抽帧时间差超过 {VISUAL_ALIGN_WARN_SECONDS:g} 秒"
            f"（最大 {max(misaligned):.1f} 秒），已按最近镜头对齐"
        )
    if unparsable:
        warnings.append(f"{unparsable} 条帧描述缺少可用的 timestamp，未能落到轨迹")
    tracks: list[dict[str, Any]] = []
    for index, (_, item) in sorted(matched.items()):
        tracks.append({
            **frames[index],
            "kind": "visual",
            "text": str(item.get("description") or "")[:1000],
            "ocr": item.get("ocr") or [],
            "emotion": str(item.get("emotion") or ""),
            "subject_box": item.get("subject_box"),
            "track_id": f"shot-{len(tracks) + 1}",
        })
    if not tracks:
        return [], {
            "status": "unknown",
            "reason": "视觉模型没有返回可对齐的帧",
            "analysis": result,
            "warnings": warnings,
        }
    return tracks, {"status": "ok", "analysis": result, "warnings": warnings}


MATERIAL_CARD_FIELDS = (
    '{"summary":"","people":[],"scenes":[],"emotion_tags":[],'
    '"best_segments":[{"start":0,"end":1,"reason":""}],'
    '"structure":[{"start":0,"end":1,"role":"hook|setup|turn|climax|ending","reason":""}],'
    '"emotion_curve":[{"timestamp":0,"score":0,"label":""}]}'
)

MATERIAL_NOTE_FIELDS = (
    '{"points":[{"start":0,"end":1,"note":""}],"people":[],"scenes":[],"emotion_tags":[]}'
)


def _timeline_entries(speech: list[dict], visual: list[dict]) -> list[dict]:
    entries: list[tuple[float, dict]] = [
        (float(item.get("start") or 0),
         {key: item.get(key) for key in ("start", "end", "kind", "text", "speaker")})
        for item in speech
    ]
    entries += [
        (float(item.get("start") or 0),
         {key: item.get(key) for key in ("start", "end", "kind", "text", "ocr")})
        for item in visual
    ]
    entries.sort(key=lambda pair: pair[0])
    return [entry for _, entry in entries]


def _timeline_batches(entries: list[dict], budget: int = SUMMARY_BUDGET_CHARS) -> list[str]:
    """按时间顺序贪心切段，每段是完整条目的 JSON 数组且总长不超过字符预算。"""
    batches: list[str] = []
    current: list[str] = []
    size = 2  # 一对中括号
    for entry in entries:
        line = json.dumps(entry, ensure_ascii=False)
        while len(line) > budget - 2 and entry.get("text"):
            entry = {**entry, "text": str(entry["text"])[: len(str(entry["text"])) // 2]}
            line = json.dumps(entry, ensure_ascii=False)
        cost = len(line) + 1  # 逗号
        if current and size + cost > budget:
            batches.append("[" + ",".join(current) + "]")
            current, size = [], 2
        current.append(line)
        size += cost
    if current:
        batches.append("[" + ",".join(current) + "]")
    return batches or ["[]"]


def _text_summary(speech: list[dict], visual: list[dict], *, budget: int = SUMMARY_BUDGET_CHARS) -> dict:
    batches = _timeline_batches(_timeline_entries(speech, visual), budget)
    if len(batches) == 1:
        return _request_json([{"role": "user", "content": (
            "根据同一条视频的口播与画面时间线，输出素材卡和故事结构。不得补充材料没有的事实。"
            "只输出 JSON：" + MATERIAL_CARD_FIELDS + "\n" + batches[0]
        )}])
    # 长素材：时间线超出单次预算，先分段抽取要点，再基于要点汇总，避免一次全量发送。
    notes: list[dict] = []
    failures: list[str] = []
    for index, batch in enumerate(batches):
        try:
            notes.append(_request_json([{"role": "user", "content": (
                f"这是同一条视频时间线的第 {index + 1}/{len(batches)} 段。只提取本段口播要点、"
                "关键画面、人物、场景与情绪，不要下全片结论。只输出 JSON："
                + MATERIAL_NOTE_FIELDS + "\n" + batch
            )}]))
        except Exception as exc:  # noqa: BLE001
            # 单段失败不丢掉整条素材的口播结构，剩下的段继续。
            failures.append(f"第 {index + 1}/{len(batches)} 段：{exc}")
    if not notes:
        raise UnderstandingError("长素材分段理解全部失败：" + "；".join(failures))
    merged = json.dumps(notes, ensure_ascii=False)
    while len(notes) > 1 and len(merged) > budget:
        notes = notes[: len(notes) // 2]
        merged = json.dumps(notes, ensure_ascii=False)
    return _request_json([{"role": "user", "content": (
        f"以下是同一条视频按时间分 {len(batches)} 段提取的要点，请合并输出素材卡和故事结构。"
        "不得补充材料没有的事实。只输出 JSON：" + MATERIAL_CARD_FIELDS + "\n" + merged
        + (("\n（下列分段未能取出：" + "；".join(failures) + "）") if failures else "")
    )}])


def analyze_video(video_id: int, log=print) -> dict[str, Any]:
    video = get_video(video_id)
    if not video:
        raise UnderstandingError("视频不存在")
    path = VIDEOS_DIR / video["filename"]
    if not path.is_file():
        raise UnderstandingError("视频文件已丢失")
    update_video(video_id, analysis_status="running", analysis_error="")
    warnings: list[str] = []
    try:
        duration = float(video.get("duration") or probe_video(path).get("duration") or 0)
        try:
            from app.services.ai_edit import transcribe

            speech = transcribe(path, log=log)
        except Exception as exc:  # noqa: BLE001
            speech = []
            warnings.append(f"口播：{exc}")
        for item in speech:
            item["kind"] = "speech"
            item.setdefault("speaker", "speaker_unknown")
        cuts = detect_scene_cuts(path)
        try:
            visual, visual_result = _visual_understanding(path, duration, cuts)
            if visual_result.get("reason"):
                warnings.append(f"画面：{visual_result['reason']}")
            for item in visual_result.get("warnings") or []:
                warnings.append(f"画面：{item}")
        except Exception as exc:  # noqa: BLE001
            visual, visual_result = [], {"status": "unknown", "analysis": {}}
            warnings.append(f"画面：{exc}")
        try:
            silence = detect_silence(path, log=log)
            loudness = analyze_loudness(path)
            beats = detect_beats(path)
        except Exception as exc:  # noqa: BLE001
            silence, loudness, beats = [], {"status": "unknown"}, []
            warnings.append(f"声音：{exc}")
        audio = silence + [{"start": value, "end": value + 0.12, "kind": "beat"} for value in beats]
        try:
            summary = _text_summary(speech, visual)
        except Exception as exc:  # noqa: BLE001
            summary = (visual_result.get("analysis") or {})
            warnings.append(f"结构：{exc}")
        result = {
            "schema_version": 1,
            "video_id": video_id,
            "duration": duration,
            "tracks": {"speech": speech, "visual": visual, "audio": audio},
            "scene_cuts": cuts,
            "audio_summary": {**loudness, "beat_detection": "energy_onset_candidates"},
            "summary": summary.get("summary") or "",
            "people": summary.get("people") or [],
            "scenes": summary.get("scenes") or [],
            "emotion_tags": summary.get("emotion_tags") or [],
            "best_segments": summary.get("best_segments") or [],
            "structure": summary.get("structure") or [],
            "emotion_curve": summary.get("emotion_curve") or [],
            "warnings": warnings,
            "visual_status": ("unknown" if not visual else visual_result.get("status")) or "unknown",
        }
        UNDERSTANDING_DIR.mkdir(parents=True, exist_ok=True)
        (UNDERSTANDING_DIR / f"{video_id}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        update_video(
            video_id,
            analysis_status="partial" if warnings else "success",
            analysis_error="；".join(warnings)[:1000],
            analysis_json=json.dumps(result, ensure_ascii=False),
        )
        return result
    except Exception as exc:
        update_video(video_id, analysis_status="failed", analysis_error=str(exc)[:1000])
        raise


def _running_video_ids() -> set[int]:
    return {int(video["id"]) for video in list_videos() if video.get("analysis_status") == "running"}


def fail_interrupted_analyses() -> int:
    """服务重启后先前的理解线程已随进程消失，把卡住的 running 收口为 failed。"""
    running = _running_video_ids()
    for video_id in running:
        update_video(video_id, analysis_status="failed", analysis_error="服务重启，素材理解中断")
    return len(running)


def start_analysis(video_id: int) -> None:
    if video_id in _running_video_ids():
        return

    def worker() -> None:
        try:
            analyze_video(video_id)
        except Exception:
            return

    threading.Thread(target=worker, daemon=True, name=f"understanding-{video_id}").start()


def get_analysis(video_id: int) -> dict[str, Any]:
    video = get_video(video_id)
    if not video:
        raise UnderstandingError("视频不存在")
    analysis = video.get("analysis")
    if isinstance(analysis, dict) and analysis:
        return analysis
    path = UNDERSTANDING_DIR / f"{video_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnderstandingError("素材理解尚未生成") from exc
