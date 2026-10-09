"""X1 compatibility and durable revision transport acceptance matrix."""
import asyncio
import hashlib
import json

import httpx
import pytest

from .cloud.config import CloudConfig
from .cloud.repository import CloudRepository
from .models import LibraryPatch
from .modules.book_memory import repository as memory
from .modules.cloud_account import repository as accounts, service as cloud_client
from .modules.library import repository as library, resegmentation_store as generations
from .modules.linguistics import repository as structures
from .modules.sync import repository as sync, emission
from .modules.sync.projection import apply_remote_changes
from .modules.sync.protocol import capabilities


TEXT = '猫だ。'
TEXT_HASH = hashlib.sha256(TEXT.encode()).hexdigest()


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, 'SYNC_PATH', tmp_path / 'sync.sqlite3')
    monkeypatch.setattr(sync, '_initialized_path', None)
    monkeypatch.setattr(library, 'LIBRARY_PATH', tmp_path / 'library.sqlite3')
    monkeypatch.setattr(library, 'LEGACY_PATH', tmp_path / 'absent.json')
    monkeypatch.setattr(structures, 'STORE_PATH', tmp_path / 'linguistics.sqlite3')
    monkeypatch.setattr(memory, 'STORE_PATH', tmp_path / 'book_memory.sqlite3')
    monkeypatch.setattr(accounts, 'CLIENT_PATH', tmp_path / 'client.sqlite3')
    monkeypatch.setattr(accounts, 'KEY_PATH', tmp_path / 'client.key')
    monkeypatch.setattr(accounts, '_initialized_path', None)
    cloud = CloudRepository(CloudConfig(database_path=tmp_path / 'cloud.sqlite3',
        public_url='http://127.0.0.1:8010', secret='test-secret', allowed_origins=(), dev_mode=True))
    cloud.initialize()
    return cloud


def seed(user='reader', generation=None, revision=1):
    library.apply_library_patch(user, LibraryPatch(upserts={
        'books': [{'id': 'b', 'title': '猫'}],
        'chapters': [{'id': 'c', 'bookId': 'b', 'text': TEXT, 'order': 0, 'status': 'complete'}],
        'sentences': [{'id': 's', 'chapter_id': 'c', 'start': 0, 'end': len(TEXT), 'original': TEXT}]}))
    if generation:
        activate(user, generation, revision)


def activate(user, generation, revision, parent=None):
    generations.initialize_store(user)
    with library._connect(user) as connection:
        chapter = json.loads(connection.execute("SELECT payload FROM records WHERE owner_user_id=? AND table_name='chapters' AND record_key='c'", (user,)).fetchone()[0])
        chapter.update(active_generation=generation, analysis_revision=revision)
        connection.execute("UPDATE records SET payload=? WHERE owner_user_id=? AND table_name='chapters' AND record_key='c'", (json.dumps(chapter), user))
        connection.execute("UPDATE segmentation_generations SET status='archived' WHERE owner_user_id=? AND chapter_id='c'", (user,))
        connection.execute('INSERT INTO segmentation_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (user, generation, 'c', 'b', f'job-{generation}', parent or 'legacy:c:1', revision - 1, revision,
             TEXT_HASH, 'rules', 'active', '{}', '{}', '{}', float(revision), float(revision)))
        connection.execute('INSERT INTO segmentation_anchor_maps VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (user, 'c', parent or 'legacy:c:1', generation, 'sentence', 'old-s', 's', 0, len(TEXT), 'mapped', '{}'))


def change(kind, identity, payload, change_id=None, stamp=1):
    return {'change_id': change_id or f'{kind}:{identity}:{stamp}', 'entity_type': kind,
            'entity_id': identity, 'payload': payload, 'updated_at': stamp}


def manifest(generation='g2', revision=2, parent='legacy:c:1'):
    return {'book_id': 'b', 'chapter_id': 'c', 'generation_id': generation,
            'parent_generation': parent, 'parent_revision': revision - 1,
            'source_revision': revision, 'text_hash': TEXT_HASH, 'status': 'committed', 'maps': []}


@pytest.mark.parametrize('target', ['local', 'cloud'])
def test_capability_matrix_is_atomic_and_legacy_pull_does_not_advance_cursor(stores, target):
    store = sync if target == 'local' else stores
    legacy = change('bookmark', 'mark', {'bookId': 'b'}, stamp=1)
    revision = change('book_entity', 'b:person', {'book_id': 'b', 'object': {'id': 'person', 'name': '猫', 'revision': 1}}, stamp=2)
    with pytest.raises(ValueError, match='sync_capability_required'):
        store.push_changes('u', 'd', [legacy, revision], schema_version=1, peer_capabilities=[])
    assert store.sync_status('u')['entities'] == 0
    assert store.push_changes('u', 'd', [legacy], schema_version=1, peer_capabilities=[])['accepted']
    assert store.push_changes('u', 'd', [revision], schema_version=3, peer_capabilities=capabilities()['capabilities'])['accepted']
    with pytest.raises(ValueError, match='sync_capability_required'):
        store.pull_changes('u', 'old', 0, 500, schema_version=1, peer_capabilities=[])
    context = sync._connect() if target == 'local' else stores.connect()
    with context as connection:
        assert connection.execute("SELECT 1 FROM sync_device_cursors WHERE user_id='u' AND device_id='old'").fetchone() is None
    assert len(store.pull_changes('u', 'new', 0, 500)['changes']) == 2


@pytest.mark.parametrize('target', ['local', 'cloud'])
def test_old_generation_cannot_replace_current_or_write_new_source_object(stores, target):
    store = sync if target == 'local' else stores
    assert store.push_changes('u', 'new', [change('source_generation', 'c', manifest(), stamp=1)])['accepted']
    newer = manifest('g3', 3, 'g2')
    assert store.push_changes('u', 'new', [change('source_generation', 'c', newer, stamp=2)])['accepted']
    stale = store.push_changes('u', 'old', [change('source_generation', 'c', manifest(), stamp=9)])
    assert stale['conflicts'][0]['reason'] == 'source_generation_conflict'
    old_progress = change('reading_progress', 'new-progress-id', {'bookId': 'b', 'chapterId': 'c', 'sentenceId': 'old-s'}, stamp=10)
    assert store.push_changes('u', 'old', [old_progress], schema_version=1, peer_capabilities=[])['conflicts'][0]['reason'] == 'missing_source_generation'
    # Saved examples remain historical references and can arrive after a recut.
    card = change('card', 'saved-example', {'id': 'saved-example', 'chapter_id': 'c', 'source': {'generation_id': 'g2', 'source_revision': 2}}, stamp=11)
    assert store.push_changes('u', 'old', [card])['accepted']


@pytest.mark.parametrize('extra', [{'stage_json': '{}'}, {'snapshot': {}}, {'maps': [{'entity_kind': 'sentence', 'old_id': 's', 'new_id': 'n', 'start': 0, 'end': 3, 'status': 'mapped', 'text': TEXT}]}])
def test_generation_rejects_body_or_snapshot_including_nested_mapping(stores, extra):
    with pytest.raises(ValueError, match='book_source_upload_forbidden'):
        sync.push_changes('u', 'd', [change('source_generation', 'c', {**manifest(), **extra})])
    assert sync.sync_status('u')['entities'] == 0


def test_collect_recovers_all_user_history_idempotently_and_excludes_staging(stores):
    seed(generation='g2', revision=2)
    structures.save_span_override('reader', 's', 'span', TEXT_HASH, 'v1', 0, {'selected_candidate': 'a'})
    structures.save_span_override('reader', 's', 'span', TEXT_HASH, 'v1', 1, {'selected_candidate': 'b'})
    memory.put('reader', 'b', 'entity', {'id': 'person', 'name': '猫', 'aliases': [], 'deleted': False})
    memory.put('reader', 'b', 'entity', {'id': 'person', 'name': 'ねこ', 'aliases': [], 'deleted': False}, 1)
    with library._connect('reader') as connection:
        connection.execute('INSERT INTO segmentation_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('reader', 'staged', 'c', 'b', 'staged-job', 'g2', 2, 3, TEXT_HASH, 'rules', 'staged',
             '{"text":"private staging"}', '{"text":"private snapshot"}', '{}', 3, None))
    first = emission.collect_user_revisions('reader', 'd')
    second = emission.collect_user_revisions('reader', 'd')
    assert first['accepted'] == 7 and second['accepted'] == 0 and not second['conflicts']
    rows = sync.list_changes_after('reader')['changes']
    assert len([row for row in rows if row['entity_type'] == 'span_override_revision']) == 2
    assert len([row for row in rows if row['entity_type'] == 'book_memory_revision']) == 2
    generation = next(row['payload'] for row in rows if row['entity_type'] == 'source_generation')
    assert generation['generation_id'] == 'g2' and generation['maps'][0]['old_id'] == 'old-s'
    assert 'private' not in json.dumps(generation) and set(generation).isdisjoint({'stage_json', 'snapshot_json', 'text'})
    assert sync.list_changes_after('other')['changes'] == []


def test_emission_failure_preserves_domain_commit_and_collection_recovers(stores, monkeypatch, caplog):
    seed()
    memory.put('reader', 'b', 'entity', {'id': 'p', 'name': '猫', 'aliases': []})
    push = sync.push_changes
    def unavailable(*args, **kwargs):
        raise OSError('temporary transport store failure')
    monkeypatch.setattr(sync, 'push_changes', unavailable)
    assert not emission.after_commit('book_memory', 'reader', 'd', 'b')
    assert memory.get('reader', 'b', 'entity', 'p')['name'] == '猫'
    assert 'collection will retry' in caplog.text
    monkeypatch.setattr(sync, 'push_changes', push)
    assert emission.collect_user_revisions('reader', 'd')['accepted'] == 2


def test_local_undo_advances_history_flag_without_forking_immutable_revision(stores):
    seed()
    memory.put('reader', 'b', 'entity', {'id': 'p', 'name': '猫', 'aliases': []})
    assert emission.emit_book_memory('reader', 'd', 'b')['accepted']
    revision = memory.snapshot('reader', 'b')['history'][0]['id']
    memory.undo('reader', 'b', revision)
    result = emission.emit_book_memory('reader', 'd', 'b')
    assert not result['conflicts'] and len(result['accepted']) == 3
    transported = [row for row in sync.list_changes_after('reader')['changes']
                   if row['entity_type'] == 'book_memory_revision' and row['entity_id'] == revision]
    assert [row['payload']['undone'] for row in transported] == [0, 1]


def test_preserved_manual_choice_distinguishes_current_placement_from_original_confirmation(stores):
    seed()
    original = structures.save_span_override('reader', 's', 'span', TEXT_HASH, 'v1', 0,
        {'choice_id': 'candidate', 'source': 'user', 'analysis_revision': 1})
    emission.emit_span_override('reader', 'd', 's', 'span')
    activate('reader', 'g2', 2)
    emission.emit_source_generation('reader', 'd', 'g2')
    result = emission.emit_span_override('reader', 'd', 's', 'span')
    assert not result['conflicts']
    current = [row['payload'] for row in sync.list_changes_after('reader')['changes'] if row['entity_type'] == 'span_override'][-1]
    assert current['source']['source_revision'] == 2 and current['source']['generation_id'] == 'g2'
    assert current['origin_source']['source_revision'] == 1 and current['source_binding'] == 'same_text_preserved'
    assert current['payload'] == original and current['revision'] == 1


def test_archived_sentence_keeps_revision_history_without_rebinding_to_new_text(stores):
    seed()
    structures.save_span_override('reader', 's', 'span', TEXT_HASH, 'v1', 0,
        {'choice_id': 'candidate', 'source': 'user', 'analysis_revision': 1})
    with library._connect('reader') as connection:
        connection.execute("DELETE FROM records WHERE owner_user_id='reader' AND table_name='sentences' AND record_key='s'")
    result = emission.emit_span_override('reader', 'd', 's', 'span')
    assert len(result['accepted']) == 1
    changes = sync.list_changes_after('reader')['changes']
    assert changes[0]['entity_type'] == 'span_override_revision'
    assert changes[0]['payload']['payload']['analysis_revision'] == 1


@pytest.mark.parametrize('target', ['local', 'cloud'])
def test_revision_body_cannot_be_rewritten_but_undo_flag_can_advance(stores, target):
    store = sync if target == 'local' else stores
    payload = {'id': 'r', 'book_id': 'b', 'kind': 'entity', 'object_id': 'p', 'before_json': None,
               'after_json': '{"name":"cat","revision":1}', 'created_at': 1, 'undone': 0}
    assert store.push_changes('u', 'd', [change('book_memory_revision', 'r', payload, stamp=1)])['accepted']
    assert store.push_changes('u', 'd', [change('book_memory_revision', 'r', {**payload, 'undone': 1}, stamp=2)])['accepted']
    assert store.push_changes('u', 'other', [change('book_memory_revision', 'r', {**payload, 'after_json': '{}'}, stamp=3)])['conflicts']


def test_span_projection_preserves_history_and_old_generation_is_explicit_conflict(stores):
    seed(generation='g2', revision=2)
    payload = {'book_id': 'b', 'chapter_id': 'c', 'sentence_id': 's', 'span_id': 'span',
        'text_hash': TEXT_HASH, 'revision': 2, 'source': {'generation_id': 'g2', 'source_revision': 2},
        'payload': {'selected_candidate': 'b', 'version': 'v1', 'revision': 2}}
    earlier = change('span_override_revision', 's:span:1', {'book_id': 'b', 'sentence_id': 's', 'span_id': 'span',
        'revision': 1, 'created_at': 1, 'payload': {'selected_candidate': 'a', 'version': 'v1', 'revision': 1}}, stamp=1)
    current = change('span_override', 's:span', payload, stamp=2)
    assert apply_remote_changes('reader', 'd', [earlier, current])['applied'] == 2
    assert apply_remote_changes('reader', 'd', [earlier, current])['skipped'] == 2
    assert structures.load_span_override('reader', 's', 'span', TEXT_HASH, 'v1')['revision'] == 2
    activate('reader', 'g3', 3, 'g2')
    stale = change('span_override', 's:span', {**payload, 'revision': 3}, stamp=9)
    assert apply_remote_changes('reader', 'd', [stale])['conflicts'] == 1
    assert structures.load_span_override('reader', 's', 'span', TEXT_HASH, 'v1')['revision'] == 2
    assert sync.sync_status('reader')['projection_conflicts'] == 1
    assert sync.sync_status('reader')['conflicts'] == 1
    without_source = change('span_override', 's:new-span', {**payload, 'span_id': 'new-span', 'source': {}}, stamp=10)
    assert apply_remote_changes('reader', 'd', [without_source])['conflicts'] == 1


def test_committed_archived_manifest_is_safe_to_receive_without_source_activation(stores):
    seed(generation='g2', revision=2)
    activate('reader', 'g3', 3, 'g2')
    remote = change('source_generation', 'c', manifest(), stamp=1)
    assert apply_remote_changes('reader', 'd', [remote])['applied'] == 1
    chapter = library.load_chapter('reader', 'c').chapter
    assert chapter['active_generation'] == 'g3' and chapter['analysis_revision'] == 3
    unknown = change('source_generation', 'c', manifest('unknown', 2), stamp=2)
    assert apply_remote_changes('reader', 'd', [unknown])['conflicts'] == 1


def test_memory_projection_retries_missing_source_and_history_remains_reversible(stores):
    entity = {'id': 'person', 'name': '猫', 'aliases': [], 'deleted': False, 'revision': 1}
    history = {'id': 'r1', 'book_id': 'b', 'kind': 'entity', 'object_id': 'person', 'before_json': None,
               'after_json': json.dumps(entity), 'created_at': 1, 'undone': 0}
    remote = change('book_entity', 'b:person', {'book_id': 'b', 'object': entity, 'history': [history]})
    assert apply_remote_changes('reader', 'd', [remote])['pending'] == 1
    seed()
    assert apply_remote_changes('reader', 'd', [])['applied'] == 1
    assert apply_remote_changes('reader', 'd', [remote])['skipped'] == 1
    snapshot = memory.snapshot('reader', 'b')
    assert snapshot['entities'][0] == entity and len(snapshot['history']) == 1
    assert memory.undo('reader', 'b', 'r1')['deleted']
    assert memory.snapshot('other', 'b')['entities'] == []


def cloud_binding(cloud):
    result = cloud.register('reader@example.com', 'a secure test password', 'Reader', 'd', 'Device')
    token = cloud.request_action_token('reader@example.com', 'verify_email')[1]
    cloud.verify_email(token)
    accounts.save_account('reader', result)
    return result


def test_real_cloud_routes_advertise_protocol_and_reject_old_client_revision_batch(stores, monkeypatch):
    from . import cloud_app
    monkeypatch.setattr(cloud_app, 'repository', stores)
    binding = cloud_binding(stores)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud_app.app), base_url='http://test') as client:
            headers = {'Authorization': 'Bearer ' + binding['access_token']}
            offered = await client.get('/v1/sync/capabilities', headers=headers)
            assert offered.status_code == 200 and offered.json()['schema_version'] == 3
            assert 'span_override_revision' in offered.json()['entity_types']
            mutation = change('book_entity', 'b:p', {'book_id': 'b', 'object': {'id': 'p', 'revision': 1, 'name': '猫'}})
            denied = await client.post('/v1/sync/push', headers=headers, json={'changes': [mutation]})
            assert denied.status_code == 422 and 'sync_capability_required' in denied.text
            accepted = await client.post('/v1/sync/push', headers=headers, json={'changes': [mutation], **capabilities()})
            assert accepted.status_code == 200 and accepted.json()['accepted']
            assert (await client.get('/v1/sync/pull', headers=headers)).status_code == 409
            modern = await client.get('/v1/sync/pull', headers=headers, params={'schema_version': 3, 'capabilities': ','.join(capabilities()['capabilities'])})
            assert modern.status_code == 200 and modern.json()['changes'][0]['entity_type'] == 'book_entity'
    asyncio.run(scenario())


def test_legacy_cloud_does_not_receive_new_domain_or_report_sync_complete(stores, monkeypatch):
    cloud_binding(stores)
    seed()
    memory.put('reader', 'b', 'entity', {'id': 'p', 'name': '猫', 'aliases': []})
    requests = []
    async def legacy_request(user, method, path, **kwargs):
        requests.append(path)
        response = httpx.Response(404, request=httpx.Request(method, 'http://test' + path))
        response.raise_for_status()
    monkeypatch.setattr(cloud_client, '_authorized_request', legacy_request)
    with pytest.raises(ValueError, match='sync_capability_required'):
        asyncio.run(cloud_client.sync('reader', 'd'))
    assert requests == ['/v1/sync/capabilities']
    status = accounts.account_status('reader')
    assert status['local_cursor'] == 0 and status['last_sync_at'] is None
    assert 'sync_capability_required' in status['last_error']


def test_cloud_sync_pending_source_is_visible_and_not_completed(stores, monkeypatch):
    from . import cloud_app
    monkeypatch.setattr(cloud_app, 'repository', stores)
    binding = cloud_binding(stores)
    remote = change('book_entity', 'b:p', {'book_id': 'b', 'object': {'id': 'p', 'name': '猫', 'revision': 1}})
    stores.push_changes(binding['user']['id'], 'other', [remote])
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=cloud_app.app), base_url='http://test') as client:
            async def request(method, path, **kwargs):
                return await client.request(method, path, **kwargs)
            monkeypatch.setattr(cloud_client, '_plain_request', request)
            pending = await cloud_client.sync('reader', 'd', pull_only=True)
            assert pending['pending'] == 1 and not pending['complete']
            assert pending['last_sync_at'] is None and pending['remote_cursor'] > 0
            seed()
            completed = await cloud_client.sync('reader', 'd', pull_only=True)
            assert completed['complete'] and completed['pending'] == 0 and completed['last_sync_at'] is not None
            assert memory.get('reader', 'b', 'entity', 'p')['name'] == '猫'
    asyncio.run(scenario())
