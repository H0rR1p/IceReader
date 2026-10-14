from copy import deepcopy
import pytest

from . import nlp
from .nlp import kata, split_sentences, tokenize_sentence


def test_split_sentences_preserves_offsets():
    text = " 彼は言った。「行こう！」\nしかし、雨だった。"
    spans = split_sentences(text)
    assert [text[item.start:item.end] for item in spans] == [item.text for item in spans]
    assert [item.text for item in spans] == ["彼は言った。", "「行こう！」", "しかし、雨だった。"]


def test_only_unpunctuated_single_line_break_is_uncertain():
    text = "文の途中で\n改行する。\n次の文！\n\n新段落"
    spans = split_sentences(text)
    assert [item.text for item in spans] == ["文の途中で\n改行する。", "次の文！", "新段落"]
    assert not any(item.uncertain_after for item in spans)


def test_quotes_and_repeated_terminal_marks_do_not_require_ai_review():
    spans = split_sentences("「本当!?」次です。")
    assert [item.text for item in spans] == ["「本当!?」", "次です。"]
    assert not any(item.uncertain_after for item in spans)


def test_structural_hard_boundary_is_preserved():
    text = "第一章\n吾輩は猫である。"
    spans = split_sentences(text, {3})
    assert [item.text for item in spans] == ["第一章", "吾輩は猫である。"]


def test_token_offsets_are_exact():
    text = "本を読みました。"
    tokens = tokenize_sentence("s1", text)
    assert "".join(token["surface"] for token in tokens) == text
    assert all(text[token["start"]:token["end"]] == token["surface"] for token in tokens)


@pytest.mark.parametrize("text", [
    "そうか…", "そうか……", "そうか……？", "……", "……？", "猫……犬", "猫…  …犬",
    "🍧猫……？犬。", "ＡＢＣ……。", "ｶﾀｶﾅ……", "　猫\n……　犬。",
])
def test_ellipsis_normalization_never_creates_empty_or_shifted_original_atoms(text):
    from .modules.linguistics.service import analyze_sentence
    tokens = tokenize_sentence("ellipsis", text)
    assert "".join(token["surface"] for token in tokens) == text
    assert all(0 <= token["start"] < token["end"] <= len(text) for token in tokens)
    assert all(text[token["start"]:token["end"]] == token["surface"] for token in tokens)
    result = analyze_sentence("ellipsis", text, tokens)
    assert all(text[span["start"]:span["end"]] == span["surface"] for span in result["learning_spans"])


def test_existing_zero_width_punctuation_is_filtered_only_in_the_analysis_view():
    from .modules.linguistics.service import analyze_sentence
    text = "そうか……"
    tokens = tokenize_sentence("ellipsis", text)
    tokens.insert(3, {"id": "legacy-empty", "surface": "", "start": 4, "end": 4,
                      "pos_full": ["補助記号", "句点", "*", "*", "*", "*"]})
    before = deepcopy(tokens)
    result = analyze_sentence("ellipsis", text, tokens)
    assert tokens == before
    assert not any("legacy-empty" in span["token_ids"] for span in result["learning_spans"])
    tokens[3]["pos_full"][0] = "名詞"
    with pytest.raises(ValueError):
        analyze_sentence("ellipsis", text, tokens)


def test_hiragana_to_katakana():
    assert kata("たべる") == "タベル"


def test_sudachi_dictionary_is_created_lazily_once(monkeypatch):
    created = []
    sentinel = object()

    class FakeDictionary:
        def __init__(self, *, dict):
            assert dict == "core"

        def create(self):
            created.append(True)
            return sentinel

    monkeypatch.setattr(nlp, "_tokenizer", None)
    monkeypatch.setattr(nlp.dictionary, "Dictionary", FakeDictionary)

    assert nlp._get_tokenizer() is sentinel
    assert nlp._get_tokenizer() is sentinel
    assert len(created) == 1

