from .nlp import kata, split_sentences, tokenize_sentence


def test_split_sentences_preserves_offsets():
    text = " 彼は言った。「行こう！」\nしかし、雨だった。"
    spans = split_sentences(text)
    assert [text[item.start:item.end] for item in spans] == [item.text for item in spans]
    assert [item.text for item in spans] == ["彼は言った。", "「行こう！」", "しかし、雨だった。"]


def test_token_offsets_are_exact():
    text = "本を読みました。"
    tokens = tokenize_sentence("s1", text)
    assert "".join(token["surface"] for token in tokens) == text
    assert all(text[token["start"]:token["end"]] == token["surface"] for token in tokens)


def test_hiragana_to_katakana():
    assert kata("たべる") == "タベル"

