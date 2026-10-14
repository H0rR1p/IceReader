"""Independent graph / attachment / source-position regression contracts."""
from copy import deepcopy
import json
import time

import pytest

from .nlp import tokenize_sentence
from .modules.linguistics.functional import (
    AUX_CONNECTIONS, COMPOSITIONS, PARTICLE_CLASSES, _connection_patterns,
    atomic_spans, auxiliary_attaches, connection_matches, functional_data,
    functional_inventory, functional_rule, functional_version,
)
from .modules.linguistics.models import LearningSpan
from .modules.linguistics.service import analyze_sentence


def graph(text, identity="functional-gold"):
    return analyze_sentence(identity, text)["learning_spans"]


SENTENCES = [
    "読んだらよかったんじゃないか？", "何か問題でもあるか？", "これくらいなら遊んでいる学生も多いと思うが……。",
    "この本だけでも読んでおけばよかった。", "明日は休むつもりだ。", "学生じゃないか。", "行くんじゃないか。",
    "読んでるんじゃなかったっけ？", "書かせられていなかったのでしょう。", "雨でも行く。", "静かである。",
    "雪が降るかもしれないけれど、行かなければならない。", "先生に読ませていただきました。", "それを知っていたはずなのに黙っていた。",
    "急いだところで間に合うとは限らない。", "試すにせよやめるにせよ、まず相談してほしい。", "本を読めば読むほど面白くなる。",
    "読んではいけないのだろうか。", "見つけたって言ってたじゃん。", "これを読まなきゃいけない。",
    "行かずともよい。", "知らぬが仏。", "忘るべからず。", "それが好きやねん。", "あの人は来えへん。",
    "🍧猫がいるのに気づいた。", "読んで\nいるんじゃないか？", "「大学ではそうなんですね。」",
]


@pytest.mark.parametrize("text", SENTENCES)
def test_every_meaningful_original_atom_and_every_grammar_level_survive(text):
    tokens, spans = tokenize_sentence("functional-gold", text), graph(text)
    atoms = [span for span in spans if "atomic" in span["features"]]
    meaningful = [token for token in tokens if token["pos_full"][0] not in {"補助記号", "記号", "空白"}]
    assert {span["token_ids"][0] for span in atoms} == {token["id"] for token in meaningful}
    identities = {span["id"] for span in spans}
    assert len(identities) == len(spans)
    for span in spans:
        assert text[span["start"]:span["end"]] == span["surface"]
        assert all(value in identities for value in span["children"] + span["overlap_ids"])
        assert all(span["start"] <= marker["start"] < marker["end"] <= span["end"] for marker in span["marker_ranges"])
        assert LearningSpan.model_validate(span).model_dump() == span
    by_id = {span["id"]: span for span in spans}
    def visit(identity, parents):
        assert identity not in parents, "A containment graph must never have a cycle"
        for child in by_id[identity]["children"]:
            visit(child, parents | {identity})
    for identity in identities:
        visit(identity, set())


def test_ja_nai_ka_whole_children_and_semantic_alternatives_are_separate():
    spans = graph("読んだらよかったんじゃないか？")
    published = next(span for span in spans if span["surface"] == "じゃないか" and "functional_expression" in span["features"])
    assert published["unit_role"] == "ending"
    assert published["status"] == "ambiguous"
    assert published["boundary_status"] == "supported"
    assert len(published["candidates"]) == 2
    assert {candidate["id"] for candidate in published["candidates"]} == set(published["grammar_ids"])
    surfaces = {span["surface"] for span in spans}
    assert {"よかったんじゃないか", "んじゃないか", "じゃないか", "じゃない", "じゃ", "ない", "か"} <= surfaces
    atom = next(span for span in spans if span["surface"] == "ない" and "atomic" in span["features"])
    assert atom["unit_role"] == "auxiliary"
    assert published["normalized_form"] == "ではないか"
    assert published["normalization_steps"][0]["surface"] == "じゃ"


def test_degree_condition_is_composed_from_typed_units_and_not_wrong_comparison():
    spans = graph("これくらいなら大丈夫だ。")
    unit = next(span for span in spans if span["surface"] == "くらいなら" and "degree_condition" in span["features"])
    assert "程度" in unit["explanation_zh"]
    assert "与其" not in unit["explanation_zh"]
    assert len(unit["children"]) >= 2
    assert any(span["surface"] == "これくらいなら" for span in spans)
    comparison = graph("辞めるくらいなら続ける。")
    assert any("functional_expression" in span["features"] and "くらい" in span["surface"] for span in comparison)


def test_formal_noun_copula_chain_continues_after_existing_crossing_unit():
    spans = graph("明日は休むつもりだ。")
    assert {"休むつもり", "つもりだ", "休むつもりだ"} <= {span["surface"] for span in spans}
    crossing = next(span for span in spans if span["surface"] == "休むつもり")
    other = next(span for span in spans if span["surface"] == "つもりだ")
    assert other["id"] in crossing["overlap_ids"]
    assert crossing["id"] in other["overlap_ids"]


@pytest.mark.parametrize("positive,negative,key", [
    ("読みます。", "読むます。", "predicate_auxiliary"),
    ("読んで。", "読むて。", "predicate_te"),
    ("読んでみる。", "読むみる。", "te_auxiliary"),
    ("学生だ。", "読むだ。", "nominal_copula"),
    ("学生じゃない。", "学生じゃよい。", "copular_negative"),
    ("読むのだ。", "読むをだ。", "predicate_nominalizer"),
    ("読むつもりだ。", "読みつもりだ。", "predicate_nominal_expression"),
    ("読んだらよかった。", "読むだらよかった。", "predicate_functional"),
    ("読むのだ。", "読むをだ。", "nominalized_copula"),
    ("読むか。", "読んか。", "predicate_ending"),
    ("読むんじゃないか。", "読むをじゃないか。", "nominalized_ending"),
    ("本だけでも。", "本をに。", "particle_sequence"),
    ("これくらいなら。", "これをなら。", "degree_condition"),
    ("本を。", "読むを。", "nominal_particle"),
    ("読めば。", "読むば。", "predicate_connective"),
])
def test_each_enabled_composition_has_natural_positive_and_negative(positive, negative, key):
    assert key in COMPOSITIONS
    assert any(key in span["features"] and span["start"] == 0 for span in graph(positive)), (positive, key)
    assert not any(key in span["features"] and span["start"] == 0 for span in graph(negative)), (negative, key)


def _token(pos, form="*", ctype="*", lemma="試す", surface="試す"):
    return {"pos_full": [pos, "一般", "*", "*", ctype, form], "lemma": lemma, "surface": surface,
            "conjugation_type": ctype, "conjugation_form": form}


@pytest.mark.parametrize("ctype,forms", AUX_CONNECTIONS.items())
def test_each_auxiliary_class_typed_form_has_positive_and_negative(ctype, forms):
    current = _token("助動詞", "終止形-一般", ctype, lemma="検証", surface="検証")
    for form in forms:
        assert auxiliary_attaches(_token("動詞", form + "-一般"), current)
    if ctype not in {"助動詞-ラシイ", "文語助動詞-ゴトシ"}:
        assert not auxiliary_attaches(_token("名詞"), current)
    assert not auxiliary_attaches(_token("補助記号"), current)
    assert not auxiliary_attaches(_token("動詞", "語幹-検証用"), current)


def test_pinned_inventory_has_definitions_for_all_functional_classes_and_lemmas():
    inventory = functional_inventory()
    particles = set()
    for entry in inventory["forms"]:
        pos = inventory["pos"][entry["pos_id"]]
        lemma = entry["lemma"]
        if pos[0] == "助詞":
            particles.add(pos[1])
            identity = "ja.functional.atom.particle." + PARTICLE_CLASSES[pos[1]][0] + "." + lemma.encode().hex()
        else:
            identity = "ja.functional.atom.auxiliary." + pos[4].encode().hex() + "." + lemma.encode().hex()
        rule = functional_rule(identity)
        assert rule and rule["explanation_zh"] and rule["label"] == lemma, entry
    assert particles == set(PARTICLE_CLASSES)


def test_all_published_connections_are_finite_and_mapped_without_wildcard_fallback():
    resource = functional_data()
    used = {entry[key][:2] for entry in resource["entries"] for key in ("left", "right")}
    for code in resource["connections"]:
        patterns = _connection_patterns(code)
        if code == "90":
            assert connection_matches(code, None)
            continue
        positives = [(pos, pattern) for pattern in patterns for pos in pattern["_allowed_pos"]]
        if not positives:
            assert code not in used
            assert not connection_matches(code, _token("不存在的词性"))
            continue
        pos, pattern = positives[0]
        candidate = {"pos_full": list(pos), "lemma": next(iter(pattern["_allowed_lemma"]), "検証")}
        assert connection_matches(code, candidate), code
        assert not connection_matches(code, _token("不存在的词性")), code
        assert not connection_matches(code, None), code
    assert len(resource["entries"]) == 16801


def test_whole_graph_original_data_and_old_dto_are_compatible():
    text = "🍧読んでるんじゃないか？"
    tokens = tokenize_sentence("functional-gold", text)
    snapshot = deepcopy(tokens)
    result = analyze_sentence("functional-gold", text, tokens)
    assert tokens == snapshot
    packed = json.loads(json.dumps(result, ensure_ascii=False))
    assert packed == result
    assert functional_version() in result["analysis_manifest"]["version"]
    old = {"id": "old", "sentence_id": "old", "start": 0, "end": 2, "surface": "読む"}
    dto = LearningSpan.model_validate(old)
    assert dto.unit_role is None and dto.marker_ranges == [] and dto.normalization_steps == []


def test_long_sentence_trie_and_graph_remain_bounded_and_do_not_omit_atoms():
    # A deliberately long EPUB-style sentence, not 16,801 regex scans per
    # position. Warm resource loading separately from steady-state analysis.
    graph("猫じゃないか。")
    text = "本だけでも読んでおけばよかったんじゃないか、" * 35 + "と思う。"
    start = time.perf_counter()
    spans = graph(text)
    elapsed = time.perf_counter() - start
    tokens = tokenize_sentence("functional-gold", text)
    assert sum("atomic" in span["features"] for span in spans) == sum(token["pos_full"][0] not in {"補助記号", "記号", "空白"} for token in tokens)
    assert len(spans) < len(tokens) * 16
    assert elapsed < 4.0, (elapsed, len(tokens), len(spans))
