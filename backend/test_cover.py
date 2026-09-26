from fastapi.testclient import TestClient

from . import app as app_module


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
