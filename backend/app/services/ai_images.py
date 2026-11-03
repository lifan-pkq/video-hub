"""AI 图片素材库：把生成图保存为可复用参考图。"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Any

from app.config import DATA_DIR, PUBLIC_BASE_URL
from app.db import add_ai_image, delete_ai_image, get_ai_image, list_ai_images
from app.services import ai_media

AI_IMAGES_DIR = DATA_DIR / "ai_images"


class AIImageError(RuntimeError):
    pass


def _dimensions(path: Path) -> tuple[int | None, int | None]:
    try:
        from PIL import Image
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except Exception:  # noqa: BLE001
        return None, None


def _row(image: dict) -> dict:
    filename = str(image["filename"])
    return {
        **image,
        "url": f"/api/ai/images/{image['id']}/file",
        "public_url": f"{PUBLIC_BASE_URL}/api/ai/images/{image['id']}/file",
        "download_url": f"/api/ai/images/{image['id']}/download",
        "filename": filename,
    }


def list_images(limit: int = 100) -> list[dict]:
    return [_row(item) for item in list_ai_images(limit)]


def _insert(path: Path, title: str = "", prompt: str = "") -> dict:
    width, height = _dimensions(path)
    row = add_ai_image(
        filename=path.name,
        title=(title or prompt or "AI 图片")[:80],
        prompt=(prompt or "")[:1000],
        size=path.stat().st_size,
        width=width,
        height=height,
    )
    return _row(row)


def save_generated(name: str, title: str = "", prompt: str = "") -> dict:
    safe = Path(name).name
    source = ai_media.GENERATED_IMAGE_DIR / safe
    if not source.is_file():
        raise AIImageError("生成图片不存在")
    AI_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    target = AI_IMAGES_DIR / safe
    for item in list_ai_images(300):
        if item.get("filename") == safe:
            return _row(item)
    shutil.copy2(source, target)
    return _insert(target, title, prompt)


def upload(data: bytes, filename: str, title: str = "") -> dict:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"
    AI_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    name = f"lib_{uuid.uuid4().hex}{suffix}"
    target = AI_IMAGES_DIR / name
    target.write_bytes(data)
    return _insert(target, title)


def path_for(image_id: int) -> Path:
    row = get_ai_image(image_id)
    if not row:
        raise AIImageError("图片不存在")
    path = AI_IMAGES_DIR / str(row["filename"])
    if not path.is_file():
        raise AIImageError("图片文件丢失")
    return path


def remove(image_id: int) -> None:
    row = get_ai_image(image_id)
    if not row:
        return
    try:
        (AI_IMAGES_DIR / str(row["filename"])).unlink()
    except OSError:
        pass
    delete_ai_image(image_id)
