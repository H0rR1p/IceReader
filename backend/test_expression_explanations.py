import asyncio
import copy
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.modules.linguistics import explanations as target, repository


@pytest.fixture
def explanation(monkeypatch):
    manifest = {"text_hash": "a" * 64, "version": "graph-test", "revision": 1}
    span = {"id": "node", "kind": "construction", "surface": "じゃないか", "start": 2, "end": 7,
            "unit_role": "ending", "status": "ambiguous", "candidates": [{"id": "confirmation", "status": "possible"}]}
    structure = {"analysis_manifest": manifest, "learning_spans": [span]}
    sentence = {"id": "sentence", "chapter_id": "chapter", "start": 0, "end": 8, "original": "学生じゃないか。"}
    saved, prompts = [], []
    context = SimpleNamespace(context_hash="context", prompt_data=lambda: {"preceding": ["その人は学生だ。"]})
    monkeypatch.setattr(target.service, "structure_results", lambda *_: {"results": [copy.deepcopy(structure)]})
    monkeypatch.setattr(target.service, "owned_sentence", lambda *_: (sentence, {}))
    monkeypatch.setattr(target, "build_contexts", lambda *_: {"sentence": context})
    monkeypatch.setattr(target, "resolve_settings", lambda *_: ("test-key", "https://example.invalid/v1", "test-model"))
    monkeypatch.setattr(target, "get_cached_response", lambda *_: None)
    monkeypatch.setattr(target, "set_cached_response", lambda *_: None)
    monkeypatch.setattr(target.repository, "merge_span_sense", lambda *args: saved.append(args))
    async def chat(*args, **kwargs):
        prompts.append(args[-1])
        return {"quote": "じゃないか", "gloss_zh": "是在确认对方是不是学生。"}
    monkeypatch.setattr(target.ai, "_chat_json", chat)
    request = target.ExpressionExplanationRequest(sentence_id="sentence", span_id="node",
        text_hash=manifest["text_hash"], version=manifest["version"], analysis_revision=1)
    return request, structure, context, saved, prompts


def test_targeted_meaning_preserves_ambiguity_and_owned_original(explanation):
    request, structure, _context, saved, prompts = explanation
    original = copy.deepcopy(structure)
    result = asyncio.run(target.explain_expression(request, "owner"))
    assert result["span_id"] == "node" and result["gloss_zh"] == "是在确认对方是不是学生。"
    assert "学生じゃないか。" in prompts[0] and "その人は学生だ。" in prompts[0]
    assert saved[0][0:2] == ("owner", "sentence")
    assert structure == original


def test_stale_version_does_not_call_ai(explanation):
    request, _structure, _context, saved, prompts = explanation
    with pytest.raises(HTTPException) as error:
        asyncio.run(target.explain_expression(request.model_copy(update={"version": "old"}), "owner"))
    assert error.value.status_code == 409 and not prompts and not saved


def test_changed_context_during_generation_does_not_save(explanation, monkeypatch):
    request, _structure, context, saved, _prompts = explanation
    async def chat(*_args, **_kwargs):
        context.context_hash = "changed"
        return {"quote": "じゃないか", "gloss_zh": "不能保存旧前文的解释。"}
    monkeypatch.setattr(target.ai, "_chat_json", chat)
    with pytest.raises(HTTPException) as error:
        asyncio.run(target.explain_expression(request, "owner"))
    assert error.value.status_code == 409 and not saved


def test_foreign_sentence_is_rejected_before_ai(explanation, monkeypatch):
    request, _structure, _context, saved, prompts = explanation
    def denied(*_args):
        raise HTTPException(404, "句子不存在或无权访问")
    monkeypatch.setattr(target.service, "structure_results", denied)
    with pytest.raises(HTTPException) as error:
        asyncio.run(target.explain_expression(request, "other"))
    assert error.value.status_code == 404 and not saved and not prompts


def test_changed_internal_choice_during_generation_does_not_save(explanation, monkeypatch):
    request, structure, _context, saved, _prompts = explanation
    child = {"id": "inner", "surface": "ない", "start": 4, "end": 6,
             "override_revision": 1, "choice_id": "negative"}
    structure["learning_spans"].append(child)
    async def chat(*_args, **_kwargs):
        child["override_revision"] = 2
        child["choice_id"] = None
        return {"quote": "じゃないか", "gloss_zh": "内部确认变化后不能保存旧解释。"}
    monkeypatch.setattr(target.ai, "_chat_json", chat)
    with pytest.raises(HTTPException) as error:
        asyncio.run(target.explain_expression(request, "owner"))
    assert error.value.status_code == 409 and not saved


def test_confirming_a_candidate_clears_old_context_meanings(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "STORE_PATH", tmp_path / "linguistics.sqlite3")
    manifest = {"text_hash": "hash", "version": "graph", "revision": 1}
    repository.merge_span_sense("user", "sentence", {"span_id": "parent", "gloss_zh": "旧解释"}, manifest, "context")
    repository.save_span_override("user", "sentence", "child", "hash", "graph", 0,
                                  {"choice_id": "negative", "source": "user"})
    assert repository.load_span_senses("user", "sentence", "hash", "graph", 1, "context") == []


def test_incremental_meanings_retain_other_nodes_and_reject_stale_context(tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "STORE_PATH", tmp_path / "linguistics.sqlite3")
    manifest = {"text_hash": "hash", "version": "graph", "revision": 1}
    repository.merge_span_sense("user", "sentence", {"span_id": "one", "gloss_zh": "第一处"}, manifest, "context")
    repository.merge_span_sense("user", "sentence", {"span_id": "two", "gloss_zh": "第二处"}, manifest, "context")
    assert {row["span_id"] for row in repository.load_span_senses("user", "sentence", "hash", "graph", 1, "context")} == {"one", "two"}
    repository.merge_span_sense("user", "sentence", {"span_id": "new", "gloss_zh": "新前文"}, manifest, "new-context")
    assert not repository.load_span_senses("user", "sentence", "hash", "graph", 1, "context")
    assert [row["span_id"] for row in repository.load_span_senses("user", "sentence", "hash", "graph", 1, "new-context")] == ["new"]
