import sqlite3

from .desktop_launcher import migrate_data


def test_migration_uses_sqlite_backup_and_retains_source(tmp_path):
    source, destination = tmp_path / "old", tmp_path / "new"
    source.mkdir()
    (destination / "books").mkdir(parents=True)
    (destination / "books" / "existing.png").write_bytes(b"keep")
    with sqlite3.connect(source / "library.sqlite3") as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE books(title TEXT)")
        connection.execute("INSERT INTO books VALUES('猫')")
        connection.commit()
        (source / "books").mkdir()
        (source / "books" / "image.png").write_bytes(b"image")
        migrate_data(destination, source)
    with sqlite3.connect(destination / "library.sqlite3") as connection:
        assert connection.execute("SELECT title FROM books").fetchone()[0] == "猫"
        connection.execute("INSERT INTO books VALUES('追加')")
    migrate_data(destination, source)
    with sqlite3.connect(destination / "library.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 2
    assert (source / "library.sqlite3").exists()
    assert (destination / "books" / "image.png").read_bytes() == b"image"
    assert (destination / "books" / "existing.png").read_bytes() == b"keep"


def test_private_sidecar_rejects_missing_secret(monkeypatch):
    from fastapi.testclient import TestClient
    from .app import app
    monkeypatch.setenv("BINGDU_DESKTOP_SECRET", "unit-test-secret")
    client = TestClient(app)
    assert client.get("/api/health").status_code == 403
    assert client.get("/bingdu-logo.png").status_code == 403
    assert client.get("/api/health", headers={"x-bingdu-desktop-secret": "wrong"}).status_code == 403
