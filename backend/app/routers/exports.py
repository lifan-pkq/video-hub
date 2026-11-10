"""成品套装导出 API。"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.services import exporter

router = APIRouter(prefix="/api/exports", tags=["exports"])


class CreateExportBody(BaseModel):
    video_id: int


class SelectExportBody(BaseModel):
    cover: str = ""
    title: str = ""


@router.post("")
def create_video_export(body: CreateExportBody):
    try:
        return exporter.create_export(body.video_id)
    except exporter.ExportError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{export_id}")
def get_video_export(export_id: str):
    try:
        return exporter.get_export(export_id)
    except exporter.ExportError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/{export_id}/select")
def select_video_export(export_id: str, body: SelectExportBody):
    try:
        return exporter.select_export(export_id, body.cover, body.title)
    except exporter.ExportError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{export_id}/download")
def download_video_export(export_id: str):
    try:
        path = exporter.export_file(export_id, "video-package.zip")
        return FileResponse(path, media_type="application/zip", filename="video-package.zip")
    except exporter.ExportError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/{export_id}/files/{name}")
def get_export_file(export_id: str, name: str):
    try:
        return FileResponse(exporter.export_file(export_id, name))
    except exporter.ExportError as exc:
        raise HTTPException(404, str(exc)) from exc
