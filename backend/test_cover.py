import asyncio

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from . import app as app_module
from .modules.library import repository as library_store
from .modules.library import router as library_router
from .models import LibraryPatch
from .core.request_context import RequestContext


def test_custom_cover_upload_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(library_router, "BOOK_DATA_DIR", tmp_path / "books")
    monkeypatch.setattr(library_store, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    client = TestClient(app_module.app)
    user_id = client.get("/api/me").json()["user_id"]
    library_store.apply_library_patch(user_id, LibraryPatch(upserts={
        "books": [{"id": "book_test", "title": "test"}],
    }))

    uploaded = client.post(
        "/api/books/book_test/cover",
        files={"file": ("cover.png", b"png-image-data", "image/png")},
    )

    assert uploaded.status_code == 200
    assert uploaded.json()["url"].startswith(f"/api/assets/custom-covers/{user_id}/book_test.png?v=")
    cover_path = tmp_path / "books" / "custom-covers" / user_id / "book_test.png"
    assert cover_path.read_bytes() == b"png-image-data"

    deleted = client.delete("/api/books/book_test/cover")

    assert deleted.status_code == 200
    assert not cover_path.exists()


def test_custom_cover_rejects_unsupported_file_type(tmp_path, monkeypatch):
    monkeypatch.setattr(library_router, "BOOK_DATA_DIR", tmp_path / "books")
    monkeypatch.setattr(library_store, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    client = TestClient(app_module.app)
    user_id = client.get("/api/me").json()["user_id"]
    library_store.apply_library_patch(user_id, LibraryPatch(upserts={
        "books": [{"id": "book_test", "title": "test"}],
    }))
    response = client.post(
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
    monkeypatch.setattr(library_router, "BOOK_DATA_DIR", books_dir)
    monkeypatch.setattr(library_store, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    library_store.apply_library_patch("user-1", LibraryPatch(upserts={
        "books": [{"id": "book-1", "title": "test"}],
        "chapters": [{
            "id": "chapter-1", "bookId": "book-1",
            "originalHtmlUrl": f"/api/assets/{resource_key}/documents/chapter.html",
        }],
    }))

    result = library_router.delete_book_and_resources("user-1", "book-1")

    assert result["resources_deleted"] == 1
    assert not resource_dir.exists()
    assert not (cover_dir / "book-1.png").exists()


def test_epub_assets_require_owning_user(tmp_path, monkeypatch):
    books_dir = tmp_path / "books"
    resource_key = "c" * 20
    asset = books_dir / resource_key / "images" / "page.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"image")
    monkeypatch.setattr(library_router, "BOOK_DATA_DIR", books_dir)
    monkeypatch.setattr(library_store, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    library_store.apply_library_patch("user-a", LibraryPatch(upserts={
        "books": [{"id": "book-1", "title": "test"}],
        "chapters": [{
            "id": "chapter-1", "bookId": "book-1",
            "originalHtmlUrl": f"/api/assets/{resource_key}/documents/chapter.html",
        }],
    }))
    owner = RequestContext("user-a", "s-a", "d-a", "local", "r-a")
    stranger = RequestContext("user-b", "s-b", "d-b", "local", "r-b")

    response = asyncio.run(library_router.read_book_asset(f"{resource_key}/images/page.png", owner))
    assert response.path == asset.resolve()
    with pytest.raises(HTTPException) as error:
        asyncio.run(library_router.read_book_asset(f"{resource_key}/images/page.png", stranger))
    assert error.value.status_code == 404
