from fastapi.testclient import TestClient

from . import app as app_module
from . import library_store
from .models import LibraryPatch


def test_custom_cover_upload_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BOOK_DATA_DIR", tmp_path / "books")
    client = TestClient(app_module.app)

    uploaded = client.post(
        "/api/books/book_test/cover",
        files={"file": ("cover.png", b"png-image-data", "image/png")},
    )

    assert uploaded.status_code == 200
    assert uploaded.json()["url"].startswith("/api/assets/custom-covers/book_test.png?v=")
    assert (tmp_path / "books" / "custom-covers" / "book_test.png").read_bytes() == b"png-image-data"

    deleted = client.delete("/api/books/book_test/cover")

    assert deleted.status_code == 200
    assert not (tmp_path / "books" / "custom-covers" / "book_test.png").exists()


def test_custom_cover_rejects_unsupported_file_type(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "BOOK_DATA_DIR", tmp_path / "books")
    response = TestClient(app_module.app).post(
        "/api/books/book_test/cover",
        files={"file": ("cover.svg", b"<svg></svg>", "image/svg+xml")},
    )

    assert response.status_code == 400


def test_deleting_book_removes_managed_epub_and_custom_cover(tmp_path, monkeypatch):
    books_dir = tmp_path / "books"
    resource_key = "b" * 20
    resource_dir = books_dir / resource_key
    (resource_dir / "source").mkdir(parents=True)
    (resource_dir / "source" / "book.epub").write_bytes(b"epub")
    cover_dir = books_dir / "custom-covers"
    cover_dir.mkdir(parents=True)
    (cover_dir / "book-1.png").write_bytes(b"cover")
    monkeypatch.setattr(app_module, "BOOK_DATA_DIR", books_dir)
    monkeypatch.setattr(library_store, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    library_store.apply_library_patch(LibraryPatch(upserts={
        "books": [{"id": "book-1", "title": "test"}],
        "chapters": [{
            "id": "chapter-1", "bookId": "book-1",
            "originalHtmlUrl": f"/api/assets/{resource_key}/documents/chapter.html",
        }],
    }))

    result = app_module._delete_book_and_resources("book-1")

    assert result["resources_deleted"] == 1
    assert not resource_dir.exists()
    assert not (cover_dir / "book-1.png").exists()
