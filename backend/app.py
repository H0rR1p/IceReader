import asyncio
import re
import time
import zipfile
from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .ai import explain_sentence as explain_sentence_with_ai
from .ai import review_sentence_boundaries
from .dictionary_store import import_yomitan, lookup
from .epub import BOOK_DATA_DIR, parse_epub
from .library_store import load_library, save_library
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnnotationOut,
    ContentBlock,
    ContextSenseOut,
    ExplainSentenceRequest,
    ImportedBook,
    ImportedChapter,
    LexemeOut,
    LibrarySnapshot,
    LocalAiSettingsInput,
    LocalAiSettingsStatus,
    SegmentChapterRequest,
    SentenceOut,
    TextImportRequest,
    TokenOut,
)
from .nlp import lexeme_key, split_sentences, stable_id, tokenize_sentence
from .settings_store import get_settings_status, resolve_settings, save_settings


app = FastAPI(title="日读本地 API", version="0.2.0")
app.mount("/api/assets", StaticFiles(directory=BOOK_DATA_DIR, check_dir=False), name="book-assets")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


def _candidate_batches(rows: list[dict], max_items: int = 80, max_chars: int = 8_000) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for row in rows:
        if current and (len(current) >= max_items or size + len(row["text"]) > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(row)
        size += len(row["text"])
    if current:
        batches.append(current)
    return batches


async def _segment_chapter(chapter: ImportedChapter, api_key: str, base_url: str, model: str, semaphore: asyncio.Semaphore) -> tuple[ImportedChapter, str | None]:
    spans = split_sentences(chapter.text)
    if not spans:
        chapter.segmentation_source = "empty"
        return chapter, None
    if not api_key:
        chapter.sentences, chapter.tokens = _local_analysis(chapter.id, chapter.text, spans)
        chapter.segmentation_source = "local-fallback"
        return chapter, "未配置 API Key，使用本地句子边界"

    text_ranges = [(block.start, block.end) for block in chapter.blocks if block.text and block.end > block.start]
    candidates: list[dict] = []
    for index, span in enumerate(spans):
        next_span = spans[index + 1] if index + 1 < len(spans) else None
        same_block = any(start <= span.start and next_span and next_span.end <= end for start, end in text_ranges)
        candidates.append({
            "id": f"{chapter.id}:{index}", "text": span.text,
            "can_merge_next": bool(next_span and same_block),
        })
    merge_ids: set[str] = set()
    async def review_batch(batch: list[dict]) -> set[str]:
        async with semaphore:
            return await review_sentence_boundaries(batch, api_key, base_url, model)

    try:
        for result in await asyncio.gather(*(review_batch(batch) for batch in _candidate_batches(candidates))):
            merge_ids.update(result)
    except Exception as exc:
        chapter.sentences, chapter.tokens = _local_analysis(chapter.id, chapter.text, spans)
        chapter.segmentation_source = "local-fallback"
        return chapter, f"{chapter.title} 的 AI 句界审校失败，已使用本地边界：{exc}"

    merged = []
    index = 0
    while index < len(spans):
        start, end = spans[index].start, spans[index].end
        while index < len(spans) - 1 and candidates[index]["id"] in merge_ids:
            index += 1
            end = spans[index].end
        text = chapter.text[start:end]
        merged.append(type(spans[0])(start=start, end=end, text=text))
        index += 1
    chapter.sentences, chapter.tokens = _local_analysis(chapter.id, chapter.text, merged)
    chapter.segmentation_source = "ai-reviewed"
    return chapter, None


def _personal_entry(token: TokenOut) -> dict | None:
    snapshot = load_library()
    if not snapshot:
        return None
    exact_key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
    exact = next((row for row in snapshot.lexemes if row.get("key") == exact_key), None)
    if exact:
        return exact
    return next((row for row in snapshot.lexemes
                 if row.get("lemma") in {token.lemma, token.surface} and row.get("reading") == token.reading), None)


def _entry_for_token(token: TokenOut) -> dict | None:
    return _personal_entry(token) or lookup(token.lemma, token.reading, token.surface)


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/settings", response_model=LocalAiSettingsStatus)
async def read_settings() -> LocalAiSettingsStatus:
    return get_settings_status()


@app.put("/api/settings", response_model=LocalAiSettingsStatus)
async def update_settings(settings: LocalAiSettingsInput) -> LocalAiSettingsStatus:
    return save_settings(settings)


@app.get("/api/library", response_model=LibrarySnapshot | None)
async def read_library() -> LibrarySnapshot | None:
    return load_library()


@app.put("/api/library", response_model=LibrarySnapshot)
async def update_library(snapshot: LibrarySnapshot) -> LibrarySnapshot:
    return save_library(snapshot)


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
        return parse_epub(payload, file.filename or "book.epub")
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
    sentences, tokens = _local_analysis(request.chapter_id, request.text)
    if not sentences:
        raise HTTPException(422, "章节中没有可处理的日文正文")
    return AnalyzeResponse(sentences=sentences, tokens=tokens, annotations=[], context_senses=[], lexemes=[], warnings=[])


@app.post("/api/chapters/segment", response_model=AnalyzeResponse)
async def segment_chapter(request: SegmentChapterRequest, x_api_key: str | None = Header(default=None)) -> AnalyzeResponse:
    api_key, base_url, model = resolve_settings(x_api_key, str(request.settings.base_url), request.settings.model)
    chapter = ImportedChapter(
        id=request.chapter_id, title="当前章节", order=0, text=request.text, blocks=request.blocks,
    )
    chapter, warning = await _segment_chapter(chapter, api_key, base_url, model, asyncio.Semaphore(3))
    warnings = [warning] if warning else []
    return AnalyzeResponse(
        sentences=chapter.sentences, tokens=chapter.tokens, annotations=[], context_senses=[], lexemes=[], warnings=warnings,
    )


@app.post("/api/sentences/explain", response_model=AnalyzeResponse)
async def explain_sentence(request: ExplainSentenceRequest, x_api_key: str | None = Header(default=None)) -> AnalyzeResponse:
    api_key, base_url, model = resolve_settings(x_api_key, str(request.settings.base_url), request.settings.model)
    if not api_key:
        raise HTTPException(401, "请在设置中输入 API Key")
    content_tokens = [token for token in request.tokens if token.is_content]
    entries = {token.id: _entry_for_token(token) for token in content_tokens}
    try:
        enriched = await explain_sentence_with_ai(
            request.sentence.model_dump(), [token.model_dump() for token in request.tokens], entries,
            api_key, base_url, model,
        )
    except Exception as exc:
        raise HTTPException(502, f"本句释义失败：{exc}") from exc

    sense_rows = {str(row.get("token_id")): row for row in enriched.get("token_senses", [])}
    context_senses: list[ContextSenseOut] = []
    lexemes: dict[str, LexemeOut] = {}
    warnings: list[str] = []
    for token in content_tokens:
        entry = entries[token.id]
        ai_row = sense_rows.get(token.id, {})
        gloss = str(ai_row.get("gloss_zh", "")).strip()
        if not gloss and entry and entry.get("senses_zh"):
            gloss = str(entry["senses_zh"][0])
        if gloss:
            context_senses.append(ContextSenseOut(token_id=token.id, gloss_zh=gloss))
        key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
        if entry and entry.get("senses_zh"):
            lexemes[key] = LexemeOut(
                key=key, lemma=token.lemma, reading=token.reading, part_of_speech=token.part_of_speech,
                senses_zh=[str(value) for value in entry["senses_zh"] if str(value).strip()],
                source=str(entry.get("source") or "本地词典"),
            )
        else:
            fallback = [str(value).strip() for value in ai_row.get("fallback_senses_zh", []) if str(value).strip()]
            if fallback:
                lexemes[key] = LexemeOut(
                    key=key, lemma=token.lemma, reading=token.reading, part_of_speech=token.part_of_speech,
                    senses_zh=list(dict.fromkeys(fallback)), source="AI 补充释义",
                )
            else:
                warnings.append(f"{token.surface} 未获得释义")

    original = request.sentence.original
    annotations: list[AnnotationOut] = []
    for note in enriched.get("annotations", []):
        try:
            start, end = int(note["anchor_start"]), int(note["anchor_end"])
            quote, note_type = str(note["quote"]), str(note["type"])
            explanation = str(note.get("explanation_zh", "")).strip()
            if note_type not in {"grammar", "pragmatics", "ellipsis", "culture"}:
                continue
            if start < 0 or end <= start or end > len(original) or original[start:end] != quote or not explanation:
                continue
            annotations.append(AnnotationOut(
                id=stable_id("ann", f"{request.sentence.id}:{start}:{end}:{explanation}"),
                sentence_id=request.sentence.id, type=note_type, anchor_start=start, anchor_end=end,
                quote=quote, explanation_zh=explanation,
            ))
        except (KeyError, TypeError, ValueError):
            continue
    sentence = request.sentence.model_copy(update={
        "translation_zh": str(enriched.get("meaning_zh", "")).strip(),
        "status": "complete", "error": None, "explanation_status": "complete",
    })
    return AnalyzeResponse(
        sentences=[sentence], tokens=request.tokens, annotations=annotations,
        context_senses=context_senses, lexemes=list(lexemes.values()), warnings=warnings,
    )
