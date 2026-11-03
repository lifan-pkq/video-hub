"""深度分析：整片分段 → 每段密集抽帧理解 → 持久化 → 汇总给统一脚本规划。

设计目标是质量优先：
- 固定时间窗切分整条视频（默认 30 秒一段，可覆盖长直播）；
- 每段用更密的帧间隔（默认 1.5 秒）单独做视觉分析；
- 每段解析结果落盘到任务目录 analysis/segments；
- 全部解析完后拼成带全局时间戳的 timeline，再交给最终脚本模型做取舍与剪辑规划。
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from engine.store import TaskStore

TIMESTAMP_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def _fmt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole = int(secs)
    millis = int(round((secs - whole) * 1000))
    if millis >= 1000:
        whole += 1
        millis = 0
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def shift_timestamps(markdown: str, offset: float) -> str:
    """把片段内的相对时间戳平移到整条视频的全局时间轴。"""
    if offset <= 0:
        return markdown

    def replace(match: re.Match) -> str:
        total = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + int(match.group(3))
        total += int(match.group(4)) / 1000 + offset
        return _fmt(total)

    return TIMESTAMP_RE.sub(replace, markdown)


def detect_scene_cuts(source: Path, threshold: float = 0.32, timeout: int = 900) -> list[float]:
    """用 ffmpeg scene 分数找镜头切换点（参考 PySceneDetect 的内容检测思路）。

    不引入 OpenCV 依赖；失败时返回空列表，由固定时间窗兜底。
    """
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-i", str(source),
        "-filter:v", f"select='gt(scene,{max(0.05, min(threshold, 0.9))})',showinfo",
        "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    cuts: set[float] = set()
    for line in (result.stderr or "").splitlines():
        match = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
        if match:
            try:
                cuts.add(round(float(match.group(1)), 2))
            except ValueError:
                continue
    return sorted(value for value in cuts if value > 0)


def build_segments(
    cuts: list[float],
    duration: float,
    target: float = 30.0,
    minimum: float = 8.0,
    maximum: float = 45.0,
) -> list[tuple[float, float]]:
    """把镜头切换点合并成 8–45 秒的分析段，避免镜头太碎或一段太长。"""
    duration = max(1.0, float(duration))
    target = max(5.0, float(target))
    minimum = max(2.0, min(float(minimum), target))
    maximum = max(target, float(maximum))
    points = sorted({0.0, duration, *(c for c in cuts if 0 < c < duration)})
    segments: list[tuple[float, float]] = []
    start = 0.0
    for point in points[1:]:
        length = point - start
        if point >= duration:
            break
        if length < minimum:
            continue
        if length >= target or length >= maximum:
            segments.append((round(start, 3), round(point, 3)))
            start = point
    if start < duration:
        # 收尾段过短时并入前一段
        if segments and duration - start < minimum:
            segments[-1] = (segments[-1][0], round(duration, 3))
        else:
            segments.append((round(start, 3), round(duration, 3)))
    if not segments:
        segments = [(0.0, round(duration, 3))]
    # 超长没有切点的视频按最大长度硬切
    expanded: list[tuple[float, float]] = []
    for start, end in segments:
        cursor = start
        while end - cursor > maximum:
            expanded.append((round(cursor, 3), round(cursor + target, 3)))
            cursor += target
        if end > cursor:
            expanded.append((round(cursor, 3), round(end, 3)))
    return expanded or [(0.0, round(duration, 3))]


def _cache_key(source: Path, spec: dict[str, Any], segment_seconds: float, frame_interval: float) -> str:
    import hashlib

    try:
        stat = source.stat()
        fingerprint = f"{source.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
    except OSError:
        fingerprint = str(source)
    fingerprint += "|" + "|".join(
        [
            f"{segment_seconds:.3f}",
            f"{frame_interval:.3f}",
            str(bool(spec.get("deep_scene_detection", True))),
            str(spec.get("topic") or ""),
            str(spec.get("instruction") or ""),
        ]
    )
    return hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]


def _restore_cache(cache_dir: Path, analysis_dir: Path) -> bool:
    timeline = cache_dir / "timeline.md"
    if not timeline.is_file():
        return False
    analysis_dir.mkdir(parents=True, exist_ok=True)
    for item in cache_dir.iterdir():
        target = analysis_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        elif item.is_file():
            shutil.copy2(item, target)
    return True


def _save_cache(analysis_dir: Path, cache_dir: Path, key: str) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for item in analysis_dir.iterdir():
            target = cache_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, target)
        (cache_dir / "cache_meta.json").write_text(
            json.dumps({"key": key, "created_at": time.time()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def _quality_score(brightness: float, contrast: float, motion: float) -> dict:
    """把亮度/对比/运动换算成 0-100 的可读质量分，供规划参考。"""
    brightness_score = max(0.0, 100.0 - abs(brightness - 128.0) / 1.28)
    contrast_score = max(0.0, min(100.0, contrast / 60.0 * 100.0))
    motion_score = max(0.0, 100.0 - abs(motion - 8.0) * 8.0)
    score = round(brightness_score * 0.45 + contrast_score * 0.3 + motion_score * 0.25, 1)
    flags: list[str] = []
    if brightness < 35:
        flags.append("过暗")
    elif brightness > 220:
        flags.append("过亮")
    if contrast < 15:
        flags.append("低对比")
    if motion < 1.0:
        flags.append("静止")
    elif motion > 25:
        flags.append("晃动")
    return {
        "score": score,
        "brightness": round(brightness, 1),
        "contrast": round(contrast, 1),
        "motion": round(motion, 1),
        "flags": flags,
    }


def probe_quality(path: Path) -> dict | None:
    """用 ffmpeg signalstats 取每帧亮度/对比/帧差，汇总成镜头质量指标。"""
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(path),
        "-vf", "signalstats,metadata=mode=print", "-an", "-f", "null", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    values: dict[str, list[float]] = {"YAVG": [], "YLOW": [], "YHIGH": [], "YDIF": []}
    for line in (result.stderr or "").splitlines():
        for key in values:
            marker = f"lavfi.signalstats.{key}="
            if marker in line:
                try:
                    values[key].append(float(line.split(marker, 1)[1].split()[0]))
                except (ValueError, IndexError):
                    pass
    if not values["YAVG"]:
        return None
    average = {key: sum(items) / len(items) for key, items in values.items() if items}
    brightness = average.get("YAVG", 128.0)
    contrast = max(0.0, average.get("YHIGH", 0.0) - average.get("YLOW", 0.0))
    motion = average.get("YDIF", 0.0)
    return _quality_score(brightness, contrast, motion)


def _cut_segment(source: Path, target: Path, start: float, duration: float) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(source),
         "-t", f"{duration:.3f}", "-c", "copy", "-avoid_negative_ts", "make_zero", str(target)],
        # 关键帧不在起点时 copy 可能失败，降级为快速重编码（仅用于分析，不参与成片）
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-i", str(source),
         "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "ultrafast",
         "-crf", "28", str(target)],
    ]
    for command in commands:
        try:
            subprocess.run(command, capture_output=True, timeout=900, check=False)
            if target.is_file() and target.stat().st_size > 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def _copy_artifact(source: Any, target: Path) -> str:
    if not source:
        return ""
    try:
        shutil.copy2(str(source), target)
        return target.name
    except OSError:
        return ""


def analyze_deep(
    store: TaskStore,
    spec: dict[str, Any],
    log: Callable[[str], None] = print,
) -> str:
    from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService
    from app.services.generate_narration_script import parse_frame_analysis_to_markdown

    video_path = Path(spec["video_path"])
    duration = float(spec.get("video_duration") or spec.get("target_duration") or 60)
    duration = max(1.0, duration)
    segment_seconds = max(10.0, min(float(spec.get("deep_segment_seconds") or 30), 120.0))
    frame_interval = max(0.5, min(float(spec.get("deep_frame_interval") or 1.5), 5.0))
    batch_size = max(1, min(int(spec.get("deep_vision_batch_size") or 8), 20))
    concurrency = max(1, min(int(spec.get("vision_max_concurrency") or 2), 6))
    cuts: list[float] = []
    if spec.get("deep_scene_detection", True) and duration > 8:
        store.update_state(
            spec["task_id"],
            status="running",
            phase="analyzing",
            progress=4,
            message="深度分析：正在检测镜头切换",
            step_current=1,
            step_total=6,
        )
        cuts = detect_scene_cuts(video_path)
    ranges = build_segments(cuts, duration, target=segment_seconds)
    count = len(ranges)

    task_dir = store.task_dir(spec["task_id"])
    analysis_dir = task_dir / "analysis"
    segment_dir = analysis_dir / "segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _cache_key(video_path, spec, segment_seconds, frame_interval)
    cache_dir = store.root / "storage" / "analysis_cache" / cache_key
    if _restore_cache(cache_dir, analysis_dir):
        timeline_text = (analysis_dir / "timeline.md").read_text(encoding="utf-8")
        store.update_state(
            spec["task_id"],
            status="running",
            phase="analyzing",
            progress=26,
            message="深度分析命中缓存，复用已有分段解析",
            step_current=1,
            step_total=6,
        )
        log(f"深度分析命中缓存：{cache_key}")
        return timeline_text

    service = DocumentaryFrameAnalysisService()
    theme_context = spec.get("topic") or ""
    parts: list[str] = []
    errors: list[str] = []

    for index, (start, end) in enumerate(ranges):
        length = max(1.0, end - start)
        segment_file = segment_dir / f"segment_{index + 1:04d}.mp4"

        store.update_state(
            spec["task_id"],
            status="running",
            phase="analyzing",
            progress=round(5 + (index / count) * 20, 1),
            message=f"深度分析：正在切分第 {index + 1}/{count} 段",
            step_current=1,
            step_total=6,
        )
        log(f"深度分析片段 {index + 1}/{count}：{start:.1f}-{end:.1f}s")
        if not segment_file.exists() and not _cut_segment(video_path, segment_file, start, length):
            errors.append(f"{start:.1f}-{end:.1f}s 切分失败")
            continue
        quality = probe_quality(segment_file)

        def progress(value: float, message: str = "", _index=index) -> None:
            mapped = 5 + ((_index + max(0.0, min(100.0, float(value))) / 100) / count) * 20
            store.update_state(
                spec["task_id"],
                status="running",
                phase="analyzing",
                progress=round(mapped, 1),
                message=message or f"深度分析第 {_index + 1}/{count} 段",
                step_current=1,
                step_total=6,
            )

        try:
            result = asyncio.run(
                service.analyze_video(
                    video_path=str(segment_file),
                    video_theme=theme_context,
                    custom_prompt=spec.get("instruction", ""),
                    frame_interval_input=frame_interval,
                    vision_batch_size=batch_size,
                    max_concurrency=concurrency,
                    progress_callback=progress,
                )
            )
            artifact_source = result.get("analysis_json_path")
            markdown = parse_frame_analysis_to_markdown(artifact_source) if artifact_source else ""
            if not str(markdown or "").strip():
                raise RuntimeError("画面解析结果为空")
            global_markdown = shift_timestamps(markdown, start)
            artifact_name = _copy_artifact(
                artifact_source,
                segment_dir / f"segment_{index + 1:04d}.analysis.json",
            )
            record = {
                "index": index + 1,
                "start": round(start, 3),
                "end": round(end, 3),
                "artifact": artifact_name,
                "quality": quality,
                "markdown": global_markdown,
            }
            (segment_dir / f"segment_{index + 1:04d}.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            quality_line = ""
            if quality:
                flags = "、".join(quality.get("flags") or []) or "无"
                quality_line = (
                    f"- 镜头质量：{quality.get('score')} / 100"
                    f"（亮度 {quality.get('brightness')}，对比 {quality.get('contrast')}，"
                    f"运动 {quality.get('motion')}，标记：{flags}）\n"
                )
            parts.append(
                f"## 片段 {index + 1}（{start:.1f}-{end:.1f} 秒）\n{quality_line}{global_markdown}"
            )
        except Exception as exc:  # noqa: BLE001 - 单段失败不阻塞其余段
            errors.append(f"{start:.1f}-{end:.1f}s：{exc}")
            log(f"片段 {index + 1} 分析失败：{exc}")
        finally:
            try:
                segment_file.unlink(missing_ok=True)
            except OSError:
                pass

    if not parts:
        raise RuntimeError("深度分析没有产出任何有效片段：" + "；".join(errors[:3]))

    timeline = "# 全片分段解析\n\n" + "\n\n".join(parts)
    (analysis_dir / "timeline.md").write_text(timeline, encoding="utf-8")
    (analysis_dir / "analysis.json").write_text(
        json.dumps(
            {
                "duration": duration,
                "segment_seconds": segment_seconds,
                "frame_interval": frame_interval,
                "segments": count,
                "scene_aware": bool(spec.get("deep_scene_detection", True)),
                "scene_cuts": cuts[:1000],
                "ranges": ranges,
                "failed": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _save_cache(analysis_dir, cache_dir, cache_key)
    store.update_state(
        spec["task_id"],
        status="running",
        phase="analyzing",
        progress=26,
        message=f"深度分析完成：{len(parts)}/{count} 段",
        step_current=1,
        step_total=6,
    )
    log(f"深度分析完成：{len(parts)}/{count} 段")
    return timeline
