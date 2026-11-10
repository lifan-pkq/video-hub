"""ASS subtitles burned by ffmpeg. Times are on the output timeline."""

from __future__ import annotations

from pathlib import Path


def ass_time(seconds: float) -> str:
    centiseconds = int(round(max(0.0, seconds) * 100))
    seconds_part, cs = divmod(centiseconds, 100)
    minutes, sec = divmod(seconds_part, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours}:{minute:02d}:{sec:02d}.{cs:02d}"


def escape_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def chinese_font() -> tuple[str, Path | None]:
    candidates = [
        ("Droid Sans Fallback", Path("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf")),
        ("WenQuanYi Micro Hei", Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc")),
        ("Noto Sans CJK SC", Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")),
    ]
    for family, path in candidates:
        if path.is_file():
            return family, path
    return "Sans", None


def write_ass(
    path: Path,
    cues: list[dict],
    *,
    width: int,
    height: int,
    font_size: int = 46,
    margin_v: int = 48,
) -> None:
    family, _font = chinese_font()
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{family},{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,2,0,2,40,40,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    for cue in cues:
        start = ass_time(float(cue["start"]))
        end = ass_time(float(cue["end"]))
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{escape_text(str(cue['text']))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
