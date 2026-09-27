import hashlib
import posixpath
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import ebooklib
from bs4 import BeautifulSoup, Tag
from ebooklib import epub

from .models import ContentBlock, ImportReport, ImportedBook, ImportedChapter
from .nlp import stable_id
from .paths import DATA_DIR


BOOK_DATA_DIR = DATA_DIR / "books"
TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote"}


def _safe_css(value: str) -> str:
    value = re.sub(r"@import[^;]+;?", "", value, flags=re.I)
    return re.sub(r"url\((['\"]?)(?:https?:|data:|javascript:)[^)]+\)", "none", value, flags=re.I)


def _resolve_href(document_name: str, href: str) -> str:
    path = unquote(urlsplit(href).path)
    return posixpath.normpath(posixpath.join(posixpath.dirname(document_name), path)).lstrip("./")


def _placement(tag: Tag) -> str:
    marker = (" ".join(tag.get("class", [])) + " " + str(tag.get("style", ""))).lower()
    if "center" in marker or "text-align: center" in marker:
        return "center"
    if "right" in marker or "float: right" in marker:
        return "right"
    if tag.parent and tag.parent.name in {"p", "span"}:
        return "inline"
    return "left"


def _integer_attr(tag: Tag, name: str) -> int | None:
    match = re.search(r"\d+", str(tag.get(name, "")))
    return int(match.group()) if match else None


def _text_content(tag: Tag) -> str:
    fragment = BeautifulSoup(str(tag), "html.parser")
    # Ruby readings are rendered above the base text. Including <rt> in the
    # plain text duplicates every annotated word and breaks sentence offsets.
    for reading in fragment.find_all(["rt", "rp"]):
        reading.decompose()
    for line_break in fragment.find_all("br"):
        line_break.replace_with("\n")
    value = fragment.get_text("", strip=True)
    value = re.sub(r"[ \t\u3000]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value)


def _extract_blocks(soup: BeautifulSoup, document_name: str, asset_urls: dict[str, str]) -> tuple[str, list[ContentBlock]]:
    blocks: list[ContentBlock] = []
    plain_text = ""
    body = soup.body or soup
    for node in body.descendants:
        if not isinstance(node, Tag):
            continue
        name = node.name.lower()
        if "pagebreak" in str(node.get("epub:type", "")):
            blocks.append(ContentBlock(id=stable_id("block", f"{document_name}:{len(blocks)}:page"), type="page-break", start=len(plain_text), end=len(plain_text)))
            continue
        if name in {"img", "image"}:
            source = str(node.get("src") or node.get("href") or node.get("xlink:href") or "")
            resolved = _resolve_href(document_name, source) if source else ""
            asset_url = asset_urls.get(resolved)
            if asset_url:
                blocks.append(ContentBlock(
                    id=stable_id("block", f"{document_name}:{len(blocks)}:{resolved}"), type="image",
                    start=len(plain_text), end=len(plain_text), asset_url=asset_url,
                    alt=str(node.get("alt", "")), placement=_placement(node),
                    width=_integer_attr(node, "width"), height=_integer_attr(node, "height"),
                ))
            continue
        if name == "hr":
            blocks.append(ContentBlock(id=stable_id("block", f"{document_name}:{len(blocks)}:hr"), type="separator", start=len(plain_text), end=len(plain_text)))
            continue
        if name not in TEXT_TAGS:
            # Some Aozora/Kobo EPUBs store each visual line in a top-level
            # span instead of a paragraph. Nested spans remain inline content
            # and are collected with their outer span.
            if name != "span":
                continue
            if any(parent.name in {"span", "ruby", "rt", "rp"} | TEXT_TAGS for parent in node.parents if isinstance(parent, Tag)):
                continue
        if name == "blockquote" and node.find(TEXT_TAGS - {"blockquote"}):
            continue
        if any(parent.name in TEXT_TAGS - {"blockquote"} for parent in node.parents if isinstance(parent, Tag)):
            continue
        value = _text_content(node)
        if not value:
            continue
        if plain_text:
            plain_text += "\n"
        start = len(plain_text)
        plain_text += value
        block_type = "heading" if name.startswith("h") else "list-item" if name == "li" else "quote" if name == "blockquote" or node.find_parent("blockquote") else "paragraph"
        blocks.append(ContentBlock(
            id=stable_id("block", f"{document_name}:{len(blocks)}:{value[:80]}"), type=block_type,
            start=start, end=len(plain_text), text=value,
            level=int(name[1]) if name.startswith("h") else None,
        ))
    return plain_text, blocks


def _sanitize_original(soup: BeautifulSoup, document_name: str, asset_urls: dict[str, str], css: str) -> str:
    for tag in soup(["script", "iframe", "object", "embed"]):
        tag.decompose()
    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            if attribute.lower().startswith("on"):
                del tag.attrs[attribute]
        if tag.has_attr("style"):
            tag["style"] = _safe_css(str(tag["style"]))
    for tag in soup.find_all(["img", "image"]):
        attribute = "src" if tag.name == "img" else "href"
        source = str(tag.get(attribute) or tag.get("xlink:href") or "")
        resolved = _resolve_href(document_name, source) if source else ""
        if resolved in asset_urls:
            tag[attribute] = asset_urls[resolved]
            tag.attrs.pop("xlink:href", None)
        else:
            tag.decompose()
    for link in soup.find_all("link"):
        link.decompose()
    if css:
        style = soup.new_tag("style")
        style.string = css
        (soup.head or soup).append(style)
    return str(soup)


def parse_epub(payload: bytes, filename: str) -> ImportedBook:
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as temp_file:
            temp_file.write(payload)
            temp_path = Path(temp_file.name)
        book = epub.read_epub(str(temp_path), options={"ignore_ncx": False})
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    book_key = hashlib.sha256(payload).hexdigest()[:20]
    book_dir = BOOK_DATA_DIR / book_key
    assets_dir = book_dir / "assets"
    documents_dir = book_dir / "documents"
    assets_dir.mkdir(parents=True, exist_ok=True)
    documents_dir.mkdir(parents=True, exist_ok=True)

    asset_urls: dict[str, str] = {}
    for item in book.get_items():
        if not str(getattr(item, "media_type", "")).startswith("image/"):
            continue
        content = item.get_content()
        suffix = Path(item.get_name()).suffix.lower() or ".bin"
        asset_name = hashlib.sha256(content).hexdigest()[:20] + suffix
        (assets_dir / asset_name).write_bytes(content)
        asset_urls[posixpath.normpath(item.get_name()).lstrip("./")] = f"/api/assets/{book_key}/assets/{asset_name}"

    css = "\n".join(_safe_css(item.get_content().decode("utf-8", errors="ignore")) for item in book.get_items_of_type(ebooklib.ITEM_STYLE))
    metadata_title = book.get_metadata("DC", "title")
    metadata_author = book.get_metadata("DC", "creator")
    title = metadata_title[0][0] if metadata_title else Path(filename).stem
    author = metadata_author[0][0] if metadata_author else ""

    spine_documents = []
    for entry in book.spine:
        item = book.get_item_with_id(entry[0])
        if item is not None and item.get_type() == ebooklib.ITEM_DOCUMENT:
            spine_documents.append(item)
    if not spine_documents:
        spine_documents = list(book.get_items_of_type(ebooklib.ITEM_DOCUMENT))

    chapters: list[ImportedChapter] = []
    image_references = 0
    broken_image_references = 0
    image_only_sections = 0
    for order, item in enumerate(spine_documents):
        document_name = posixpath.normpath(item.get_name()).lstrip("./")
        soup = BeautifulSoup(item.get_content(), "html.parser")
        if soup.find("nav", attrs={"epub:type": "toc"}) or Path(document_name).stem.lower() in {"nav", "toc"}:
            continue
        for image in soup.find_all(["img", "image"]):
            source = str(image.get("src") or image.get("href") or image.get("xlink:href") or "")
            image_references += 1
            if not source or _resolve_href(document_name, source) not in asset_urls:
                broken_image_references += 1
        text, blocks = _extract_blocks(soup, document_name, asset_urls)
        if not text.strip() and not any(block.type == "image" for block in blocks):
            continue
        heading = soup.find(["h1", "h2", "h3"]) or soup.find("title")
        chapter_title = heading.get_text("", strip=True) if heading else f"第 {len(chapters) + 1} 节"
        document_file = f"{order:04d}-{hashlib.sha1(document_name.encode()).hexdigest()[:10]}.html"
        (documents_dir / document_file).write_text(_sanitize_original(soup, document_name, asset_urls, css), encoding="utf-8")
        if not text.strip() and any(block.type == "image" for block in blocks):
            image_only_sections += 1
        chapters.append(ImportedChapter(
            id=stable_id("chapter", f"{filename}:{document_name}:{order}"),
            title=chapter_title or f"第 {len(chapters) + 1} 节", order=len(chapters), text=text, blocks=blocks,
            original_html_url=f"/api/assets/{book_key}/documents/{document_file}",
        ))
    if not chapters:
        raise ValueError("EPUB 中没有可读取的正文或图片")
    return ImportedBook(
        title=title, author=author, chapters=chapters,
        import_report=ImportReport(
            source_documents=len(spine_documents), imported_sections=len(chapters), images=len(asset_urls),
            image_references=image_references, image_only_sections=image_only_sections,
            broken_image_references=broken_image_references,
        ),
    )
