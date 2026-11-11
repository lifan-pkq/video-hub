"""成品套装导出 API。"""

from fastapi import APIRouter
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.problems import problem
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
        raise problem(400, "export", str(exc), "确认这条视频还在素材库里。") from exc


@router.post("/{export_id}/cancel")
def cancel_video_export(export_id: str):
    try:
        return exporter.cancel_export(export_id)
    except exporter.ExportError as exc:
        raise problem(404, "not_found", str(exc), "重新生成一套。") from exc


@router.get("/{export_id}")
def get_video_export(export_id: str):
    try:
        return exporter.get_export(export_id)
    except exporter.ExportError as exc:
        raise problem(404, "not_found", str(exc), "重新生成一套。") from exc


@router.put("/{export_id}/select")
def select_video_export(export_id: str, body: SelectExportBody):
    try:
        return exporter.select_export(export_id, body.cover, body.title)
    except exporter.ExportError as exc:
        raise problem(400, "export", str(exc), "等导出完成后再选择封面或标题。") from exc


@router.get("/{export_id}/download")
def download_video_export(export_id: str):
    try:
        path = exporter.export_file(export_id, "video-package.zip")
        return FileResponse(path, media_type="application/zip", filename="video-package.zip")
    except exporter.ExportError as exc:
        raise problem(404, "not_found", str(exc), "等导出完成后再下载。") from exc


@router.get("/{export_id}/files/{name}")
def get_export_file(export_id: str, name: str):
    try:
        return FileResponse(exporter.export_file(export_id, name))
    except exporter.ExportError as exc:
        raise problem(404, "not_found", str(exc), "确认文件名来自本次导出清单。") from exc
