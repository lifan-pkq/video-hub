"""Standalone subtitle preview service for NarratoAI.

Renders subtitle mask overlays and styled subtitle previews as PNG images,
using the same layout math as the NarratoAI web UI.
"""

import io
import os
import tomllib

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import Response
from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageFilter

PROJECT_ROOT = os.environ.get("NARRATOAI_ROOT", "/opt/NarratoAI")
FONT_DIR = os.path.join(PROJECT_ROOT, "resource", "fonts")
SAFE_AREA_DIR = os.path.join(PROJECT_ROOT, "resource", "safe_areas")
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.toml")

SAFE_AREA_IMAGES = {
    "landscape": "subtitle_safe_area_landscape.png",
    "portrait": "subtitle_safe_area_portrait.png",
}
FALLBACK_SIZES = {"landscape": (1920, 1080), "portrait": (1080, 1920)}

# Defaults mirror the NarratoAI config.toml [ui] subtitle mask defaults.
MASK_DEFAULTS = {
    "landscape": {"x_percent": 10, "y_percent": 78, "width_percent": 80, "height_percent": 14, "blur_radius": 18, "opacity_percent": 82},
    "portrait": {"x_percent": 8, "y_percent": 79, "width_percent": 84, "height_percent": 16, "blur_radius": 26, "opacity_percent": 84},
}
POSITION_Y_DEFAULTS = {"landscape": 85, "portrait": 82}

app = FastAPI(title="NarratoAI Subtitle Preview", version="1.0.0")


def _clamp(value, low, high):
    return max(low, min(high, value))


def _config_ui() -> dict:
    try:
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f).get("ui", {})
    except Exception:
        return {}


def _resolve_font_path(font_name: str) -> str:
    if not font_name:
        return ""
    font_name = str(font_name)
    candidates = []
    if os.path.isabs(font_name):
        candidates.append(font_name)
    candidates.extend(
        [
            os.path.join(FONT_DIR, font_name),
            os.path.join(FONT_DIR, os.path.basename(font_name)),
        ]
    )
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def _load_font(font_name: str, font_size: int) -> ImageFont.FreeTypeFont:
    font_path = _resolve_font_path(font_name)
    font_size = max(12, int(round(font_size or 60)))
    if font_path:
        try:
            return ImageFont.truetype(font_path, font_size)
        except OSError:
            pass
    return ImageFont.load_default(size=font_size)


def _resolve_color(color, fallback):
    try:
        value = ImageColor.getrgb(str(color or fallback).strip())
    except ValueError:
        value = ImageColor.getrgb(fallback)
    if len(value) == 4:
        return value
    return (*value, 255)


def _mask_params(
    orientation: str,
    x_percent: float | None,
    y_percent: float | None,
    width_percent: float | None,
    height_percent: float | None,
    blur_radius: float | None,
    opacity_percent: float | None,
) -> dict:
    defaults = MASK_DEFAULTS.get(orientation, MASK_DEFAULTS["portrait"])
    return {
        "x_percent": defaults["x_percent"] if x_percent is None else x_percent,
        "y_percent": defaults["y_percent"] if y_percent is None else y_percent,
        "width_percent": defaults["width_percent"] if width_percent is None else width_percent,
        "height_percent": defaults["height_percent"] if height_percent is None else height_percent,
        "blur_radius": defaults["blur_radius"] if blur_radius is None else blur_radius,
        "opacity_percent": defaults["opacity_percent"] if opacity_percent is None else opacity_percent,
    }


def _resolve_mask_region(video_width: int, video_height: int, region: dict) -> dict:
    """Same math as app.services.generate_video._resolve_subtitle_mask_region."""
    orientation = "portrait" if video_height > video_width else "landscape"

    x = _clamp(round(video_width * region["x_percent"] / 100), 0, max(0, video_width - 2))
    y = _clamp(round(video_height * region["y_percent"] / 100), 0, max(0, video_height - 2))
    width = _clamp(round(video_width * region["width_percent"] / 100), 2, max(2, video_width - x))
    height = _clamp(round(video_height * region["height_percent"] / 100), 2, max(2, video_height - y))

    base_height = 1920 if orientation == "portrait" else 1080
    blur_radius = (
        0
        if region["blur_radius"] == 0
        else max(1, round(region["blur_radius"] * (video_height / base_height)))
    )
    corner_radius = max(8, round(min(height * 0.32, blur_radius * 1.4 or height * 0.24)))
    feather = max(6, round(max(blur_radius * 0.85, 8)))
    padding = blur_radius
    padded_x = max(0, x - padding)
    padded_y = max(0, y - padding)
    padded_width = _clamp(width + padding * 2, 2, video_width - padded_x)
    padded_height = _clamp(height + padding * 2, 2, video_height - padded_y)

    return {
        "orientation": orientation,
        "x": int(x),
        "y": int(y),
        "width": int(width),
        "height": int(height),
        "blur_radius": int(blur_radius),
        "opacity": _clamp(region["opacity_percent"] / 100, 0, 1),
        "corner_radius": int(corner_radius),
        "feather": int(feather),
        "padded_x": int(padded_x),
        "padded_y": int(padded_y),
        "padded_width": int(padded_width),
        "padded_height": int(padded_height),
    }


def _extract_frame(video_path: str, timestamp: float):
    import numpy as np
    from moviepy import VideoFileClip

    clip = VideoFileClip(video_path)
    try:
        duration = float(clip.duration or 0)
        safe_time = _clamp(float(timestamp or 0), 0.0, max(duration, 0.0))
        frame = np.asarray(clip.get_frame(safe_time))
        if frame.dtype != np.uint8:
            frame = np.clip(frame, 0, 255).astype(np.uint8)
        return frame
    finally:
        clip.close()


def _draw_mask_overlay(frame, region: dict, subtitle_y_percent: float | None):
    """Mirror of the web UI subtitle mask preview drawing."""
    image = Image.fromarray(frame).convert("RGBA")
    orientation = region["orientation"]
    if subtitle_y_percent is None:
        subtitle_y_percent = POSITION_Y_DEFAULTS.get(orientation, 82)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    rect = (
        region["x"],
        region["y"],
        region["x"] + region["width"],
        region["y"] + region["height"],
    )
    draw.rounded_rectangle(
        rect,
        radius=region["corner_radius"],
        fill=(0, 0, 0, 96),
        outline=(255, 75, 85, 235),
        width=max(2, round(min(image.width, image.height) * 0.004)),
    )
    subtitle_y = round((image.height - 1) * _clamp(float(subtitle_y_percent), 0, 100) / 100)
    line_width = max(2, round(min(image.width, image.height) * 0.004))
    draw.line((0, subtitle_y, image.width, subtitle_y), fill=(59, 130, 246, 220), width=line_width)

    image.alpha_composite(overlay)
    return image.convert("RGB")


def _background(orientation: str) -> Image.Image:
    image_path = os.path.join(SAFE_AREA_DIR, SAFE_AREA_IMAGES.get(orientation, SAFE_AREA_IMAGES["portrait"]))
    if os.path.exists(image_path):
        return Image.open(image_path).convert("RGBA")
    width, height = FALLBACK_SIZES.get(orientation, FALLBACK_SIZES["portrait"])
    return Image.new("RGBA", (width, height), (19, 24, 35, 255))


def _png_response(image: Image.Image) -> Response:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    return Response(content=buffer.getvalue(), media_type="image/png")


@app.get("/health")
def health():
    return {"status": "ok", "service": "subtitle-preview", "project_root": PROJECT_ROOT}


@app.get("/api/fonts")
def fonts():
    items = []
    if os.path.isdir(FONT_DIR):
        for name in sorted(os.listdir(FONT_DIR)):
            if name.lower().endswith((".ttf", ".otf", ".ttc")):
                items.append(name)
    return {"fonts": items, "font_dir": FONT_DIR}


@app.get("/api/defaults")
def defaults():
    ui = _config_ui()
    return {
        "mask": MASK_DEFAULTS,
        "position_y_percent": POSITION_Y_DEFAULTS,
        "font_name": ui.get("font_name", "DroidSansFallbackFull.ttf"),
        "font_size": ui.get("font_size", 60),
        "text_fore_color": ui.get("text_fore_color", "#FFFFFF"),
        "stroke_color": ui.get("stroke_color", "#000000"),
        "stroke_width": ui.get("stroke_width", 1.5),
    }


@app.post("/api/preview/mask")
async def preview_mask(
    video: UploadFile = File(...),
    timestamp: float = Form(0.0),
    subtitle_y_percent: float = Form(None),
    landscape_x_percent: float = Form(None),
    landscape_y_percent: float = Form(None),
    landscape_width_percent: float = Form(None),
    landscape_height_percent: float = Form(None),
    landscape_blur_radius: float = Form(None),
    landscape_opacity_percent: float = Form(None),
    portrait_x_percent: float = Form(None),
    portrait_y_percent: float = Form(None),
    portrait_width_percent: float = Form(None),
    portrait_height_percent: float = Form(None),
    portrait_blur_radius: float = Form(None),
    portrait_opacity_percent: float = Form(None),
):
    suffix = os.path.splitext(video.filename or "upload.mp4")[1] or ".mp4"
    tmp_path = f"/tmp/subtitle_preview_{os.getpid()}_{int(timestamp * 1000)}{suffix}"
    with open(tmp_path, "wb") as f:
        f.write(await video.read())
    try:
        frame = _extract_frame(tmp_path, timestamp)
        height, width = frame.shape[0], frame.shape[1]
        orientation = "portrait" if height > width else "landscape"
        params = _mask_params(
            orientation,
            landscape_x_percent if orientation == "landscape" else portrait_x_percent,
            landscape_y_percent if orientation == "landscape" else portrait_y_percent,
            landscape_width_percent if orientation == "landscape" else portrait_width_percent,
            landscape_height_percent if orientation == "landscape" else portrait_height_percent,
            landscape_blur_radius if orientation == "landscape" else portrait_blur_radius,
            landscape_opacity_percent if orientation == "landscape" else portrait_opacity_percent,
        )
        region = _resolve_mask_region(width, height, params)
        image = _draw_mask_overlay(frame, region, subtitle_y_percent)
        image.thumbnail((1280, 1280))
        return _png_response(image)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@app.post("/api/preview/style")
async def preview_style(
    text: str = Form("这是一句字幕预览示例"),
    font_name: str = Form("DroidSansFallbackFull.ttf"),
    font_size: int = Form(60),
    text_color: str = Form("#FFFFFF"),
    stroke_color: str = Form("#000000"),
    stroke_width: float = Form(1.5),
    orientation: str = Form("portrait"),
    subtitle_y_percent: float = Form(None),
    subtitle_position: str = Form("bottom"),
    background: UploadFile = File(None),
):
    if background is not None:
        image = Image.open(io.BytesIO(await background.read())).convert("RGBA")
    else:
        image = _background(orientation)

    draw = ImageDraw.Draw(image)
    font = _load_font(font_name, font_size)
    stroke_width_px = max(0, int(round(stroke_width or 0)))
    bbox = draw.textbbox((0, 0), text, font=font, stroke_width=stroke_width_px)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    if subtitle_position == "top":
        y = max(12, round(image.height * 0.05))
    elif subtitle_position == "center":
        y = max(0, round((image.height - text_height) / 2))
    else:
        if subtitle_y_percent is None:
            subtitle_y_percent = POSITION_Y_DEFAULTS.get(orientation, 82)
        y = max(0, round((image.height - text_height) * _clamp(float(subtitle_y_percent), 0, 100) / 100))

    x = max(20, round((image.width - text_width) / 2))
    margin = max(10, stroke_width_px * 2 + 6)
    y = max(margin, min(y, image.height - text_height - margin))

    draw.text(
        (x - bbox[0], y - bbox[1]),
        text,
        font=font,
        fill=_resolve_color(text_color, "#FFFFFF"),
        stroke_width=stroke_width_px,
        stroke_fill=_resolve_color(stroke_color, "#000000"),
    )
    return _png_response(image)


@app.get("/")
def index():
    return {
        "service": "NarratoAI Subtitle Preview",
        "endpoints": ["/health", "/api/fonts", "/api/defaults", "/api/preview/mask", "/api/preview/style"],
    }
