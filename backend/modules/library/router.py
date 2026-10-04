import asyncio
import re
import shutil
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from ...android_compat import BaseModel, Field

from ...core.request_context import RequestContext, current_request_context
from ...epub import BOOK_DATA_DIR
from ...models import ChapterSnapshot, LibraryIndex, LibraryPatch, LibrarySnapshot, StudyDataSnapshot
from .repository import (
    apply_library_patch,
    bulk_update_lexemes,
    delete_book,
    load_bookmarks,
    load_chapter_details,
    load_chapter_view,
    load_library,
    load_library_index,
    load_study_data,
    page_lexemes,
    load_translation_queue,
    owns_book,
    owns_resource_key,
    save_library,
)
from ..sync.repository import push_changes


router = APIRouter(prefix="/api", tags=["library"])


class LexemeBulkInput(BaseModel):
    keys: list[str] = Field(min_items=1, max_items=500)
    operation: str
    value: Any = None


@router.get("/assets/{asset_path:path}")
async def read_book_asset(
    asset_path: str,
    context: RequestContext = Depends(current_request_context),
) -> FileResponse:
    parts = tuple(part for part in asset_path.split("/") if part)
    if not parts or "\\" in asset_path or any(part in {".", ".."} for part in parts):
        raise HTTPException(404, "资源不存在")
    allowed = False
    if parts[0] == "custom-covers":
        if len(parts) == 3 and parts[1] == context.user_id:
            allowed = owns_book(context.user_id, parts[2].rsplit(".", 1)[0])
        elif len(parts) == 2:
            allowed = owns_book(context.user_id, parts[1].rsplit(".", 1)[0])
    elif re.fullmatch(r"[0-9a-f]{20}", parts[0]):
        allowed = owns_resource_key(context.user_id, parts[0])
    if not allowed:
        raise HTTPException(404, "资源不存在")
    root = BOOK_DATA_DIR.resolve()
    target = root.joinpath(*parts).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "资源不存在")
    return FileResponse(target)


@router.get("/library", response_model=LibrarySnapshot | None)
async def read_library(context: RequestContext = Depends(current_request_context)) -> LibrarySnapshot | None:
    return load_library(context.user_id)


@router.put("/library", response_model=LibrarySnapshot)
async def update_library(
    snapshot: LibrarySnapshot,
    context: RequestContext = Depends(current_request_context),
) -> LibrarySnapshot:
    return save_library(context.user_id, snapshot)


@router.get("/library/index", response_model=LibraryIndex)
async def read_library_index(context: RequestContext = Depends(current_request_context)) -> LibraryIndex:
    return load_library_index(context.user_id)


@router.get("/library/chapters/{chapter_id}", response_model=ChapterSnapshot)
async def read_library_chapter(
    chapter_id: str,
    context: RequestContext = Depends(current_request_context),
) -> ChapterSnapshot:
    return load_chapter_view(context.user_id, chapter_id)


@router.get("/library/chapters/{chapter_id}/details")
async def read_library_chapter_details(
    chapter_id: str,
    offset: int = 0,
    limit: int = 120,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    if limit < 1 or limit > 240:
        raise HTTPException(422, "limit 必须在 1 到 240 之间")
    return load_chapter_details(context.user_id, chapter_id, offset, limit)


@router.get("/library/books/{book_id}/translation-queue")
async def read_translation_queue(
    book_id: str,
    chapter_id: str | None = None,
    cursor: str = "",
    limit: int = 160,
    detail_mode: str = "meaning",
    include_tokens: bool = False,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    if limit < 1 or limit > 240:
        raise HTTPException(422, "limit 必须在 1 到 240 之间")
    if detail_mode not in {"meaning", "full"}:
        raise HTTPException(422, "detail_mode 必须是 meaning 或 full")
    try:
        return load_translation_queue(
            context.user_id, book_id, chapter_id, cursor, limit, detail_mode, include_tokens,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/library/study-data", response_model=StudyDataSnapshot)
async def read_library_study_data(
    context: RequestContext = Depends(current_request_context),
) -> StudyDataSnapshot:
    return load_study_data(context.user_id)


@router.get("/library/lexemes")
async def read_lexeme_page(
    q: str = "", kana: str = "", source: str = "", part_of_speech: str = "",
    group: str = "", corrected: bool | None = None,
    limit: int = Query(80, ge=20, le=200), offset: int = Query(0, ge=0),
    context: RequestContext = Depends(current_request_context),
) -> dict:
    return await asyncio.to_thread(
        page_lexemes, context.user_id, q=q, kana=kana, source=source,
        part_of_speech=part_of_speech, group=group, corrected=corrected,
        limit=limit, offset=offset,
    )


@router.patch("/library/lexemes/bulk")
async def patch_lexemes_bulk(
    payload: LexemeBulkInput,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    try:
        rows = await asyncio.to_thread(
            bulk_update_lexemes, context.user_id, payload.keys, payload.operation, payload.value,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    now = time.time()
    if rows:
        await asyncio.to_thread(push_changes, context.user_id, context.device_id, [{
            "change_id": str(uuid.uuid4()), "entity_type": "lexeme", "entity_id": str(row["key"]),
            "payload": row,
            "updated_at": float(row.get("updatedAt") or now * 1000) / 1000,
        } for row in rows])
    return {"updated": len(rows), "items": rows}


@router.get("/library/bookmarks")
async def read_library_bookmarks(
    book_id: str,
    context: RequestContext = Depends(current_request_context),
) -> list[dict]:
    return load_bookmarks(context.user_id, book_id)


@router.patch("/library")
async def patch_library(
    patch: LibraryPatch,
    context: RequestContext = Depends(current_request_context),
) -> dict[str, int]:
    try:
        result=await asyncio.to_thread(apply_library_patch,context.user_id,patch)
        mutations=[]
        now=time.time()
        for row in patch.upserts.get("bookmarks",[]):
            updated=float(row.get("createdAt") or now); updated=updated/1000 if updated>10_000_000_000 else updated
            mutations.append({"change_id":str(uuid.uuid4()),"entity_type":"bookmark","entity_id":str(row["id"]),"payload":row,"updated_at":updated})
        for row in patch.upserts.get("books",[]):
            updated=float(row.get("updatedAt") or now); updated=updated/1000 if updated>10_000_000_000 else updated
            mutations.append({"change_id":str(uuid.uuid4()),"entity_type":"reading_progress","entity_id":str(row["id"]),"payload":{"book_id":row["id"],"chapter_id":row.get("currentChapterId"),"sentence_id":row.get("currentSentenceId"),"last_opened_at":row.get("lastOpenedAt")},"updated_at":updated})
        for row in patch.upserts.get("lexemes", []):
            updated=float(row.get("updatedAt") or now * 1000); updated=updated/1000 if updated>10_000_000_000 else updated
            mutations.append({"change_id":str(uuid.uuid4()),"entity_type":"lexeme","entity_id":str(row["key"]),"payload":row,"updated_at":updated})
        for key in patch.deletes.get("bookmarks",[]):
            mutations.append({"change_id":str(uuid.uuid4()),"entity_type":"bookmark","entity_id":str(key),"payload":{},"updated_at":now,"deleted_at":now})
        if mutations: await asyncio.to_thread(push_changes,context.user_id,context.device_id,mutations)
        return result
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def delete_book_and_resources(user_id: str, book_id: str) -> dict[str, Any]:
    result = delete_book(user_id, book_id)
    resource_keys = result.pop("resource_keys", [])
    root = BOOK_DATA_DIR.resolve()
    deleted_resources = 0
    cleanup_errors: list[str] = []
    for resource_key in resource_keys:
        if not re.fullmatch(r"[0-9a-f]{20}", str(resource_key)):
            continue
        target = (root / str(resource_key)).resolve()
        if target.parent != root or not target.is_dir():
            continue
        try:
            shutil.rmtree(target)
            deleted_resources += 1
        except OSError as exc:
            cleanup_errors.append(f"{resource_key}: {exc}")

    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        cover_dirs = [root / "custom-covers" / user_id, root / "custom-covers"]
        for suffix in (".jpg", ".png", ".webp", ".gif"):
            for cover_dir in cover_dirs:
                try:
                    (cover_dir / f"{book_id}{suffix}").unlink(missing_ok=True)
                except OSError as exc:
                    cleanup_errors.append(f"custom-cover{suffix}: {exc}")

    result["resources_deleted"] = deleted_resources
    if cleanup_errors:
        result["resource_cleanup_errors"] = cleanup_errors
    return result


@router.delete("/library/books/{book_id}")
async def delete_library_book_data(
    book_id: str,
    context: RequestContext = Depends(current_request_context),
) -> dict[str, Any]:
    return await asyncio.to_thread(delete_book_and_resources, context.user_id, book_id)


@router.post("/books/{book_id}/cover")
async def upload_book_cover(
    book_id: str,
    file: UploadFile = File(...),
    context: RequestContext = Depends(current_request_context),
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        raise HTTPException(400, "书籍 ID 不合法")
    if not owns_book(context.user_id, book_id):
        raise HTTPException(404, "书籍不存在")
    suffixes = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    suffix = suffixes.get((file.content_type or "").lower())
    if not suffix:
        raise HTTPException(400, "封面只支持 JPG、PNG、WebP 或 GIF 图片")
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "封面图片为空")
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(413, "封面图片不能超过 10 MB")
    cover_dir = BOOK_DATA_DIR / "custom-covers" / context.user_id
    cover_dir.mkdir(parents=True, exist_ok=True)
    for old_suffix in suffixes.values():
        (cover_dir / f"{book_id}{old_suffix}").unlink(missing_ok=True)
    (cover_dir / f"{book_id}{suffix}").write_bytes(payload)
    return {"url": f"/api/assets/custom-covers/{context.user_id}/{book_id}{suffix}?v={time.time_ns()}"}


@router.delete("/books/{book_id}/cover")
async def delete_book_cover(
    book_id: str,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        raise HTTPException(400, "书籍 ID 不合法")
    if not owns_book(context.user_id, book_id):
        raise HTTPException(404, "书籍不存在")
    cover_dir = BOOK_DATA_DIR / "custom-covers" / context.user_id
    for suffix in (".jpg", ".png", ".webp", ".gif"):
        (cover_dir / f"{book_id}{suffix}").unlink(missing_ok=True)
    return {"deleted": True}

