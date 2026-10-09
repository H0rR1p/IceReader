"""Build bounded discourse from owned, complete original sentence order.

Pending translation queues are deliberately not used as the source: a completed
sentence between two pending ones is still part of their reading context.
"""
import hashlib
import json
from dataclasses import dataclass

from fastapi import HTTPException

from ..library import sources as library_sources
from ..linguistics.models import ContextPolicy
from ..linguistics import repository as linguistics_store


CONTEXT_VERSION = "discourse-v1"


def dependency_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def estimate_tokens(value) -> int:
    """Conservative UTF-8 byte estimate when provider tokenizer is unavailable.

This is a budgeting estimate, never reported as billed/model usage. Keeping
whole sentences avoids presenting a cut-off honorific or quotation as evidence.
"""
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


@dataclass
class SentenceContext:
    target_sentence_id: str
    book_id: str | None
    preceding_sentence_refs: list[dict]
    optional_translation_refs: list[dict]
    entity_fact_refs: list[dict]
    policy: dict
    source_revision: dict
    context_hash: str
    mode: str = "reference"
    truncated: bool = False

    def prompt_data(self) -> dict:
        result = {"preceding": [{"id": ref["sentence_id"], "text": ref["original"]}
                                if "sentence_id" in ref else {"text": ref["original"]}
                                for ref in self.preceding_sentence_refs]}
        if self.optional_translation_refs:
            result["previous_translations"] = self.optional_translation_refs
        if self.entity_fact_refs:
            result["entity_facts"] = self.entity_fact_refs
        return result

    def metadata(self, batch_hash: str) -> dict:
        return {"target_sentence_ids": [self.target_sentence_id],
                "preceding_sentence_refs": self.preceding_sentence_refs,
                "optional_translation_refs": self.optional_translation_refs,
                "entity_fact_refs": self.entity_fact_refs, "context_policy": self.policy,
                "context_hash": self.context_hash, "batch_dependency_hash": batch_hash,
                "source_revision": self.source_revision, "mode": self.mode,
                "budget_estimator": "utf8_bytes", "truncated": self.truncated}


def _revision(sentence: dict, chapter: dict) -> dict:
    return {"chapter": chapter.get("analysis_revision", chapter.get("analysisRevision", 0)),
            "generation": chapter.get("active_generation", chapter.get("activeGeneration", "")),
            "sentence": sentence.get("analysis_revision", sentence.get("analysisRevision", 0)),
            "manifest": sentence.get("analysis_manifest", chapter.get("analysis_manifest", {}))}


def _translation_ref(row: dict) -> dict | None:
    text = str(row.get("translation_zh") or "").strip()
    # Automatically generated translations remain soft hints. Failed, pending
    # and explicitly rejected results are never included as usable evidence.
    quality = str(row.get("translation_quality_status", row.get("quality_status", "unreviewed")))
    if not text or row.get("explanation_status") != "complete" or quality not in {"confirmed", "verified", "user_confirmed"}:
        return None
    return {"sentence_id": row["id"], "translation_zh": text, "quality_status": quality,
            "translation_version": row.get("translation_version", dependency_hash(text))}


def _make_context(sentence: dict, chapter: dict, previous: list[tuple[dict, dict]],
                  policy: ContextPolicy, book_id: str, entity_snapshot=None, user_id=None) -> SentenceContext:
    refs, translations = [], []
    remaining = policy.token_budget
    truncated = False
    # Select nearest intact references first, then present chronological order.
    for row, preceding_chapter in reversed(previous[-policy.preceding_sentences:] if policy.preceding_sentences else []):
        ref = {"sentence_id": row["id"], "chapter_id": row["chapter_id"],
               "original": row["original"], "start": int(row.get("start", 0)),
               "end": int(row.get("end", 0)), "revision": _revision(row, preceding_chapter)}
        translated = _translation_ref(row) if policy.include_previous_translation else None
        cost = estimate_tokens({"id": ref["sentence_id"], "text": ref["original"]}) + (estimate_tokens(translated) if translated else 0)
        if cost > remaining:
            truncated = True
            break
        remaining -= cost
        refs.insert(0, ref)
        if translated:
            translations.insert(0, translated)
    policy_data = policy.model_dump()
    revision = _revision(sentence, chapter)
    entities = []
    if user_id and policy.entity_token_budget:
        from ..book_memory.service import context_facts
        entities = context_facts(user_id, book_id, '\n'.join([*[row['original'] for row in refs], sentence['original']]),
            int(chapter.get('order',0)), int(sentence['end']), budget=policy.entity_token_budget,
            allow_future=policy.allow_future_facts, snapshot=entity_snapshot)
    value = {"version": CONTEXT_VERSION, "policy": policy_data, "preceding": refs,
             "translations": translations, "entities": entities, "revision": revision,
             "book_id": book_id, "target": {"chapter_id": sentence["chapter_id"],
                                              "start": sentence["start"], "original": sentence["original"]}}
    return SentenceContext(sentence["id"], book_id, refs, translations, entities, policy_data,
                           revision, dependency_hash(value), truncated=truncated)


def _validate_target(request_sentence, stored: dict) -> None:
    if request_sentence.analysis_revision != int(stored.get('analysis_revision', 1)):
        raise HTTPException(409, '切分版本已变化，请重新打开章节')
    for field in ("chapter_id", "start", "end", "original"):
        if getattr(request_sentence, field) != stored.get(field):
            raise HTTPException(409, "原文或切分版本已变化，请重新打开章节")


def validate_tokens(sentence, tokens) -> None:
    ids: set[str] = set()
    for token in tokens:
        if token.id in ids or token.sentence_id != sentence.id or token.start < 0 or token.end <= token.start or token.end > len(sentence.original) or sentence.original[token.start:token.end] != token.surface:
            raise HTTPException(422, "词语位置与原句不一致，请重新打开章节")
        ids.add(token.id)


def build_contexts(user_id: str, sentences: list, policy: ContextPolicy | None = None,
                   legacy_context: list[str] | None = None) -> dict[str, SentenceContext]:
    """Take one SQLite read snapshot and resolve every target through ownership.

Explicit policies require persisted references. The older transient text path
remains available without a policy, and cannot impersonate an existing ID.
"""
    if policy is not None and legacy_context:
        raise HTTPException(422, "context_policy 与旧 context_before 不能混用")
    effective = policy or ContextPolicy.model_validate(linguistics_store.load_preferences(user_id)["context_policy"])
    if len({sentence.id for sentence in sentences}) != len(sentences):
        raise HTTPException(422, "不能重复提交同一句子")
    result = {}
    sources = library_sources.load_context_sources(user_id, [target.id for target in sentences],
        preceding_sentences=effective.preceding_sentences, cross_chapter=effective.cross_chapter)
    from ..book_memory.service import read as memory_snapshot
    snapshots = {book_id: memory_snapshot(user_id,book_id) for book_id in {source['book_id'] for source in sources.values()}}
    for target in sentences:
        source = sources.get(target.id)
        if source is None:
            continue
        _validate_target(target, source["sentence"])
        result[target.id] = _make_context(source["sentence"], source["chapter"],
            source["preceding"], effective, source["book_id"], snapshots[source['book_id']], user_id)
    if result and len(result) != len(sentences):
        raise HTTPException(422, "已保存句引用与临时原文不能混用")
    for target in sentences:
        if target.id in result:
            continue
        if policy is not None:
            raise HTTPException(404, "句子尚未保存，无法查询正文上下文")
        values = [str(value) for value in (legacy_context or [])[-2:]]
        refs = []
        remaining = effective.token_budget
        for value in reversed(values):
            ref = {"original": value, "source": "legacy_transient"}
            if estimate_tokens(ref) > remaining:
                break
            refs.insert(0, ref)
            remaining -= estimate_tokens(ref)
        hashed = {"version": CONTEXT_VERSION, "policy": effective.model_dump(), "preceding": refs}
        result[target.id] = SentenceContext(target.id, None, refs, [], [], effective.model_dump(),
                                            {}, dependency_hash(hashed), mode="transient",
                                            truncated=len(refs) != len(values))
    return result
