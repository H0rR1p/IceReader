import asyncio
import json

import pytest

from . import ai
from .models import ExplainBatchRequest, LibraryPatch
from .modules.analysis import service
from .modules.analysis.prompt_payloads import compact_learning_batch, lexical_tokens
from .modules.linguistics import service as linguistics
from .modules.linguistics import repository
from .test_analysis_context import corpus
from .test_sentence_flow import CONTEXT


@pytest.fixture(autouse=True)
def isolate_stores(tmp_path, monkeypatch):
    from .modules.library import repository as library
    monkeypatch.setattr(library, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    monkeypatch.setattr(repository, "STORE_PATH", tmp_path / "linguistics.sqlite3")


@pytest.mark.parametrize("text,expected", [
    ("食べさせられなかった。", [("食べる", "タベル")]),
    ("読ませました。", [("読む", "ヨム")]),
    ("勉強している。", [("勉強する", "ベンキョウスル")]),
    ("優しかった。", [("優しい", "ヤサシイ")]),
])
def test_complete_chains_keep_dictionary_head_and_preserve_raw_atoms(text, expected):
    sentences, atoms = service._local_analysis("c", text)
    before = [token.model_dump() for token in atoms]
    structure = linguistics.analyze_sentence(sentences[0].id, text)
    selected = lexical_tokens(atoms, structure["learning_spans"])
    assert [(token.lemma, token.reading) for token in selected] == expected
    assert before == [token.model_dump() for token in atoms]
    assert any(len(span["token_ids"]) > 1 for span in structure["learning_spans"])


def test_unverified_reading_is_not_saved_as_dictionary_pronunciation():
    sentences, atoms = service._local_analysis("c", "食べた。")
    base = atoms[0].model_copy(update={"lemma_reading": None, "reading": "タベ"})
    selected = lexical_tokens([base], [])
    assert selected[0].reading == "" and base.reading == "タベ"


def test_dictionary_is_deduplicated_but_occurrence_offsets_stay_distinct():
    data = {"sentence": {"id": "s", "original": "猫と猫。"}, "unresolved_tokens": [
        {"id": "a", "surface": "猫", "lemma": "猫", "reading": "ネコ", "part_of_speech": "名詞", "start": 0, "end": 1},
        {"id": "b", "surface": "猫", "lemma": "猫", "reading": "ネコ", "part_of_speech": "名詞", "start": 2, "end": 3}]}
    compact, refs = compact_learning_batch([data])
    assert len(compact["lexicon"]) == 1 and refs["a"] == refs["b"]
    assert [value[2:4] for value in compact["sentences"][0]["lexical"]] == [[0, 1], [2, 3]]


def test_dictionary_output_is_expanded_without_repeating_api_basic_senses(monkeypatch):
    items = [{"sentence": {"id": "s", "original": "猫と猫。"}, "unresolved_tokens": [
        {"id": "a", "surface": "猫", "lemma": "猫", "reading": "ネコ", "part_of_speech": "名詞", "start": 0, "end": 1},
        {"id": "b", "surface": "猫", "lemma": "猫", "reading": "ネコ", "part_of_speech": "名詞", "start": 2, "end": 3}]}]
    compact, refs = compact_learning_batch(items)
    async def chat(*_args, **_kwargs):
        return {"dictionary": [[refs["a"], ["猫"]]], "results": [
            {"id": "s", "meaning": "猫和猫。", "contexts": [["a", "前面的猫"], ["b", "后面的猫"]], "unit_senses": []}]}
    monkeypatch.setattr(ai, "_chat_json", chat)
    rows = asyncio.run(ai.explain_sentences("user", items, "key", "url", "model"))
    assert rows[0]["words"] == [["a", "前面的猫", ["猫"]], ["b", "后面的猫", ["猫"]]]


def test_full_response_has_local_structures_unit_context_and_canonical_basic_sense(monkeypatch):
    sentences, atoms = service._local_analysis("c", "食べさせられなかった。")
    sentence = sentences[0]
    monkeypatch.setattr(service, "resolve_settings", lambda *_: ("key", "url", "model"))
    monkeypatch.setattr(service, "get_cached_response", lambda *_: None)
    monkeypatch.setattr(service, "set_cached_response", lambda *_: None)
    monkeypatch.setattr(service, "_entry_for_token", lambda *_: None)
    async def explain(_user, items, *_args, **_kwargs):
        assert len(items[0]["unresolved_tokens"]) == 1
        token = items[0]["unresolved_tokens"][0]
        assert token["lemma"] == "食べる" and token["reading"] == "タベル"
        unit = items[0]["learning_units"][0]
        assert unit[1] == "食べさせられなかった"
        return [{"id": sentence.id, "meaning": "没有被迫吃。", "words": [[token["id"], "吃", ["吃，进食"]]],
                 "unit_senses": [[unit[0], "没有被迫吃"], ["injected-span", "非法跨度"]]}]
    monkeypatch.setattr(service, "explain_sentences_with_ai", explain)
    result = asyncio.run(service._explain_batch(CONTEXT.user_id, ExplainBatchRequest(items=[{"sentence": sentence, "tokens": atoms}]), None))
    assert result.tokens == atoms
    assert result.lexemes[0].key.startswith("食べる|タベル|")
    assert result.lexemes[0].senses_zh == ["吃，进食"]
    assert result.learning_spans[0].source == "rule"
    assert [value.gloss_zh for value in result.learning_span_senses] == ["没有被迫吃"]
    assert result.annotations == []


def test_unit_senses_cache_remaps_new_sentence_span_ids(monkeypatch):
    sentences, atoms = service._local_analysis("c", "読ませました。")
    stored, calls = {}, []
    monkeypatch.setattr(service, "resolve_settings", lambda *_: ("key", "url", "model"))
    monkeypatch.setattr(service, "_entry_for_token", lambda *_: None)
    monkeypatch.setattr(service, "get_cached_response", lambda _user, key: stored.get(key))
    monkeypatch.setattr(service, "set_cached_response", lambda _user, key, _kind, _model, _version, value: stored.update({key: value}))
    async def explain(_user, items, *_args, **_kwargs):
        calls.append(items)
        token = items[0]["unresolved_tokens"][0]
        return [{"id": items[0]["sentence"]["id"], "meaning": "让其读了。", "words": [[token["id"], "读", ["阅读"]]],
                 "unit_senses": [[items[0]["learning_units"][0][0], "让其读了"]]}]
    monkeypatch.setattr(service, "explain_sentences_with_ai", explain)
    def run(sentence, tokens):
        return asyncio.run(service._explain_batch(CONTEXT.user_id, ExplainBatchRequest(items=[{"sentence": sentence, "tokens": tokens}]), None))
    original = run(sentences[0], atoms)
    shifted = sentences[0].model_copy(update={"id": "remapped"})
    moved_atoms = [token.model_copy(update={"sentence_id": shifted.id, "id": f"remapped-{index}"}) for index, token in enumerate(atoms)]
    remapped = run(shifted, moved_atoms)
    assert len(calls) == 1
    assert original.learning_span_senses[0].span_id != remapped.learning_span_senses[0].span_id
    assert remapped.learning_span_senses[0].gloss_zh == "让其读了"


def test_unit_meanings_are_persisted_in_source_revision_snapshot(corpus, monkeypatch):
    monkeypatch.setattr(service, "resolve_settings", lambda *_: ("key", "url", "model"))
    monkeypatch.setattr(service, "get_cached_response", lambda *_: None)
    monkeypatch.setattr(service, "set_cached_response", lambda *_: None)
    sentence = corpus[3]
    _, atoms = service._local_analysis("chapter", sentence.original)
    atoms = [token.model_copy(update={"sentence_id": sentence.id}) for token in atoms]
    async def explain(_user, items, *_args, **_kwargs):
        return [{"id": sentence.id, "meaning": "笑着说了。", "words": [],
                 "unit_senses": [[items[0]["learning_units"][0][0], "笑着"]]}]
    monkeypatch.setattr(service, "explain_sentences_with_ai", explain)
    result = asyncio.run(service._explain_batch(CONTEXT.user_id, ExplainBatchRequest(items=[{"sentence": sentence, "tokens": atoms}]), None))
    returned = linguistics.structure_results(CONTEXT.user_id, [sentence.id])["results"][0]
    assert returned["learning_span_senses"][0]["gloss_zh"] == "笑着"
    assert returned["analysis_manifest"] == result.analysis_manifest
