"""Independent expected teaching units, including tokenizer error cases."""
from copy import deepcopy

import pytest

from .nlp import tokenize_sentence
from .modules.linguistics.models import LearningSpan
from .modules.linguistics.morphology import analyze_morphology


def analyze(text):
    tokens = tokenize_sentence("morph-gold", text)
    before = deepcopy(tokens)
    spans = analyze_morphology("morph-gold", text, tokens)
    assert tokens == before
    for span in spans:
        LearningSpan.model_validate(span)
        assert text[span["start"]:span["end"]] == span["surface"]
        assert span["token_ids"]
    return spans


@pytest.mark.parametrize("text,surface,lemma,features", [
    ("本を読みました。", "読みました", "読む", {"polite", "past"}),
    ("本を読ませました。", "読ませました", "読む", {"causative", "polite", "past"}),
    ("食べさせられなかった。", "食べさせられなかった", "食べる", {"causative", "voice_ambiguous", "negative", "past"}),
    ("渡らされた。", "渡らされた", "渡る", {"short_causative_passive", "past"}),
    ("させられた。", "させられた", "する", {"causative", "voice_ambiguous", "past"}),
    ("来させられる。", "来させられる", "来る", {"causative", "voice_ambiguous"}),
    ("勉強しています。", "勉強しています", "勉強する", {"te_form", "progressive_resultative", "polite"}),
    ("読みませんでした。", "読みませんでした", "読む", {"negative", "polite", "past"}),
    ("美しくなかった。", "美しくなかった", "美しい", {"negative", "past"}),
    ("高くない。", "高くない", "高い", {"negative"}),
    ("怖かった。", "怖かった", "怖い", {"past"}),
    ("静かでした。", "静かでした", "静か", {"copula", "polite", "past"}),
    ("好きではなかった。", "好きではなかった", "好き", {"copula", "negative", "past"}),
    ("おいしくありません。", "おいしくありません", "おいしい", {"negative", "polite"}),
    ("学生ではない。", "学生ではない", "学生", {"copula", "negative"}),
    ("学生ではありませんでした。", "学生ではありませんでした", "学生", {"copula", "negative", "polite", "past"}),
    ("食べたくありません。", "食べたくありません", "食べる", {"desiderative", "negative", "polite"}),
    ("食べたくなかった。", "食べたくなかった", "食べる", {"desiderative", "negative", "past"}),
    ("行っている。", "行っている", "行く", {"te_form", "progressive_resultative"}),
    ("開けてある。", "開けてある", "開ける", {"resultative"}),
    ("買っておいた。", "買っておいた", "買う", {"preparative", "past"}),
    ("忘れてしまった。", "忘れてしまった", "忘れる", {"completion", "past"}),
    ("食べちゃった。", "食べちゃった", "食べる", {"contracted_completion", "past"}),
    ("飲んじゃった。", "飲んじゃった", "飲む", {"contracted_completion", "past"}),
    ("会ってみた。", "会ってみた", "会う", {"trial", "past"}),
    ("読んでくれた。", "読んでくれた", "読む", {"benefactive", "past"}),
    ("読めば。", "読めば", "読む", {"conditional"}),
    ("食べれば。", "食べれば", "食べる", {"conditional"}),
    ("読もう。", "読もう", "読む", {"volitional"}),
    ("読め。", "読め", "読む", {"imperative"}),
    ("帰れる。", "帰れる", "帰る", {"potential"}),
    ("知らぬ。", "知らぬ", "知る", {"negative"}),
])
def test_real_sudachi_inflection_gold(text, surface, lemma, features):
    span = next(item for item in analyze(text) if item["surface"] == surface)
    assert span["lemma"] == lemma
    assert features <= set(span["features"])


def test_passive_potential_honorific_are_not_decided_without_context():
    span = analyze("食べられる。")[0]
    assert span["status"] == "ambiguous"
    assert {item["id"] for item in span["candidates"]} == {"passive", "potential", "honorific", "spontaneous"}
    assert all(item["status"] == "possible" for item in span["candidates"])


@pytest.mark.parametrize("text", ["書かれる。", "読まれる。", "話される。", "される。"])
def test_godan_and_suru_passives_do_not_offer_modern_potential(text):
    span = analyze(text)[0]
    assert "passive" in {item["id"] for item in span["candidates"]}
    assert "potential" not in {item["id"] for item in span["candidates"]}


@pytest.mark.parametrize("text,lexical,restored", [
    ("先生に励まされた。", "励ます", "励む"),
    ("秘密が明かされた。", "明かす", "明く"),
    ("読まされる。", "読ます", "読む"),
    ("書かされました。", "書かす", "書く"),
    ("走らされた。", "走らす", "走る"),
])
def test_dictionary_existence_does_not_decide_short_causative_homographs(text, lexical, restored):
    span = analyze(text)[0]
    assert span["lemma"] == lexical
    assert span["status"] == "ambiguous"
    assert "short_causative_passive" not in span["features"]
    candidates = {item["id"]: item for item in span["candidates"]}
    assert candidates["passive"]["lemma"] == lexical
    assert candidates["causative_passive"]["lemma"] == restored
    assert all(row["status"] == "possible" for row in candidates.values())
    assert candidates["causative_passive"]["derivation"][0] == restored
    assert candidates["causative_passive"]["derivation"][-1] == span["surface"]


def test_manual_short_causative_selection_changes_head_without_mutating_original_and_clear_restores():
    from .modules.linguistics.ambiguity import apply_override
    span = analyze("先生に励まされた。")[0]
    selected = apply_override(span, {"revision": 1, "source": "user", "choice_id": "causative_passive"})
    assert selected["lemma"] == selected["steps"][0]["lemma"] == "励む"
    assert selected["captures"]["dictionary_form"] == "励む"
    assert selected["derivation"][0] == "励む"
    assert span["lemma"] == "励ます"
    assert apply_override(span, {"revision": 2, "source": "user", "choice_id": None})["lemma"] == "励ます"
    assert apply_override(span, {"revision": 2, "source": "user", "choice_id": None})["captures"]["dictionary_form"] == "励ます"
    assert apply_override(span, {"revision": 3, "source": "ai", "choice_id": "causative_passive"})["lemma"] == "励ます"


def test_causative_voice_candidates_remain_legal_and_unselected():
    span = analyze("食べさせられる。")[0]
    assert "causative_passive" in {item["id"] for item in span["candidates"]}
    assert span["status"] == "ambiguous"


def test_reading_key_uses_dictionary_form_not_inflected_reading():
    span = analyze("読みました。")[0]
    assert span["reading"] == "ヨム"


@pytest.mark.parametrize("text", ["桜が咲く。", "昨日買った本を読んだ。", "山田さんに会う。", "目を閉じた。", "三回目に来た。"])
def test_morphology_does_not_swallow_nominal_arguments(text):
    spans = analyze(text)
    assert all("が" not in item["surface"] and "を" not in item["surface"] and "さん" not in item["surface"] for item in spans)


def test_legacy_tokens_are_unknown_instead_of_guessing_class_from_ru():
    token = {"id": "old", "start": 0, "end": 2, "surface": "帰る", "lemma": "帰る", "reading": "カエル", "part_of_speech": "動詞-一般"}
    span = analyze_morphology("old", "帰る", [token])[0]
    assert span["status"] == "unknown"
    assert span["features"] == ["unknown"]
    assert span["reading"] == ""


def test_arbitrary_dictionary_ending_is_not_restored_as_short_causative():
    tokens = tokenize_sentence("fake", "話される")
    tokens[0]["lemma"] = "架空ざます"
    tokens[0]["conjugation_type"] = "五段-サ行"
    spans = analyze_morphology("fake", "話される", tokens)
    assert "short_causative_passive" not in spans[0]["features"]


def test_emoji_unicode_and_newline_are_replayed_without_merging():
    text = "🍧本を読みました。\n食べさせた。"
    spans = analyze(text)
    assert [item["surface"] for item in spans] == ["読みました", "食べさせた"]
    assert spans[0]["start"] == 3


def test_invalid_original_text_offsets_are_rejected():
    tokens = tokenize_sentence("s", "読む。")
    tokens[0]["surface"] = "飲む"
    with pytest.raises(ValueError, match="replay"):
        analyze_morphology("s", "読む。", tokens)


def test_punctuation_prevents_cross_sentence_auxiliary_chain():
    spans = analyze("読む。ない。")[0]
    assert spans["surface"] == "読む"
    assert "negative" not in spans["features"]


def test_reading_a_lexical_ru_verb_does_not_claim_a_potential():
    span = analyze("切れる。")[0]
    assert "potential" not in span["features"]


def test_damaged_te_auxiliary_sequence_remains_unresolved_atoms():
    # Actual core-dictionary parsing for 読んどいた can be 読ん / ど / い / た.
    # Do not silently reinterpret this as a validated ておく chain.
    spans = analyze("読んどいた。")
    assert all("preparative" not in item["features"] for item in spans)


@pytest.mark.parametrize("text,expected", [
    ("食べさせられなかった。", ["食べる", "食べさせる", "食べさせられる", "食べさせられない", "食べさせられなかった"]),
    ("読ませました。", ["読む", "読ませる", "読ませます", "読ませました"]),
    ("読みませんでした。", ["読む", "読みます", "読みません", "読みませんでした"]),
    ("飲んじゃった。", ["飲む", "飲んでしまう", "飲んじゃう", "飲んじゃった"]),
    ("読んでる。", ["読む", "読んでいる", "読んでる"]),
    ("帰りたがっている。", ["帰る", "帰りたがる", "帰りたがって", "帰りたがっている"]),
    ("本を読み\nました。", ["読む", "読みます", "読みました"]),
    ("眠そうです。", ["眠い", "眠そう", "眠そうです"]),
    ("読んだ。", ["読む", "読んだ"]),
])
def test_complete_derivation_forms_found_from_independent_baseline(text, expected):
    span = analyze(text)[0]
    assert span["derivation"] == expected


def test_soft_layout_shadow_retains_all_original_ids_and_newline():
    text = "本を読み\nました。"
    tokens = tokenize_sentence("soft", text)
    span = analyze_morphology("soft", text, tokens)[0]
    assert span["surface"] == "読み\nました"
    assert span["token_ids"] == [item["id"] for item in tokens if span["start"] <= item["start"] < span["end"]]
    assert "soft_line_shadow_projection" in span["evidence"]


def test_sahen_noun_rule_cannot_swallow_a_second_predicate_after_ba():
    spans = analyze("練習すればするほど上達する。")
    assert [item["surface"] for item in spans] == ["練習すれば", "する", "上達する"]
    assert spans[0]["derivation"] == ["練習する", "練習すれば"]


def test_combining_latin_mark_does_not_change_japanese_voiced_past():
    span = analyze("Caféで本を読んだ。")[0]
    assert span["surface"] == "読んだ"
    assert span["start"] == 8
    assert "past" in span["features"]
