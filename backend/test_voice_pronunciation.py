from .nlp import pronunciation_text


def test_voice_text_uses_hiragana_reading_and_preserves_punctuation():
    assert pronunciation_text("今日は晴れです。AIも使う！") == "きょうははれです。えーあいもつかう！"
