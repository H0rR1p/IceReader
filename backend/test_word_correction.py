import asyncio

import pytest
from fastapi import HTTPException
from .models import CorrectWordRequest, ExplainBatchRequest
from .modules.analysis import router, service
from .test_sentence_flow import CONTEXT


def fixture_sentence():
    sentences, tokens = service._local_analysis("chapter", "前文。姿を目にする。")
    sentence = sentences[1]
    token = next(token for token in tokens if token.sentence_id == sentence.id and token.surface == "目")
    return sentence, token


def test_correction_uses_context_and_ignores_old_sense(monkeypatch):
    sentence, token = fixture_sentence()
    monkeypatch.setattr(router, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    async def fake_correct(user, original, word, old, hint, *_args):
        assert original["original"] == "姿を目にする。"
        assert old == ["第几次"]
        assert hint == "这里是看见"
        return {"gloss": "目にする：看见", "senses": ["眼睛"]}
    monkeypatch.setattr(router, "correct_word", fake_correct)
    result = asyncio.run(router.correct_word_sense(CorrectWordRequest(
        sentence=sentence, token=token, current_senses=["第几次"], hint="这里是看见"), None, CONTEXT))
    assert result.context_sense.gloss_zh == "目にする：看见"
    assert result.lexeme.senses_zh == ["眼睛"]


def test_correction_rejects_wrong_anchor_and_propagates_rate_limit(monkeypatch):
    sentence, token = fixture_sentence()
    monkeypatch.setattr(router, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    bad = token.model_copy(update={"start": 0})
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.correct_word_sense(CorrectWordRequest(sentence=sentence, token=bad), None, CONTEXT))
    assert error.value.status_code == 422
    async def limited(*_):
        raise router.AiRateLimitError("限流", retry_after=7)
    monkeypatch.setattr(router, "correct_word", limited)
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.correct_word_sense(CorrectWordRequest(sentence=sentence, token=token), None, CONTEXT))
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "7"


def test_known_context_uses_ai_and_cache_remaps_token_ids(monkeypatch):
    sentence, token = fixture_sentence()
    stored = {}
    calls = []
    monkeypatch.setattr(service, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    monkeypatch.setattr(service, "_entry_for_token", lambda *_: {"senses_zh": ["第几次"], "source": "AI 补充释义"})
    monkeypatch.setattr(service, "get_cached_response", lambda _user, key: stored.get(key))
    monkeypatch.setattr(service, "set_cached_response", lambda _user, key, _kind, _model, _version, value: stored.update({key: value}))
    async def fake_explain(_user, items, *_args, **_kwargs):
        calls.append(items)
        assert items[0]["unresolved_tokens"] == []
        return [{"id": items[0]["sentence"]["id"], "meaning": "看到身影。", "words": [],
                 "contexts": [[items[0]["known_tokens"][0][0], "目にする：看见"]]}]
    monkeypatch.setattr(service, "explain_sentences_with_ai", fake_explain)
    def explain(s, t):
        return asyncio.run(service._explain_batch(CONTEXT.user_id, ExplainBatchRequest(items=[{"sentence": s, "tokens": [t]}]), None))
    first = explain(sentence, token)
    moved_sentence = sentence.model_copy(update={"id": "new-sentence"})
    moved_token = token.model_copy(update={"id": "new-token", "sentence_id": "new-sentence"})
    second = explain(moved_sentence, moved_token)
    assert len(calls) == 1
    assert first.context_senses[0].gloss_zh == "目にする：看见"
    assert second.context_senses[0].token_id == "new-token"
    assert second.context_senses[0].gloss_zh != "第几次"
    # A missing context must never silently become the first dictionary sense.
    result = service._result_for_sentence(ExplainBatchRequest(items=[{"sentence": sentence, "tokens": [token]}]).items[0],
        {token.id: {"senses_zh": ["第几次"]}}, {"meaning": "看到身影。"}, "none", "full")
    assert result.context_senses == []
    assert result.warnings
