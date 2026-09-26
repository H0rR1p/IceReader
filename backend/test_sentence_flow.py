import asyncio

from . import app as app_module
from .models import ExplainSentenceRequest, ImportedBook, ImportedChapter


def test_import_does_not_segment_book(monkeypatch):
    async def fail_if_called(*_):
        raise AssertionError("import must not call AI sentence review")

    monkeypatch.setattr(app_module, "review_sentence_boundaries", fail_if_called)
    book = ImportedBook(title="测试", chapters=[ImportedChapter(
        id="chapter-1", title="正文", order=0, text="彼は来た。私は帰った。",
    )])

    assert book.chapters[0].sentences == []
    assert book.chapters[0].tokens == []


def test_segment_one_chapter_on_demand(monkeypatch):
    async def keep_boundaries(*_):
        return set()

    monkeypatch.setattr(app_module, "review_sentence_boundaries", keep_boundaries)
    chapter = ImportedChapter(id="chapter-1", title="正文", order=0, text="彼は来た。私は帰った。")
    segmented, warning = asyncio.run(app_module._segment_chapter(
        chapter, "key", "https://example.invalid", "model", asyncio.Semaphore(3),
    ))

    assert [sentence.original for sentence in segmented.sentences] == ["彼は来た。", "私は帰った。"]
    assert segmented.tokens
    assert segmented.segmentation_source == "ai-reviewed"
    assert warning is None


def test_explain_sentence_uses_dictionary_then_ai_fallback(monkeypatch):
    sentences, tokens = app_module._local_analysis("chapter-1", "図書館へ行く。")
    sentence = sentences[0]
    content = [token for token in tokens if token.is_content]
    monkeypatch.setattr(app_module, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    monkeypatch.setattr(app_module, "_entry_for_token", lambda token: {
        "senses_zh": ["图书馆"], "source": "测试词典",
    } if token.lemma == "図書館" else None)

    async def fake_explain(sentence_row, token_rows, entries, *_):
        rows = []
        for token in token_rows:
            if not token["is_content"]:
                continue
            matched = entries[token["id"]]
            rows.append({
                "token_id": token["id"],
                "gloss_zh": "图书馆" if matched else f"{token['surface']}的语境义",
                "fallback_senses_zh": [] if matched else [f"{token['lemma']}的补充义"],
            })
        return {"sentence_id": sentence_row["id"], "meaning_zh": "去图书馆。", "token_senses": rows, "annotations": []}

    monkeypatch.setattr(app_module, "explain_sentence_with_ai", fake_explain)
    request = ExplainSentenceRequest(sentence=sentence, tokens=tokens)

    result = asyncio.run(app_module.explain_sentence(request))

    assert result.sentences[0].translation_zh == "去图书馆。"
    assert len(result.context_senses) == len(content)
    sources = {entry.lemma: entry.source for entry in result.lexemes}
    assert sources["図書館"] == "测试词典"
    assert "AI 补充释义" in sources.values()
