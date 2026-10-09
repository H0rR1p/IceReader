import asyncio

from fastapi import HTTPException

from ...ai import (
    AiRateLimitError,
    PROMPT_VERSION,
    explain_sentences as explain_sentences_with_ai,
    review_sentence_boundaries,
)
from ...ai_store import get_cached_response, make_cache_key, set_cached_response
from ...dictionary_store import lookup
from ...models import (
    AnalyzeResponse,
    AnnotationOut,
    ContextSenseOut,
    ExplainBatchRequest,
    ImportedChapter,
    LexemeOut,
    LearningSpanSenseOut,
    SentenceOut,
    TokenOut,
)
from ...modules.library.service import resolve_personal_lexeme
from ...nlp import (
    CLOSERS,
    SENTENCE_END,
    SentenceSpan,
    lexeme_key,
    split_sentences,
    stable_id,
    tokenize_sentence,
)
from ...settings_store import resolve_settings
from .context import build_contexts, dependency_hash, validate_tokens
from .prompt_payloads import visible_batch_hash, lexical_tokens, learning_units
from ..linguistics import service as linguistics_service
from ..linguistics import repository as linguistics_store


def _local_analysis(chapter_id: str, text: str, spans=None) -> tuple[list[SentenceOut], list[TokenOut]]:
    sentences: list[SentenceOut] = []
    tokens: list[TokenOut] = []
    for index, span in enumerate(spans or split_sentences(text)):
        sentence_id = stable_id("sent", f"{chapter_id}:{index}:{span.start}:{span.text}")
        sentences.append(SentenceOut(
            id=sentence_id, chapter_id=chapter_id, start=span.start, end=span.end,
            original=span.text, translation_zh="", explanation_status="idle",
        ))
        tokens.extend(TokenOut(**{key: value for key, value in token.items() if key != "lexeme_key"})
                      for token in tokenize_sentence(sentence_id, span.text))
    return sentences, tokens


async def _local_analysis_async(chapter_id: str, text: str, spans=None) -> tuple[list[SentenceOut], list[TokenOut]]:
    """Keep Sudachi and long chapter analysis off the ASGI event loop."""
    return await asyncio.to_thread(_local_analysis, chapter_id, text, spans)


def _chapter_sentence_spans(chapter: ImportedChapter) -> list[SentenceSpan]:
    # Paragraph-to-paragraph newlines are layout hints in many Aozora/Kobo
    # books. Structural content still forms a hard boundary.
    hard_boundaries: set[int] = set()
    for block in chapter.blocks:
        if block.type != "paragraph":
            hard_boundaries.update((block.start, block.end))
    return split_sentences(chapter.text, {value for value in hard_boundaries if 0 < value < len(chapter.text)})


MAX_MERGED_FRAGMENTS = 6
MAX_MERGED_CHARS = 400


def _ends_with_terminal(value: str) -> bool:
    stripped = value.rstrip().rstrip("".join(CLOSERS)).rstrip()
    return bool(stripped and stripped[-1] in SENTENCE_END)


def _merge_reviewed_spans(
    chapter_id: str, text: str, spans: list[SentenceSpan], merge_ids: set[str],
) -> list[SentenceSpan]:
    """Apply reviewed joins with guards against cascading false positives."""
    if not spans:
        return []
    merged: list[SentenceSpan] = []
    index = 0
    while index < len(spans):
        start, end = spans[index].start, spans[index].end
        fragment_count = 1
        while index < len(spans) - 1:
            boundary_id = f"{chapter_id}:{index}"
            proposed_end = spans[index + 1].end
            if boundary_id not in merge_ids:
                break
            if fragment_count >= MAX_MERGED_FRAGMENTS or proposed_end - start > MAX_MERGED_CHARS:
                break
            if _ends_with_terminal(text[start:end]):
                break
            index += 1
            end = proposed_end
            fragment_count += 1
        merged.append(SentenceSpan(start=start, end=end, text=text[start:end].strip()))
        index += 1
    return merged


def _candidate_batches(rows: list[dict], max_items: int = 20, max_chars: int = 4_000) -> list[list[dict]]:
    batches: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for row in rows:
        row_size = len(row.get("left", "")) + len(row.get("right", ""))
        if current and (len(current) >= max_items or size + row_size > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(row)
        size += row_size
    if current:
        batches.append(current)
    return batches


async def _segment_chapter(user_id: str, chapter: ImportedChapter, api_key: str, base_url: str, model: str, semaphore: asyncio.Semaphore) -> tuple[ImportedChapter, str | None]:
    spans = await asyncio.to_thread(_chapter_sentence_spans, chapter)
    if not spans:
        chapter.segmentation_source = "empty"
        return chapter, None
    text_ranges = [(block.start, block.end) for block in chapter.blocks if block.text and block.end > block.start]
    candidates: list[dict] = []
    for index, span in enumerate(spans):
        next_span = spans[index + 1] if index + 1 < len(spans) else None
        same_block = any(start <= span.start and next_span and next_span.end <= end for start, end in text_ranges)
        if next_span and span.uncertain_after and same_block:
            candidates.append({
                "id": f"{chapter.id}:{index}",
                "left": span.text[-96:],
                "right": next_span.text[:96],
                "span_index": index,
            })
    if not candidates or not api_key:
        chapter.sentences, chapter.tokens = await _local_analysis_async(chapter.id, chapter.text, spans)
        chapter.segmentation_source = "local-fallback"
        warning = "未配置 API Key，可疑换行已保留为句界" if candidates and not api_key else None
        return chapter, warning
    merge_ids: set[str] = set()
    async def review_batch(batch: list[dict]) -> set[str]:
        async with semaphore:
            return await review_sentence_boundaries(user_id, batch, api_key, base_url, model)

    try:
        for result in await asyncio.gather(*(review_batch(batch) for batch in _candidate_batches(candidates))):
            merge_ids.update(result)
    except AiRateLimitError:
        raise
    except Exception as exc:
        chapter.sentences, chapter.tokens = await _local_analysis_async(chapter.id, chapter.text, spans)
        chapter.segmentation_source = "local-fallback"
        return chapter, f"{chapter.title} 的 AI 句界审校失败，已使用本地边界：{exc}"

    merged = _merge_reviewed_spans(chapter.id, chapter.text, spans, merge_ids)
    chapter.sentences, chapter.tokens = await _local_analysis_async(chapter.id, chapter.text, merged)
    chapter.segmentation_source = "ai-reviewed"
    return chapter, None


def _personal_entry(user_id: str, token: TokenOut) -> dict | None:
    exact_key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
    return resolve_personal_lexeme(user_id, exact_key, token.lemma, token.reading, token.surface)


def _entry_for_token(user_id: str, token: TokenOut) -> dict | None:
    return _personal_entry(user_id, token) or lookup(token.lemma, token.reading, token.surface)


def _result_for_sentence(
    request_item, entries: dict[str, dict | None], enriched: dict,
    annotation_mode: str, detail_mode: str, lexical_targets=None, structure: dict | None = None,
) -> AnalyzeResponse:
    content_tokens = lexical_targets if lexical_targets is not None else [token for token in request_item.tokens if token.is_content]
    sense_rows = {
        str(row[0]): {"gloss_zh": row[1], "fallback_senses_zh": row[2]}
        for row in enriched.get("words", [])
        if isinstance(row, list) and len(row) >= 3
    }
    context_senses: list[ContextSenseOut] = []
    known_contexts = {str(row[0]): str(row[1]).strip() for row in enriched.get("contexts", [])
                      if isinstance(row, list) and len(row) >= 2 and isinstance(row[1], str)}
    lexemes: dict[str, LexemeOut] = {}
    warnings: list[str] = []
    for token in (content_tokens if annotation_mode != "grammar" and detail_mode == "full" else []):
        entry = entries[token.id]
        ai_row = sense_rows.get(token.id, {})
        if entry and entry.get("senses_zh"):
            senses = [str(value).strip() for value in entry["senses_zh"] if str(value).strip()]
            gloss = known_contexts.get(token.id, "")
            if gloss:
                context_senses.append(ContextSenseOut(token_id=token.id, gloss_zh=gloss))
            else:
                warnings.append(f"{token.surface} 未获得语境义；已有词典义仅供参考")
            key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
            lexemes[key] = LexemeOut(
                key=key, lemma=token.lemma, reading=token.reading, part_of_speech=token.part_of_speech,
                senses_zh=senses, source=str(entry.get("source") or "本地词典"),
            )
            continue
        gloss = str(ai_row.get("gloss_zh", "") or known_contexts.get(token.id, "")).strip()
        fallback = [
            str(value).strip() for value in ai_row.get("fallback_senses_zh", []) if str(value).strip()
        ]
        if gloss:
            context_senses.append(ContextSenseOut(token_id=token.id, gloss_zh=gloss))
        if fallback:
            key = lexeme_key(token.lemma, token.reading, token.part_of_speech)
            lexemes[key] = LexemeOut(
                key=key, lemma=token.lemma, reading=token.reading, part_of_speech=token.part_of_speech,
                senses_zh=list(dict.fromkeys(fallback)), source="AI 补充释义",
            )
        elif not gloss:
            warnings.append(f"{token.surface} 未获得释义")

    original = request_item.sentence.original
    annotations: list[AnnotationOut] = []
    if annotation_mode == "grammar":
        for note in enriched.get("annotations", []):
            try:
                if not isinstance(note, list) or len(note) < 6:
                    continue
                note_type, start, end, quote, structure_name, explanation = (
                    str(note[0]), int(note[1]), int(note[2]), str(note[3]),
                    str(note[4]).strip(), str(note[5]).strip(),
                )
                if note_type != "grammar" or not structure_name:
                    continue
                if start < 0 or end <= start or end > len(original) or original[start:end] != quote or not explanation:
                    continue
                annotations.append(AnnotationOut(
                    id=stable_id("ann", f"{request_item.sentence.id}:{start}:{end}:{structure_name}:{explanation}"),
                    sentence_id=request_item.sentence.id, type=note_type, anchor_start=start, anchor_end=end,
                    quote=quote, structure=structure_name, explanation_zh=explanation,
                ))
            except (TypeError, ValueError):
                continue
    sentence = request_item.sentence.model_copy(update={
        "translation_zh": (
            request_item.sentence.translation_zh if annotation_mode == "grammar"
            else str(enriched.get("meaning", "")).strip()
        ),
        "status": "complete", "error": None, "explanation_status": "complete",
        "translation_quality_status": request_item.sentence.translation_quality_status if annotation_mode == 'grammar' else 'unreviewed',
        "analysis_stale_reason": request_item.sentence.analysis_stale_reason if annotation_mode == 'grammar' else None,
        "explanation_detail": (
            request_item.sentence.explanation_detail if annotation_mode == "grammar" else detail_mode
        ),
    })
    result = AnalyzeResponse(
        sentences=[sentence], tokens=request_item.tokens, annotations=annotations,
        context_senses=context_senses, lexemes=list(lexemes.values()), warnings=warnings,
    )
    for annotation in result.annotations:
        annotation.analysis_revision = sentence.analysis_revision
    for sense in result.context_senses:
        sense.analysis_revision = sentence.analysis_revision
    if structure:
        result.learning_spans = [linguistics_service.LearningSpan.model_validate(value) for value in structure["learning_spans"]]
        result.analysis_manifest = structure["analysis_manifest"]
        allowed_spans = {span["id"] for span in structure["learning_spans"]}
        result.learning_span_senses = [LearningSpanSenseOut(span_id=str(value[0]), gloss_zh=value[1].strip(),
                                                          sentence_id=request_item.sentence.id)
            for value in enriched.get("unit_senses", []) if isinstance(value, list) and len(value) >= 2
            and str(value[0]) in allowed_spans and isinstance(value[1], str) and value[1].strip()]
    return result


def _build_learning_structures(user_id: str, items, contexts: dict) -> dict[str, dict]:
    reference_ids = [item.sentence.id for item in items if contexts[item.sentence.id].mode == "reference"]
    persisted = linguistics_service.structure_results(user_id, reference_ids,context_hashes={sid:contexts[sid].context_hash for sid in reference_ids})["results"] if reference_ids else []
    results = {row["sentence_id"]: row for row in persisted}
    for item in items:
        if item.sentence.id not in results:
            results[item.sentence.id] = linguistics_service.analyze_sentence(item.sentence.id, item.sentence.original)
    return results


def _is_splittable_ai_error(error: Exception) -> bool:
    value = str(error)
    return any(marker in value for marker in ("token 上限", "可解析的 JSON", "JSON 未完成", "AI 未返回句子"))


async def _fetch_ai_rows_resilient(
    user_id: str, misses: list[dict], api_key: str, base_url: str, model: str,
    annotation_mode: str, detail_mode: str, context_before: list[str], depth: int = 0,
    attempt_budget: list[int] | None = None,
) -> list[dict]:
    targets = [item for item in misses if item.get("generate", True)]
    if not targets:
        return []
    # One request-wide recovery budget prevents missing rows from recursively
    # multiplying JSON repairs. 429/network/5xx never trigger batch splitting.
    budget = attempt_budget if attempt_budget is not None else [min(16, len(targets) * 2 + 2)]
    if budget[0] <= 0:
        raise RuntimeError("AI 分批恢复预算已用完，请稍后重试")
    budget[0] -= 1
    batch_hash = visible_batch_hash(misses, annotation_mode, detail_mode)
    try:
        rows = await explain_sentences_with_ai(
            user_id, misses, api_key, base_url, model, annotation_mode,
            detail_mode=detail_mode, context_before=context_before,
        )
    except AiRateLimitError:
        raise
    except Exception as exc:
        if len(targets) > 1 and depth < 4 and _is_splittable_ai_error(exc):
            middle = max(1, len(targets) // 2)
            left_ids = {str(item["sentence"]["id"]) for item in targets[:middle]}
            right_ids = {str(item["sentence"]["id"]) for item in targets[middle:]}
            left_items = [item for item in misses if not item.get("generate", True) or str(item["sentence"]["id"]) in left_ids]
            right_items = [item for item in misses if not item.get("generate", True) or str(item["sentence"]["id"]) in right_ids]
            left = await _fetch_ai_rows_resilient(
                user_id, left_items, api_key, base_url, model, annotation_mode,
                detail_mode, context_before, depth + 1, budget,
            )
            right = await _fetch_ai_rows_resilient(
                user_id, right_items, api_key, base_url, model, annotation_mode,
                detail_mode, context_before, depth + 1, budget,
            )
            return [*left, *right]
        raise
    allowed = {str(item["sentence"]["id"]) for item in targets}
    rows = [{**row, "_batch_dependency_hash": batch_hash} for row in rows
            if isinstance(row, dict) and str(row.get("id")) in allowed]
    returned = {str(row.get("id")) for row in rows}
    if len(returned) != len(rows):
        raise RuntimeError("AI 返回了重复句子结果")
    missing_ids = allowed - returned
    missing_rows = [{**item, "generate": str(item["sentence"]["id"]) in missing_ids}
                    for item in misses]
    if missing_ids:
        if depth >= 4:
            raise RuntimeError(f"AI 未返回句子 {sorted(missing_ids)[0]} 的结果")
        rows.extend(await _fetch_ai_rows_resilient(
            user_id, missing_rows, api_key, base_url, model, annotation_mode,
            detail_mode, context_before, depth + 1, budget,
        ))
    return rows


async def _explain_batch(
    user_id: str, request: ExplainBatchRequest, x_api_key: str | None,
) -> AnalyzeResponse:
    api_key, base_url, model = resolve_settings(user_id, x_api_key, str(request.settings.base_url), request.settings.model)
    if not api_key:
        raise HTTPException(401, "请在设置中输入 API Key")
    for item in request.items:
        validate_tokens(item.sentence, item.tokens)
    contexts = await asyncio.to_thread(build_contexts, user_id,
        [item.sentence for item in request.items], request.context_policy, request.context_before)
    structures = await asyncio.to_thread(_build_learning_structures, user_id, request.items, contexts) if request.annotation_mode != "grammar" and request.detail_mode == "full" else {}
    prepared: list[dict] = []
    cached_rows: dict[str, dict] = {}
    visible_items: list[dict] = []
    for item in request.items:
        structure = structures.get(item.sentence.id)
        content_tokens = lexical_tokens(item.tokens, structure["learning_spans"]) if structure else [token for token in item.tokens if token.is_content]
        entries = {} if request.annotation_mode == "grammar" or request.detail_mode == "meaning" else {
            token.id: _entry_for_token(user_id, token) for token in content_tokens
        }
        unresolved = [] if request.annotation_mode == "grammar" or request.detail_mode == "meaning" else [
            token for token in content_tokens if not (entries[token.id] or {}).get("senses_zh")
        ]
        cache_value = {
            "text": item.sentence.original,
            "unknown": [
                [token.lemma, token.reading, token.part_of_speech, token.surface] for token in unresolved
            ],
            "known": [[token.lemma, token.reading, token.part_of_speech, token.surface,
                       (entries[token.id] or {}).get("senses_zh", [])[:3]]
                      for token in content_tokens if entries.get(token.id)],
            "annotation_mode": request.annotation_mode,
            "detail_mode": request.detail_mode,
            "context_hash": contexts[item.sentence.id].context_hash,
            "provider": base_url.rstrip("/"),
            "token_analysis": [[token.surface, token.lemma, token.reading, token.part_of_speech,
                                getattr(token, "role", None), getattr(token, "conjugation_type", None),
                                getattr(token, "conjugation_form", None), token.start, token.end] for token in item.tokens],
            "structure_manifest": structure["analysis_manifest"] if structure else None,
            "learning_units": [value[1:] for value in learning_units(structure["learning_spans"])] if structure else [],
        }
        prepared_item = {
            "item": item, "entries": entries, "unresolved_tokens": unresolved, "cache_value": cache_value,
            "lexical_targets": content_tokens, "structure": structure,
        }
        prepared.append(prepared_item)
        visible_items.append({
            "sentence": item.sentence.model_dump(),
            "unresolved_tokens": [token.model_dump() for token in unresolved],
            "known_tokens": [[token.id, token.surface, token.lemma, token.reading, token.part_of_speech,
                              entries[token.id]["senses_zh"][:3], token.start, token.end]
                             for token in content_tokens if entries.get(token.id)],
            "analysis_context": contexts[item.sentence.id].prompt_data(),
            "learning_units": learning_units(structure["learning_spans"]) if structure else [],
            "analysis_manifest": structure["analysis_manifest"] if structure else {},
        })

    initial_batch_hash = visible_batch_hash(visible_items, request.annotation_mode, request.detail_mode)
    misses = []
    for prepared_item, visible in zip(prepared, visible_items):
        item = prepared_item["item"]
        content_tokens = prepared_item["lexical_targets"]
        unresolved = prepared_item["unresolved_tokens"]
        cache_key = make_cache_key(user_id, "sentence", model, PROMPT_VERSION,
                                   {**prepared_item["cache_value"], "batch_dependency_hash": initial_batch_hash})
        prepared_item["cache_key"] = cache_key
        cached = get_cached_response(user_id, cache_key)
        if cached:
            restored_words = []
            for word in cached.get("words", []):
                if not isinstance(word, list) or len(word) < 3:
                    continue
                try:
                    token = unresolved[int(word[0])]
                except (ValueError, TypeError, IndexError):
                    continue
                restored_words.append([token.id, word[1], word[2]])
            cached_rows[item.sentence.id] = {
                **cached, "id": item.sentence.id, "words": restored_words,
                "contexts": [[content_tokens[row[0]].id, row[1]] for row in cached.get("contexts", [])
                             if isinstance(row, list) and len(row) >= 2 and isinstance(row[0], int)
                             and 0 <= row[0] < len(content_tokens)],
                "unit_senses": [[prepared_item["structure"]["learning_spans"][value[0]]["id"], value[1]]
                                for value in cached.get("unit_senses", [])
                                if prepared_item["structure"] and isinstance(value, list) and len(value) >= 2
                                and isinstance(value[0], int) and 0 <= value[0] < len(prepared_item["structure"]["learning_spans"])],
            }
        visible["generate"] = not bool(cached)
        misses.append(visible)

    try:
        legacy_window = [ref["original"] for ref in next(iter(contexts.values())).preceding_sentence_refs] if all(value.mode == "transient" for value in contexts.values()) else []
        fresh_rows = await _fetch_ai_rows_resilient(
            user_id, misses, api_key, base_url, model, request.annotation_mode,
            request.detail_mode, legacy_window,
        ) if any(item["generate"] for item in misses) else []
    except AiRateLimitError:
        raise
    except Exception as exc:
        raise HTTPException(502, f"句子释义失败：{exc}") from exc
    fresh_by_id = {str(row.get("id")): row for row in fresh_rows if isinstance(row, dict)}
    if any(value.mode=='reference' for value in contexts.values()):
        current_contexts=await asyncio.to_thread(build_contexts,user_id,[item.sentence for item in request.items],request.context_policy,request.context_before)
        if any(current_contexts[sid].context_hash!=value.context_hash for sid,value in contexts.items()):
            raise HTTPException(409,'原文、前文或人物事实在释义期间变化，请刷新后重试；旧结果未覆盖当前版本')
    combined = AnalyzeResponse(
        sentences=[], tokens=[], annotations=[], context_senses=[], lexemes=[], warnings=[],
    )
    combined.analysis_source = "ai+cache" if cached_rows and fresh_rows else "ai-cache" if cached_rows else "ai"
    lexemes_by_key: dict[str, LexemeOut] = {}
    for prepared_item in prepared:
        item = prepared_item["item"]
        row = cached_rows.get(item.sentence.id) or fresh_by_id.get(item.sentence.id)
        if row is None:
            raise HTTPException(502, f"AI 未返回句子 {item.sentence.id} 的结果")
        if item.sentence.id in fresh_by_id:
            token_indexes = {
                token.id: index for index, token in enumerate(prepared_item["unresolved_tokens"])
            }
            cache_words = [
                [token_indexes[word[0]], word[1], word[2]]
                for word in row.get("words", [])
                if isinstance(word, list) and len(word) >= 3 and word[0] in token_indexes
            ]
            context_indexes = {token.id: index for index, token in enumerate(prepared_item["lexical_targets"])}
            cache_contexts = [[context_indexes[value[0]], value[1]] for value in row.get("contexts", [])
                              if isinstance(value, list) and len(value) >= 2 and value[0] in context_indexes]
            actual_hash = str(row.get("_batch_dependency_hash") or initial_batch_hash)
            actual_key = make_cache_key(user_id, "sentence", model, PROMPT_VERSION,
                {**prepared_item["cache_value"], "batch_dependency_hash": actual_hash})
            cache_payload = {key: value for key, value in row.items() if not key.startswith("_")}
            cache_payload.update(id="cached", words=cache_words, contexts=cache_contexts,
                                 batch_dependency_hash=actual_hash)
            span_indexes = {span["id"]: index for index, span in enumerate(prepared_item["structure"]["learning_spans"])} if prepared_item["structure"] else {}
            cache_payload["unit_senses"] = [[span_indexes[value[0]], value[1]] for value in row.get("unit_senses", [])
                                           if isinstance(value, list) and len(value) >= 2 and value[0] in span_indexes]
            set_cached_response(
                user_id, actual_key, "sentence", model, PROMPT_VERSION, cache_payload,
            )
        else:
            actual_hash = str(row.get("batch_dependency_hash") or initial_batch_hash)
        combined.analysis_contexts.append(contexts[item.sentence.id].metadata(actual_hash))
        if contexts[item.sentence.id].book_id:
            from ..book_memory.repository import record_dependencies,record_context_target
            await asyncio.to_thread(record_dependencies,user_id,contexts[item.sentence.id].book_id,item.sentence.id,contexts[item.sentence.id].entity_fact_refs)
            source=await asyncio.to_thread(linguistics_service.owned_sentence,user_id,item.sentence.id)
            await asyncio.to_thread(record_context_target,user_id,contexts[item.sentence.id].book_id,item.sentence.id,
                {'source_text':'\n'.join([*[ref['original'] for ref in contexts[item.sentence.id].preceding_sentence_refs],item.sentence.original]),
                 'chapter_order':int(source[1].get('order',0)),'target_end':item.sentence.end,
                 'budget':contexts[item.sentence.id].policy['entity_token_budget'],'allow_future':contexts[item.sentence.id].policy['allow_future_facts']})
        if contexts[item.sentence.id].truncated:
            combined.warnings.append("前文已按保守预算保留最近的完整句；实际消耗以 AI usage 为准")
        result = _result_for_sentence(
            item, prepared_item["entries"], row, request.annotation_mode, request.detail_mode,
            prepared_item["lexical_targets"], prepared_item["structure"],
        )
        combined.sentences.extend(result.sentences)
        combined.tokens.extend(result.tokens)
        combined.annotations.extend(result.annotations)
        combined.context_senses.extend(result.context_senses)
        combined.warnings.extend(result.warnings)
        combined.learning_spans.extend(result.learning_spans)
        combined.learning_span_senses.extend(result.learning_span_senses)
        if result.analysis_manifest:
            combined.analysis_manifest = result.analysis_manifest
            if contexts[item.sentence.id].mode == "reference":
                await asyncio.to_thread(linguistics_store.save_span_senses, user_id, item.sentence.id,
                    [value.model_dump() for value in result.learning_span_senses], result.analysis_manifest,
                    contexts[item.sentence.id].context_hash)
        for lexeme in result.lexemes:
            lexemes_by_key[lexeme.key] = lexeme
    combined.lexemes = list(lexemes_by_key.values())
    combined.context_hash = dependency_hash(combined.analysis_contexts)
    return combined


