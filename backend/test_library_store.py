from . import library_store
from .models import LibraryPatch, LibrarySnapshot


def test_library_snapshot_round_trip(tmp_path, monkeypatch):
    path = tmp_path / "data" / "library.json"
    monkeypatch.setattr(library_store, "LIBRARY_PATH", path)
    snapshot = LibrarySnapshot(
        books=[{"id": "book-1", "title": "test"}],
        chapters=[{"id": "chapter-1", "bookId": "book-1"}],
    )

    library_store.save_library(snapshot)
    restored = library_store.load_library()

    assert restored is not None
    assert restored.books[0]["id"] == "book-1"
    assert restored.chapters[0]["bookId"] == "book-1"


def test_chapter_view_and_details_are_loaded_separately(tmp_path, monkeypatch):
    path = tmp_path / "data" / "library.sqlite3"
    monkeypatch.setattr(library_store, "LIBRARY_PATH", path)
    library_store.apply_library_patch(LibraryPatch(upserts={
        "books": [{"id": "book-1", "title": "test"}],
        "chapters": [{"id": "chapter-1", "bookId": "book-1", "text": "猫。犬。", "order": 0}],
        "sentences": [
            {"id": "sentence-1", "chapter_id": "chapter-1", "start": 0, "end": 2, "original": "猫。"},
            {"id": "sentence-2", "chapter_id": "chapter-1", "start": 2, "end": 4, "original": "犬。"},
        ],
        "tokens": [
            {"id": "token-1", "sentence_id": "sentence-1", "start": 0, "end": 1, "surface": "猫", "lexemeKey": "猫|ネコ|名詞"},
            {"id": "token-2", "sentence_id": "sentence-2", "start": 0, "end": 1, "surface": "犬", "lexemeKey": "犬|イヌ|名詞"},
        ],
        "annotations": [
            {"id": "annotation-2", "sentence_id": "sentence-2", "type": "culture"},
        ],
        "contextSenses": [
            {"token_id": "token-2", "gloss_zh": "狗"},
        ],
        "lexemes": [
            {"key": "猫|ネコ|名詞", "lemma": "猫", "reading": "ネコ"},
            {"key": "犬|イヌ|名詞", "lemma": "犬", "reading": "イヌ"},
        ],
        "bookmarks": [
            {"id": "sentence-2", "bookId": "book-1", "chapterId": "chapter-1", "sentenceId": "sentence-2", "chapterOrder": 0, "sentenceStart": 2, "text": "犬。"},
        ],
    }))

    view = library_store.load_chapter_view("chapter-1")
    assert [row["id"] for row in view.sentences] == ["sentence-1", "sentence-2"]
    assert view.tokens == []
    assert view.annotations == []

    details = library_store.load_chapter_details("chapter-1", offset=1, limit=1)
    assert details["sentence_ids"] == ["sentence-2"]
    assert [row["id"] for row in details["tokens"]] == ["token-2"]
    assert [row["id"] for row in details["annotations"]] == ["annotation-2"]
    assert details["contextSenses"] == [{"token_id": "token-2", "gloss_zh": "狗"}]
    assert [row["key"] for row in details["lexemes"]] == ["犬|イヌ|名詞"]
    assert [row["sentenceId"] for row in library_store.load_bookmarks("book-1")] == ["sentence-2"]


def test_translation_queue_is_stable_and_filters_by_detail(tmp_path, monkeypatch):
    path = tmp_path / "data" / "library.sqlite3"
    monkeypatch.setattr(library_store, "LIBRARY_PATH", path)
    library_store.apply_library_patch(LibraryPatch(upserts={
        "books": [{"id": "book-1", "title": "test"}],
        "chapters": [{"id": "chapter-1", "bookId": "book-1", "title": "正文", "order": 0}],
        "sentences": [
            {"id": "s1", "chapter_id": "chapter-1", "start": 0, "original": "一。", "translation_zh": "一。", "explanation_status": "complete", "explanation_detail": "meaning"},
            {"id": "s2", "chapter_id": "chapter-1", "start": 2, "original": "二。", "translation_zh": "", "explanation_status": "idle"},
            {"id": "s3", "chapter_id": "chapter-1", "start": 4, "original": "三。", "translation_zh": "三。", "explanation_status": "complete", "explanation_detail": "full"},
            {"id": "s4", "chapter_id": "chapter-1", "start": 6, "original": "四。", "translation_zh": "四。", "explanation_status": "complete"},
        ],
        "tokens": [
            {"id": "t1", "sentence_id": "s1", "start": 0, "end": 1, "surface": "一"},
            {"id": "t2", "sentence_id": "s2", "start": 0, "end": 1, "surface": "二"},
        ],
    }))

    meaning_page = library_store.load_translation_queue("book-1", limit=1, detail_mode="meaning")
    assert [item["sentence"]["id"] for item in meaning_page["items"]] == ["s2"]
    assert meaning_page["items"][0]["tokens"] == []
    assert meaning_page["contextBefore"] == ["一。"]
    assert meaning_page["nextCursor"] is None

    first_full_page = library_store.load_translation_queue(
        "book-1", limit=1, detail_mode="full", include_tokens=True,
    )
    assert [item["sentence"]["id"] for item in first_full_page["items"]] == ["s1"]
    assert [token["id"] for token in first_full_page["items"][0]["tokens"]] == ["t1"]
    assert first_full_page["nextCursor"]

    second_full_page = library_store.load_translation_queue(
        "book-1", cursor=first_full_page["nextCursor"], limit=2,
        detail_mode="full", include_tokens=True,
    )
    assert [item["sentence"]["id"] for item in second_full_page["items"]] == ["s2"]
    assert [token["id"] for token in second_full_page["items"][0]["tokens"]] == ["t2"]
    assert second_full_page["contextBefore"] == ["一。"]


def test_library_index_marks_only_fully_translated_books(tmp_path, monkeypatch):
    path = tmp_path / "data" / "library.sqlite3"
    monkeypatch.setattr(library_store, "LIBRARY_PATH", path)
    library_store.apply_library_patch(LibraryPatch(upserts={
        "books": [{"id": "book-1", "title": "test"}],
        "chapters": [
            {"id": "chapter-1", "bookId": "book-1", "text": "猫。", "order": 0},
            {"id": "chapter-2", "bookId": "book-1", "text": "犬。", "order": 1},
        ],
        "sentences": [
            {"id": "s1", "chapter_id": "chapter-1", "start": 0, "original": "猫。", "translation_zh": "猫。", "explanation_status": "complete", "explanation_detail": "meaning"},
            {"id": "s2", "chapter_id": "chapter-2", "start": 0, "original": "犬。", "translation_zh": "", "explanation_status": "idle"},
        ],
    }))

    assert library_store.load_library_index().books[0]["translationComplete"] is False

    library_store.apply_library_patch(LibraryPatch(upserts={"sentences": [
        {"id": "s2", "chapter_id": "chapter-2", "start": 0, "original": "犬。", "translation_zh": "狗。", "explanation_status": "complete", "explanation_detail": "meaning"},
    ]}))
    assert library_store.load_library_index().books[0]["translationComplete"] is True


def test_read_connection_does_not_repeat_schema_migration(tmp_path, monkeypatch):
    path = tmp_path / "data" / "library.sqlite3"
    monkeypatch.setattr(library_store, "LIBRARY_PATH", path)
    library_store.apply_library_patch(LibraryPatch(upserts={
        "books": [{"id": "book-1", "title": "test"}],
    }))

    statements: list[str] = []
    original_connect = library_store.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(library_store.sqlite3, "connect", traced_connect)
    library_store.load_library_index()

    writes = ("BEGIN", "DELETE", "DROP", "CREATE", "INSERT", "UPDATE", "REPLACE")
    assert not [statement for statement in statements if statement.lstrip().upper().startswith(writes)]


def test_delete_book_only_releases_unreferenced_epub_resources(tmp_path, monkeypatch):
    path = tmp_path / "data" / "library.sqlite3"
    monkeypatch.setattr(library_store, "LIBRARY_PATH", path)
    resource_key = "a" * 20
    library_store.apply_library_patch(LibraryPatch(upserts={
        "books": [
            {"id": "book-1", "title": "first"},
            {"id": "book-2", "title": "duplicate"},
        ],
        "chapters": [
            {"id": "chapter-1", "bookId": "book-1", "originalHtmlUrl": f"/api/assets/{resource_key}/documents/one.html"},
            {"id": "chapter-2", "bookId": "book-2", "originalHtmlUrl": f"/api/assets/{resource_key}/documents/one.html"},
        ],
    }))

    first = library_store.delete_book("book-1")
    second = library_store.delete_book("book-2")

    assert first["resource_keys"] == []
    assert second["resource_keys"] == [resource_key]
