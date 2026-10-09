import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from ...model_compat import BaseModel, Field

from ...core.request_context import RequestContext, current_request_context
from ..jobs import runner
from ..library import resegmentation_store as store
from . import resegmentation


router = APIRouter(prefix="/api", tags=["resegmentation"])


class ResegmentRequest(BaseModel):
    scope: Literal["book", "chapter"] = "chapter"
    chapter_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    expected_text_hash: str | None = Field(default=None, min_length=64, max_length=64)
    request_id: str | None = Field(default=None, min_length=1, max_length=120)


class RestoreRequest(BaseModel):
    expected_revision: int = Field(ge=1)


@router.get('/books/{book_id}/resegment-preview')
async def preview(book_id: str,scope: Literal['book','chapter']='book',chapter_id: str | None=None,context: RequestContext=Depends(current_request_context)):
    await asyncio.to_thread(resegmentation.prepare_request,context.user_id,book_id,scope,chapter_id)
    return await asyncio.to_thread(store.preview,context.user_id,book_id,chapter_id)


@router.post("/books/{book_id}/resegment")
async def submit_resegmentation(book_id: str, request: ResegmentRequest,
                                context: RequestContext = Depends(current_request_context)):
    frozen = await asyncio.to_thread(resegmentation.prepare_request, context.user_id, book_id, request.scope,
        request.chapter_id, request.expected_revision, request.expected_text_hash)
    # A per-book lock prevents overlapping whole-book / single-chapter jobs
    # from racing each other; unrelated books remain parallelizable.
    job = await runner.submit(context.user_id, resegmentation.KIND, frozen,
                              lock_key=f"resegment:{book_id}", request_id=request.request_id)
    return {"job_id": job["id"], "job": job, "chapter_count": len(frozen["targets"]),
            "translation_policy": "旧译文与词义按版本归档，新切分需重新释义；书签、阅读锚点、卡片和复习历史保留。"}


@router.get("/chapters/{chapter_id}/segmentation-generations")
async def generations(chapter_id: str, context: RequestContext = Depends(current_request_context)):
    try:
        return {"generations": await asyncio.to_thread(store.list_generations, context.user_id, chapter_id)}
    except store.ResegmentationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/segmentation-generations/{generation_id}/restore")
async def restore(generation_id: str, request: RestoreRequest,
                  context: RequestContext = Depends(current_request_context)):
    try:
        result=await asyncio.to_thread(store.restore_generation, context.user_id, generation_id, request.expected_revision)
        await asyncio.to_thread(resegmentation.replay_events,context.user_id)
        return result
    except store.ResegmentationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except store.ResegmentationConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/chapters/{chapter_id}/historical-sources/{sentence_id}")
async def historical_source(chapter_id: str, sentence_id: str,
                             context: RequestContext = Depends(current_request_context)):
    try:
        return await asyncio.to_thread(store.resolve_source, context.user_id, chapter_id, sentence_id)
    except store.ResegmentationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
