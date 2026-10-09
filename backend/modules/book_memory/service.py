import hashlib
import re
import uuid
from fastapi import HTTPException
from ...nlp import tokenize_sentence
from ..library.sources import get_book_source
from ..linguistics.repository import invalidate_sentences
from . import repository
from .models import EntityInput, FactInput


def _book(user, book):
    return get_book_source(user, book)


def read(user, book):
    _book(user, book)
    return repository.snapshot(user, book)


def save_entity(user, book, request: EntityInput, entity_id=None):
    _book(user, book)
    name = request.name.strip()
    aliases = list(dict.fromkeys(value.strip() for value in request.aliases if value.strip()))
    if not name or any(len(value) > 100 for value in aliases):
        raise HTTPException(422, '名称或别名不合法')
    before=read(user,book)
    previous = repository.get(user, book, 'entity', entity_id) if entity_id else {}
    value=repository.put(user, book, 'entity', {**previous, 'id': entity_id or str(uuid.uuid4()),
        'name': name, 'aliases': aliases, 'source': 'user', 'deleted': False}, request.expected_revision)
    return {**value,'affected_sentence_ids':_invalidate(user,book,before,repository.snapshot(user,book))}


def validate_evidence(user, book, evidence):
    _, chapters = _book(user, book)
    by_id = {chapter['id']: chapter for chapter in chapters}
    result = []
    for item in evidence:
        value = item.model_dump() if hasattr(item, 'model_dump') else dict(item)
        chapter = by_id.get(value['chapter_id'])
        if not chapter:
            raise HTTPException(404, '证据不属于此书')
        text = chapter.get('text', '')
        if not (0 <= value['start'] < value['end'] <= len(text)) or text[value['start']:value['end']] != value['quote']:
            raise HTTPException(422, '证据范围与原文不一致')
        revision = int(chapter.get('analysis_revision', 1))
        if int(value.get('source_revision', 1)) != revision:
            raise HTTPException(409, '证据来源版本已变化')
        result.append({**value, 'chapter_order': int(chapter.get('order', 0))})
    return result


def _invalidate(user, book, before, after):
    changed = [value['id'] for value in after['facts'] if next((old.get('revision') for old in before['facts'] if old['id'] == value['id']), 0) != value['revision']]
    affected=set(repository.affected_sentences(user, book, changed))
    for sentence_id,target in repository.context_targets(user,book):
        arguments={'budget':target['budget'],'allow_future':target['allow_future']}
        prior=context_facts(user,book,target['source_text'],target['chapter_order'],target['target_end'],snapshot=before,**arguments)
        current=context_facts(user,book,target['source_text'],target['chapter_order'],target['target_end'],snapshot=after,**arguments)
        if prior!=current: affected.add(sentence_id)
    affected=sorted(affected)
    invalidate_sentences(user, affected)
    from ..library.sources import mark_context_stale
    mark_context_stale(user,affected,'人物事实或名称修订')
    return affected


def invalidate_snapshot_change(user,book,before,after):
    """Public hook for sync after a validated user-owned snapshot change."""
    _book(user,book)
    return _invalidate(user,book,before,after)


def save_fact(user, book, entity_id, request: FactInput, fact_id=None):
    repository.get(user, book, 'entity', entity_id)
    evidence = validate_evidence(user, book, request.evidence)
    if request.status == 'candidate' and not evidence:
        raise HTTPException(422, '候选事实需要原文证据；人工确认可以直接设置')
    before = read(user, book)
    previous = repository.get(user, book, 'fact', fact_id) if fact_id else {}
    if previous and previous['entity_id'] != entity_id:
        raise HTTPException(404, '事实不属于此人物')
    value = repository.put_fact(user, book, {**previous, 'id': fact_id or str(uuid.uuid4()),
        'entity_id': entity_id, 'key': request.key, 'value': request.value.strip(),
        'status': request.status, 'evidence': evidence, 'source': 'user', 'deleted': False}, request.expected_revision)
    affected = _invalidate(user, book, before, repository.snapshot(user, book))
    return {'fact': value, 'affected_sentence_ids': affected}


def merge(user, book, entity_id, target, expected_revision):
    _book(user, book)
    source = repository.get(user, book, 'entity', entity_id)
    destination = repository.get(user, book, 'entity', target)
    if target == entity_id or source.get('merged_into') or destination.get('merged_into') or destination.get('deleted'):
        raise HTTPException(422, '不能合并自身或已合并的人物')
    before=read(user,book)
    value=repository.put(user, book, 'entity', {**source, 'merged_into': target}, expected_revision)
    return {**value,'affected_sentence_ids':_invalidate(user,book,before,repository.snapshot(user,book))}


def undo(user, book, revision_id):
    before = read(user, book)
    value = repository.undo(user, book, revision_id)
    affected = _invalidate(user, book, before, repository.snapshot(user, book))
    return {'object': value, 'affected_sentence_ids': affected}


def context_facts(user, book, text, chapter_order, target_end, *, budget=200, allow_future=False, snapshot=None):
    """Only matched entities and usable evidence influence a context hash."""
    snapshot = snapshot if snapshot is not None else repository.snapshot(user, book)
    entities = {value['id']: value for value in snapshot['entities'] if not value.get('deleted')}
    matched = set()
    for entity in entities.values():
        if any(name and name in text for name in [entity['name'], *entity.get('aliases', [])]):
            matched.add(entity['id'])
            if entity.get('merged_into') in entities:
                matched.add(entity['merged_into'])
    selected, remaining = [], budget
    for fact in sorted(snapshot['facts'], key=lambda value: value['id']):
        if fact['entity_id'] not in matched or fact.get('deleted') or fact['status'] not in {'confirmed', 'unknown', 'conflict'}:
            continue
        entity = entities.get(fact['entity_id'])
        if not entity:
            continue
        evidence = fact.get('evidence', [])
        available = [value for value in evidence if allow_future or value['chapter_order'] < chapter_order or (value['chapter_order'] == chapter_order and value['end'] <= target_end)]
        if not allow_future and evidence and not available:
            continue
        ref = {'id': fact['id'], 'revision': fact['revision'], 'entity_id': entity['id'],
               'name': entity['name'], 'key': fact['key'], 'value': fact['value'],
               'status': fact['status'], 'entity_revision': entity['revision']}
        ref['evidence'] = [{key: available[0][key] for key in ('chapter_id','end','source_revision','quote')}] if available else []
        ref['source'] = fact.get('source', 'user')
        cost = len(str({key: ref[key] for key in ('name','key','value','status','evidence')}).encode('utf-8'))
        if cost <= remaining:
            selected.append(ref)
            remaining -= cost
    return selected


def extract_candidates(user, book, chapter, start=0, end=None):
    """Local suggestions use explicit personal-name morphology or honorifics."""
    text = chapter.get('text', '')
    end = len(text) if end is None else end
    fragment = text[start:end]
    tokens = tokenize_sentence('entity-candidates', fragment)
    hits = [(token['surface'], start + token['start'], start + token['end']) for token in tokens
            if '人名' in token.get('pos_full', [])]
    hits.extend((match.group(1), start + match.start(1), start + match.end(1)) for match in re.finditer(r'([一-龯ァ-ヺ]{2,16})(?:さん|ちゃん|君|様)', fragment))
    existing = read(user, book)
    inserted = []
    for name, left, right in dict.fromkeys(hits):
        # Extraction never merges same-name records. A deterministic evidence
        # identity only makes rerunning this exact occurrence idempotent.
        identity = hashlib.sha256(f'{chapter["id"]}:{left}:{right}:{name}'.encode()).hexdigest()[:24]
        if any(value['id'] == identity for value in existing['entities']):
            continue
        evidence = {'chapter_id': chapter['id'], 'start': left, 'end': right,
                    'quote': text[left:right], 'source_revision': int(chapter.get('analysis_revision', 1)),
                    'chapter_order': int(chapter.get('order', 0))}
        entity = repository.put(user, book, 'entity', {'id': identity, 'name': name,
            'aliases': [], 'source': 'rule', 'status': 'candidate', 'evidence': [evidence]})
        inserted.append(entity)
    return inserted


def rebase_sources(user,event):
    """Idempotent delivery of a committed source-generation change."""
    book=event['book_id']
    before=read(user,book)
    from ..library.repository import load_chapter
    current=load_chapter(user,event['chapter_id'])
    current_revision=int(current.chapter.get('analysis_revision',1))
    mapping={item['old_id']:item for item in event['sentence_anchors']}
    for kind,rows in [('entity',before['entities']),('fact',before['facts'])]:
        for row in rows:
            changed=False; evidence=[]
            for anchor in row.get('evidence',[]):
                if anchor['chapter_id']==event['chapter_id'] and anchor['source_revision']==event['old_revision']:
                    prior=mapping.get(anchor.get('sentence_id'))
                    if current_revision!=event['new_revision']:
                        target=next((sentence for sentence in current.sentences if sentence['start']<=anchor['start']<sentence['end']),None)
                        prior={'new_id':target['id']} if target else None
                    anchor={**anchor,'original_source_revision':anchor.get('original_source_revision',anchor['source_revision']),
                            'source_revision':current_revision,
                            **({'original_sentence_id':anchor.get('original_sentence_id',anchor.get('sentence_id')),'sentence_id':prior['new_id']} if prior else {})}
                    changed=True
                evidence.append(anchor)
            if changed:
                repository.put(user,book,kind,{**row,'evidence':evidence},row['revision'])
    return invalidate_snapshot_change(user,book,before,repository.snapshot(user,book))
