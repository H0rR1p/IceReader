"""Guard fixture integrity and honest measurement, not analyzer implementation."""
import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("linguistic_evaluation", ROOT / "scripts/evaluate_linguistics.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


def test_authored_fixture_coverage_and_source_anchors():
    manifest, cases, digest = evaluation.load_fixture(ROOT / "tests/fixtures/linguistics")
    assert manifest["expected_case_count"] == 80
    assert len(digest) == 64
    assert sum(bool(c["expected"]["unknown"]) for c in cases) >= 20
    assert any(c["expected"].get("rejected_matches") for c in cases)
    assert all(c["text"][s["start"]:s["end"]] == s["surface"]
               for c in cases for s in c["expected"]["spans"])


def test_wrong_unicode_or_duplicate_anchor_is_rejected():
    manifest, cases, _ = evaluation.load_fixture(ROOT / "tests/fixtures/linguistics")
    invalid = copy.deepcopy(cases)
    invalid[0]["expected"]["spans"][0]["start"] += 1
    with pytest.raises(ValueError, match="Offset/surface mismatch"):
        evaluation.validate_cases(manifest, invalid)
    invalid = copy.deepcopy(cases)
    invalid[1]["id"] = invalid[0]["id"]
    with pytest.raises(ValueError, match="Duplicate case IDs"):
        evaluation.validate_cases(manifest, invalid)


def test_codepoint_offsets_convert_without_normalizing_source():
    value = "🙂𠮷田Cafe\u0301読む"
    assert evaluation.utf16_offset(value, 2) == 4
    assert evaluation.utf16_offset(value, len(value)) == len(value) + 2
    assert evaluation.utf16_offset("Cafe\u0301", 5) == 5


def test_usage_counts_failed_attempts_and_keeps_missing_usage_unknown(tmp_path):
    source = tmp_path / "usage.jsonl"
    rows = [
        {"request_id":"a","operation":"explanation","phase":"before","model":"test",
         "success":True,"usage":{"prompt_tokens":100,"completion_tokens":20,"prompt_tokens_details":{"cached_tokens":25}}},
        {"request_id":"b","operation":"explanation","phase":"before","model":"test",
         "success":False,"usage":{"prompt_tokens":80,"completion_tokens":10,"prompt_cache_hit_tokens":0}},
        {"request_id":"c","operation":"disambiguation","phase":"after","model":"test","success":False},
    ]
    source.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
    result = evaluation.summarize_usage(source, {"test":{"cache_hit":1,"cache_miss":2,"output":3}})
    assert result["measured_requests"] == 2
    assert result["requests_without_usage"] == 1
    assert result["complete_measurement"] is False
    assert result["token_totals"] == {"prompt_tokens":180,"completion_tokens":30,"cache_hit_tokens":25,"cache_miss_tokens":155}
    assert result["by_operation"][0]["failures"] == 1
    assert result["by_operation"][0]["measured_cost_usd"] == 0.000425
    assert evaluation.summarize_usage(None)["token_totals"] is None


def test_usage_rejects_double_counted_attempt_or_inconsistent_cache(tmp_path):
    source = tmp_path / "usage.jsonl"
    row = {"request_id":"same","operation":"x","model":"x","usage":{"prompt_tokens":10,"completion_tokens":1}}
    source.write_text(json.dumps(row)+"\n"+json.dumps(row),encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate request_id"):
        evaluation.summarize_usage(source)
    row["usage"]["prompt_cache_hit_tokens"] = 11
    source.write_text(json.dumps(row),encoding="utf-8")
    with pytest.raises(ValueError, match="Inconsistent measured token usage"):
        evaluation.summarize_usage(source)
