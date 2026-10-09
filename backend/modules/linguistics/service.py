"""Local analysis, ownership checks, and immutable source-position contracts."""
import hashlib
from importlib.metadata import PackageNotFoundError, version

from fastapi import HTTPException

from ...nlp import tokenize_sentence
from ..library.service import get_sentence_source
from .models import LearningSpan
from . import repository
from .morphology import analyze_morphology
from .grammar import match_grammar
from .rules import get_manifest, get_rule_catalog, rule_version


def _package_version(name: str, fallback: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return fallback


def analysis_version() -> str:
    return "linguistics-v2:" + rule_version()


def capabilities() -> dict:
    from .adapters.ginza_adapter import backend
    from ...platform_capabilities import platform_capabilities
    from ..data_portability.protocol import capabilities as transfer_capabilities
    return {**platform_capabilities(), "version": analysis_version(), "linguistics_v1": True,
            "rules_version": rule_version(), "grammar_count": len(get_rule_catalog()),
            "context_windows": [0, 2, 4, 8],
            "dependency_enhancement": {**backend.manifest(), "enabled": False},
            "ambiguity_resolution": False, "resegmentation": True, "grammar_learning_v1": True,
            "book_memory_v1": True, "document_preflight": True, "transfer_capabilities": transfer_capabilities()}


def analyze_sentence(sentence_id: str, text: str, tokens: list[dict] | None = None,
                     revision: int = 1) -> dict:
    atomic = tokens if tokens is not None else tokenize_sentence(sentence_id, text)
    morph = analyze_morphology(sentence_id, text, atomic)
    from .rules import get_rule
    for span in morph:
        span['grammar_ids'] = [f'ja.morph.{feature}' for feature in span['features'] if get_rule(f'ja.morph.{feature}')]
    grammar = match_grammar(sentence_id, text, atomic, morph)
    spans = [LearningSpan.model_validate(item).model_dump() for item in [*morph, *grammar]]
    for span in spans:
        if text[span["start"]:span["end"]] != span["surface"]:
            raise ValueError("Learning span no longer matches its original source")
    return {"sentence_id": sentence_id, "learning_spans": spans,
            "analysis_manifest": {"version": analysis_version(),
                "tokenizer_version": _package_version("SudachiPy", "java-sudachi-adapter"),
                "dictionary_version": _package_version("sudachidict_core", "core-20250515"),
                "rules_version": rule_version(), "parser_version": "none",
                "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "revision": revision}, "source": "rule"}


def owned_sentence(user_id: str, sentence_id: str) -> tuple[dict, dict]:
    source = get_sentence_source(user_id, sentence_id)
    if source is None:
        raise HTTPException(404, "句子不存在或无权访问")
    return source


def structure_results(user_id: str, sentence_ids: list[str], *, required_version: str | None = None,
                      force: bool = False, context_hashes: dict[str,str] | None = None) -> dict:
    if not sentence_ids or len(sentence_ids) > 120 or len(set(sentence_ids)) != len(sentence_ids):
        raise HTTPException(422, "请选择1至120个不重复句子")
    current = analysis_version()
    if required_version and required_version != current:
        raise HTTPException(409, "语法规则版本已变化，请刷新后重试")
    # Validate the entire batch before writing any cache entries.
    owned = [owned_sentence(user_id, sentence_id) for sentence_id in sentence_ids]
    from ...models import SentenceOut
    from ..analysis.context import build_contexts
    contexts = build_contexts(user_id, [SentenceOut.model_validate(row) for row, _ in owned])
    preferences = repository.load_preferences(user_id)
    results = []
    for sentence, chapter in owned:
        text = sentence["original"]
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        revision = int(sentence.get("analysis_revision", chapter.get("analysis_revision", 1)))
        context_hash=(context_hashes or {}).get(sentence['id'],contexts[sentence['id']].context_hash)
        result = None if force else repository.get_analysis(user_id, sentence["id"], digest, current, revision)
        if result is None:
            result = analyze_sentence(sentence["id"], text, revision=revision)
            repository.put_analysis(user_id, sentence["id"], result)
        result = {**result, "learning_span_senses": repository.load_span_senses(
            user_id, sentence["id"], digest, current, revision, context_hash)}
        from .ambiguity import restore_choices
        result = restore_choices(user_id,result,context_hash)
        if preferences['dependency_enhancement']:
            from .adapters.ginza_adapter import backend
            try:
                result['dependency_parse'] = backend.analyze(text)
                result['analysis_manifest'] = {**result['analysis_manifest'], 'parser_version': result['dependency_parse']['version']}
            except Exception as exc:
                result['dependency_parse'] = {'nodes': [], 'version': 'none', 'warning': str(exc)}
        results.append(result)
    return {"results": results, "version": current}
