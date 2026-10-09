import json
from pathlib import Path

import pytest

from .nlp import tokenize_sentence
from .modules.linguistics.grammar import match_grammar
from .modules.linguistics.models import LearningSpan
from .modules.linguistics.morphology import analyze_morphology
from .modules.linguistics.rules import get_manifest, get_rule, get_rule_catalog, validate_rules


def matches(text):
    tokens = tokenize_sentence("grammar-gold", text)
    morphology = analyze_morphology("grammar-gold", text, tokens)
    spans = match_grammar("grammar-gold", text, tokens, morphology)
    for item in spans:
        LearningSpan.model_validate(item)
        assert text[item["start"]:item["end"]] == item["surface"]
    return spans


@pytest.mark.parametrize("rule", get_rule_catalog(), ids=lambda rule: rule["id"])
def test_original_catalogue_positive_and_structural_negative(rule):
    for text in rule["positive"]:
        assert any(rule["id"] in item["grammar_ids"] for item in matches(text)), (rule["id"], text)
    for text in rule["negative"]:
        assert not any(rule["id"] in item["grammar_ids"] for item in matches(text)), (rule["id"], text)


def test_overlapping_structures_keep_morphology_and_nested_ids():
    spans = matches("知っているのに黙っている。")
    nested = next(item for item in spans if "ja.te_iru" in item["grammar_ids"] and item["start"] == 0)
    parent = next(item for item in spans if "ja.noni" in item["grammar_ids"])
    assert nested["id"] in parent["children"]
    assert parent["captures"]["marker"] == "のに"
    assert parent["captures"]["anchor"] == "知っている"


@pytest.mark.parametrize("text", [
    "猫がいるのに気づいた。",
    "行ったのに驚いた。",
    "寒いのに耐えた。",
    "猫がいるのに誰も気づかなかった。",
])
def test_noni_retains_nominalized_case_and_concessive_readings(text):
    # The first three clauses are objects of noticing / surprise / endurance;
    # the fourth is concessive. Their local の + に POS pair is identical,
    # so a parser must not close the legal candidate set using POS alone.
    tokens = tokenize_sentence("grammar-gold", text)
    marker = text.index("のに")
    no = next(item for item in tokens if item["start"] == marker)
    ni = next(item for item in tokens if item["start"] == marker + 1)
    assert no["pos_full"][:2] == ["助詞", "準体助詞"]
    assert ni["pos_full"][:2] == ["助詞", "格助詞"]
    span = next(item for item in matches(text) if "ja.noni" in item["grammar_ids"])
    assert span["status"] == "ambiguous"
    assert {item["id"] for item in span["candidates"]} == {
        "concessive", "purpose", "nominalized_case",
    }
    assert all(item["status"] == "possible" for item in span["candidates"])
    assert "把前面的事" in span["explanation_zh"]
    case = next(item for item in span["candidates"] if item["id"] == "nominalized_case")
    assert "作为" in case["label"] and "对象" in case["label"]
    assert "nominalized_case" in case["features"]
    # The public DTO must retain the candidate delivered to manual / AI
    # resolution, and keep the exact original source anchor.
    dto = LearningSpan.model_validate(span).model_dump()
    assert "nominalized_case" in {item["id"] for item in dto["candidates"]}
    assert text[dto["start"]:dto["end"]] == dto["surface"]


@pytest.mark.parametrize("text,forbidden", [
    ("香川県についている駅に行く。", "ja.ni_tsuite"),
    ("読んだ。ことができるという名前だ。", "ja.koto_ga_dekiru"),
    ("読んだ\nことができるという名前だ。", "ja.koto_ga_dekiru"),
    ("東京から大阪に行く。", "ja.kara_reason"),
    ("三回目に会った。", "ja.me_ni_suru"),
    ("元気になった。", "ja.ki_ni_naru"),
    ("空気が澄んだ。", "ja.ki_ga_suru"),
    ("袋に手を入れた。", "ja.te_ni_ireru"),
])
def test_real_lexical_and_boundary_counterexamples(text, forbidden):
    assert not any(forbidden in item["grammar_ids"] for item in matches(text))


def test_polite_past_suffix_is_within_te_iru_learning_unit():
    span = next(item for item in matches("読んでいました。") if "ja.te_iru" in item["grammar_ids"])
    assert span["surface"] == "読んでいました"


def test_original_cirno_symbol_and_unicode_offset_capture():
    span = next(item for item in matches("🍧猫を見ることができる。") if "ja.koto_ga_dekiru" in item["grammar_ids"])
    assert span["start"] == 3
    assert span["surface"] == "見ることができる"


def test_rule_catalogue_has_stable_unique_identities_and_original_explanations():
    rules = get_rule_catalog()
    assert len(rules) == 88
    assert len({item["id"] for item in rules}) == 88
    assert all(item["positive"] and item["negative"] and item["explanation_zh"] for item in rules)
    assert validate_rules()["manifest"]["version"] == "1.1.0"


def test_external_catalogue_views_cannot_poison_subsequent_analysis():
    manifest = get_manifest()
    manifest["capabilities"].clear()
    catalog = get_rule_catalog()
    catalog[0]["positive"].clear()
    assert get_manifest()["capabilities"]
    assert get_rule_catalog()[0]["positive"]
    assert get_rule("ja.te_iru")["label"] == "〜ている"
    assert get_rule("ja.missing_rule") is None


@pytest.mark.parametrize("mutation", ["duplicate_id", "bad_pattern", "missing_review", "mismatched_version"])
def test_rules_fail_closed_on_corrupt_resource(tmp_path, mutation):
    source = Path(__file__).resolve().parents[1] / "resources" / "grammar"
    for name in ("manifest.json", "morphology.json", "constructions.json", "idioms.json"):
        (tmp_path / name).write_bytes((source / name).read_bytes())
    data = json.loads((tmp_path / "constructions.json").read_text(encoding="utf-8"))
    if mutation == "duplicate_id":
        data[1]["id"] = data[0]["id"]
    elif mutation == "bad_pattern":
        data[0]["pattern"] = ".*"
    elif mutation == "missing_review":
        del data[0]["status"]
    else:
        morphology = json.loads((tmp_path / "morphology.json").read_text(encoding="utf-8"))
        morphology["version"] = "999"
        (tmp_path / "morphology.json").write_text(json.dumps(morphology), encoding="utf-8")
    (tmp_path / "constructions.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_rules(tmp_path)
