"""生成可发布的成品套装：多画幅、无字幕、SRT、封面、标题与 ZIP。"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from engine.finishing import reframe
from engine.spec import format_clock, parse_timestamp_range

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
SRT_TIME_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")
TITLE_KINDS = ("suspense", "information", "emotion")
TITLE_LIMITS = {"suspense": 18, "information": 26, "emotion": 20}
TITLE_STYLES = {
    "suspense": "悬念型：只抛出最有反差的疑问或未解之处，勾住人必须看完才罢休，不许剧透结论。",
    "information": "信息型：把这条视频最值得知道的干货结论说清楚，让人一眼知道能学到什么。",
    "emotion": "情绪型：抓住最有共鸣的那一下情绪，用第二人称或感叹句，先击中感受再谈事实。",
}
TITLE_PROMPT = """为下面这条视频写 3 个候选标题，每条都要是可直接发布的中文单行标题。

## 三个分型各写一版
{styles}

三条标题必须是三次真正重写：悬念型不能是信息型的截断，情绪型不能复述另外两型的句子。
字数上限：悬念型 {suspense_limit} 字，信息型 {information_limit} 字，情绪型 {emotion_limit} 字。
不要书名号、不要分点符号、不要表情、不要换行。

## 不许虚构
只能使用素材里出现的信息，可以改人称、换句式、合并前后文，但不得编造人物、数字或因果。

## 素材
成稿标题：{title}
开头：{first}
结尾：{last}
全片：{digest}

只返回 JSON：{{"titles":[{{"kind":"suspense","title":"…"}},{{"kind":"information","title":"…"}},{{"kind":"emotion","title":"…"}}]}}
"""


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _frame(source: Path, timestamp: float, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", f"{max(0, timestamp):.3f}", "-i", str(source),
         "-frames:v", "1", "-q:v", "2", str(target)],
        capture_output=True, timeout=120, check=False,
    )
    if result.returncode != 0 or not target.is_file():
        raise RuntimeError(f"封面抽帧失败：{timestamp:.2f}s")


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
            if len(lines) > TITLE_MAX_LINES:
                kept[-1] = kept[-1][:-1] + "…"
            return font, kept
        size -= 2


def _cover_with_title(
    frame: Path,
    target: Path,
    title: str,
    root: Path,
    index: int,
) -> dict[str, Any]:
    from PIL import Image, ImageDraw, ImageOps

    image = Image.open(frame).convert("RGB")
    canvas = ImageOps.fit(image, COVER_SIZE, method=Image.LANCZOS, centering=(0.5, 0.5))
    draw = ImageDraw.Draw(canvas, "RGBA")
    font_path = root / "resource" / "fonts" / "DroidSansFallbackFull.ttf"
    top = index == 1
    box = TITLE_BOX if top else TITLE_BOX_BOTTOM
    font, lines = _fit_title(title, font_path, box)
    draw.rounded_rectangle(box, radius=20, fill=(5, 8, 12, 178))
    text_height = len(lines) * (font.size + TITLE_LINE_SPACING) - TITLE_LINE_SPACING
    y = box[1] + max(0, (box[3] - box[1] - text_height) // 2)
    draw.multiline_text((TITLE_TEXT_X, y), "\n".join(lines), font=font, fill="#FFFFFF", spacing=TITLE_LINE_SPACING)
    target.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(target, quality=93)
    return {
        "image_size": list(canvas.size),
        "font_size": font.size,
        "lines": len(lines),
        "max_line_width": round(max(font.getlength(line) for line in lines), 2),
        "box_width": box[2] - box[0],
        "box_height": box[3] - box[1],
    }


def _script_first_last(script: list[dict[str, Any]], fallback: str) -> tuple[str, str]:
    first = next((str(item.get("narration") or "") for item in script if item.get("narration")), fallback)
    last = next((str(item.get("narration") or "") for item in reversed(script) if item.get("narration")), first)
    return first, last


def _fallback_titles(script: list[dict[str, Any]], fallback: str) -> list[dict[str, Any]]:
    first, last = _script_first_last(script, fallback)
    return [
        {"kind": "suspense", "title": f"{first[:22]}，接下来呢？", "description": f"从“{first[:40]}”开始，直到“{last[:40]}”。"},
        {"kind": "information", "title": first[:28] or fallback, "description": f"完整梳理：{first[:60]}。"},
        {"kind": "emotion", "title": f"这一刻，{last[:22]}", "description": f"关于“{last[:50]}”的一刻。"},
    ]


def _title_digest(script: list[dict[str, Any]], understanding: dict[str, Any], limit: int = 1200) -> str:
    texts = [str(item.get("narration") or "").strip() for item in script]
    if not any(texts):
        speech = (understanding.get("tracks") or {}).get("speech") or []
        texts = [str(item.get("text") or "").strip() for item in speech]
    return " / ".join(text for text in texts if text)[:limit]


def _model_titles(
    script: list[dict[str, Any]],
    understanding: dict[str, Any],
    fallback: str,
) -> dict[str, str]:
    from engine.script import _chat_text, _extract_json, _text_settings

    model, base_url, keys = _text_settings()
    first, last = _script_first_last(script, fallback)
    prompt = TITLE_PROMPT.format(
        styles="\n".join(f"- {TITLE_STYLES[kind]}" for kind in TITLE_KINDS),
        suspense_limit=TITLE_LIMITS["suspense"],
        information_limit=TITLE_LIMITS["information"],
        emotion_limit=TITLE_LIMITS["emotion"],
        title=fallback,
        first=(_title_digest(script, understanding, 200) or fallback)[:200],
        last=(last or fallback)[:200],
        digest=_title_digest(script, understanding) or fallback,
    )
    payload = _extract_json(_chat_text(
        model,
        base_url,
        keys,
        [{"role": "user", "content": prompt}],
        lambda message: None,
        temperature=0.8,
    ))
    titles: dict[str, str] = {}
    for raw in payload.get("titles") or []:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "").strip()
        title = " ".join(str(raw.get("title") or "").split())
        if kind in TITLE_KINDS and title:
            titles[kind] = title[:60]
    return titles


def _titles(
    fallback: str,
    script: list[dict[str, Any]],
    understanding: dict[str, Any],
) -> list[dict[str, Any]]:
    titles = _fallback_titles(script, fallback)
    try:
        generated = _model_titles(script, understanding, fallback)
    except Exception:  # noqa: BLE001 - 模型不可用属预期回落路径
        generated = {}
    if not generated:
        for item in titles:
            item["generated"] = False
        return titles
    distinct = len({title for title in generated.values()}) == len(generated)
    for item in titles:
        title = generated.get(item["kind"])
        if title and distinct:
            item["title"] = title
            item["generated"] = True
        else:
            item["generated"] = False
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


def _srt(
    script: list[dict[str, Any]],
    understanding: dict[str, Any],
    target: Path,
    task_dir: Path | None = None,
) -> dict[str, Any]:
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


def _probe_dimensions(source: Path) -> tuple[int, int]:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "json", str(source)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    try:
        payload = json.loads(probe.stdout or "{}")
    except ValueError:
        return 0, 0
    stream = (payload.get("streams") or [{}])[0]
    try:
        return int(stream.get("width") or 0), int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return 0, 0


def _is_landscape(source: Path) -> bool:
    width, height = _probe_dimensions(source)
    if not width or not height:
        return False
    return abs(width / height - LANDSCAPE_RATIO) < 0.01


def load_subject_track(subject_track_path: Path | None, task_dir: Path | None) -> list[dict[str, Any]]:
    candidates = []
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
) -> dict[str, Any]:
    root = Path(__file__).resolve().parent.parent
    work_dir.mkdir(parents=True, exist_ok=True)
    script_payload = _load_json(task_dir / "script.json") if task_dir else None
    script: list[dict[str, Any]] = script_payload if isinstance(script_payload, list) else []
    understanding_payload = (
        _load_json(task_dir / "analysis" / "understanding.json") if task_dir else None
    )
    understanding: dict[str, Any] = understanding_payload if isinstance(understanding_payload, dict) else {}
    subject_track = load_subject_track(subject_track_path, task_dir)
    clean = task_dir / "clean.mp4" if task_dir and (task_dir / "clean.mp4").is_file() else None
    files: dict[str, str] = {}
    landscape_copied = _is_landscape(source)
    if landscape_copied:
        shutil.copy2(source, work_dir / "landscape.mp4")
    else:
        reframe(source, work_dir / "landscape.mp4", "16:9", subject_track)
    files["landscape"] = "landscape.mp4"
    for aspect, name in (("9:16", "portrait"), ("1:1", "square")):
        reframe(source, work_dir / f"{name}.mp4", aspect, subject_track)
        files[name] = f"{name}.mp4"
    if clean:
        shutil.copy2(clean, work_dir / "without-subtitles.mp4")
        files["without_subtitles"] = "without-subtitles.mp4"
    srt_report = _srt(script, understanding, work_dir / "subtitles.srt", task_dir)
    if srt_report["srt_cues"]:
        files["srt"] = "subtitles.srt"
    titles = _titles(title, script, understanding)
    timestamps = _cover_timestamps(script)
    covers = []
    for index, (timestamp, title_item) in enumerate(zip(timestamps[:3], titles), 1):
        frame = work_dir / f"cover-{index}.jpg"
        _frame(source, timestamp, frame)
        _cover_with_title(frame, frame, title_item["title"], root, index - 1)
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
        manifest["without_subtitles_note"] = "任务目录没有 clean.mp4：该渲染路径未产出无字幕母版，成片已烧字幕，故不提供无字幕文件"
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
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--title", default="Video Hub 成片")
    parser.add_argument("--task-dir")
    parser.add_argument("--subject-track")
    args = parser.parse_args()
    result = export_bundle(
        Path(args.source).resolve(),
        Path(args.work_dir).resolve(),
        args.title,
        Path(args.task_dir).resolve() if args.task_dir else None,
        Path(args.subject_track).resolve() if args.subject_track else None,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
