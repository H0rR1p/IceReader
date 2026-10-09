import asyncio
import time

import pytest
from fastapi import HTTPException

from . import app as app_module
from .modules.analysis import router as analysis_router
from .modules.analysis import service as analysis_service
from .models import ContentBlock, ExplainBatchRequest, ExplainSentenceRequest, ImportedBook, ImportedChapter, SegmentChapterRequest
from .core.request_context import RequestContext


USER_ID = "user-1"
CONTEXT = RequestContext(USER_ID, "session-1", "device-1", "local", "request-1")


def test_import_does_not_segment_book(monkeypatch):
    async def fail_if_called(*_):
        raise AssertionError("import must not call AI sentence review")

    monkeypatch.setattr(analysis_service, "review_sentence_boundaries", fail_if_called)
    book = ImportedBook(title="测试", chapters=[ImportedChapter(
        id="chapter-1", title="正文", order=0, text="彼は来た。私は帰った。",
    )])

    assert book.chapters[0].sentences == []
    assert book.chapters[0].tokens == []


def test_segment_one_chapter_on_demand(monkeypatch):
    async def fail_if_called(*_):
        raise AssertionError("unambiguous punctuation must stay local")

    monkeypatch.setattr(analysis_service, "review_sentence_boundaries", fail_if_called)
    chapter = ImportedChapter(id="chapter-1", title="正文", order=0, text="彼は来た。私は帰った。")
    segmented, warning = asyncio.run(analysis_service._segment_chapter(
        USER_ID, chapter, "key", "https://example.invalid", "model", asyncio.Semaphore(3),
    ))

    assert [sentence.original for sentence in segmented.sentences] == ["彼は来た。", "私は帰った。"]
    assert segmented.tokens
    assert segmented.segmentation_source == "local-fallback"
    assert warning is None


def test_segment_joins_visual_line_break_without_ai(monkeypatch):
    async def fail_if_called(*_):
        raise AssertionError("visual line wrapping must be joined locally")

    monkeypatch.setattr(analysis_service, "review_sentence_boundaries", fail_if_called)
    text = "彼はまだ\n帰っていない。"
    chapter = ImportedChapter(
        id="chapter-1", title="正文", order=0, text=text,
        blocks=[ContentBlock(id="p1", type="paragraph", start=0, end=len(text), text=text)],
    )
    segmented, warning = asyncio.run(analysis_service._segment_chapter(
        USER_ID, chapter, "key", "https://example.invalid", "model", asyncio.Semaphore(3),
    ))

    assert [sentence.original for sentence in segmented.sentences] == [text]
    assert warning is None


def test_segment_joins_aozora_style_paragraph_spans(monkeypatch):
    async def fail_if_called(*_):
        raise AssertionError("Aozora visual spans must not spend AI tokens")

    monkeypatch.setattr(analysis_service, "review_sentence_boundaries", fail_if_called)
    text = "吾輩は\n猫である。"
    chapter = ImportedChapter(
        id="chapter-1", title="正文", order=0, text=text,
        blocks=[
            ContentBlock(id="p1", type="paragraph", start=0, end=3, text="吾輩は"),
            ContentBlock(id="p2", type="paragraph", start=4, end=len(text), text="猫である。"),
        ],
    )
    segmented, warning = asyncio.run(analysis_service._segment_chapter(
        USER_ID, chapter, "key", "https://example.invalid", "model", asyncio.Semaphore(3),
    ))

    assert [sentence.original for sentence in segmented.sentences] == [text]
    assert warning is None


def test_segment_rate_limit_reaches_endpoint_with_retry_after(monkeypatch):
    from .nlp import SentenceSpan

    text = "前半\n後半"
    spans = [SentenceSpan(0, 2, "前半", True), SentenceSpan(3, 5, "後半", False)]
    monkeypatch.setattr(analysis_service, "_chapter_sentence_spans", lambda _chapter: spans)
    monkeypatch.setattr(analysis_router, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))

    async def rate_limited(*_args, **_kwargs):
        raise analysis_service.AiRateLimitError("slow down", retry_after=9)

    monkeypatch.setattr(analysis_service, "review_sentence_boundaries", rate_limited)
    request = SegmentChapterRequest(
        chapter_id="chapter-1", text=text,
        blocks=[ContentBlock(id="p1", type="paragraph", start=0, end=len(text), text=text)],
    )

    with pytest.raises(HTTPException) as error:
        asyncio.run(analysis_router.segment_chapter(request, None, CONTEXT))

    assert error.value.status_code == 429
    assert error.value.headers == {"Retry-After": "9"}


def test_structural_heading_does_not_merge_into_paragraph():
    text = "第一章\n吾輩は猫である。"
    chapter = ImportedChapter(
        id="chapter-1", title="正文", order=0, text=text,
        blocks=[
            ContentBlock(id="h1", type="heading", start=0, end=3, text="第一章"),
            ContentBlock(id="p1", type="paragraph", start=4, end=len(text), text="吾輩は猫である。"),
        ],
    )
    segmented, _ = asyncio.run(analysis_service._segment_chapter(
        USER_ID, chapter, "", "https://example.invalid", "model", asyncio.Semaphore(3),
    ))
    assert [sentence.original for sentence in segmented.sentences] == ["第一章", "吾輩は猫である。"]


def test_reviewed_merge_chain_has_fragment_and_length_guards():
    from .nlp import SentenceSpan

    text = "\n".join("短い行" for _ in range(9))
    spans = []
    cursor = 0
    for value in text.splitlines():
        spans.append(SentenceSpan(cursor, cursor + len(value), value, True))
        cursor += len(value) + 1
    merge_ids = {f"chapter-1:{index}" for index in range(len(spans) - 1)}
    merged = analysis_service._merge_reviewed_spans("chapter-1", text, spans, merge_ids)

    assert len(merged) == 2
    assert merged[0].text.count("短い行") == analysis_service.MAX_MERGED_FRAGMENTS


def test_reviewed_merge_never_crosses_terminal_punctuation():
    from .nlp import SentenceSpan

    text = "終わり。\n次の文"
    spans = [SentenceSpan(0, 4, "終わり。", True), SentenceSpan(5, len(text), "次の文", False)]
    merged = analysis_service._merge_reviewed_spans("chapter-1", text, spans, {"chapter-1:0"})
    assert [item.text for item in merged] == ["終わり。", "次の文"]


def test_local_analysis_async_does_not_block_event_loop(monkeypatch):
    def slow_analysis(*_):
        time.sleep(0.08)
        return [], []

    monkeypatch.setattr(analysis_service, "_local_analysis", slow_analysis)

    async def run():
        task = asyncio.create_task(analysis_service._local_analysis_async("chapter-1", "本文"))
        started = time.perf_counter()
        await asyncio.sleep(0.02)
        event_loop_delay = time.perf_counter() - started
        await task
        return event_loop_delay

    assert asyncio.run(run()) < 0.06


def test_lifespan_closes_shared_ai_client_even_on_error(monkeypatch):
    closed = []

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(app_module, "close_http_client", fake_close)

    async def run():
        try:
            async with app_module.lifespan(app_module.app):
                raise RuntimeError("stop")
        except RuntimeError:
            pass

    asyncio.run(run())

    assert closed == [True]


def test_sentence_boundary_review_has_strict_output_budget(monkeypatch):
    from . import ai as ai_module

    observed = {}

    async def fake_chat(*args, **kwargs):
        observed.update(kwargs)
        return {"merge": ["b1"]}

    monkeypatch.setattr(ai_module, "_chat_json", fake_chat)
    result = asyncio.run(ai_module.review_sentence_boundaries(
        USER_ID, [{"id": "b1", "left": "文の途中で", "right": "改行した。"}],
        "key", "https://example.invalid", "model",
    ))

    assert result == {"b1"}
    assert observed["max_tokens"] == 64
    assert observed["timeout"] == 45.0


def test_explain_sentence_uses_dictionary_then_ai_fallback(monkeypatch):
    sentences, tokens = analysis_service._local_analysis("chapter-1", "図書館へ行く。")
    sentence = sentences[0]
    content = [token for token in tokens if token.is_content and token.role == "lexical"]
    monkeypatch.setattr(analysis_service, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    monkeypatch.setattr(analysis_service, "_entry_for_token", lambda _user_id, token: {
        "senses_zh": ["图书馆"], "source": "测试词典",
    } if token.lemma == "図書館" else None)

    async def fake_explain(_user_id, items, *_, **_kwargs):
        unknown = items[0]["unresolved_tokens"]
        assert all(token["lemma"] != "図書館" for token in unknown)
        return [{
            "id": sentence.id, "meaning": "去图书馆。",
            "words": [[token["id"], f"{token['surface']}的语境义", [f"{token['lemma']}的补充义"]] for token in unknown],
            "contexts": [[value[0], "图书馆"] for value in items[0]["known_tokens"]],
            "annotations": [],
        }]

    monkeypatch.setattr(analysis_service, "explain_sentences_with_ai", fake_explain)
    monkeypatch.setattr(analysis_service, "get_cached_response", lambda *_: None)
    monkeypatch.setattr(analysis_service, "set_cached_response", lambda *_: None)
    request = ExplainSentenceRequest(sentence=sentence, tokens=tokens)

    result = asyncio.run(analysis_router.explain_sentence(request, None, CONTEXT))

    assert result.sentences[0].translation_zh == "去图书馆。"
    assert len(result.context_senses) == len(content)
    sources = {entry.lemma: entry.source for entry in result.lexemes}
    assert sources["図書館"] == "测试词典"
    assert "AI 补充释义" in sources.values()


def test_grammar_analysis_returns_named_structure_without_retranslating(monkeypatch):
    sentences, tokens = analysis_service._local_analysis("chapter-1", "彼は来た。")
    sentence = sentences[0].model_copy(update={"translation_zh": "他来了。"})
    monkeypatch.setattr(analysis_service, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))

    async def fake_explain(_user_id, items, *_args, **_kwargs):
        assert items[0]["unresolved_tokens"] == []
        return [{
            "id": sentence.id,
            "annotations": [["grammar", 1, 2, "は", "Nは", "主题提示结构。"]],
        }]

    monkeypatch.setattr(analysis_service, "explain_sentences_with_ai", fake_explain)
    monkeypatch.setattr(analysis_service, "get_cached_response", lambda *_: None)
    monkeypatch.setattr(analysis_service, "set_cached_response", lambda *_: None)
    request = ExplainSentenceRequest(sentence=sentence, tokens=tokens, annotation_mode="grammar")

    result = asyncio.run(analysis_router.explain_sentence(request, None, CONTEXT))

    assert result.sentences[0].translation_zh == "他来了。"
    assert result.context_senses == []
    assert len(result.annotations) == 1
    assert result.annotations[0].type == "grammar"
    assert result.annotations[0].structure == "Nは"


def test_fast_meaning_mode_does_not_request_or_replace_lexical_data(monkeypatch):
    sentences, tokens = analysis_service._local_analysis("chapter-1", "彼は来た。")
    sentence = sentences[0]
    monkeypatch.setattr(analysis_service, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    monkeypatch.setattr(
        analysis_service,
        "_entry_for_token",
        lambda _user_id, _token: (_ for _ in ()).throw(AssertionError("fast mode must not query dictionaries")),
    )

    async def fake_explain(_user_id, items, *_args, **kwargs):
        assert items[0]["unresolved_tokens"] == []
        assert kwargs["detail_mode"] == "meaning"
        assert kwargs["context_before"] == ["前の文。"]
        return [{"id": sentence.id, "meaning": "他来了。", "words": [], "annotations": []}]

    monkeypatch.setattr(analysis_service, "explain_sentences_with_ai", fake_explain)
    monkeypatch.setattr(analysis_service, "get_cached_response", lambda *_: None)
    monkeypatch.setattr(analysis_service, "set_cached_response", lambda *_: None)

    result = asyncio.run(analysis_router.explain_sentence(ExplainSentenceRequest(
        sentence=sentence, tokens=tokens, detail_mode="meaning", context_before=["前の文。"],
    ), None, CONTEXT))

    assert result.sentences[0].translation_zh == "他来了。"
    assert result.sentences[0].explanation_detail == "meaning"
    assert result.context_senses == []
    assert result.lexemes == []


def test_truncated_batch_is_split_and_missing_rows_are_retried(monkeypatch):
    rows = [
        {"sentence": {"id": "s1", "original": "一。"}, "unresolved_tokens": []},
        {"sentence": {"id": "s2", "original": "二。"}, "unresolved_tokens": []},
        {"sentence": {"id": "s3", "original": "三。"}, "unresolved_tokens": []},
    ]
    calls = []

    async def fake_explain(_user_id, items, *_args, **_kwargs):
        ids = [item["sentence"]["id"] for item in items if item.get("generate", True)]
        calls.append(ids)
        if ids == ["s1", "s2", "s3"]:
            raise RuntimeError("AI 输出达到 token 上限，JSON 未完成")
        if ids == ["s2", "s3"]:
            return [{"id": "s2", "meaning": "二"}]
        if ids == ["s3"]:
            assert items[0]["sentence"]["id"] == "s2" and items[0]["generate"] is False
        return [{"id": item_id, "meaning": item_id} for item_id in ids]

    monkeypatch.setattr(analysis_service, "explain_sentences_with_ai", fake_explain)
    result = asyncio.run(analysis_service._fetch_ai_rows_resilient(
        USER_ID, rows, "key", "url", "model", "none", "meaning", [],
    ))

    assert {row["id"] for row in result} == {"s1", "s2", "s3"}
    assert calls == [["s1", "s2", "s3"], ["s1"], ["s2", "s3"], ["s3"]]


def test_rate_limit_passes_through_batch_wrapper_with_retry_after(monkeypatch):
    sentences, tokens = analysis_service._local_analysis("chapter-1", "彼は来た。")
    request = ExplainBatchRequest(items=[{"sentence": sentences[0], "tokens": tokens}], detail_mode="meaning")
    monkeypatch.setattr(analysis_service, "resolve_settings", lambda *_: ("key", "https://example.invalid", "model"))
    monkeypatch.setattr(analysis_service, "get_cached_response", lambda *_: None)

    async def rate_limited(*_args, **_kwargs):
        raise analysis_service.AiRateLimitError("slow down", retry_after=7)

    monkeypatch.setattr(analysis_service, "_fetch_ai_rows_resilient", rate_limited)

    with pytest.raises(HTTPException) as error:
        asyncio.run(analysis_router.explain_sentence_batch(request, None, CONTEXT))

    assert error.value.status_code == 429
    assert error.value.headers == {"Retry-After": "7"}
