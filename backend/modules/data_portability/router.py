import asyncio
import json
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from ...core.request_context import RequestContext, current_request_context
from .service import create_backup, create_book_transfer, import_book_transfer, restore_backup, save_upload


router = APIRouter(prefix="/api/data", tags=["data-portability"])


@router.get("/backup")
async def download_backup(context: RequestContext = Depends(current_request_context)) -> FileResponse:
    path = await asyncio.to_thread(create_backup, context.user_id)
    return FileResponse(
        path, media_type="application/zip", filename=f"冰读备份-{time.strftime('%Y%m%d-%H%M%S')}.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post("/restore")
async def upload_backup(
    file: UploadFile = File(...),
    context: RequestContext = Depends(current_request_context),
) -> dict:
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "请选择冰读导出的 ZIP 备份")
    temporary: Path | None = None
    try:
        temporary = await asyncio.to_thread(save_upload, file.file, file.filename or "backup.zip")
        return await asyncio.to_thread(restore_backup, context.user_id, temporary)
    except (ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


@router.get("/book-transfer")
async def download_book_transfer(context: RequestContext = Depends(current_request_context)) -> FileResponse:
    path = await asyncio.to_thread(create_book_transfer, context.user_id)
    return FileResponse(
        path, media_type="application/zip", filename=f"冰读数据迁移包-{time.strftime('%Y%m%d-%H%M%S')}.zip",
        background=BackgroundTask(path.unlink, missing_ok=True),
    )


@router.post("/book-transfer/import")
async def upload_book_transfer(
    file: UploadFile = File(...),
    context: RequestContext = Depends(current_request_context),
) -> dict:
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "请选择冰读导出的 ZIP 书籍迁移包")
    temporary: Path | None = None
    try:
        temporary = await asyncio.to_thread(save_upload, file.file, file.filename or "book-transfer.zip")
        return await asyncio.to_thread(import_book_transfer, context.user_id, temporary)
    except (ValueError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
