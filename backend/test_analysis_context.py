import asyncio

import pytest
from fastapi import HTTPException

from .models import LibraryPatch, SentenceOut, CorrectWordRequest, TokenOut
from .modules.analysis import context as contexts, router
from .modules.linguistics.models import ContextPolicy
from .modules.library import repository as library_store
from .test_sentence_flow import CONTEXT


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    repository = library_store
    monkeypatch.setattr(repository, "LIBRARY_PATH", tmp_path / "library.sqlite3")
    monkeypatch.setattr(contexts.linguistics_store, "STORE_PATH", tmp_path / "linguistics.sqlite3")
    original = ["ユキさんが来た。", "その人は教師だ。", "隣に座った。", "笑って話した。", "また会う約束をした。"]
    position, rows = 0, []
    for index, text in enumerate(original):
        rows.append({"id": f"s{index}", "chapter_id": "chapter", "start": position,
                     "end": position + len(text), "original": text, "translation_zh": f"译文{index}",
                     "explanation_status": "complete" if index % 2 == 0 else "idle",
                     "analysis_revision": 1})
        position += len(text)
    repository.apply_library_patch(CONTEXT.user_id, LibraryPatch(upserts={
        "books": [{"id": "book", "title": "原创上下文验收样例"}],
        "chapters": [{"id": "chapter", "bookId": "book", "order": 0, "text": "".join(original), "analysis_revision": 1}],
        "sentences": rows,
    }))
    return [SentenceOut.model_validate(row) for row in rows]


def test_window_reads_complete_original_order_including_completed_sentences(corpus):
    result = contexts.build_contexts(CONTEXT.user_id, [corpus[1], corpus[3]])
    assert [ref["sentence_id"] for ref in result[corpus[3].id].preceding_sentence_refs] == ["s1", "s2"]
    assert result[corpus[3].id].optional_translation_refs == []
    assert result[corpus[1].id].preceding_sentence_refs[0]["original"] == corpus[0].original


def test_manual_and_background_fixed_windows_do_not_depend_on_request_batch(corpus):
    manual = contexts.build_contexts(CONTEXT.user_id, [corpus[3]])["s3"]
    background = contexts.build_contexts(CONTEXT.user_id, [corpus[1], corpus[3]])["s3"]
    assert manual.context_hash == background.context_hash
    assert manual.prompt_data() == background.prompt_data()


def test_legacy_strings_cannot_override_owned_original(corpus):
    result = contexts.build_contexts(CONTEXT.user_id, [corpus[3]], legacy_context=["错误的旧待办队列。"])["s3"]
    assert [ref["sentence_id"] for ref in result.preceding_sentence_refs] == ["s1", "s2"]
    with pytest.raises(HTTPException) as failure:
        contexts.build_contexts(CONTEXT.user_id, [corpus[3]], ContextPolicy(), ["混用。"])
    assert failure.value.status_code == 422


def test_foreign_ids_and_modified_reference_are_rejected(corpus):
    with pytest.raises(HTTPException) as failure:
        contexts.build_contexts("another-user", [corpus[0]])
    assert failure.value.status_code == 404
    with pytest.raises(HTTPException) as stale:
        contexts.build_contexts(CONTEXT.user_id, [corpus[3].model_copy(update={"original": "改写。"})])
    assert stale.value.status_code == 409


def test_reference_policy_requires_persisted_target(corpus):
    missing = corpus[0].model_copy(update={"id": "missing"})
    with pytest.raises(HTTPException) as failure:
        contexts.build_contexts(CONTEXT.user_id, [missing], ContextPolicy())
    assert failure.value.status_code == 404
    with pytest.raises(HTTPException) as mixed:
        contexts.build_contexts(CONTEXT.user_id, [corpus[0], missing])
    assert mixed.value.status_code == 422


def test_budget_keeps_nearest_whole_sentences_and_zero_is_zero(corpus):
    result = contexts.build_contexts(CONTEXT.user_id, [corpus[3]], ContextPolicy(token_budget=70))["s3"]
    assert [ref["sentence_id"] for ref in result.preceding_sentence_refs] == ["s2"]
    assert result.truncated
    empty = contexts.build_contexts(CONTEXT.user_id, [corpus[3]], ContextPolicy(preceding_sentences=0))["s3"]
    assert empty.preceding_sentence_refs == [] and not empty.truncated
    assert empty.context_hash != result.context_hash


def test_only_confirmed_translations_are_soft_hints(corpus):
    repository = library_store
    current = corpus[2].model_dump()
    current.update(translation_quality_status="confirmed", translation_version=2)
    repository.apply_library_patch(CONTEXT.user_id, LibraryPatch(upserts={"sentences": [current]}))
    policy = ContextPolicy(include_previous_translation=True, token_budget=2000)
    result = contexts.build_contexts(CONTEXT.user_id, [corpus[3]], policy)["s3"]
    assert result.optional_translation_refs == [{"sentence_id": "s2", "translation_zh": "译文2", "quality_status": "confirmed", "translation_version": 2}]
    first_hash = result.context_hash
    current.update(translation_zh="人工修订", translation_version=3)
    repository.apply_library_patch(CONTEXT.user_id, LibraryPatch(upserts={"sentences": [current]}))
    updated = contexts.build_contexts(CONTEXT.user_id, [corpus[3]], policy)["s3"]
    assert first_hash != updated.context_hash
    default = contexts.build_contexts(CONTEXT.user_id, [corpus[3]])["s3"]
    assert default.optional_translation_refs == []


def test_chapter_and_explicit_scene_boundaries_are_respected(corpus):
    repository = library_store
    last = corpus[-1].end
    next_sentence = {"id": "next", "chapter_id": "chapter2", "start": 0, "end": 3,
                     "original": "次だ。", "translation_zh": ""}
    repository.apply_library_patch(CONTEXT.user_id, LibraryPatch(upserts={
        "chapters": [{"id": "chapter2", "bookId": "book", "order": 1, "text": "次だ。"}],
        "sentences": [next_sentence],
    }))
    target = SentenceOut.model_validate(next_sentence)
    assert contexts.build_contexts(CONTEXT.user_id, [target])["next"].preceding_sentence_refs == []
    crossed = contexts.build_contexts(CONTEXT.user_id, [target], ContextPolicy(cross_chapter=True))["next"]
    assert [ref["sentence_id"] for ref in crossed.preceding_sentence_refs] == ["s3", "s4"]
    repository.apply_library_patch(CONTEXT.user_id, LibraryPatch(upserts={"chapters": [
        {"id": "chapter", "bookId": "book", "order": 0, "text": "".join(row.original for row in corpus),
         "blocks": [{"type": "separator", "start": corpus[2].end, "end": corpus[2].end}], "analysis_revision": 1}
    ]}))
    assert contexts.build_contexts(CONTEXT.user_id, [corpus[3]])["s3"].preceding_sentence_refs == []


def test_resegment_revision_invalidates_context_without_resetting_history(corpus):
    before = contexts.build_contexts(CONTEXT.user_id, [corpus[3]])["s3"].context_hash
    library_store.apply_library_patch(CONTEXT.user_id, LibraryPatch(upserts={"chapters": [
        {"id": "chapter", "bookId": "book", "order": 0, "text": "".join(row.original for row in corpus), "analysis_revision": 2}
    ]}))
    after = contexts.build_contexts(CONTEXT.user_id, [corpus[3]])["s3"].context_hash
    assert before != after


def test_persisted_policy_is_default_and_scoped_to_user(corpus):
    contexts.linguistics_store.save_preferences(CONTEXT.user_id, {"context_policy": {"preceding_sentences": 4}})
    result = contexts.build_contexts(CONTEXT.user_id, [corpus[4]])["s4"]
    assert len(result.preceding_sentence_refs) == 4
    explicit = contexts.build_contexts(CONTEXT.user_id, [corpus[4]], ContextPolicy(preceding_sentences=0))["s4"]
    assert explicit.preceding_sentence_refs == []
    assert contexts.linguistics_store.load_preferences("another-user")["context_policy"]["preceding_sentences"] == 2


def test_word_correction_uses_same_server_window(corpus, monkeypatch):
    sentence = corpus[3]
    word = TokenOut(id="word", sentence_id=sentence.id, start=0, end=2, surface="笑っ", lemma="笑う", reading="ワラッ", part_of_speech="動詞", is_content=True)
    observed = {}
    monkeypatch.setattr(router, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    async def correct(_user, data, *_args):
        observed.update(data["analysis_context"])
        return {"gloss": "笑着", "senses": ["笑"]}
    monkeypatch.setattr(router, "correct_word", correct)
    asyncio.run(router.correct_word_sense(CorrectWordRequest(sentence=sentence, token=word), None, CONTEXT))
    assert observed == contexts.build_contexts(CONTEXT.user_id, [sentence])[sentence.id].prompt_data()
