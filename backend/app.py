import json
from collections import defaultdict
from uuid import uuid4

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .ai import chunks, enrich_batch
from .epub import parse_epub
from .models import (
    AnalyzeRequest,
    AnalyzeResponse,
    AnnotationOut,
    ContextSenseOut,
    ImportedBook,
    ImportedChapter,
    LexemeOut,
    LibrarySnapshot,
    LocalAiSettingsInput,
    LocalAiSettingsStatus,
    SentenceOut,
    TextImportRequest,
    TokenOut,
)
from .nlp import split_sentences, stable_id, tokenize_sentence
from .library_store import load_library, save_library
from .settings_store import get_settings_status, resolve_settings, save_settings


app = FastAPI(title="日读本地 API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
    return ImportedBook(
        title=request.title.strip() or "粘贴文本",
        chapters=[ImportedChapter(
            id=stable_id("chapter", f"{request.title}:{request.text[:100]}"),
            title="正文",
            order=0,
            text=request.text,
        )],
    )


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


@app.post("/api/analyze", response_model=AnalyzeResponse)
async def analyze(
    request: AnalyzeRequest,
    x_api_key: str | None = Header(default=None),
) -> AnalyzeResponse:
    api_key, base_url, model = resolve_settings(
        x_api_key,
        str(request.settings.base_url),
        request.settings.model,
    )
    if not api_key:
        raise HTTPException(401, "请在设置中输入 API Key")
    spans = split_sentences(request.text)
    if not spans:
        raise HTTPException(422, "章节中没有可处理的日文正文")

    sentence_rows: list[dict] = []
    all_tokens: list[dict] = []
    tokens_by_sentence: dict[str, list[dict]] = defaultdict(list)
    for index, span in enumerate(spans):
        sentence_id = stable_id("sent", f"{request.chapter_id}:{index}:{span.start}:{span.text}")
        if request.only_sentence_ids and sentence_id not in request.only_sentence_ids:
            continue
        sentence = {
            "id": sentence_id,
            "chapter_id": request.chapter_id,
            "start": span.start,
            "end": span.end,
            "original": span.text,
            "translation_zh": "",
            "status": "complete",
        }
        sentence_rows.append(sentence)
        tokens = tokenize_sentence(sentence_id, span.text)
        tokens_by_sentence[sentence_id] = tokens
        all_tokens.extend(tokens)

    if request.only_sentence_ids and not sentence_rows:
        raise HTTPException(422, "找不到需要重试的失败句子；请重新处理整章")

    annotations: list[AnnotationOut] = []
    context_senses: list[ContextSenseOut] = []
    lexemes: dict[str, LexemeOut] = {}
    warnings: list[str] = []
    sentence_by_id = {row["id"]: row for row in sentence_rows}
    token_by_id = {row["id"]: row for row in all_tokens}

    for batch in chunks(sentence_rows):
        try:
            enriched = await enrich_batch(
                batch,
                tokens_by_sentence,
                api_key,
                base_url,
                model,
                set(request.known_lexeme_keys),
            )
            for item in enriched.get("sentences", []):
                sentence = sentence_by_id.get(item.get("sentence_id"))
                if not sentence:
                    continue
                sentence["translation_zh"] = str(item.get("translation_zh", "")).strip()
                if not sentence["translation_zh"]:
                    sentence["status"] = "failed"
                    sentence["error"] = "AI 未返回译文"
                for sense in item.get("context_senses", []):
                    token_id = sense.get("token_id")
                    if token_id in token_by_id and token_by_id[token_id]["sentence_id"] == sentence["id"]:
                        gloss = str(sense.get("gloss_zh", "")).strip()
                        if gloss:
                            context_senses.append(ContextSenseOut(token_id=token_id, gloss_zh=gloss))
                for note in item.get("annotations", []):
                    try:
                        start, end = int(note["anchor_start"]), int(note["anchor_end"])
                        quote = str(note["quote"])
                        note_type = str(note["type"])
                        if note_type not in {"grammar", "pragmatics", "ellipsis", "culture"}:
                            continue
                        if start < 0 or end <= start or end > len(sentence["original"]):
                            continue
                        if sentence["original"][start:end] != quote:
                            continue
                        explanation = str(note.get("explanation_zh", "")).strip()
                        if not explanation:
                            continue
                        annotations.append(AnnotationOut(
                            id=stable_id("ann", f"{sentence['id']}:{start}:{end}:{explanation}"),
                            sentence_id=sentence["id"],
                            type=note_type,
                            anchor_start=start,
                            anchor_end=end,
                            quote=quote,
                            explanation_zh=explanation,
                        ))
                    except (KeyError, TypeError, ValueError):
                        continue
            for entry in enriched.get("lexemes", []):
                key = str(entry.get("lexeme_key", ""))
                if not key or key in request.known_lexeme_keys:
                    continue
                senses = [str(x).strip() for x in entry.get("senses_zh", []) if str(x).strip()]
                if senses:
                    lexemes[key] = LexemeOut(
                        key=key,
                        lemma=str(entry.get("lemma", "")),
                        reading=str(entry.get("reading", "")),
                        part_of_speech=str(entry.get("part_of_speech", "")),
                        senses_zh=senses,
                        source="AI 日中词典",
                    )
        except Exception as exc:
            message = f"一批句子处理失败：{exc}"
            warnings.append(message)
            for sentence in batch:
                sentence["status"] = "failed"
                sentence["error"] = message

    sentence_models = [SentenceOut(**row) for row in sentence_rows]
    token_models = [TokenOut(**{key: value for key, value in row.items() if key != "lexeme_key"}) for row in all_tokens]
    return AnalyzeResponse(
        sentences=sentence_models,
        tokens=token_models,
        annotations=annotations,
        context_senses=context_senses,
        lexemes=list(lexemes.values()),
        warnings=warnings,
    )
