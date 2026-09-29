import asyncio
from contextlib import asynccontextmanager
import re
import shutil
import time
import zipfile
from typing import Any
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .ai import AiRateLimitError, PROMPT_VERSION, close_http_client, explain_sentences as explain_sentences_with_ai
from .ai import review_sentence_boundaries
from .ai_store import close_store as close_ai_store
from .ai_store import get_cached_response, make_cache_key, set_cached_response, usage_summary
from .dictionary_store import import_yomitan, lookup
from .epub import BOOK_DATA_DIR, parse_epub
from .library_store import (
    apply_library_patch,
    delete_book as delete_library_book,
    find_personal_lexeme,
    load_bookmarks,
    load_chapter_details,
    load_chapter_view,
    load_library,
    load_library_index,
    load_translation_queue,
    load_study_data,
    initialize_store as initialize_library_store,
    save_library,
)
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnnotationOut,
    ContentBlock,
    ContextSenseOut,
    ExplainBatchRequest,
    ExplainSentenceRequest,
    ImportedBook,
    ImportedChapter,
    LexemeOut,
    LibraryIndex,
    LibraryPatch,
    LibrarySnapshot,
    LocalAiSettingsInput,
    LocalAiSettingsStatus,
    SegmentChapterRequest,
    SentenceOut,
    TextImportRequest,
    TokenOut,
    ChapterSnapshot,
    StudyDataSnapshot,
    VoiceJobStatus,
    VoiceSettingsInput,
    VoiceSettingsStatus,
    VoiceSynthesisRequest,
)
from .nlp import CLOSERS, SENTENCE_END, SentenceSpan, lexeme_key, split_sentences, stable_id, tokenize_sentence
from .paths import DIST_DIR
from .settings_store import get_settings_status, resolve_settings, save_settings
from .settings_store import get_pricing
from .voice_service import (
    CACHE_DIR,
    cancel_voice_job,
    get_voice_job,
    get_voice_settings,
    install_template,
    save_voice_settings,
    start_voice_job,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        await asyncio.to_thread(initialize_library_store)
        yield
    finally:
        try:
            await close_http_client()
        finally:
            close_ai_store()


app = FastAPI(title="冰读本地 API", version="0.2.0", lifespan=lifespan)
app.mount("/api/assets", StaticFiles(directory=BOOK_DATA_DIR, check_dir=False), name="book-assets")
app.mount("/api/voice/audio", StaticFiles(directory=CACHE_DIR, check_dir=False), name="voice-audio")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_shell_cache(request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/index.html"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


def _local_analysis(chapter_id: str, text: str, spans=None) -> tuple[list[SentenceOut], list[TokenOut]]:
    sentences: list[SentenceOut] = []
    tokens: list[TokenOut] = []
    for index, span in enumerate(spans or split_sentences(text)):
        sentence_id = stable_id("sent", f"{chapter_id}:{index}:{span.start}:{span.text}")
        sentences.append(SentenceOut(
            id=sentence_id, chapter_id=chapter_id, start=span.start, end=span.end,
            original=span.text, translation_zh="", explanation_status="idle",
        ))
        tokens.extend(TokenOut(**{key: value for key, value in token.items() if key != "lexeme_key"})
                      for token in tokenize_sentence(sentence_id, span.text))
    return sentences, tokens


async def _local_analysis_async(chapter_id: str, text: str, spans=None) -> tuple[list[SentenceOut], list[TokenOut]]:
    """Keep Sudachi and long chapter analysis off the ASGI event loop."""
    return await asyncio.to_thread(_local_analysis, chapter_id, text, spans)


def _chapter_sentence_spans(chapter: ImportedChapter) -> list[SentenceSpan]:
    # Paragraph-to-paragraph newlines are layout hints in many Aozora/Kobo
    # books. Structural content still forms a hard boundary.
    hard_boundaries: set[int] = set()
    for block in chapter.blocks:
        if block.type != "paragraph":
            hard_boundaries.update((block.start, block.end))
    return split_sentences(chapter.text, {value for value in hard_boundaries if 0 < value < len(chapter.text)})


MAX_MERGED_FRAGMENTS = 6
MAX_MERGED_CHARS = 400


def _ends_with_terminal(value: str) -> bool:
    stripped = value.rstrip().rstrip("".join(CLOSERS)).rstrip()
    return bool(stripped and stripped[-1] in SENTENCE_END)


def _merge_reviewed_spans(
    chapter_id: str, text: str, spans: list[SentenceSpan], merge_ids: set[str],
) -> list[SentenceSpan]:
    """Apply reviewed joins with guards against cascading false positives."""
    if not spans:
        return []
    merged: list[SentenceSpan] = []
    index = 0
    while index < len(spans):
        start, end = spans[index].start, spans[index].end
        fragment_count = 1
        while index < len(spans) - 1:
            boundary_id = f"{chapter_id}:{index}"
            proposed_end = spans[index + 1].end
            if boundary_id not in merge_ids:
                break
            if fragment_count >= MAX_MERGED_FRAGMENTS or proposed_end - start > MAX_MERGED_CHARS:
                break
            if _ends_with_terminal(text[start:end]):
                break
            index += 1
            end = proposed_end
            fragment_count += 1
        merged.append(SentenceSpan(start=start, end=end, text=text[start:end].strip()))
        index += 1
    return merged


def _candidate_batches(rows: list[dict], max_items: int = 20, max_chars: int = 4_000) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for row in rows:
        row_size = len(row.get("left", "")) + len(row.get("right", ""))
        if current and (len(current) >= max_items or size + row_size > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(row)
        size += row_size
    if current:
        batches.append(current)
    return batches


async def _segment_chapter(chapter: ImportedChapter, api_key: str, base_url: str, model: str, semaphore: asyncio.Semaphore) -> tuple[ImportedChapter, str | None]:
    spans = await asyncio.to_thread(_chapter_sentence_spans, chapter)
    if not spans:
        chapter.segmentation_source = "empty"
        return chapter, None
    text_ranges = [(block.start, block.end) for block in chapter.blocks if block.text and block.end > block.start]
    candidates: list[dict] = []
    for index, span in enumerate(spans):
        next_span = spans[index + 1] if index + 1 < len(spans) else None
        same_block = any(start <= span.start and next_span and next_span.end <= end for start, end in text_ranges)
        if next_span and span.uncertain_after and same_block:
            candidates.append({
                "id": f"{chapter.id}:{index}",
                "left": span.text[-96:],
                "right": next_span.text[:96],
                "span_index": index,
            })
    if not candidates or not api_key:
        chapter.sentences, chapter.tokens = await _local_analysis_async(chapter.id, chapter.text, spans)
        chapter.segmentation_source = "local-fallback"
        warning = "未配置 API Key，可疑换行已保留为句界" if candidates and not api_key else None
        return chapter, warning
    merge_ids: set[str] = set()
    async def review_batch(batch: list[dict]) -> set[str]:
        async with semaphore:
            return await review_sentence_boundaries(batch, api_key, base_url, model)

    try:
        for result in await asyncio.gather(*(review_batch(batch) for batch in _candidate_batches(candidates))):
            merge_ids.update(result)
    except AiRateLimitError:
        raise
    except Exception as exc:
        chapter.sentences, chapter.tokens = await _local_analysis_async(chapter.id, chapter.text, spans)
        chapter.segmentation_source = "local-fallback"
        return chapter, f"{chapter.title} 的 AI 句界审校失败，已使用本地边界：{exc}"

    merged = _merge_reviewed_spans(chapter.id, chapter.text, spans, merge_ids)
    chapter.sentences, chapter.tokens = await _local_analysis_async(chapter.id, chapter.text, merged)
    chapter.segmentation_source = "ai-reviewed"
    return chapter, None


def _personal_entry(token: TokenOut) -> dict | None:
    exact_key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
    return find_personal_lexeme(exact_key, token.lemma, token.reading, token.surface)


def _entry_for_token(token: TokenOut) -> dict | None:
    return _personal_entry(token) or lookup(token.lemma, token.reading, token.surface)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "app": "bingdu", "version": app.version}


@app.get("/api/settings", response_model=LocalAiSettingsStatus)
async def read_settings() -> LocalAiSettingsStatus:
    return get_settings_status()


@app.put("/api/settings", response_model=LocalAiSettingsStatus)
async def update_settings(settings: LocalAiSettingsInput) -> LocalAiSettingsStatus:
    return save_settings(settings)


@app.get("/api/ai/usage")
async def read_ai_usage() -> dict:
    return usage_summary(get_pricing())


@app.get("/api/voice/settings", response_model=VoiceSettingsStatus)
async def read_voice_settings() -> VoiceSettingsStatus:
    return get_voice_settings()


@app.put("/api/voice/settings", response_model=VoiceSettingsStatus)
async def update_voice_settings(settings: VoiceSettingsInput) -> VoiceSettingsStatus:
    try:
        return save_voice_settings(settings)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/voice/template", response_model=VoiceSettingsStatus)
async def upload_voice_template(file: UploadFile = File(...)) -> VoiceSettingsStatus:
    if not (file.filename or "").lower().endswith(".ymmp"):
        raise HTTPException(400, "请选择 .ymmp 配音模板")
    try:
        return install_template(await file.read())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/voice/jobs", response_model=VoiceJobStatus)
async def create_voice_job(request: VoiceSynthesisRequest) -> VoiceJobStatus:
    try:
        return await start_voice_job(request.text, request.force)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/voice/jobs/{job_id}", response_model=VoiceJobStatus)
async def read_voice_job(job_id: str) -> VoiceJobStatus:
    try:
        return get_voice_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, "配音任务不存在") from exc


@app.delete("/api/voice/jobs/{job_id}", response_model=VoiceJobStatus)
async def delete_voice_job(job_id: str) -> VoiceJobStatus:
    try:
        return await cancel_voice_job(job_id)
    except KeyError as exc:
        raise HTTPException(404, "配音任务不存在") from exc


@app.get("/api/library", response_model=LibrarySnapshot | None)
async def read_library() -> LibrarySnapshot | None:
    return load_library()


@app.put("/api/library", response_model=LibrarySnapshot)
async def update_library(snapshot: LibrarySnapshot) -> LibrarySnapshot:
    return save_library(snapshot)


@app.get("/api/library/index", response_model=LibraryIndex)
async def read_library_index() -> LibraryIndex:
    return load_library_index()


@app.get("/api/library/chapters/{chapter_id}", response_model=ChapterSnapshot)
async def read_library_chapter(chapter_id: str) -> ChapterSnapshot:
    return load_chapter_view(chapter_id)


@app.get("/api/library/chapters/{chapter_id}/details")
async def read_library_chapter_details(chapter_id: str, offset: int = 0, limit: int = 120) -> dict:
    if offset < 0:
        raise HTTPException(422, "offset 不能小于 0")
    if limit < 1 or limit > 240:
        raise HTTPException(422, "limit 必须在 1 到 240 之间")
    return load_chapter_details(chapter_id, offset, limit)


@app.get("/api/library/books/{book_id}/translation-queue")
async def read_translation_queue(
    book_id: str,
    chapter_id: str | None = None,
    cursor: str = "",
    limit: int = 160,
    detail_mode: str = "meaning",
    include_tokens: bool = False,
) -> dict:
    if limit < 1 or limit > 240:
        raise HTTPException(422, "limit 必须在 1 到 240 之间")
    if detail_mode not in {"meaning", "full"}:
        raise HTTPException(422, "detail_mode 必须是 meaning 或 full")
    try:
        return load_translation_queue(
            book_id, chapter_id, cursor, limit, detail_mode, include_tokens,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/library/study-data", response_model=StudyDataSnapshot)
async def read_library_study_data() -> StudyDataSnapshot:
    return load_study_data()


@app.get("/api/library/bookmarks")
async def read_library_bookmarks(book_id: str) -> list[dict]:
    return load_bookmarks(book_id)


@app.patch("/api/library")
async def patch_library(patch: LibraryPatch) -> dict[str, int]:
    try:
        return apply_library_patch(patch)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _delete_book_and_resources(book_id: str) -> dict[str, Any]:
    result = delete_library_book(book_id)
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
        cover_dir = root / "custom-covers"
        for suffix in (".jpg", ".png", ".webp", ".gif"):
            try:
                (cover_dir / f"{book_id}{suffix}").unlink(missing_ok=True)
            except OSError as exc:
                cleanup_errors.append(f"custom-cover{suffix}: {exc}")

    result["resources_deleted"] = deleted_resources
    if cleanup_errors:
        result["resource_cleanup_errors"] = cleanup_errors
    return result


@app.delete("/api/library/books/{book_id}")
async def delete_library_book_data(book_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_delete_book_and_resources, book_id)


@app.post("/api/import/text", response_model=ImportedBook)
async def import_text(request: TextImportRequest) -> ImportedBook:
    blocks: list[ContentBlock] = []
    cursor = 0
    for index, line in enumerate(request.text.splitlines()):
        value = line.strip()
        if not value:
            cursor += len(line) + 1
            continue
        start = request.text.find(value, cursor)
        end = start + len(value)
        blocks.append(ContentBlock(id=stable_id("block", f"text:{index}:{value[:80]}"), type="paragraph", start=start, end=end, text=value))
        cursor = end
    if not blocks:
        blocks.append(ContentBlock(id=stable_id("block", request.text), type="paragraph", start=0, end=len(request.text), text=request.text))
    book = ImportedBook(title=request.title.strip() or "粘贴文本", chapters=[ImportedChapter(
        id=stable_id("chapter", f"{request.title}:{request.text[:100]}"), title="正文", order=0, text=request.text, blocks=blocks,
    )])
    return book


@app.post("/api/import/epub", response_model=ImportedBook)
async def import_epub(file: UploadFile = File(...)) -> ImportedBook:
    if not (file.filename or "").lower().endswith(".epub"):
        raise HTTPException(400, "只接受 .epub 文件")
    payload = await file.read()
    if len(payload) > 80 * 1024 * 1024:
        raise HTTPException(413, "EPUB 文件不能超过 80 MB")
    try:
        return await asyncio.to_thread(parse_epub, payload, file.filename or "book.epub")
    except Exception as exc:
        raise HTTPException(422, f"无法解析 EPUB：{exc}") from exc


@app.post("/api/books/{book_id}/cover")
async def upload_book_cover(book_id: str, file: UploadFile = File(...)) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        raise HTTPException(400, "书籍 ID 不合法")
    content_type = (file.content_type or "").lower()
    suffixes = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    suffix = suffixes.get(content_type)
    if not suffix:
        raise HTTPException(400, "封面只支持 JPG、PNG、WebP 或 GIF 图片")
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "封面图片为空")
    if len(payload) > 10 * 1024 * 1024:
        raise HTTPException(413, "封面图片不能超过 10 MB")
    cover_dir = BOOK_DATA_DIR / "custom-covers"
    cover_dir.mkdir(parents=True, exist_ok=True)
    for old_suffix in suffixes.values():
        (cover_dir / f"{book_id}{old_suffix}").unlink(missing_ok=True)
    (cover_dir / f"{book_id}{suffix}").write_bytes(payload)
    return {"url": f"/api/assets/custom-covers/{book_id}{suffix}?v={time.time_ns()}"}


@app.delete("/api/books/{book_id}/cover")
async def delete_book_cover(book_id: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", book_id):
        raise HTTPException(400, "书籍 ID 不合法")
    cover_dir = BOOK_DATA_DIR / "custom-covers"
    for suffix in (".jpg", ".png", ".webp", ".gif"):
        (cover_dir / f"{book_id}{suffix}").unlink(missing_ok=True)
    return {"deleted": True}


@app.post("/api/dictionary/import")
async def import_dictionary(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "请选择 Yomitan 词典 ZIP")
    try:
        return import_yomitan(await file.read(), file.filename or "dictionary.zip")
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, f"无法导入词典：{exc}") from exc


@app.get("/api/dictionary/lookup")
async def lookup_dictionary(lemma: str, reading: str = "", surface: str = "") -> dict:
    return {"entry": lookup(lemma, reading, surface)}


@app.post("/api/preprocess", response_model=AnalyzeResponse)
async def preprocess(request: AnalyzeRequest) -> AnalyzeResponse:
    sentences, tokens = await _local_analysis_async(request.chapter_id, request.text)
    if not sentences:
        raise HTTPException(422, "章节中没有可处理的日文正文")
    return AnalyzeResponse(sentences=sentences, tokens=tokens, annotations=[], context_senses=[], lexemes=[], warnings=[])


@app.post("/api/chapters/segment", response_model=AnalyzeResponse)
async def segment_chapter(request: SegmentChapterRequest, x_api_key: str | None = Header(default=None)) -> AnalyzeResponse:
    api_key, base_url, model = resolve_settings(x_api_key, str(request.settings.base_url), request.settings.model)
    chapter = ImportedChapter(
        id=request.chapter_id, title="当前章节", order=0, text=request.text, blocks=request.blocks,
    )
    try:
        chapter, warning = await _segment_chapter(chapter, api_key, base_url, model, asyncio.Semaphore(3))
    except AiRateLimitError as exc:
        retry_after = max(1, round(exc.retry_after or 1))
        raise HTTPException(429, str(exc), headers={"Retry-After": str(retry_after)}) from exc
    warnings = [warning] if warning else []
    return AnalyzeResponse(
        sentences=chapter.sentences, tokens=chapter.tokens, annotations=[], context_senses=[], lexemes=[], warnings=warnings,
    )


def _result_for_sentence(
    request_item, entries: dict[str, dict | None], enriched: dict,
    annotation_mode: str, detail_mode: str,
) -> AnalyzeResponse:
    content_tokens = [token for token in request_item.tokens if token.is_content]
    sense_rows = {
        str(row[0]): {"gloss_zh": row[1], "fallback_senses_zh": row[2]}
        for row in enriched.get("words", [])
        if isinstance(row, list) and len(row) >= 3
    }
    context_senses: list[ContextSenseOut] = []
    lexemes: dict[str, LexemeOut] = {}
    warnings: list[str] = []
    for token in (content_tokens if annotation_mode != "grammar" and detail_mode == "full" else []):
        entry = entries[token.id]
        ai_row = sense_rows.get(token.id, {})
        if entry and entry.get("senses_zh"):
            senses = [str(value).strip() for value in entry["senses_zh"] if str(value).strip()]
            gloss = senses[0] if senses else ""
            if gloss:
                context_senses.append(ContextSenseOut(token_id=token.id, gloss_zh=gloss))
            key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
            lexemes[key] = LexemeOut(
                key=key, lemma=token.lemma, reading=token.reading, part_of_speech=token.part_of_speech,
                senses_zh=senses, source=str(entry.get("source") or "本地词典"),
            )
            continue
        gloss = str(ai_row.get("gloss_zh", "")).strip()
        fallback = [
            str(value).strip() for value in ai_row.get("fallback_senses_zh", []) if str(value).strip()
        ]
        if gloss:
            context_senses.append(ContextSenseOut(token_id=token.id, gloss_zh=gloss))
        if fallback:
            key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
            lexemes[key] = LexemeOut(
                key=key, lemma=token.lemma, reading=token.reading, part_of_speech=token.part_of_speech,
                senses_zh=list(dict.fromkeys(fallback)), source="AI 补充释义",
            )
        elif not gloss:
            warnings.append(f"{token.surface} 未获得释义")

    original = request_item.sentence.original
    annotations: list[AnnotationOut] = []
    if annotation_mode == "grammar":
        for note in enriched.get("annotations", []):
            try:
                if not isinstance(note, list) or len(note) < 6:
                    continue
                note_type, start, end, quote, structure, explanation = (
                    str(note[0]), int(note[1]), int(note[2]), str(note[3]),
                    str(note[4]).strip(), str(note[5]).strip(),
                )
                if note_type != "grammar" or not structure:
                    continue
                if start < 0 or end <= start or end > len(original) or original[start:end] != quote or not explanation:
                    continue
                annotations.append(AnnotationOut(
                    id=stable_id("ann", f"{request_item.sentence.id}:{start}:{end}:{structure}:{explanation}"),
                    sentence_id=request_item.sentence.id, type=note_type, anchor_start=start, anchor_end=end,
                    quote=quote, structure=structure, explanation_zh=explanation,
                ))
            except (TypeError, ValueError):
                continue
    sentence = request_item.sentence.model_copy(update={
        "translation_zh": (
            request_item.sentence.translation_zh if annotation_mode == "grammar"
            else str(enriched.get("meaning", "")).strip()
        ),
        "status": "complete", "error": None, "explanation_status": "complete",
        "explanation_detail": (
            request_item.sentence.explanation_detail if annotation_mode == "grammar" else detail_mode
        ),
    })
    return AnalyzeResponse(
        sentences=[sentence], tokens=request_item.tokens, annotations=annotations,
        context_senses=context_senses, lexemes=list(lexemes.values()), warnings=warnings,
    )


def _is_splittable_ai_error(error: Exception) -> bool:
    value = str(error)
    return any(marker in value for marker in ("token 上限", "可解析的 JSON", "JSON 未完成", "AI 未返回句子"))


async def _fetch_ai_rows_resilient(
    misses: list[dict], api_key: str, base_url: str, model: str,
    annotation_mode: str, detail_mode: str, context_before: list[str], depth: int = 0,
) -> list[dict]:
    if not misses:
        return []
    try:
        rows = await explain_sentences_with_ai(
            misses, api_key, base_url, model, annotation_mode,
            detail_mode=detail_mode, context_before=context_before,
        )
    except Exception as exc:
        if len(misses) > 1 and depth < 4 and _is_splittable_ai_error(exc):
            middle = max(1, len(misses) // 2)
            left = await _fetch_ai_rows_resilient(
                misses[:middle], api_key, base_url, model, annotation_mode,
                detail_mode, context_before, depth + 1,
            )
            right_context = [
                *(context_before[-2:]),
                *(str(item["sentence"].get("original", "")) for item in misses[:middle]),
            ][-2:]
            right = await _fetch_ai_rows_resilient(
                misses[middle:], api_key, base_url, model, annotation_mode,
                detail_mode, right_context, depth + 1,
            )
            return [*left, *right]
        raise
    returned = {str(row.get("id")) for row in rows if isinstance(row, dict)}
    missing_rows = [item for item in misses if str(item["sentence"].get("id")) not in returned]
    if missing_rows:
        if depth >= 4:
            raise RuntimeError(f"AI 未返回句子 {missing_rows[0]['sentence'].get('id', '')} 的结果")
        rows.extend(await _fetch_ai_rows_resilient(
            missing_rows, api_key, base_url, model, annotation_mode,
            detail_mode, context_before, depth + 1,
        ))
    return rows


async def _explain_batch(
    request: ExplainBatchRequest, x_api_key: str | None,
) -> AnalyzeResponse:
    api_key, base_url, model = resolve_settings(x_api_key, str(request.settings.base_url), request.settings.model)
    if not api_key:
        raise HTTPException(401, "请在设置中输入 API Key")
    prepared: list[dict] = []
    cached_rows: dict[str, dict] = {}
    misses: list[dict] = []
    for item in request.items:
        content_tokens = [token for token in item.tokens if token.is_content]
        entries = {} if request.annotation_mode == "grammar" or request.detail_mode == "meaning" else {
            token.id: _entry_for_token(token) for token in content_tokens
        }
        unresolved = [] if request.annotation_mode == "grammar" or request.detail_mode == "meaning" else [
            token for token in content_tokens if not (entries[token.id] or {}).get("senses_zh")
        ]
        cache_value = {
            "text": item.sentence.original,
            "unknown": [
                [token.lemma, token.reading, token.part_of_speech, token.surface] for token in unresolved
            ],
            "annotation_mode": request.annotation_mode,
            "detail_mode": request.detail_mode,
            "context_before": request.context_before,
        }
        cache_key = make_cache_key("sentence", model, PROMPT_VERSION, cache_value)
        prepared_item = {
            "item": item, "entries": entries, "unresolved_tokens": unresolved, "cache_key": cache_key,
        }
        prepared.append(prepared_item)
        cached = get_cached_response(cache_key)
        if cached:
            restored_words = []
            for word in cached.get("words", []):
                if not isinstance(word, list) or len(word) < 3:
                    continue
                try:
                    token = unresolved[int(word[0])]
                except (ValueError, TypeError, IndexError):
                    continue
                restored_words.append([token.id, word[1], word[2]])
            cached_rows[item.sentence.id] = {
                **cached, "id": item.sentence.id, "words": restored_words,
            }
        else:
            misses.append({
                "sentence": item.sentence.model_dump(),
                "unresolved_tokens": [token.model_dump() for token in unresolved],
            })

    try:
        fresh_rows = await _fetch_ai_rows_resilient(
            misses, api_key, base_url, model, request.annotation_mode,
            request.detail_mode, request.context_before,
        ) if misses else []
    except AiRateLimitError:
        raise
    except Exception as exc:
        raise HTTPException(502, f"句子释义失败：{exc}") from exc
    fresh_by_id = {str(row.get("id")): row for row in fresh_rows if isinstance(row, dict)}
    combined = AnalyzeResponse(
        sentences=[], tokens=[], annotations=[], context_senses=[], lexemes=[], warnings=[],
    )
    lexemes_by_key: dict[str, LexemeOut] = {}
    for prepared_item in prepared:
        item = prepared_item["item"]
        row = cached_rows.get(item.sentence.id) or fresh_by_id.get(item.sentence.id)
        if row is None:
            raise HTTPException(502, f"AI 未返回句子 {item.sentence.id} 的结果")
        if item.sentence.id in fresh_by_id:
            token_indexes = {
                token.id: index for index, token in enumerate(prepared_item["unresolved_tokens"])
            }
            cache_words = [
                [token_indexes[word[0]], word[1], word[2]]
                for word in row.get("words", [])
                if isinstance(word, list) and len(word) >= 3 and word[0] in token_indexes
            ]
            cache_payload = {**row, "id": "cached", "words": cache_words}
            set_cached_response(
                prepared_item["cache_key"], "sentence", model, PROMPT_VERSION, cache_payload,
            )
        result = _result_for_sentence(
            item, prepared_item["entries"], row, request.annotation_mode, request.detail_mode,
        )
        combined.sentences.extend(result.sentences)
        combined.tokens.extend(result.tokens)
        combined.annotations.extend(result.annotations)
        combined.context_senses.extend(result.context_senses)
        combined.warnings.extend(result.warnings)
        for lexeme in result.lexemes:
            lexemes_by_key[lexeme.key] = lexeme
    combined.lexemes = list(lexemes_by_key.values())
    return combined


@app.post("/api/sentences/explain-batch", response_model=AnalyzeResponse)
async def explain_sentence_batch(
    request: ExplainBatchRequest, x_api_key: str | None = Header(default=None),
) -> AnalyzeResponse:
    try:
        return await _explain_batch(request, x_api_key)
    except AiRateLimitError as exc:
        retry_after = max(1, round(exc.retry_after or 1))
        raise HTTPException(429, str(exc), headers={"Retry-After": str(retry_after)}) from exc


@app.post("/api/sentences/explain", response_model=AnalyzeResponse)
async def explain_sentence(
    request: ExplainSentenceRequest, x_api_key: str | None = Header(default=None),
) -> AnalyzeResponse:
    batch = ExplainBatchRequest(
        items=[{"sentence": request.sentence, "tokens": request.tokens}],
        annotation_mode=request.annotation_mode,
        detail_mode=request.detail_mode,
        context_before=request.context_before,
        settings=request.settings,
    )
    try:
        return await _explain_batch(batch, x_api_key)
    except AiRateLimitError as exc:
        retry_after = max(1, round(exc.retry_after or 1))
        raise HTTPException(429, str(exc), headers={"Retry-After": str(retry_after)}) from exc


if DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="web")
