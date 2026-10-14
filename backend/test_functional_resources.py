"""Completeness and tagset-attachment gates for the generated resources."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import zipfile
import xml.etree.ElementTree as ET

import pytest
from sudachipy import dictionary, tokenizer

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def catalog():
    return json.loads((ROOT / "resources/grammar/functional_expressions.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def inventory():
    return json.loads((ROOT / "resources/grammar/functional_inventory.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sudachi():
    return dictionary.Dictionary()


def accepts(catalog, code, morph):
    code = code[:2]
    rule = catalog["connections"][code]
    pos = list(morph.part_of_speech())
    index = catalog["pos_sets"].index(pos)
    if any(accepts(catalog, ref, morph) for ref in rule["refs"]):
        return True
    return any(index in row["pos_ids"] and (not row["lemma"] or morph.dictionary_form() in row["lemma"])
               for row in rule["patterns"])


def test_every_surface_and_all_nine_ancestor_levels_are_preserved(catalog):
    assert catalog["source"]["sha256"] == "38ecf8f9d147edd7c167d31fa0a8c11648a7896fbdb1d66544aacd98c4697270"
    assert catalog["counts"]["levels"] == {"1": 341, "2": 435, "3": 555, "4": 774, "5": 1187, "6": 1810,
                                            "7": 6870, "8": 9722, "9": 16801}
    assert len(catalog["entries"]) == len({entry["id"] for entry in catalog["entries"]}) == 16801
    nodes = {node["id"]: node for node in catalog["nodes"]}
    assert len(nodes) == 38495
    for entry in catalog["entries"]:
        assert entry["surface"] == "".join(entry["segments"])
        assert len(entry["path"]) == 9
        assert entry["path"][-1] == entry["id"]
        for index, node_id in enumerate(entry["path"]):
            assert nodes[node_id]["level"] == index + 1
            assert nodes[node_id]["parent"] == (entry["path"][index - 1] if index else None)


def test_published_ambiguous_janai_ka_senses_are_not_collapsed(catalog):
    candidates = [e for e in catalog["entries"] if e["surface"] == "じゃないか"]
    assert {e["id"] for e in candidates} == {"2441M.1hx.28n01", "2442M.1hx.28n01"}
    assert {e["mclass"] for e in candidates} == {"w21", "x21"}
    assert all(e["left"] == "g090" and e["right"] == "6K90" for e in candidates)


def test_all_semantic_classes_have_original_chinese_and_valid_parent(catalog):
    classes = catalog["semantic_classes"]
    assert len(classes) == 372
    for code, meaning in classes.items():
        assert meaning["name_zh"] and len(meaning["explanation_zh"]) >= 8
        assert meaning["explanation_source"] == "IceReader original Chinese teaching text"
        assert meaning["parent"] is None or meaning["parent"] in classes
    assert {entry["mclass"] for entry in catalog["entries"]} <= classes.keys()
    assert "本句" not in classes["w21"]["explanation_zh"]  # no fabricated contextual resolution


def test_every_used_attachment_code_has_a_finite_mapping(catalog):
    definitions = catalog["connections"]
    assert len(definitions) == 117
    assert definitions["90"]["patterns"][0]["legacy"] == ["*"] * 7
    assert set(definitions["90"]["patterns"][0]["pos_ids"]) == set(range(1558))
    for code, rule in definitions.items():
        assert not (rule["patterns"] and rule["refs"])
        assert all(ref in definitions for ref in rule["refs"])
        if code != "7V":
            assert all(p["pos_ids"] for p in rule["patterns"])
    used = {entry[key][:2] for entry in catalog["entries"] for key in ("left", "right")}
    assert "7V" not in used
    assert not any("7V" in row["refs"] for row in definitions.values())
    assert definitions["7V"]["mapping_status"] == "unrepresented_unused"
    assert catalog["connection_semantics"]["right"].endswith("NOT following-token constraint")


@pytest.mark.parametrize("text,index,code,accepted", [
    ("行かない", 0, "2M", True), ("行く", 0, "2M", False),
    ("食べない", 0, "2m", True), ("食べる", 0, "2m", False),
    ("食べて", 0, "2g", True), ("話して", 0, "2g", True), ("行きます", 0, "2g", False),
    ("書いて", 0, "b8", True), ("読んで", 0, "2h", True), ("書いて", 0, "2h", False),
    ("猫", 0, "b8", False),
    ("高くて", 0, "3I", True), ("高かった", 0, "3H", True),
    ("高い", 0, "3H", False), ("高こうございます", 0, "3K", True),
    ("学生だ", 1, "7A", True), ("学生だった", 1, "7H", True),
    ("学生なだけ", 1, "7D", True), ("学生だろう", 1, "8N", True),
    ("行かずに", 1, "82", True),
    ("これ", 0, "10", True), ("よかったんじゃないか", 2, "g0", True),
    ("問題でもあるか", 1, "10", False), ("問題でもあるか", 2, "10", False),
    ("読んで", 1, "6H", True), ("問題でもあるか", 4, "6H", False),
])
def test_real_sudachi_legal_and_illegal_attachments(catalog, sudachi, text, index, code, accepted):
    morph = sudachi.create().tokenize(text, tokenizer.Tokenizer.SplitMode.A)[index]
    assert accepts(catalog, code, morph) is accepted


def test_inventory_covers_every_functional_pos_and_preserves_variants(inventory):
    assert inventory["counts"] == {"all_pos": 1558, "functional_pos": 782, "all_dictionary_words": 1626778,
                                    "functional_words": 9593,
                                    "by_kind": {"auxiliary": 1189, "particle": 374, "support": 8030}}
    rows = inventory["forms"]
    assert len(rows) == len({row["word_id"] for row in rows}) == 9593
    assert {row["pos_id"] for row in rows} == set(inventory["functional_pos_ids"])
    particle_classes = {inventory["pos"][row["pos_id"]][1] for row in rows if row["kind"] == "particle"}
    assert particle_classes == {"格助詞", "係助詞", "副助詞", "接続助詞", "終助詞", "準体助詞"}
    assert {"で", "も", "じゃ", "ない", "ぬ", "ます", "ん", "っけ"} <= {row["surface"] for row in rows}
    assert {"連用形-促音便", "未然形-サ", "意志推量形", "仮定形-融合"} <= {
        inventory["pos"][row["pos_id"]][5] for row in rows}


def test_binary_enumeration_matches_public_api_for_each_pos(inventory, sudachi):
    # Verify every directly retrievable POS configuration. Nine additional
    # literary verb POS configurations contain only split-only dictionary
    # records (left/right=-1), which must still remain in the full inventory.
    representatives = {}
    for row in inventory["forms"]:
        if row["pos_id"] not in representatives and any(m.word_id() == row["word_id"] for m in sudachi.lookup(row["surface"])):
            representatives[row["pos_id"]] = row
    assert len(representatives) == 773
    missing = set(inventory["functional_pos_ids"]) - representatives.keys()
    assert missing == {798, 807, 808, 810, 815, 816, 819, 820, 824}
    assert all(row["split_only"] for row in inventory["forms"] if row["pos_id"] in missing)
    for pos_id, row in representatives.items():
        candidates = list(sudachi.lookup(row["surface"]))
        actual = next(m for m in candidates if m.word_id() == row["word_id"])
        assert list(actual.part_of_speech()) == inventory["pos"][pos_id]
        assert actual.dictionary_form() == row["lemma"]
        assert actual.reading_form() == row["reading"]
        assert actual.normalized_form() == row["normalized"]


def test_unknown_legacy_connection_field_is_rejected(catalog):
    spec = importlib.util.spec_from_file_location("build_functional_resources", ROOT / "scripts/build_functional_resources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with pytest.raises(ValueError, match="Unknown legacy conjugation form"):
        module.compile_pattern(["動詞", "*", "*", "*", "*", "未登録の形", "*"], catalog["pos_sets"])


def test_all_upstream_local_attributes_are_losslessly_preserved(catalog):
    archive = ROOT / "build/legal-cache/tsutsuji-1.1u.zip"
    if not archive.exists():
        pytest.skip("Optional full source audit requires the corresponding-source/cache archive")
    with zipfile.ZipFile(archive) as source:
        root = ET.fromstring(source.read("tsutsuji-1.1u/tsutsuji1.1.xml"))
    actual = {node["id"]: node["attrs"] for node in catalog["nodes"]}
    upstream = {}
    for node in root.iter():
        level = node.tag.rsplit("}", 1)[-1]
        if level.startswith("L"):
            number = int(level[1:])
            key = "ID" if number == 9 else ("L1ID" if number == 1 else f"L1to{number}ID")
            upstream[node.attrib[key]] = node.attrib
    assert actual == upstream
