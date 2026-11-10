"""创作档案 API。"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.services import profiles

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


@router.get("/bundle")
def get_profile_bundle(character_id: str = "", scene_id: str = "", style_id: str = ""):
    try:
        return profiles.resolve_bundle(character_id, scene_id, style_id)
    except profiles.ProfileError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/references")
async def upload_profile_reference(file: UploadFile = File(...)):
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(400, "只支持图片参考")
    try:
        return profiles.save_reference(await file.read(), file.filename or "reference.png")
    except profiles.ProfileError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/references/{name}")
def get_profile_reference(name: str):
    try:
        return FileResponse(profiles.reference_path(name))
    except profiles.ProfileError as exc:
        raise HTTPException(404, str(exc)) from exc




@router.post("/seed")
def seed_builtin_profiles(force: bool = False):
    """安装内置人物/场景/风格种子；默认幂等，force=true 时按固定 id 覆盖写入。"""
    try:
        return profiles.seed_builtins(force=force)
    except profiles.ProfileError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{kind}")
def list_creative_profiles(kind: str):
    try:
        return profiles.list_profiles(kind)
    except profiles.ProfileError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{kind}")
def create_creative_profile(kind: str, payload: dict):
    try:
        return profiles.save_profile(kind, payload)
    except profiles.ProfileError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{kind}/{profile_id}")
def get_creative_profile(kind: str, profile_id: str):
    try:
        return profiles.get_profile(kind, profile_id)
    except profiles.ProfileError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/{kind}/{profile_id}")
def update_creative_profile(kind: str, profile_id: str, payload: dict):
    try:
        return profiles.save_profile(kind, payload, profile_id)
    except profiles.ProfileError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{kind}/{profile_id}")
def delete_creative_profile(kind: str, profile_id: str):
    try:
        return profiles.delete_profile(kind, profile_id)
    except profiles.ProfileError as exc:
        status = 400 if "不可删除" in str(exc) else 404
        raise HTTPException(status, str(exc)) from exc
