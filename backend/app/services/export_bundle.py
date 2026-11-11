"""封面与多画幅导出，在当前进程里完成。

以前这一步会再拉起 narratoai 虚拟环境跑 ``engine.export_task``。
现在直接调用本模块，失败信息回到导出任务，不再依赖那棵目录。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from app.config import douyin_settings

FONT_PATH = Path(__file__).resolve().parents[2] / "assets" / "fonts" / "DroidSansFallbackFull.ttf"
COVER_SIZE = (1280, 720)
TITLE_BOX = (42, 40, 1238, 230)
TITLE_BOX_BOTTOM = (42, 490, 1238, 680)
TITLE_FONT_SIZE = 52
TITLE_MIN_FONT_SIZE = 24
TITLE_MAX_LINES = 3
TITLE_LINE_SPACING = 14
TITLE_PADDING = 60
TITLE_TEXT_X = 72
LANDSCAPE_RATIO = 1920 / 1080
DIMENSIONS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
SRT_TIME_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})"
)
TITLE_KINDS = ("suspense", "information", "emotion")
TITLE_LIMITS = {"suspense": 18, "information": 26, "emotion": 20}
TITLE_STYLES = {
    "suspense": "悬念型：只抛出最有反差的疑问，不许剧透结论。",
    "information": "信息型：把这条视频最值得知道的结论说清楚。",
    "emotion": "情绪型：抓住最有共鸣的那一下，先击中感受。",
}
Progress = Callable[[float, str], None]


class _TitleItem(BaseModel):
    kind: str = ""
    title: str = ""


class _TitlePayload(BaseModel):
    titles: list[_TitleItem] = Field(default_factory=list)


class ExportBundleError(RuntimeError):
    pass


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _parse_clock(value: str) -> float:
    text = value.strip().replace(",", ".")
    parts = text.split(":")
    if not text or len(parts) > 3:
        raise ValueError(f"时间戳格式错误：{value}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"时间戳格式错误：{value}") from exc
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    if seconds < 0:
        raise ValueError(f"时间戳格式错误：{value}")
    return seconds


def format_clock(seconds: float) -> str:
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


def parse_timestamp_range(timestamp: str) -> tuple[float, float]:
    text = str(timestamp or "").strip()
    parts = re.split(r"\s*[-~—至]\s*", text, maxsplit=1)
    if len(parts) != 2:
        raise ValueError(f"时间戳必须是“开始-结束”：{text}")
    start = _parse_clock(parts[0])
    end = _parse_clock(parts[1])
    if end <= start:
        raise ValueError(f"时间戳结束必须大于开始：{text}")
    return start, end


def _run(command: list[str], timeout: int = 3600) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ExportBundleError(f"ffmpeg 超时（{timeout}s）") from exc
    except OSError as exc:
        raise ExportBundleError(f"无法启动 ffmpeg：{exc}") from exc
    if result.returncode != 0:
        raise ExportBundleError("画面处理失败：" + (result.stderr or result.stdout or "ffmpeg 失败")[-500:])


def _piecewise_crop(steps: list[tuple[float, str]], fallback: str) -> str:
    expression = steps[-1][1]
    for index in range(len(steps) - 2, -1, -1):
        expression = f"if(lt(t,{steps[index + 1][0]:.3f}),{steps[index][1]},{expression})"
    return f"if(lt(t,{steps[0][0]:.3f}),{fallback},{expression})"


def crop_filter(
    source_width: int,
    source_height: int,
    aspect: str,
    subject_track: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    target_width, target_height = DIMENSIONS[aspect]
    ratio = target_width / target_height
    if source_width / source_height > ratio:
        crop_height = source_height
        crop_width = int(source_height * ratio) // 2 * 2
    else:
        crop_width = source_width
        crop_height = int(source_width / ratio) // 2 * 2
    crop_width = min(source_width // 2 * 2, crop_width)
    crop_height = min(source_height // 2 * 2, crop_height)
    points = sorted(subject_track or [], key=lambda item: float(item.get("timestamp") or 0))
    if not points:
        return f"crop={crop_width}:{crop_height}:(iw-{crop_width})/2:(ih-{crop_height})/2", "safe_center"
    x_values: list[tuple[float, str]] = []
    y_values: list[tuple[float, str]] = []
    for point in points:
        box = point.get("subject_box") or point.get("box") or {}
        center_x = float(box.get("x", 500)) + float(box.get("width", 0)) / 2
        center_y = float(box.get("y", 500)) + float(box.get("height", 0)) / 2
        source_x = center_x / 1000 * source_width
        source_y = center_y / 1000 * source_height
        x = f"min(max({source_x:.3f}-{crop_width}/2,0),iw-{crop_width})"
        y = f"min(max({source_y:.3f}-{crop_height}/2,0),ih-{crop_height})"
        timestamp = float(point.get("timestamp") or 0)
        x_values.append((timestamp, x))
        y_values.append((timestamp, y))
    return (
        f"crop={crop_width}:{crop_height}:x='{_piecewise_crop(x_values, 'iw/2')}':y='{_piecewise_crop(y_values, 'ih/2')}'",
        "subject_track",
    )


def _probe_dimensions(source: Path) -> tuple[int, int]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(source)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    try:
        payload = json.loads(probe.stdout or "{}")
        stream = (payload.get("streams") or [{}])[0]
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (ValueError, TypeError):
        return 0, 0


def reframe(source: Path, target: Path, aspect: str, subject_track: list[dict[str, Any]] | None = None) -> None:
    if aspect not in DIMENSIONS:
        raise ExportBundleError("不支持的输出画幅")
    width, height = _probe_dimensions(source)
    if not width or not height:
        raise ExportBundleError("无法读取原片尺寸，确认 ffprobe 可用且文件能打开")
    crop, _tracking = crop_filter(width, height, aspect, subject_track)
    target_width, target_height = DIMENSIONS[aspect]
    threads = str(douyin_settings()["threads"])
    _run([
        "ffmpeg", "-y", "-v", "error", "-i", str(source),
        "-vf", f"{crop},scale={target_width}:{target_height},setsar=1",
        "-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-threads", threads,
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(target),
    ])


def _frame(source: Path, timestamp: float, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, timestamp):.3f}", "-i", str(source),
        "-frames:v", "1", "-q:v", "2", str(target),
    ], timeout=120)
    if not target.is_file():
        raise ExportBundleError(f"封面抽帧失败：{timestamp:.2f}s")


def _wrap_title(title: str, font: Any, limit: float) -> list[str]:
    if font.getlength(title) <= limit:
        return [title]
    lines: list[str] = []
    current = ""
    for char in title:
        if current and font.getlength(current + char) > limit:
            lines.append(current)
            current = ""
        current += char
    if current:
        lines.append(current)
    return lines


def _fit_title(title: str, font_path: Path, box: tuple[int, int, int, int]) -> tuple[Any, list[str]]:
    from PIL import ImageFont

    limit = (box[2] - box[0]) - TITLE_PADDING
    available = (box[3] - box[1]) - TITLE_PADDING
    size = TITLE_FONT_SIZE
    while True:
        font = ImageFont.truetype(str(font_path), size)
        lines = _wrap_title(title, font, limit)
        if len(lines) <= TITLE_MAX_LINES and len(lines) * (size + TITLE_LINE_SPACING) <= available:
            return font, lines
        if size <= TITLE_MIN_FONT_SIZE:
            kept = lines[:TITLE_MAX_LINES]
            if len(lines) > TITLE_MAX_LINES and kept:
                kept[-1] = kept[-1][:-1] + "…"
            return font, kept or [title[:12]]
        size -= 2


def _cover_with_title(frame: Path, target: Path, title: str, index: int) -> None:
    from PIL import Image, ImageDraw, ImageOps

    if not FONT_PATH.is_file():
        raise ExportBundleError(f"缺少中文封面字体：{FONT_PATH.name}")
    image = Image.open(frame).convert("RGB")
    canvas = ImageOps.fit(image, COVER_SIZE, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    draw = ImageDraw.Draw(canvas, "RGBA")
    box = TITLE_BOX if index == 0 else TITLE_BOX_BOTTOM
    font, lines = _fit_title(title, FONT_PATH, box)
    draw.rounded_rectangle(box, radius=20, fill=(5, 8, 12, 178))
    text_height = len(lines) * (font.size + TITLE_LINE_SPACING) - TITLE_LINE_SPACING
    y = box[1] + max(0, (box[3] - box[1] - text_height) // 2)
    draw.multiline_text((TITLE_TEXT_X, y), "\n".join(lines), font=font, fill="#FFFFFF", spacing=TITLE_LINE_SPACING)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, quality=93)


def _script_first_last(script: list[dict[str, Any]], fallback: str) -> tuple[str, str]:
    first = next((str(item.get("narration") or "") for item in script if item.get("narration")), fallback)
    last = next((str(item.get("narration") or "") for item in reversed(script) if item.get("narration")), first)
    return first, last


def _fallback_titles(script: list[dict[str, Any]], fallback: str) -> list[dict[str, Any]]:
    first, last = _script_first_last(script, fallback)
    return [
        {"kind": "suspense", "title": f"{first[:22]}，接下来呢？", "description": f"从“{first[:40]}”开始，直到“{last[:40]}”。", "generated": False},
        {"kind": "information", "title": (first[:28] or fallback)[:28], "description": f"完整梳理：{first[:60]}。", "generated": False},
        {"kind": "emotion", "title": f"这一刻，{last[:22]}", "description": f"关于“{last[:50]}”的一刻。", "generated": False},
    ]


def _title_digest(script: list[dict[str, Any]], understanding: dict[str, Any], limit: int = 1200) -> str:
    texts = [str(item.get("narration") or "").strip() for item in script]
    if not any(texts):
        speech = (understanding.get("tracks") or {}).get("speech") or []
        texts = [str(item.get("text") or "").strip() for item in speech]
    return " / ".join(text for text in texts if text)[:limit]


def _model_titles(script: list[dict[str, Any]], understanding: dict[str, Any], fallback: str) -> dict[str, str]:
    from engine.errors import EngineError
    from engine.llm import complete_json

    prompt = (
        "为这条视频写 3 个可直接发布的中文单行标题，分型必须真正不同。\n"
        + "\n".join(f"- {TITLE_STYLES[kind]} 不超过 {TITLE_LIMITS[kind]} 字。" for kind in TITLE_KINDS)
        + "\n不要书名号、分点和表情。只能使用素材里的信息。\n"
        f"成稿标题：{fallback}\n素材：{_title_digest(script, understanding) or fallback}"
    )
    try:
        payload = complete_json(
            system="你只返回 JSON：{\"titles\":[{\"kind\":\"suspense|information|emotion\",\"title\":\"…\"}]}",
            user=prompt,
            model=_TitlePayload,
            timeout=40,
        )
    except EngineError:
        return {}
    titles: dict[str, str] = {}
    for raw in payload.titles:
        kind = raw.kind.strip()
        title = " ".join(raw.title.split())
        if kind in TITLE_KINDS and title:
            titles[kind] = title[:60]
    return titles


def _titles(fallback: str, script: list[dict[str, Any]], understanding: dict[str, Any]) -> list[dict[str, Any]]:
    titles = _fallback_titles(script, fallback)
    try:
        generated = _model_titles(script, understanding, fallback)
    except Exception:
        generated = {}
    distinct = bool(generated) and len({title for title in generated.values()}) == len(generated) == len(TITLE_KINDS)
    for item in titles:
        title = generated.get(item["kind"])
        if title and distinct:
            item["title"] = title
            item["generated"] = True
    return titles


def _cover_timestamps(script: list[dict[str, Any]]) -> list[float]:
    def middle(item: dict[str, Any]) -> float | None:
        try:
            start, end = parse_timestamp_range(str(item.get("timestamp") or ""))
        except (TypeError, ValueError):
            return None
        return start + (end - start) / 2

    times: list[float] = []
    for role in ("hook", "climax", "ending"):
        item = next((entry for entry in script if entry.get("story_role") == role), None)
        value = middle(item) if item else None
        if value is not None:
            times.append(round(value, 3))
    for item in script:
        if len(times) >= 3:
            break
        value = middle(item)
        if value is not None and value not in times:
            times.append(round(value, 3))
    while len(times) < 3:
        times.append(round((times[-1] + 3.0) if times else 1.0, 3))
    return times[:3]


def _speech_cues(understanding: dict[str, Any]) -> list[tuple[float, float, str]]:
    cues: list[tuple[float, float, str]] = []
    for item in (understanding.get("tracks") or {}).get("speech") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if text and end > start:
            cues.append((start, end, text))
    return sorted(cues)


def _srt_seconds(match: re.Match) -> tuple[float, float]:
    values = [int(value) for value in match.groups()]
    start = values[0] * 3600 + values[1] * 60 + values[2] + values[3] / 1000
    end = values[4] * 3600 + values[5] * 60 + values[6] + values[7] / 1000
    return start, end


def _merged_srt_cues(task_dir: Path | None) -> list[tuple[float, float, str]]:
    if not task_dir:
        return []
    try:
        text = (task_dir / "script_subtitles.srt").read_text(encoding="utf-8-sig")
    except OSError:
        return []
    cues: list[tuple[float, float, str]] = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
        match = next((SRT_TIME_RE.search(line) for line in lines[:2] if SRT_TIME_RE.search(line)), None)
        if not match:
            continue
        start, end = _srt_seconds(match)
        narration = " ".join(lines[2:]).strip()
        if narration and end > start:
            cues.append((start, end, narration))
    return cues


def _srt(script: list[dict[str, Any]], understanding: dict[str, Any], target: Path, task_dir: Path | None) -> dict[str, Any]:
    cues = _merged_srt_cues(task_dir)
    source = "merged_subtitles"
    timing = "tts"
    if not cues:
        source = "understanding_speech"
        cues = _speech_cues(understanding)
    if not cues:
        source = "planned_script"
        timing = "planned"
        for item in script:
            narration = str(item.get("narration") or "").strip()
            if not narration:
                continue
            try:
                start, end = parse_timestamp_range(str(item.get("timestamp") or ""))
            except (TypeError, ValueError):
                continue
            cues.append((start, end, narration))
    if not cues:
        return {"srt_timing": "unavailable", "srt_cues": 0, "srt_source": "none"}
    lines = [
        f"{index}\n{format_clock(start)} --> {format_clock(end)}\n{text}"
        for index, (start, end, text) in enumerate(cues, 1)
    ]
    target.write_text("\n\n".join(lines) + "\n", encoding="utf-8")
    return {"srt_timing": timing, "srt_cues": len(cues), "srt_source": source}


def _is_landscape(source: Path) -> bool:
    width, height = _probe_dimensions(source)
    if not width or not height:
        return False
    return abs(width / height - LANDSCAPE_RATIO) < 0.01


def load_subject_track(subject_track_path: Path | None, task_dir: Path | None) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    if subject_track_path:
        candidates.append(Path(subject_track_path))
    if task_dir:
        candidates.append(Path(task_dir) / "analysis" / "understanding.json")
    for candidate in candidates:
        payload = _load_json(candidate)
        if not isinstance(payload, dict):
            continue
        track = (payload.get("tracks") or {}).get("visual") or []
        if track:
            return track
    return []


def export_bundle(
    source: Path,
    work_dir: Path,
    title: str,
    task_dir: Path | None = None,
    subject_track_path: Path | None = None,
    on_progress: Progress | None = None,
) -> dict[str, Any]:
    def progress(value: float, label: str) -> None:
        if on_progress:
            on_progress(value, label)

    work_dir.mkdir(parents=True, exist_ok=True)
    script_payload = _load_json(task_dir / "script.json") if task_dir else None
    script: list[dict[str, Any]] = script_payload if isinstance(script_payload, list) else []
    understanding_payload = _load_json(task_dir / "analysis" / "understanding.json") if task_dir else None
    understanding: dict[str, Any] = understanding_payload if isinstance(understanding_payload, dict) else {}
    subject_track = load_subject_track(subject_track_path, task_dir)
    clean = task_dir / "clean.mp4" if task_dir and (task_dir / "clean.mp4").is_file() else None
    files: dict[str, Any] = {}
    progress(12, "生成横屏")
    if _is_landscape(source):
        shutil.copy2(source, work_dir / "landscape.mp4")
        landscape_copied = True
    else:
        reframe(source, work_dir / "landscape.mp4", "16:9", subject_track)
        landscape_copied = False
    files["landscape"] = "landscape.mp4"
    for index, (aspect, name) in enumerate((("9:16", "portrait"), ("1:1", "square"))):
        progress(28 + index * 18, f"生成{name}")
        reframe(source, work_dir / f"{name}.mp4", aspect, subject_track)
        files[name] = f"{name}.mp4"
    if clean:
        shutil.copy2(clean, work_dir / "without-subtitles.mp4")
        files["without_subtitles"] = "without-subtitles.mp4"
    progress(68, "写字幕和标题")
    srt_report = _srt(script, understanding, work_dir / "subtitles.srt", task_dir)
    if srt_report["srt_cues"]:
        files["srt"] = "subtitles.srt"
    titles = _titles(title, script, understanding)
    timestamps = _cover_timestamps(script)
    covers = []
    for index, (timestamp, title_item) in enumerate(zip(timestamps[:3], titles)):
        progress(74 + index * 6, f"封面 {index + 1}/3")
        frame = work_dir / f"cover-{index + 1}.jpg"
        _frame(source, timestamp, frame)
        _cover_with_title(frame, frame, title_item["title"], index)
        covers.append(frame.name)
    files["covers"] = covers
    (work_dir / "titles.json").write_text(json.dumps(titles, ensure_ascii=False, indent=2), encoding="utf-8")
    files["titles"] = "titles.json"
    manifest: dict[str, Any] = {
        "schema_version": 2,
        "title": title,
        "files": files,
        "title_options": titles,
        "titles_generated": all(item["generated"] for item in titles),
        "cover_timestamps": timestamps[:3],
        "srt_timing": srt_report["srt_timing"],
        "srt_cues": srt_report["srt_cues"],
        "srt_source": srt_report["srt_source"],
        "without_subtitles_source": "clean.mp4" if clean else "unavailable",
        "landscape_reencoded": not landscape_copied,
        "selected_cover": covers[0],
        "selected_title": titles[0]["title"],
        "complete": bool(script),
    }
    if not manifest["complete"]:
        manifest["incomplete_reason"] = "缺少 script.json：标题与封面时间点为回落值，无可导出字幕"
    if not clean:
        manifest["without_subtitles_note"] = "任务目录没有 clean.mp4，不提供无字幕文件"
    progress(94, "打包 ZIP")
    manifest_path = work_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = work_dir / "video-package.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(work_dir.iterdir()):
            if path.is_file() and path.name != archive.name:
                bundle.write(path, arcname=path.name)
    manifest["archive"] = archive.name
    manifest["archive_size"] = archive.stat().st_size
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    progress(100, "导出完成")
    return manifest
