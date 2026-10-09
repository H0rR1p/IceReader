"""Versioned, validated data for the deterministic teaching-unit analyser.

Rules describe observed morphology and token boundaries.  They are not a
context-free claim that Japanese meaning can be decided from suffixes alone.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from ...paths import resource_path


RULES_DIRECTORY = "resources/grammar"


def _read(name: str, directory: Path | None = None):
    path = (directory or resource_path(RULES_DIRECTORY)) / name
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_rules(directory: Path | None = None) -> dict:
    """Reject corrupt, ambiguous identities and unsafe unbounded patterns."""
    manifest = _read("manifest.json", directory)
    if manifest.get("schema_version") != 1 or not manifest.get("version"):
        raise ValueError("Unsupported grammar manifest")
    morphology = _read("morphology.json", directory)
    if morphology.get("version") != manifest["version"]:
        raise ValueError("Morphology and manifest versions differ")
    features = morphology.get("features", {})
    if not isinstance(features, dict) or not features:
        raise ValueError("Missing morphology features")
    if not isinstance(morphology.get("max_chain_tokens"), int) or not 1 <= morphology["max_chain_tokens"] <= 64:
        raise ValueError("Invalid morphology chain limit")
    for name in ("auxiliaries", "te_auxiliaries"):
        if not isinstance(morphology.get(name), dict) or any(feature not in features for feature in morphology[name].values()):
            raise ValueError(f"Undefined morphology feature in {name}")
    for feature, forms in morphology.get("connections", {}).items():
        if feature not in features or not isinstance(forms, list) or any(form not in {"未然形", "連用形", "終止形", "連体形", "仮定形", "命令形", "意志推量形"} for form in forms):
            raise ValueError("Invalid conjugation connection")
    rules = _read("constructions.json", directory) + _read("idioms.json", directory)
    identities: set[str] = set()
    for item in rules:
        identity = item.get("id", "")
        if not re.fullmatch(r"ja\.[a-z0-9_.]+", identity) or identity in identities:
            raise ValueError(f"Invalid or duplicate grammar identity: {identity}")
        identities.add(identity)
        if item.get("status") not in {"enabled", "experimental", "disabled"}:
            raise ValueError(f"Missing review status: {identity}")
        if not all(item.get(key) for key in ("label", "explanation_zh", "positive", "negative")):
            raise ValueError(f"Missing teaching text or examples: {identity}")
        if item.get("anchor") not in {"predicate", "nominal", "adjective", "any", "te_predicate", "fixed"}:
            raise ValueError(f"Invalid anchor: {identity}")
        pattern = item.get("pattern", "")
        if len(pattern) > 400 or any(value in pattern for value in (".*", ".+", "(?=", "(?<=", "(?<!", "(?!")):
            raise ValueError(f"Unbounded or lookaround pattern rejected: {identity}")
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"Invalid grammar regular expression: {identity}") from exc
        if compiled.match(""):
            raise ValueError(f"Empty grammar match: {identity}")
        if item.get("required_pos") and any(pos not in {"名詞", "動詞", "形容詞", "形状詞", "助詞", "助動詞", "副詞"} for pos in item["required_pos"]):
            raise ValueError(f"Invalid token constraint: {identity}")
    if len(rules) != manifest["grammar_count"]:
        raise ValueError("Grammar catalogue count differs from manifest")
    return {"manifest": manifest, "morphology": morphology, "rules": rules}


@lru_cache(maxsize=1)
def rule_data() -> dict:
    return validate_rules()


def get_manifest() -> dict:
    """Return a detached manifest so callers cannot mutate cached rules."""
    return deepcopy(rule_data()["manifest"])


def get_rule_catalog(*, include_disabled: bool = False) -> list[dict]:
    return deepcopy([item for item in rule_data()["rules"] if include_disabled or item["status"] == "enabled"])


def get_rule(grammar_id: str) -> dict | None:
    """Disabled rules remain addressable by old cards and learning history."""
    if grammar_id.startswith('ja.morph.'):
        feature = grammar_id[len('ja.morph.'):]
        explanation = rule_data()['morphology']['features'].get(feature)
        if explanation and feature not in {'dictionary','stem','irrealis','attributive','unknown'}:
            return {'id': grammar_id, 'label': explanation.split('：')[0].split('；')[0],
                    'explanation_zh': explanation, 'status': 'enabled', 'kind': 'morphology'}
    for item in rule_data()["rules"]:
        if item["id"] == grammar_id:
            return deepcopy(item)
    return None


def morphology_rules() -> dict:
    return rule_data()["morphology"]


def rule_version() -> str:
    return rule_data()["manifest"]["version"]


def stable_span_id(sentence_id: str, start: int, end: int, kind: str) -> str:
    value = f"{sentence_id}:{start}:{end}:{kind}:{rule_version()}"
    return "span_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def token_pos(token: dict) -> tuple[str, ...]:
    raw = token.get("pos_full")
    if raw:
        return tuple(raw)
    # Legacy records remain readable, but a missing conjugation class must
    # never be inferred from a final る or a guessed kana suffix.
    label = token.get("part_of_speech", "")
    values = label.split("-")[:2]
    return tuple(values + ["*"] * (6 - len(values)))


def token_form(token: dict) -> str:
    values = token_pos(token)
    return token.get("conjugation_form") or (values[5] if len(values) > 5 else "*")


def token_class(token: dict) -> str:
    values = token_pos(token)
    return token.get("conjugation_type") or (values[4] if len(values) > 4 else "*")


def validate_tokens(text: str, tokens: list[dict]) -> None:
    previous_end = 0
    for token in tokens:
        start, end = token.get("start"), token.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(text):
            raise ValueError("Token has invalid original-text offsets")
        if start < previous_end or text[start:end] != token.get("surface"):
            raise ValueError("Token does not replay original text")
        previous_end = end
