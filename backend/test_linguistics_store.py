import pytest
from fastapi import HTTPException

from .models import LibraryPatch
from .modules.linguistics import repository, service
from .modules.library import repository as library


def test_preferences_are_durable_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "STORE_PATH", tmp_path / "linguistics.sqlite3")
    repository.save_preferences("a", {"context_policy": {"preceding_sentences": 8},
                                     "ambiguity_resolution": True})
    assert repository.load_preferences("a")["context_policy"]["preceding_sentences"] == 8
    assert repository.load_preferences("b")["context_policy"]["preceding_sentences"] == 2
    assert not repository.load_preferences("b")["ambiguity_resolution"]


def test_owned_structures_and_revision_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "STORE_PATH", tmp_path / "linguistics.sqlite3")
    monkeypatch.setattr(library, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    monkeypatch.setattr(library, "LEGACY_PATH", tmp_path / "absent.json")
    library.apply_library_patch("a", LibraryPatch(upserts={
        "books": [{"id": "b", "title": "原创fixture"}],
        "chapters": [{"id": "c", "bookId": "b", "order": 0, "text": "本を読ませました。"}],
        "sentences": [{"id": "s", "chapter_id": "c", "start": 0, "end": 10,
                       "original": "本を読ませました。", "analysis_revision": 1}],
    }))
    result = service.structure_results("a", ["s"])
    assert result["results"][0]["learning_spans"][0]["lemma"] == "読む"
    assert service.structure_results("a", ["s"]) == result
    with pytest.raises(HTTPException) as denied:
        service.structure_results("b", ["s"])
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as stale:
        service.structure_results("a", ["s"], required_version="old")
    assert stale.value.status_code == 409
    monkeypatch.setattr(service, "analyze_sentence", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("cache should hit")))
    assert service.structure_results("a", ["s"]) == result
    with pytest.raises(HTTPException):
        service.structure_results("a", ["s", "other"])
