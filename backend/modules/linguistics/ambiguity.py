"""User-confirmed candidate selection with source and revision checks."""
import copy
import hashlib
from fastapi import HTTPException
from . import repository

def checked_span(user, request):
    from .service import analyze_sentence, owned_sentence
    sentence, chapter = owned_sentence(user, request['sentence_id'])
    result = analyze_sentence(sentence['id'], sentence['original'], revision=int(sentence.get('analysis_revision', chapter.get('analysis_revision', 1))))
    manifest = result['analysis_manifest']
    if any(request.get(key) != manifest[target] for key, target in [('text_hash','text_hash'),('version','version'),('analysis_revision','revision')]):
        raise HTTPException(409, '原文或规则版本已变化，请刷新后重试')
    span = next((value for value in result['learning_spans'] if value['id'] == request['span_id']), None)
    if not span:
        raise HTTPException(422, '结构锚点不存在')
    return sentence, span, manifest


def apply_override(span, override):
    value = copy.deepcopy(span)
    if not override:
        value['override_revision'] = 0
        return value
    value['override_revision'] = override['revision']
    if not override.get('choice_id') or override['choice_id'] not in {row['id'] for row in value['candidates']}:
        return value
    value['source'] = override['source']
    value['status'] = 'user_confirmed' if override['source'] == 'user' else 'ambiguous'
    for candidate in value['candidates']:
        candidate['status'] = 'selected' if candidate['id'] == override['choice_id'] else 'possible'
        if candidate['status'] == 'selected' and candidate.get('lemma') and override['source'] == 'user':
            value['lemma'] = candidate['lemma']
            value['reading'] = candidate.get('reading', '')
            value['derivation'] = candidate.get('derivation', [])
            value['captures']['dictionary_form'] = candidate['lemma']
            if value.get('steps'):
                value['steps'][0]['lemma'] = candidate['lemma']
    value['evidence'] += [str(override.get('reason', ''))]
    value['choice_id'] = override['choice_id']
    return value


def _compatible_legacy(override, span, manifest):
    return (override.get('source') == 'user'
        and override.get('_stored_text_hash') == manifest['text_hash']
        and str(override.get('version', '')).startswith('linguistics-v2:')
        and manifest['version'].startswith('linguistics-v3:')
        and (override.get('start'), override.get('end'), override.get('analysis_revision'))
            == (span['start'], span['end'], manifest['revision'])
        and override.get('choice_id') in {value['id'] for value in span['candidates']})


def _legacy_identity(sentence_id, span, old_version):
    if 'composed' in span.get('features', []) or 'functional_expression' in span.get('features', []):
        return set()
    kinds = ['morphology'] if span['kind'] == 'morphology' else [value for value in span['grammar_ids']
             if not value.startswith('ja.functional.')]
    parts = old_version.split(':')
    if len(parts) < 2:
        return set()
    return {'span_' + hashlib.sha256(f"{sentence_id}:{span['start']}:{span['end']}:{kind}:{parts[1]}".encode()).hexdigest()[:24]
            for kind in kinds}


def restore_choices(user, result, context_hash):
    manifest=result['analysis_manifest']
    overrides = repository.load_sentence_overrides(user, result['sentence_id'])
    spans=[]
    for span in result['learning_spans']:
        override = overrides.get(span['id'])
        if not override and span.get('candidates'):
            for old_id, old in overrides.items():
                if (_compatible_legacy(old, span, manifest)
                        and old_id in _legacy_identity(result['sentence_id'], span, str(old.get('version', '')))):
                    try:
                        override = repository.save_span_override(user, result['sentence_id'], span['id'],
                            manifest['text_hash'], manifest['version'], 0,
                            {**{key: old[key] for key in ('choice_id', 'source', 'start', 'end', 'analysis_revision')},
                             'reason': '延续旧版相同原文候选的用户确认', 'legacy_span_id': old_id,
                             'legacy_version': old['version']})
                        override['_stored_text_hash'] = manifest['text_hash']
                    except HTTPException as error:
                        if error.status_code != 409:
                            raise
                        override = repository.load_span_override(user, result['sentence_id'], span['id'],
                            manifest['text_hash'], manifest['version'], include_retired=True)
                    break
        if override and (override.get('_stored_text_hash') != manifest['text_hash'] or override.get('version') != manifest['version']):
            # Carry only unchanged, explicitly confirmed legacy candidates.
            # Otherwise retain the CAS revision so a fresh choice can replace
            # a retired row without silently discarding its history.
            compatible = _compatible_legacy(override, span, manifest)
            if not compatible:
                override = {**override, 'choice_id': None}
        if override and override.get('source') != 'user':
            # Keep the CAS revision so a user can replace a retired suggestion.
            override={**override, 'choice_id': None}
        spans.append(apply_override(span,override))
    return {**result,'learning_spans':spans}


def choose(user, request, choice_id):
    sentence, span, manifest = checked_span(user, request)
    if choice_id is not None and choice_id not in {row['id'] for row in span['candidates']}:
        raise HTTPException(422, '只能选择已存在的候选')
    value = repository.save_span_override(user, sentence['id'], span['id'], manifest['text_hash'], manifest['version'], request.get('expected_override_revision'),
        {'choice_id': choice_id, 'source': 'user', 'reason': '用户确认' if choice_id else '用户清除选择',
         'start': span['start'], 'end': span['end'], 'analysis_revision': manifest['revision']})
    return {'status': 'confirmed' if choice_id else 'unresolved', 'choice': value, 'span': apply_override(span, value)}
