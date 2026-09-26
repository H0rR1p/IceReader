import asyncio

from . import app as app_module
from .models import ExplainSentenceRequest, ImportedBook, ImportedChapter


def test_import_prepares_stable_sentences_and_tokens_without_key(monkeypatch):
    monkeypatch.setattr(app_module, "resolve_settings", lambda *_: ("", "https://example.invalid", "model"))
    book = ImportedBook(title="测试", chapters=[ImportedChapter(
        id="chapter-1", title="正文", order=0, text="彼は来た。私は帰った。",
    )])

    prepared = asyncio.run(app_module._prepare_imported_book(book))

    chapter = prepared.chapters[0]
    assert [sentence.original for sentence in chapter.sentences] == ["彼は来た。", "私は帰った。"]
    assert chapter.tokens
    assert chapter.segmentation_source == "local-fallback"
    assert prepared.import_report.segmentation_warnings


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
