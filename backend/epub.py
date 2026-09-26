import re
import tempfile
from pathlib import Path

import ebooklib
from bs4 import BeautifulSoup
from ebooklib import epub

from .models import ImportedBook, ImportedChapter
from .nlp import stable_id


def clean_text(html: bytes) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "svg", "iframe", "object", "embed"]):
        tag.decompose()
    blocks = []
    for node in soup.find_all(["h1", "h2", "h3", "h4", "p", "blockquote", "li"]):
        value = node.get_text("", strip=True)
        value = re.sub(r"[ \t\u3000]+", " ", value)
        if value:
            blocks.append(value)
    if not blocks:
        value = soup.get_text("\n", strip=True)
        blocks = [line for line in value.splitlines() if line.strip()]
    return "\n".join(blocks)


def parse_epub(payload: bytes, filename: str) -> ImportedBook:
    # EbookLib 0.20 resolves companion files relative to a filesystem path and
    # no longer accepts BytesIO here. Keep uploads in memory at the API edge,
    # then expose one short-lived file only for the parser.
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as temp_file:
            temp_file.write(payload)
            temp_path = Path(temp_file.name)
        book = epub.read_epub(str(temp_path), options={"ignore_ncx": False})
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    metadata_title = book.get_metadata("DC", "title")
    metadata_author = book.get_metadata("DC", "creator")
    title = metadata_title[0][0] if metadata_title else Path(filename).stem
    author = metadata_author[0][0] if metadata_author else ""
    chapters: list[ImportedChapter] = []
    order = 0
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        text = clean_text(item.get_content())
        if len(text.strip()) < 2:
            continue
        soup = BeautifulSoup(item.get_content(), "html.parser")
        heading = soup.find(["h1", "h2", "h3", "title"])
        chapter_title = heading.get_text("", strip=True) if heading else f"第 {order + 1} 章"
        chapters.append(ImportedChapter(
            id=stable_id("chapter", f"{filename}:{item.get_name()}:{order}"),
            title=chapter_title or f"第 {order + 1} 章",
            order=order,
            text=text,
        ))
        order += 1
    if not chapters:
        raise ValueError("EPUB 中没有可读取的正文")
    return ImportedBook(title=title, author=author, chapters=chapters)
