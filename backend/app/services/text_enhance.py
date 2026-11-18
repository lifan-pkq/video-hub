"""可选的字幕、旁注和轻效果。默认关闭，也不代表陈泽风格。

这里只生成我们自己的字幕稿和固定滤镜参数。用户文字不能进入滤镜图。
"""

from __future__ import annotations

import re
import struct
from decimal import Decimal, InvalidOperation
from pathlib import Path

from app.services.douyin_plan import ass_timestamp

STYLES = {"outline", "plain", "yellow"}
EMPHASIS = {"none", "fade", "pop"}
FORBIDDEN_LINE_START = set("，。、；：！？、,.;:!?」』）】》")
PUNCTUATION = set("，。、；：！？,.;:!?「」『』（）()【】《》…—-~· ")
MAX_CUES = 80
MAX_CUE_CHARS = 180
MAX_TITLE_CHARS = 40


class TextEnhanceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def disabled_settings() -> dict:
    return {
        "enabled": False,
        "captions": True,
        "title_text": "",
        "style": "outline",
        "fade": False,
        "emphasis": "none",
    }


def normalize_settings(raw: dict | None) -> dict:
    settings = disabled_settings()
    if not isinstance(raw, dict):
        return settings
    settings["enabled"] = bool(raw.get("enabled"))
    settings["captions"] = bool(raw.get("captions", True))
    title = str(raw.get("title_text") or "").replace("\x00", "").strip()
    if len(title) > MAX_TITLE_CHARS:
        raise TextEnhanceError("title", f"旁注请控制在 {MAX_TITLE_CHARS} 字以内")
    settings["title_text"] = title
    style = str(raw.get("style") or "outline")
    emphasis = str(raw.get("emphasis") or "none")
    if style not in STYLES or emphasis not in EMPHASIS:
        raise TextEnhanceError("style", "样式或动效不在可选范围内")
    settings["style"] = style
    settings["emphasis"] = emphasis
    settings["fade"] = bool(raw.get("fade"))
    return settings


def _decimal(value, label: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise TextEnhanceError("INVALID_INTERVAL", f"{label} 不是有效时间") from exc
    if not number.is_finite():
        raise TextEnhanceError("INVALID_INTERVAL", f"{label} 必须是有限数值")
    return number


def clean_display_text(text: str) -> str:
    if not isinstance(text, str):
        raise TextEnhanceError("INVALID_TEXT", "字幕必须是文本")
    if "\x00" in text:
        raise TextEnhanceError("INVALID_TEXT", "字幕含有非法空字符")
    kept = []
    for char in text.replace("\r", "").replace("\n", ""):
        if char >= " " and char != "\x7f":
            kept.append(char)
    return "".join(kept).strip()


def prepare_cues(cues: list, duration: float) -> list[dict]:
    """校验输出时间轴上的口播字幕。重叠、逆序和越界都拒绝，不改成片时长。"""
    if not isinstance(cues, list):
        raise TextEnhanceError("INVALID_TEXT", "字幕列表无法读取")
    if len(cues) > MAX_CUES:
        raise TextEnhanceError("LIMIT", f"一条成片最多 {MAX_CUES} 条字幕")
    span = _decimal(duration, "成片时长")
    if span <= 0:
        raise TextEnhanceError("INVALID_INTERVAL", "成片时长必须大于 0")
    prepared = []
    for item in cues:
        if not isinstance(item, dict):
            raise TextEnhanceError("INVALID_TEXT", "有一条字幕格式不对")
        start = _decimal(item.get("start"), "字幕起点")
        end = _decimal(item.get("end"), "字幕终点")
        if start < 0 or end <= start:
            raise TextEnhanceError("INVALID_INTERVAL", "字幕时间必须满足 0 ≤ 起点 < 终点")
        if end > span + Decimal("0.05"):
            raise TextEnhanceError("INVALID_INTERVAL", "字幕超出成片时长")
        if end > span:
            end = span
        text = clean_display_text(str(item.get("text") or ""))
        if not text:
            continue
        if len(text) > MAX_CUE_CHARS:
            raise TextEnhanceError("LIMIT", f"单条字幕请控制在 {MAX_CUE_CHARS} 字以内")
        if all(char in PUNCTUATION for char in text):
            raise TextEnhanceError("PUNCTUATION_ONLY", "只有标点的字幕不会烧进画面")
        prepared.append({"start": start, "end": end, "text": text, "role": "quote"})
    prepared.sort(key=lambda cue: (cue["start"], cue["end"]))
    previous = None
    for cue in prepared:
        if previous is not None and cue["start"] < previous["end"]:
            raise TextEnhanceError("OVERLAPPING_CUES", "口播字幕互相重叠，请先改开时间")
        previous = cue
    return prepared


def wrap_lines(text: str, max_chars: int) -> list[str]:
    """按字数换行，并避免换行后把标点留在下一行开头。

    这是我们插入的换行，不是 libass 的中文禁则，也不是国标验收。
    """
    max_chars = max(4, int(max_chars))
    chars = list(clean_display_text(text))
    lines = []
    index = 0
    while index < len(chars):
        end = min(len(chars), index + max_chars)
        if end < len(chars) and chars[end] in FORBIDDEN_LINE_START:
            if end - index < max_chars + 1:
                end += 1
            else:
                end = max(index + 1, end - 1)
                while end > index + 1 and chars[end] in FORBIDDEN_LINE_START:
                    end -= 1
        if end <= index:
            end = index + 1
        lines.append("".join(chars[index:end]))
        index = end
    return lines


def escape_ass(text: str) -> str:
    cleaned = clean_display_text(text)
    return cleaned.replace("\\", "").replace("{", "｛").replace("}", "｝")


def _emphasis_prefix(emphasis: str) -> str:
    if emphasis == "fade":
        return r"{\fad(180,180)}"
    if emphasis == "pop":
        return r"{\fscx80\fscy80\t(0,160,\fscx100\fscy100)}"
    return ""


def _style_row(name: str, font: str, size: int, colour: str, outline: int, shadow: int, align: int, margin_v: int) -> str:
    return (
        f"Style: {name},{font},{size},{colour},&H000000FF,&H00000000,&H64000000,"
        f"0,0,0,0,100,100,0,0,1,{outline},{shadow},{align},48,48,{margin_v},1"
    )


def build_document(
    cues: list[dict],
    title: str,
    *,
    width: int,
    height: int,
    font_name: str,
    style: str,
    emphasis: str,
) -> str:
    width = int(width)
    height = int(height)
    if width < 2 or height < 2:
        raise TextEnhanceError("frame", "画面尺寸无效")
    font_size = max(16, int(height * 0.034))
    title_size = max(font_size + 4, int(height * 0.042))
    margin_v = max(28, int(height * 0.12))
    title_margin = max(24, int(height * 0.08))
    outline = 0 if style == "plain" else 4
    shadow = 0 if style == "plain" else 1
    fill = "&H0000FFFF" if style == "yellow" else "&H00FFFFFF"
    max_chars = max(8, int((width - 96) / max(font_size, 1)))
    prefix = _emphasis_prefix(emphasis if emphasis in EMPHASIS else "none")
    events = []
    if title:
        lines = wrap_lines(title, max(6, max_chars - 2))
        shown = "\\N".join(escape_ass(line) for line in lines)
        events.append(
            f"Dialogue: 1,0:00:00.00,9:59:59.00,Title,,0,0,0,,{prefix}{shown}"
        )
    for cue in cues:
        lines = wrap_lines(cue["text"], max_chars)
        shown = "\\N".join(escape_ass(line) for line in lines)
        if not shown:
            continue
        events.append(
            f"Dialogue: 0,{ass_timestamp(float(cue['start']))},{ass_timestamp(float(cue['end']))},"
            f"Caption,,0,0,0,,{prefix}{shown}"
        )
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {width}\n"
        f"PlayResY: {height}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{_style_row('Caption', font_name, font_size, fill, outline, shadow, 2, margin_v)}\n"
        f"{_style_row('Title', font_name, title_size, '&H00FFFFFF', 4, 1, 8, title_margin)}\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    return header + "\n".join(events) + ("\n" if events else "")


def write_srt(cues: list[dict], path: Path) -> None:
    lines = []
    for index, cue in enumerate(cues, start=1):
        lines.append(str(index))
        lines.append(f"{_srt_time(cue['start'])} --> {_srt_time(cue['end'])}")
        lines.append(str(cue["text"]))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _srt_time(value) -> str:
    total = float(value)
    if total < 0:
        total = 0.0
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    seconds = total % 60
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis = 0
        seconds = int(seconds) + 1
    else:
        seconds = int(seconds)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def fade_filter(duration: float) -> str:
    span = float(duration)
    fade = min(0.35, span / 4)
    if fade < 0.12:
        return ""
    end = max(0.0, span - fade)
    return f"fade=t=in:st=0:d={fade:.3f},fade=t=out:st={end:.3f}:d={fade:.3f}"


def public_status(settings: dict, *, applied: list[str] | None = None, font: str = "") -> dict:
    return {
        "enabled": bool(settings.get("enabled")),
        "applied_effects": list(applied or []),
        "style": settings.get("style") or "outline",
        "emphasis": settings.get("emphasis") or "none",
        "fade": bool(settings.get("fade")),
        "font": font,
        "review_status": "pending" if settings.get("enabled") else "",
        "validated_chenze_style": False,
        "audio": "original",
        "added_audio": False,
        "typography": "explicit_line_breaks" if settings.get("enabled") else "",
        "typography_note": (
            "中文行首标点靠显式换行避开，不是 libass 禁则，也未按国标验收。"
            if settings.get("enabled") else ""
        ),
    }


def _table(data: bytes, base: int, tag: bytes) -> bytes | None:
    if base + 12 > len(data):
        return None
    count = struct.unpack_from(">H", data, base + 4)[0]
    records = base + 12
    for index in range(count):
        offset = records + index * 16
        if offset + 16 > len(data):
            return None
        if data[offset:offset + 4] == tag:
            start, length = struct.unpack_from(">II", data, offset + 8)
            if start + length > len(data):
                return None
            return data[start:start + length]
    return None


def _faces(data: bytes):
    if data[:4] == b"ttcf":
        count = struct.unpack_from(">I", data, 8)[0]
        offsets = struct.unpack_from(f">{count}I", data, 12)
        for offset in offsets:
            yield offset
    else:
        yield 0


def _cmap_has(cmap: bytes, codepoint: int) -> bool:
    if len(cmap) < 4:
        return False
    count = struct.unpack_from(">H", cmap, 2)[0]
    subtables = []
    for index in range(count):
        position = 4 + index * 8
        if position + 8 > len(cmap):
            break
        platform, encoding, offset = struct.unpack_from(">HHI", cmap, position)
        if offset >= len(cmap):
            continue
        if (platform, encoding) in {(3, 1), (3, 10), (0, 3), (0, 4)}:
            subtables.append(cmap[offset:])
    for sub in subtables:
        if len(sub) < 2:
            continue
        fmt = struct.unpack_from(">H", sub, 0)[0]
        if fmt == 4 and _format4_has(sub, codepoint):
            return True
        if fmt == 12 and _format12_has(sub, codepoint):
            return True
    return False


def _format4_has(sub: bytes, codepoint: int) -> bool:
    if codepoint > 0xFFFF or len(sub) < 16:
        return False
    seg_count = struct.unpack_from(">H", sub, 6)[0] // 2
    if seg_count <= 0:
        return False
    end_at = 14
    reserved = end_at + seg_count * 2
    start_at = reserved + 2
    delta_at = start_at + seg_count * 2
    range_at = delta_at + seg_count * 2
    glyph_at = range_at + seg_count * 2
    if glyph_at > len(sub):
        return False
    for index in range(seg_count):
        end = struct.unpack_from(">H", sub, end_at + index * 2)[0]
        start = struct.unpack_from(">H", sub, start_at + index * 2)[0]
        if start <= codepoint <= end:
            delta = struct.unpack_from(">h", sub, delta_at + index * 2)[0]
            range_offset = struct.unpack_from(">H", sub, range_at + index * 2)[0]
            if range_offset == 0:
                glyph = (codepoint + delta) & 0xFFFF
            else:
                glyph_pos = range_at + index * 2 + range_offset + (codepoint - start) * 2
                if glyph_pos + 2 > len(sub):
                    return False
                glyph = struct.unpack_from(">H", sub, glyph_pos)[0]
                if glyph:
                    glyph = (glyph + delta) & 0xFFFF
            return glyph != 0
    return False


def _format12_has(sub: bytes, codepoint: int) -> bool:
    if len(sub) < 16:
        return False
    groups = struct.unpack_from(">I", sub, 12)[0]
    position = 16
    for _ in range(groups):
        if position + 12 > len(sub):
            return False
        start, end, glyph = struct.unpack_from(">III", sub, position)
        if start <= codepoint <= end:
            return glyph != 0
        position += 12
    return False


def font_covers(path: Path, text: str) -> list[str]:
    """返回字体文件里覆盖不到的字符。读不出 cmap 时抛错，不靠 FFmpeg 的退出码。"""
    if path is None or not Path(path).is_file():
        raise TextEnhanceError("FONT_FILE_NOT_FOUND", "找不到可用的中文字体文件")
    data = Path(path).read_bytes()
    if len(data) < 12:
        raise TextEnhanceError("FONT_CMAP_UNREADABLE", "字体文件不完整")
    needed = []
    seen = set()
    for char in text:
        if char in seen or char in PUNCTUATION or ord(char) < 128:
            continue
        seen.add(char)
        needed.append(char)
    if not needed:
        return []
    missing = []
    faces = list(_faces(data))
    if not faces:
        raise TextEnhanceError("FONT_CMAP_UNREADABLE", "字体里没有可读取的字表")
    for char in needed:
        covered = False
        readable = False
        for base in faces:
            cmap = _table(data, base, b"cmap")
            if not cmap:
                continue
            readable = True
            if _cmap_has(cmap, ord(char)):
                covered = True
                break
        if not readable:
            raise TextEnhanceError("FONT_CMAP_UNREADABLE", "字体字表无法读取")
        if not covered:
            missing.append(char)
    return missing


def assert_font_covers(path: Path | None, text: str) -> None:
    missing = font_covers(path, text)
    if missing:
        shown = "、".join(missing[:8])
        raise TextEnhanceError("MISSING_GLYPHS", f"字体缺这些字：{shown}")


def font_selected(log: str, family: str) -> bool | None:
    lines = [line for line in str(log or "").splitlines() if "fontselect" in line.lower()]
    if not lines:
        return None
    key = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", family.lower())
    if not key:
        return None
    for line in lines:
        compact = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", line.lower())
        if key in compact:
            return True
    return False


def overlapped_duration(left: float, right: float, overlap: float) -> Decimal:
    """两段重叠转场后的时长。当前一条短视频导出不用这个，只保留已核对的算法。"""
    a = _decimal(left, "前段")
    b = _decimal(right, "后段")
    gap = _decimal(overlap, "重叠")
    if a <= 0 or b <= 0 or gap < 0 or gap >= a or gap >= b:
        raise TextEnhanceError("INVALID_INTERVAL", "重叠转场的时长不合法")
    return a + b - gap
