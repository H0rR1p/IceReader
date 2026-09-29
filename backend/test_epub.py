from ebooklib import epub
from bs4 import BeautifulSoup

from . import epub as epub_parser


def test_original_preview_removes_active_url_schemes_and_handlers():
    soup = BeautifulSoup("""<html><head>
      <base href="https://attacker.invalid/">
      <meta http-equiv="refresh" content="0;url=javascript:alert(1)">
      <style>.bad { background: url(javascript:alert(1)); }</style>
    </head><body>
      <a href="java&#x09;script:alert(1)" onclick="alert(2)">bad</a>
      <a href="https://example.com/ok">safe</a>
      <form action="vbscript:alert(1)"><button formaction="data:text/html,bad">go</button></form>
      <div srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;" style="width: expression(alert(1))">x</div>
    </body></html>""", "html.parser")

    sanitized = epub_parser._sanitize_original(soup, "chapter.xhtml", {}, "")
    lowered = sanitized.lower()

    assert "javascript:" not in lowered
    assert "vbscript:" not in lowered
    assert "data:text/html" not in lowered
    assert "onclick" not in lowered
    assert "srcdoc" not in lowered
    assert "http-equiv=\"refresh\"" not in lowered
    assert "<base" not in lowered
    assert 'href="https://example.com/ok"' in lowered


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
    managed_book_dirs = [path for path in (tmp_path / "books").iterdir() if path.is_dir()]
    assert len(managed_book_dirs) == 1
    assert (managed_book_dirs[0] / "source" / "book.epub").read_bytes() == source.read_bytes()
    assert (managed_book_dirs[0] / "source" / "filename.txt").read_text(encoding="utf-8") == "sample.epub"


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
