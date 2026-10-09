"""Offline evaluation on explicitly authored fixtures; never reads a user DB.

The seed corpus has partial annotations. Report annotated-unit recall, not
precision or global translation accuracy. Provider usage is optional explicit
JSONL; character budgets and max_tokens are not measured token consumption.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURES = ROOT / "tests/fixtures/linguistics"


def load_fixture(path: Path) -> tuple[dict, list[dict], str]:
    manifest_path = path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise ValueError("Unsupported fixture schema")
    cases: list[dict] = []
    digest = hashlib.sha256(manifest_path.read_bytes())
    for name in manifest["files"]:
        source = (path / name).resolve()
        if source.parent != path.resolve() or source.suffix != ".json":
            raise ValueError("Fixture files must be direct JSON children")
        digest.update(source.read_bytes())
        document = json.loads(source.read_text(encoding="utf-8"))
        if document.get("license") != "AGPL-3.0-or-later":
            raise ValueError(f"Missing original fixture license: {name}")
        cases.extend(document["cases"])
    validate_cases(manifest, cases)
    return manifest, cases, digest.hexdigest()


def validate_cases(manifest: dict, cases: list[dict]) -> None:
    ids = [item["id"] for item in cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate case IDs")
    if len(cases) != manifest["expected_case_count"]:
        raise ValueError("Fixture count changed without manifest update")
    if dict(Counter(item["category"] for item in cases)) != manifest["category_counts"]:
        raise ValueError("Fixture categories changed without manifest update")
    for item in cases:
        text = item["text"]
        if not text or not item.get("annotation_note"):
            raise ValueError(f"Missing source/annotation: {item['id']}")
        if len(item.get("preceding_translations", [])) > len(item.get("preceding", [])):
            raise ValueError(f"Unanchored preceding translations: {item['id']}")
        for span in item["expected"].get("spans", []):
            if not 0 <= span["start"] < span["end"] <= len(text):
                raise ValueError(f"Invalid span: {item['id']}")
            if text[span["start"]:span["end"]] != span["surface"]:
                raise ValueError(f"Offset/surface mismatch: {item['id']}")
        for ruby in item.get("ruby", []):
            if not 0 <= ruby["start"] < ruby["end"] <= len(text) or not ruby["reading"]:
                raise ValueError(f"Invalid ruby anchor: {item['id']}")


def utf16_offset(text: str, codepoint_offset: int) -> int:
    return len(text[:codepoint_offset].encode("utf-16-le")) // 2


def _version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"


def load_nlp(source: Path | None):
    if source is None:
        return importlib.import_module("backend.nlp")
    spec = importlib.util.spec_from_file_location("icereader_frozen_nlp", source)
    if spec is None or spec.loader is None:
        raise ValueError("Cannot load frozen NLP source")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses inspect their defining module.
    spec.loader.exec_module(module)
    return module


def read_predictions(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    result = {}
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        case_id = row["id"]
        if case_id in result:
            raise ValueError(f"Duplicate prediction: {case_id} (line {index})")
        result[case_id] = row
    return result


def summarize_usage(path: Path | None, pricing: dict | None = None) -> dict:
    """Count explicit provider usage, including failed attempts; unknown stays unknown."""
    if path is None:
        return {"source": "not_supplied", "measured_requests": 0,
                "token_totals": None, "reason": "Offline run made no AI requests"}
    groups: dict[tuple[str, str, str], dict] = {}
    missing_usage = 0
    seen: set[str] = set()
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        request_id = row.get("request_id")
        if request_id:
            if request_id in seen:
                raise ValueError(f"Duplicate request_id in explicit usage export: {request_id}")
            seen.add(request_id)
        if not row.get("operation") or not row.get("model"):
            raise ValueError(f"Usage line {index} requires operation and model")
        key = (str(row.get("phase", "unspecified")), row["operation"], row["model"])
        group = groups.setdefault(key, {"phase":key[0],"operation":key[1],"model":key[2],
                "requests":0,"failures":0,"measured_requests":0,
                "prompt_tokens":0,"cache_hit_tokens":0,"cache_miss_tokens":0,
                "completion_tokens":0,"duration_ms":0,"pricing_configured":False})
        group["requests"] += 1
        group["failures"] += int(not row.get("success", True))
        group["duration_ms"] += max(0, int(row.get("duration_ms", 0)))
        usage = row.get("usage")
        if not isinstance(usage, dict) or "prompt_tokens" not in usage or "completion_tokens" not in usage:
            missing_usage += 1
            continue
        prompt = int(usage["prompt_tokens"])
        completion = int(usage["completion_tokens"])
        details = usage.get("prompt_tokens_details") or {}
        hit = int(usage.get("prompt_cache_hit_tokens", details.get("cached_tokens", 0)))
        miss = int(usage.get("prompt_cache_miss_tokens", max(0, prompt-hit)))
        if min(prompt, completion, hit, miss) < 0 or hit > prompt or hit + miss != prompt:
            raise ValueError(f"Inconsistent measured token usage at line {index}")
        group["measured_requests"] += 1
        for field, value in (("prompt_tokens",prompt),("completion_tokens",completion),
                             ("cache_hit_tokens",hit),("cache_miss_tokens",miss)):
            group[field] += value
    operations = list(groups.values())
    totals = {name:sum(item[name] for item in operations)
              for name in ("prompt_tokens","completion_tokens","cache_hit_tokens","cache_miss_tokens")}
    for group in operations:
        rates = (pricing or {}).get(group["model"])
        if rates and all(name in rates for name in ("cache_hit", "cache_miss", "output")):
            if any(float(rates[name]) < 0 for name in ("cache_hit", "cache_miss", "output")):
                raise ValueError("Pricing must be nonnegative USD per million tokens")
            group["pricing_configured"] = True
            group["measured_cost_usd"] = round((group["cache_hit_tokens"]*float(rates["cache_hit"])
                +group["cache_miss_tokens"]*float(rates["cache_miss"])
                +group["completion_tokens"]*float(rates["output"]))/1_000_000, 8)
    return {"source": str(path), "measured_requests":sum(x["measured_requests"] for x in operations),
            "requests_without_usage":missing_usage,"token_totals":totals,"by_operation":operations,
            "complete_measurement":missing_usage == 0,
            "pricing_units":"USD per million actual provider tokens"}


def _features(span: dict) -> set[str]:
    return set(span.get("features", [])) | {step["feature"] for step in span.get("steps", []) if "feature" in step}


def _grammar_keys(span: dict) -> set[str]:
    return set(span.get("grammar_ids", [])) | {str(span.get("semantic_key", ""))}


def evaluate(cases: list[dict], nlp, *, engine: str, predictions: dict | None = None) -> tuple[dict, list[dict]]:
    structure = None
    if engine == "structure":
        structure = importlib.import_module("backend.modules.linguistics.service").analyze_sentence
    records = []
    counters = Counter()
    cold_start_ms = None
    for item in cases:
        before = time.perf_counter()
        sentences = nlp.split_sentences(item["text"])
        atoms = nlp.tokenize_sentence(item["id"], item["text"])
        prediction = (predictions or {}).get(item["id"])
        if prediction is None and structure:
            prediction = structure(item["id"], item["text"], tokens=atoms, revision=1)
            if hasattr(prediction, "model_dump"):
                prediction = prediction.model_dump()
        elapsed = (time.perf_counter()-before)*1000
        if cold_start_ms is None:
            cold_start_ms = elapsed
        learning_spans = (prediction or {}).get("learning_spans", [])
        learning_spans = [span.model_dump() if hasattr(span, "model_dump") else span for span in learning_spans]
        surface_valid = all(item["text"][a["start"]:a["end"]] == a["surface"] for a in atoms)
        counters["invalid_atom_offsets"] += int(not surface_valid)
        positive = []
        for expected in item["expected"].get("spans", []):
            overlaps = [a for a in atoms if a["start"] < expected["end"] and a["end"] > expected["start"]]
            exact = [s for s in learning_spans if s["start"] == expected["start"] and s["end"] == expected["end"]]
            qualified = [s for s in exact if (not expected.get("lemma") or s.get("lemma") == expected["lemma"])
                         and set(expected["required_features"]).issubset(_features(s))]
            counters["annotated_units"] += 1
            counters["exact_boundary_matches"] += int(bool(exact))
            counters["unit_feature_matches"] += int(bool(qualified))
            counters["fragmented_annotated_units"] += int(len(overlaps) > 1)
            counters["grammatical_atoms_marked_content"] += sum(bool(a["is_content"]) and a["part_of_speech"].startswith(("助詞", "助動詞")) for a in overlaps)
            positive.append({"surface":expected["surface"],"start":expected["start"],"end":expected["end"],
                "utf16_start":utf16_offset(item["text"],expected["start"]),
                "utf16_end":utf16_offset(item["text"],expected["end"]),
                "atom_count":len(overlaps),"atoms":[a["surface"] for a in overlaps],
                "exact_boundary_match":bool(exact),"feature_match":bool(qualified),
                "required_features":expected["required_features"]})
        negatives = []
        for rejected in item["expected"].get("rejected_matches", []):
            bad = any(rejected["semantic_key"] in _grammar_keys(s) for s in learning_spans)
            counters["annotated_negative_rules"] += 1
            counters["evaluated_negative_rules"] += int(prediction is not None)
            counters["negative_rule_violations"] += int(bad) if prediction is not None else 0
            negatives.append({**rejected,"violation":bad,"evaluated":prediction is not None})
        expected_sentences = item["expected"].get("sentence_texts")
        sentence_match = None if expected_sentences is None else [s.text for s in sentences] == expected_sentences
        if expected_sentences is not None:
            counters["annotated_sentence_sequences"] += 1
            counters["sentence_sequence_matches"] += int(sentence_match)
        records.append({"id":item["id"],"category":item["category"],"text":item["text"],
            "elapsed_ms":round(elapsed,3),"atom_offsets_valid":surface_valid,"tokens":atoms,
            "positive_units":positive,"negative_rules":negatives,"sentence_sequence_match":sentence_match,
            "expected_unknown":item["expected"].get("unknown", []),
            "translation_policy":item["expected"].get("translation_policy"),
            "unknown_and_translation_evaluation":"not_measured_without_reviewed_prediction",
            "learning_spans":learning_spans})
    count = counters["annotated_units"]
    summary = dict(counters)
    summary.update({"case_count":len(cases),"category_counts":dict(Counter(i["category"] for i in cases)),
        "annotated_unit_boundary_recall":None if not count else counters["exact_boundary_matches"]/count,
        "annotated_unit_feature_recall":None if not count else counters["unit_feature_matches"]/count,
        "precision":None,"precision_reason":"Partial seed annotations do not mark all valid spans",
        "feature_label_note":"Gold uses semantic feature names; exact feature match requires the engine to expose these labels without alias inflation",
        "cold_first_case_ms":round(cold_start_ms or 0,3),
        "warm_median_case_ms":round(statistics.median([r["elapsed_ms"] for r in records[1:]]),3) if len(records)>1 else None,
        "quality_status":"initial authored seed; human adjudication and frozen larger test set pending",
        "ai_sentence_boundary_requests":0,"online_requests":0,
        "unknown_quality_and_gender_quality":"not measured; expectations require reviewed semantic/translation output"})
    return summary, records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--engine", choices=("atoms", "structure"), default="atoms")
    parser.add_argument("--baseline-source", type=Path, help="Explicit frozen original nlp.py; never auto-discovered")
    parser.add_argument("--predictions", type=Path, help="Explicit JSONL {id,learning_spans}; no automatic database reads")
    parser.add_argument("--usage-jsonl", type=Path, help="Explicit provider request export, one row per attempt")
    parser.add_argument("--pricing-json", type=Path, help="Model rates: cache_hit,cache_miss,output USD/million")
    parser.add_argument("--output", type=Path, default=ROOT / "build/linguistics/evaluation.json")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    manifest, cases, corpus_hash = load_fixture(args.fixtures.resolve())
    nlp = load_nlp(args.baseline_source)
    summary, records = evaluate(cases, nlp, engine=args.engine, predictions=read_predictions(args.predictions))
    pricing = json.loads(args.pricing_json.read_text(encoding="utf-8")) if args.pricing_json else None
    source_path = args.baseline_source or ROOT / "backend/nlp.py"
    result = {"report_schema":1,"dataset":manifest,"corpus_sha256":corpus_hash,
        "engine":args.engine,"source_path":str(source_path),
        "source_sha256":hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "python_version":sys.version.split()[0],"dependencies":{"SudachiPy":_version("SudachiPy"),"SudachiDict-core":_version("SudachiDict-core")},
        "summary":summary,"usage":summarize_usage(args.usage_jsonl,pricing),"cases":records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"output":str(args.output),"summary":summary},ensure_ascii=False,indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
