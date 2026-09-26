from . import library_store
from .models import LibrarySnapshot


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
