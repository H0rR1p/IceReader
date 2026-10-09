import pytest
from .nlp import tokenize_sentence
from .modules.linguistics.service import analyze_sentence


@pytest.mark.parametrize('text,surface,lemma,feature',[
    ('先生がお読みになりました。','お読みになりました','読む','honorific'),
    ('先生がお話になった。','お話になった','話す','honorific'),
    ('先生がご覧になりました。','ご覧になりました','見る','honorific'),
    ('先生がおいでになりました。','おいでになりました','おいでになる','honorific'),
    ('私がご案内いたします。','ご案内いたします','案内する','humble'),
    ('ご説明いただきました。','ご説明いただきました','説明する','humble_benefactive'),
    ('ご確認くださった。','ご確認くださった','確認する','respectful_benefactive'),
    ('先生に来ていただいた。','来ていただいた','来る','humble_benefactive'),
    ('先生がおっしゃいました。','おっしゃいました','おっしゃる','honorific'),
    ('私が申し上げました。','申し上げました','申し上げる','humble'),
])
def test_complete_honorifics_keep_original_atoms(text,surface,lemma,feature):
    tokens=tokenize_sentence('honorific',text)
    result=analyze_sentence('honorific',text,tokens)
    span=next(row for row in result['learning_spans'] if row['kind']=='morphology' and row['surface']==surface)
    assert span['lemma']==lemma and feature in span['features']
    assert span['derivation'][-1]==surface
    assert span['token_ids']==[row['id'] for row in tokens if span['start']<=row['start'] and row['end']<=span['end']]
    assert all(text[row['start']:row['end']]==row['surface'] for row in tokens)


def test_nominal_rest_and_honorific_rest_remain_candidates():
    for text in ['先生がお休みになった。','明日はお休みになった。']:
        span=next(row for row in analyze_sentence('rest',text)['learning_spans'] if row['surface']=='お休みになった')
        assert span['status']=='ambiguous'
        assert {row['id'] for row in span['candidates']}=={'honorific','nominal_change'}


def test_beginner_explanations_keep_uncertainty_without_internal_jargon():
    from .modules.linguistics.rules import get_rule_catalog,morphology_rules
    texts=[row['explanation_zh'] for row in get_rule_catalog()]+list(morphology_rules()['features'].values())
    assert all(not any(word in text for word in ['前项','后项','配价','体貌','词素','谓词','强判','锚点']) for text in texts)
    assert all(len(text)<=100 for text in texts)
