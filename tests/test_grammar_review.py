import json
from pathlib import Path
import pytest
from backend.modules.linguistics.service import analyze_sentence

CASES = [json.loads(line) for line in (Path(__file__).parent/'fixtures/linguistics/review-v1.jsonl').read_text(encoding='utf-8').splitlines()]


@pytest.mark.parametrize('case',CASES,ids=lambda row:row['id'])
def test_frozen_teaching_units(case):
    spans = analyze_sentence(case['id'],case['text'])['learning_spans']
    morph = [span for span in spans if span['kind']=='morphology']
    if case['category']=='negative':
        assert not any(set(case['rejected_features']) & set(span['features']) for span in morph),case
        assert not any(span['surface']==case['rejected_prefix_span'] and 'honorific' in span['features'] for span in morph),case
        return
    selected = [span for span in morph if span['surface']==case['surface']]
    assert len(selected)==1,(case,[(span['surface'],span['features']) for span in morph])
    span=selected[0]
    assert span['lemma']==case['lemma'],(case,span)
    assert set(case['required_features'])<=set(span['features']),(case,span['features'])
    assert case['text'][span['start']:span['end']]==span['surface']
