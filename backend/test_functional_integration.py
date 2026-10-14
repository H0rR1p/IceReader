"""Functional-expression changes must retain vocabulary and portable source identities."""
import json

from backend.modules.analysis.prompt_payloads import learning_units, lexical_tokens
from backend.modules.analysis.service import _local_analysis
from backend.modules.data_portability.identity import TransferIds
from backend.modules.linguistics.service import analyze_sentence


def test_functional_graph_does_not_turn_particles_into_dictionary_targets():
    text = "猫にも読ませました。"
    sentences, tokens = _local_analysis("chapter", text)
    original = [token.model_dump() for token in tokens]
    result = analyze_sentence(sentences[0].id, text)
    vocabulary = lexical_tokens(tokens, result["learning_spans"])
    assert {token.lemma for token in vocabulary} == {"猫", "読む"}
    assert original == [token.model_dump() for token in tokens]
    prompted = {unit[0] for unit in learning_units(result["learning_spans"])}
    assert not any(span["id"] in prompted for span in result["learning_spans"]
                   if span.get("unit_role") == "lexical")
    assert any(span["id"] in prompted for span in result["learning_spans"]
               if span.get("unit_role") in {"particle", "auxiliary", "ending"})


def test_transfer_rewrites_graph_links_without_rewriting_japanese_or_offsets():
    ids = TransferIds("source-user", "target-user")
    for name in ("outer", "inner", "cross"):
        ids.add("spans", name)
    ids.add("tokens", "atom")
    ids.add("sentences", "sentence")
    source = {"learning_spans": [{"id": "outer", "sentence_id": "sentence",
        "surface": "じゃないか", "start": 4, "end": 9,
        "token_ids": ["atom"], "children": ["inner"], "overlap_ids": ["cross"],
        "grammar_ids": ["ja.functional.confirmation"],
        "marker_ranges": [{"start": 4, "end": 9}],
        "normalization_steps": [{"surface": "じゃ", "normalized": "では", "start": 4, "end": 6}]}]}
    snapshot = json.loads(json.dumps(source, ensure_ascii=False))
    target = ids.rewrite(source)
    node = target["learning_spans"][0]
    assert node["id"] == ids.get("spans", "outer")
    assert node["children"] == [ids.get("spans", "inner")]
    assert node["overlap_ids"] == [ids.get("spans", "cross")]
    assert node["token_ids"] == [ids.get("tokens", "atom")]
    for field in ("surface", "start", "end", "marker_ranges", "normalization_steps", "grammar_ids"):
        assert node[field] == source["learning_spans"][0][field]
    assert source == snapshot
