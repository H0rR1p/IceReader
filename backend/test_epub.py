from ebooklib import epub

from . import epub as epub_parser


def test_parse_epub_accepts_uploaded_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_parser, "BOOK_DATA_DIR", tmp_path / "books")
    source = tmp_path / "sample.epub"
    book = epub.EpubBook()
    book.set_identifier("sample")
    book.set_title("吾輩は猫である")
    book.add_author("夏目漱石")
    chapter = epub.EpubHtml(title="第一章", file_name="chapter.xhtml", lang="ja")
    chapter.content = "<html><body><h1>第一章</h1><p>吾輩は猫である。</p></body></html>"
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(source), book)

    imported = epub_parser.parse_epub(source.read_bytes(), "sample.epub")

    assert imported.title == "吾輩は猫である"
    assert imported.author == "夏目漱石"
    assert imported.chapters[0].title == "第一章"
    assert "吾輩は猫である。" in imported.chapters[0].text
    assert [block.type for block in imported.chapters[0].blocks] == ["heading", "paragraph"]
    assert imported.chapters[0].original_html_url
