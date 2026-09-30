import asyncio
import zipfile

from fastapi import APIRouter, File, HTTPException, UploadFile

from ...dictionary_store import import_yomitan, lookup
from ...epub import parse_epub
from ...models import ContentBlock, ImportedBook, ImportedChapter, TextImportRequest
from ...nlp import stable_id


router = APIRouter(prefix="/api", tags=["content"])


@router.post("/import/text", response_model=ImportedBook)
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
        blocks.append(ContentBlock(
            id=stable_id("block", f"text:{index}:{value[:80]}"),
            type="paragraph",
            start=start,
            end=end,
            text=value,
        ))
        cursor = end
    if not blocks:
        blocks.append(ContentBlock(
            id=stable_id("block", request.text),
            type="paragraph",
            start=0,
            end=len(request.text),
            text=request.text,
        ))
    return ImportedBook(
        title=request.title.strip() or "粘贴文本",
        chapters=[ImportedChapter(
            id=stable_id("chapter", f"{request.title}:{request.text[:100]}"),
            title="正文",
            order=0,
            text=request.text,
            blocks=blocks,
        )],
    )


@router.post("/import/epub", response_model=ImportedBook)
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


@router.post("/dictionary/import")
async def import_dictionary(file: UploadFile = File(...)) -> dict:
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, "请选择 Yomitan 词典 ZIP")
    try:
        return import_yomitan(await file.read(), file.filename or "dictionary.zip")
    except (ValueError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, f"无法导入词典：{exc}") from exc


@router.get("/dictionary/lookup")
async def lookup_dictionary(lemma: str, reading: str = "", surface: str = "") -> dict:
    return {"entry": lookup(lemma, reading, surface)}
