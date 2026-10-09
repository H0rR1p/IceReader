"""Grammar candidates reuse the existing note/card scheduler and source snapshots."""
from fastapi import HTTPException
from ..library.sources import get_sentence_source, get_book_source
from ..linguistics.service import structure_results
from ..linguistics.rules import get_rule
from .service import add_candidate


def create(user_id: str, payload: dict) -> dict:
    source = get_sentence_source(user_id, payload['sentence_id'])
    if not source:
        raise HTTPException(404, '句子不存在或无权访问')
    sentence, chapter = source
    structure = structure_results(user_id, [sentence['id']])['results'][0]
    manifest = structure['analysis_manifest']
    if payload['version'] != manifest['version'] or payload['text_hash'] != manifest['text_hash'] or payload['analysis_revision'] != manifest['revision']:
        raise HTTPException(409, '结构版本已变化，请刷新后再创建卡片')
    span = next((value for value in structure['learning_spans'] if value['id'] == payload['span_id']), None)
    rule = get_rule(payload['grammar_id'])
    if not span or payload['grammar_id'] not in span['grammar_ids'] or not rule:
        raise HTTPException(422, '此结构不包含所选语法')
    book, _ = get_book_source(user_id, chapter['bookId'])
    template = payload['card_template']
    if template == 'form-restoration' and (span['kind'] != 'morphology' or not span.get('lemma') or len(span.get('derivation', [])) < 2):
        raise HTTPException(422, '此结构不适合词形恢复题')
    text, start, end = sentence['original'], span['start'], span['end']
    question = text if template == 'grammar-recognition' else text[:start] + '【____】' + text[end:]
    answer = rule['label'] + '：' + rule['explanation_zh'] if template == 'grammar-recognition' else span['surface']
    snapshot = {'kind': 'grammar', 'grammar_id': rule['id'], 'span_id': span['id'],
        'quote': span['surface'], 'start': start, 'end': end, 'chapter_anchor': sentence['start'] + start,
        'analysis_revision': manifest['revision'], 'rules_version': manifest['rules_version'],
        'source_revision': int(chapter.get('analysis_revision',1)),
        'generation_id': chapter.get('active_generation') or f"legacy:{chapter['id']}:{int(chapter.get('analysis_revision',1))}",
        'original_sentence': text, 'question': question, 'answer': answer, 'base_form': span['lemma'],
        'explanation': span['explanation_zh'], 'derivation': span['derivation']}
    return add_candidate(user_id, {'item': {'id': 'grammar:' + rule['id'], 'type': 'grammar',
        'canonical_key': rule['id'], 'grammar_pattern': rule['label']},
        'lemma': rule['label'] if template == 'grammar-recognition' else span['lemma'], 'reading': '',
        'gloss': answer, 'sentence': text, 'book_id': book['id'], 'book_title': book.get('title',''),
        'chapter_id': chapter['id'], 'sentence_id': sentence['id'], 'card_template': template, 'source': snapshot})
