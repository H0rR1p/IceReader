import pytest
from pydantic import ValidationError

from .models import TokenOut, ExplainSentenceRequest, SentenceOut
from .modules.linguistics.models import ContextPolicy
from .nlp import tokenize_sentence


def test_old_token_payload_remains_readable():
    row = TokenOut(id="t", sentence_id="s", start=0, end=1,
                   surface="字", lemma="字", reading="ジ",
                   part_of_speech="名詞", is_content=True)
    assert row.pos_full == [] and row.lemma_reading is None
    assert row.role == "unknown"


def test_actual_auxiliary_chain_retains_inflection_and_canonical_reading():
    tokens = tokenize_sentence("s", "本を読ませました。")
    by_surface = {row["surface"]: row for row in tokens}
    stem = by_surface["読ま"]
    assert stem["lemma"] == "読む"
    assert stem["surface_reading"] == "ヨマ"
    assert stem["lemma_reading"] == "ヨム"
    assert stem["conjugation_type"] == "五段-マ行"
    assert "未然形" in stem["conjugation_form"]
    assert all(by_surface[value]["role"] == "grammatical" for value in ["を", "せ", "まし", "た"])
    assert all("|".join(row["pos_full"][:2]).replace("|*", "") or row["part_of_speech"] for row in tokens)
    assert all(TokenOut(**{key: value for key, value in row.items() if key != "lexeme_key"})
               for row in tokens)


def test_context_policy_accepts_only_supported_windows():
    assert ContextPolicy(preceding_sentences=8).token_budget == 400
    with pytest.raises(ValidationError):
        ContextPolicy(preceding_sentences=3)
    with pytest.raises(ValidationError):
        ContextPolicy(token_budget=-1)


def test_legacy_explanation_and_zero_context_policy_are_distinct():
    sentence = SentenceOut(id="s", chapter_id="c", start=0, end=2,
                           original="本。", translation_zh="")
    assert ExplainSentenceRequest(sentence=sentence, tokens=[]).context_policy is None
    assert ExplainSentenceRequest(sentence=sentence, tokens=[],
                                  context_policy=ContextPolicy(preceding_sentences=0)).context_policy.preceding_sentences == 0
