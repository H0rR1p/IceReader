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


def test_parse_epub_reads_top_level_kobo_spans_without_ruby_duplication(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_parser, "BOOK_DATA_DIR", tmp_path / "books")
    source = tmp_path / "kobo.epub"
    book = epub.EpubBook()
    book.set_identifier("kobo")
    book.set_title("吾輩は猫である")
    chapter = epub.EpubHtml(title="一", file_name="chapter.xhtml", lang="ja")
    chapter.content = """<html><body><section>
      <div><h4>一</h4></div>
      <span><ruby><span>吾輩</span><rt><span>わがはい</span></rt></ruby><span>は猫である。</span><br/></span>
      <span><span>名前はまだ無い。</span><br/></span>
    </section></body></html>"""
    book.add_item(chapter)
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(source), book)

    imported = epub_parser.parse_epub(source.read_bytes(), "kobo.epub")

    assert imported.chapters[0].text == "一\n吾輩は猫である。\n名前はまだ無い。"
    assert "わがはい" not in imported.chapters[0].text
    assert [block.type for block in imported.chapters[0].blocks] == ["heading", "paragraph", "paragraph"]
