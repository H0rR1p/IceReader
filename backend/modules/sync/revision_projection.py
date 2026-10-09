"""Apply accepted user revisions, preserving remote history and local source guards."""
import json
import time
import uuid
from fastapi import HTTPException
from ..book_memory import repository as memory, service as memory_service
from ..linguistics import repository as structures
from ..library.sources import get_sentence_source, get_book_source
from .protocol import revision_conflict, source_metadata


class SourceConflict(ValueError):
    pass


def _check_source(user, payload):
    for source in payload.get('sources', []):
        if not isinstance(source, dict):
            raise ValueError('invalid_source_reference')
        _check_source(user, {**source, 'book_id': payload.get('book_id')})
    metadata = source_metadata(payload)
    chapter_id = payload.get('chapter_id')
    book_id = payload.get('book_id')
    if chapter_id and book_id:
        _, chapters = get_book_source(user, book_id)
        chapter = next((item for item in chapters if item['id'] == chapter_id), None)
        if not chapter:
            raise HTTPException(404, '同步来源章节尚未导入')
        generation = chapter.get('active_generation') or f"legacy:{chapter['id']}:{int(chapter.get('analysis_revision', 1))}"
        if int(chapter.get('analysis_revision', 1)) > 1 and not metadata['generation']:
            raise SourceConflict('missing_source_generation')
        if metadata['generation'] and metadata['generation'] != generation:
            raise SourceConflict('source_generation_conflict')
        if metadata['revision'] is not None and int(metadata['revision']) != int(chapter.get('analysis_revision', 1)):
            raise SourceConflict('stale_source_revision')


def _insert_history(connection, user, book, row):
    if row.get('book_id') not in {None, book}:
        raise ValueError('revision_book_mismatch')
    revision_id = str(row['id'])
    owner = connection.execute('SELECT user_id FROM revisions WHERE id=?', (revision_id,)).fetchone()
    if owner and owner[0] != user:
        revision_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'bingdu-sync:{user}:revision:{revision_id}'))
    values = (revision_id, user, book, row['kind'], row['object_id'], row.get('before_json'), row['after_json'], float(row['created_at']), int(row.get('undone', 0)))
    current = connection.execute('SELECT * FROM revisions WHERE id=?', (revision_id,)).fetchone()
    if current:
        if tuple(current) != values:
            # Undo flags may advance without rewriting the immutable revision.
            if tuple(current)[:8] != values[:8] or int(current['undone']) > values[8]:
                raise SourceConflict('revision_history_conflict')
            connection.execute('UPDATE revisions SET undone=? WHERE id=?', (values[8], revision_id))
        return
    connection.execute('INSERT INTO revisions VALUES(?,?,?,?,?,?,?,?,?)', values)


def apply_memory_change(user: str, change: dict):
    payload = change['payload']
    book = str(payload['book_id'])
    before = memory_service.read(user, book)
    _check_source(user, payload)
    if change['entity_type'] == 'book_memory_revision':
        with memory.session() as connection:
            _insert_history(connection, user, book, payload)
        return
    kind = 'entity' if change['entity_type'] == 'book_entity' else 'fact'
    obj = dict(payload.get('object') or {key: value for key, value in payload.items() if key not in {'book_id', 'chapter_id', 'source', 'history'}})
    if change.get('operation') == 'delete' or change.get('deleted_at') is not None:
        obj['deleted'] = True
    if not obj.get('id') or not isinstance(obj.get('revision'), int) or obj['revision'] < 1:
        raise ValueError('invalid_object_revision')
    if kind == 'fact':
        memory.get(user, book, 'entity', obj['entity_id'])
        memory_service.validate_evidence(user, book, obj.get('evidence', []))
    with memory.session() as connection:
        connection.execute('BEGIN IMMEDIATE')
        found = connection.execute('SELECT revision,payload FROM objects WHERE user_id=? AND book_id=? AND kind=? AND id=?', (user, book, kind, obj['id'])).fetchone()
        prior = json.loads(found['payload']) if found else None
        if prior != obj and found and int(found['revision']) >= obj['revision']:
            raise SourceConflict('stale_object_revision')
        connection.execute('INSERT INTO objects VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,book_id,kind,id) DO UPDATE SET revision=excluded.revision,payload=excluded.payload', (user, book, kind, obj['id'], obj['revision'], json.dumps(obj, ensure_ascii=False)))
        for history in payload.get('history', []):
            _insert_history(connection, user, book, history)
        if prior != obj and not payload.get('history'):
            # Older revision producers may carry only an object; make that state
            # reversible locally without pretending to possess remote history.
            revision_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"bingdu-sync:{user}:{change['change_id']}"))
            _insert_history(connection, user, book, {'id': revision_id, 'kind': kind, 'object_id': obj['id'], 'before_json': json.dumps(prior, ensure_ascii=False) if prior else None, 'after_json': json.dumps(obj, ensure_ascii=False), 'created_at': float(change.get('updated_at') or time.time())})
    memory_service.invalidate_snapshot_change(user, book, before, memory.snapshot(user, book))


def apply_span_change(user: str, change: dict):
    payload = change['payload']
    sentence_id, span_id = str(payload['sentence_id']), str(payload['span_id'])
    if change['entity_type'] == 'span_override_revision':
        revision = int(payload['revision'])
        encoded = payload['payload'] if isinstance(payload['payload'], str) else json.dumps(payload['payload'], ensure_ascii=False)
        with structures.session() as connection:
            prior = connection.execute('SELECT payload FROM span_override_history WHERE user_id=? AND sentence_id=? AND span_id=? AND revision=?', (user, sentence_id, span_id, revision)).fetchone()
            if prior and json.loads(prior[0]) != json.loads(encoded):
                raise SourceConflict('revision_history_conflict')
            connection.execute('INSERT OR IGNORE INTO span_override_history VALUES(?,?,?,?,?,?)', (user, sentence_id, span_id, revision, encoded, float(payload.get('created_at') or time.time())))
        return
    source = get_sentence_source(user, sentence_id)
    if not source:
        raise HTTPException(404, '同步来源句子尚未导入')
    sentence, chapter = source
    _check_source(user, {**payload, 'chapter_id': chapter['id'], 'book_id': chapter['bookId']})
    import hashlib
    if hashlib.sha256(sentence['original'].encode()).hexdigest() != payload['text_hash']:
        raise SourceConflict('source_text_hash_conflict')
    value = payload['payload']
    value = json.loads(value) if isinstance(value, str) else value
    revision = int(payload['revision'])
    with structures.session() as connection:
        connection.execute('BEGIN IMMEDIATE')
        prior = connection.execute('SELECT revision,payload FROM span_overrides WHERE user_id=? AND sentence_id=? AND span_id=?', (user, sentence_id, span_id)).fetchone()
        if prior and json.loads(prior['payload']) != value and int(prior['revision']) >= revision:
            raise SourceConflict('stale_object_revision')
        connection.execute('INSERT INTO span_overrides VALUES(?,?,?,?,?,?,?) ON CONFLICT(user_id,sentence_id,span_id) DO UPDATE SET text_hash=excluded.text_hash,payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at', (user, sentence_id, span_id, payload['text_hash'], json.dumps(value, ensure_ascii=False), revision, float(payload.get('updated_at') or time.time())))
        connection.execute('INSERT OR IGNORE INTO span_override_history VALUES(?,?,?,?,?,?)', (user, sentence_id, span_id, revision, json.dumps(value, ensure_ascii=False), float(payload.get('updated_at') or time.time())))
    structures.invalidate_sentences(user, [sentence_id])


def apply_source_generation(user: str, change: dict):
    # The transport log retains the manifest; activation is always a local,
    # validated chapter transaction with the full source text available.
    from .protocol import validate_source_generation
    from ..library import repository as library
    import hashlib
    payload = change['payload']
    validate_source_generation(payload)
    _, chapters = get_book_source(user, payload['book_id'])
    chapter = next((item for item in chapters if item['id'] == payload['chapter_id']), None)
    if chapter is None:
        raise HTTPException(404, '同步来源章节尚未导入')
    if payload['generation_id'] != chapter.get('active_generation'):
        # Full transfers retain archived sources needed by saved examples.
        # An authentic archived manifest is safe to receive and never activates.
        with library._connect(user) as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_master WHERE name='segmentation_generations'").fetchone()
            archived = connection.execute("SELECT text_hash,target_revision,status FROM segmentation_generations WHERE owner_user_id=? AND generation_id=? AND chapter_id=?", (user, payload['generation_id'], payload['chapter_id'])).fetchone() if exists else None
        if archived and archived[2] in {'archived', 'committed'} and archived[0] == payload['text_hash'] and int(archived[1]) == int(source_metadata(payload)['revision']):
            return
    _check_source(user, payload)
    if hashlib.sha256(chapter.get('text', '').encode()).hexdigest() != payload['text_hash']:
        raise SourceConflict('source_text_hash_conflict')
