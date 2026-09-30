import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException

from ...ai import AiRateLimitError
from ...core.request_context import RequestContext, current_request_context
from ...models import (
    AnalyzeRequest,
    AnalyzeResponse,
    ExplainBatchRequest,
    ExplainSentenceRequest,
    ImportedChapter,
    SegmentChapterRequest,
)
from ...settings_store import resolve_settings
from . import service


router = APIRouter(prefix="/api", tags=["analysis"])


@router.post("/preprocess", response_model=AnalyzeResponse)
async def preprocess(request: AnalyzeRequest) -> AnalyzeResponse:
    sentences, tokens = await service._local_analysis_async(request.chapter_id, request.text)
    if not sentences:
        raise HTTPException(422, "章节中没有可处理的日文正文")
    return AnalyzeResponse(
        sentences=sentences,
        tokens=tokens,
        annotations=[],
        context_senses=[],
        lexemes=[],
        warnings=[],
    )


@router.post("/chapters/segment", response_model=AnalyzeResponse)
async def segment_chapter(
    request: SegmentChapterRequest,
    x_api_key: str | None = Header(default=None),
    context: RequestContext = Depends(current_request_context),
) -> AnalyzeResponse:
    api_key, base_url, model = resolve_settings(
        context.user_id,
        x_api_key,
        str(request.settings.base_url),
        request.settings.model,
    )
    chapter = ImportedChapter(
        id=request.chapter_id,
        title="当前章节",
        order=0,
        text=request.text,
        blocks=request.blocks,
    )
    try:
        chapter, warning = await service._segment_chapter(
            context.user_id,
            chapter,
            api_key,
            base_url,
            model,
            asyncio.Semaphore(3),
        )
    except AiRateLimitError as exc:
        retry_after = max(1, round(exc.retry_after or 1))
        raise HTTPException(429, str(exc), headers={"Retry-After": str(retry_after)}) from exc
    return AnalyzeResponse(
        sentences=chapter.sentences,
        tokens=chapter.tokens,
        annotations=[],
        context_senses=[],
        lexemes=[],
        warnings=[warning] if warning else [],
    )


@router.post("/sentences/explain-batch", response_model=AnalyzeResponse)
async def explain_sentence_batch(
    request: ExplainBatchRequest,
    x_api_key: str | None = Header(default=None),
    context: RequestContext = Depends(current_request_context),
) -> AnalyzeResponse:
    try:
        return await service._explain_batch(context.user_id, request, x_api_key)
    except AiRateLimitError as exc:
        retry_after = max(1, round(exc.retry_after or 1))
        raise HTTPException(429, str(exc), headers={"Retry-After": str(retry_after)}) from exc


@router.post("/sentences/explain", response_model=AnalyzeResponse)
async def explain_sentence(
    request: ExplainSentenceRequest,
    x_api_key: str | None = Header(default=None),
    context: RequestContext = Depends(current_request_context),
) -> AnalyzeResponse:
    batch = ExplainBatchRequest(
        items=[{"sentence": request.sentence, "tokens": request.tokens}],
        annotation_mode=request.annotation_mode,
        detail_mode=request.detail_mode,
        context_before=request.context_before,
        settings=request.settings,
    )
    try:
        return await service._explain_batch(context.user_id, batch, x_api_key)
    except AiRateLimitError as exc:
        retry_after = max(1, round(exc.retry_after or 1))
        raise HTTPException(429, str(exc), headers={"Retry-After": str(retry_after)}) from exc
