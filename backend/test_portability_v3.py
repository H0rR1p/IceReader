import hashlib
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from backend.modules.data_portability import service
from backend.modules.data_portability.identity import mapped_id


@pytest.fixture
def durable(tmp_path, monkeypatch):
    from backend.models import LibraryPatch
    from backend.modules.library import repository as library, resegmentation_store as generations
    from backend.modules.linguistics import repository as structures
    from backend.modules.book_memory import repository as memory
    from backend.modules.jobs import repository as jobs
    monkeypatch.setattr(library, 'LIBRARY_PATH', tmp_path / 'library.sqlite3')
    monkeypatch.setattr(library, 'LEGACY_PATH', tmp_path / 'absent.json')
    monkeypatch.setattr(jobs, 'JOBS_PATH', tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(structures, 'STORE_PATH', tmp_path / 'linguistics.sqlite3')
    monkeypatch.setattr(memory, 'STORE_PATH', tmp_path / 'book_memory.sqlite3')
    specs = {name: {**spec, 'path': tmp_path / f'{name}.sqlite3'} for name, spec in service.DATABASE_SPECS.items()}
    monkeypatch.setattr(service, 'DATABASE_SPECS', specs)
    monkeypatch.setattr(service, 'DATA_DIR', tmp_path)
    library.apply_library_patch('source', LibraryPatch(upserts={
        'books': [{'id': 'b', 'title': '猫'}],
        'chapters': [{'id': 'c', 'bookId': 'b', 'text': '猫だ。', 'order': 0, 'status': 'complete'}],
        'sentences': [{'id': 's', 'chapter_id': 'c', 'start': 0, 'end': 3, 'original': '猫だ。', 'translation_zh': '猫。'}]}))
    generations.initialize_store('source')
    structures.save_preferences('source', {})
    structures.save_span_override('source', 's', 'span', 'hash', 'v1', 0, {'selected_candidate': 'possible'})
    structures.save_span_override('source', 's', 'span', 'hash', 'v1', 1, {'selected_candidate': 'confirmed'})
    memory.put('source', 'b', 'entity', {'id': 'person', 'name': '个人秘密姓名', 'aliases': [], 'deleted': False})
    memory.put('source', 'b', 'fact', {'id': 'fact', 'entity_id': 'person', 'key': 'description', 'value': '个人秘密事实', 'status': 'confirmed', 'evidence': [{'chapter_id': 'c', 'sentence_id': 's', 'start': 0, 'end': 3, 'quote': '猫だ。', 'source_revision': 2}]})
    memory.save_summary('source', 'b', 'chunk', {'text': '个人秘密总结', 'fact_ids': ['fact']})
    old = {'chapter': {'id': 'c', 'bookId': 'b', 'text': '猫だ。'}, 'sentences': [{'id': 'old-s', 'chapter_id': 'c', 'start': 0, 'end': 3, 'original': '猫だ。'}], 'tokens': [], 'annotations': [], 'contextSenses': []}
    with closing(sqlite3.connect(library.LIBRARY_PATH)) as connection:
        chapter = json.loads(connection.execute("SELECT payload FROM records WHERE owner_user_id='source' AND table_name='chapters'").fetchone()[0])
        chapter.update(active_generation='g', analysis_revision=2)
        connection.execute("UPDATE records SET payload=? WHERE owner_user_id='source' AND table_name='chapters'", (json.dumps(chapter),))
        connection.execute('INSERT INTO segmentation_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', ('source', 'g', 'c', 'b', 'job', '', 1, 2, 'hash', 'rules', 'committed', '{}', json.dumps(old), '{}', 1, 2))
        connection.execute('INSERT INTO segmentation_anchor_maps VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('source', 'c', '', 'g', 'sentence', 'old-s', 's', 0, 3, 'mapped', '{}'))
        connection.commit()
    return tmp_path, library, structures, memory


def contents(path):
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read('manifest.json')), json.loads(archive.read('data.json'))


def mutate_package(path, new_path, change):
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest, payload = json.loads(files['manifest.json']), json.loads(files['data.json'])
    change(manifest, payload)
    files['data.json'] = json.dumps(payload).encode()
    manifest['files']['data.json'] = hashlib.sha256(files['data.json']).hexdigest()
    files['manifest.json'] = json.dumps(manifest).encode()
    with zipfile.ZipFile(new_path, 'w') as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return new_path


def test_v3_migration_preserves_reversible_history_and_is_idempotent(durable):
    tmp_path, library, structures, memory = durable
    package = service.create_book_transfer('source', tmp_path / 'migration.zip')
    manifest, payload = contents(package)
    assert manifest['schema_version'] == 3 and manifest['min_reader_schema_version'] == 3
    assert len(payload['snapshots']['linguistics']['span_override_history']) == 2
    assert service.import_book_transfer('target', package)['imported_books'] == 1
    assert service.import_book_transfer('target', package)['skipped_books'] == 1
    assert structures.load_span_override('target', 's', 'span', 'hash', 'v1')['revision'] == 2
    imported = memory.snapshot('target', 'b')
    assert imported['entities'][0]['id'] != 'person'
    assert imported['facts'][0]['entity_id'] == imported['entities'][0]['id']
    assert len(imported['history']) == 2
    assert imported['facts'][0]['evidence'][0]['quote'] == '猫だ。'
    from backend.modules.library import resegmentation_store
    generation = mapped_id('source', 'target', 'generations', 'g')
    assert resegmentation_store.resolve_source('target', 'c', 'old-s')['historical']
    with closing(sqlite3.connect(library.LIBRARY_PATH)) as connection:
        active = json.loads(connection.execute("SELECT payload FROM records WHERE owner_user_id='target' AND table_name='chapters'").fetchone()[0])
        assert active['active_generation'] == generation
    revision = next(row for row in imported['history'] if row['kind'] == 'fact')
    assert memory.undo('target', 'b', revision['id'])['deleted']
    assert memory.get('source', 'b', 'fact', 'fact')['value'] == '个人秘密事实'


def test_collision_remaps_book_graph_archives_and_fact_evidence(durable):
    tmp_path, library, structures, memory = durable
    from backend.models import LibraryPatch
    library.apply_library_patch('target', LibraryPatch(upserts={'books': [{'id': 'b', 'title': '目标自己的书'}]}))
    package = service.create_book_transfer('source', tmp_path / 'collision.zip')
    assert service.import_book_transfer('target', package)['imported_books'] == 1
    book = mapped_id('source', 'target', 'books', 'b')
    chapter = mapped_id('source', 'target', 'chapters', 'c')
    sentence = mapped_id('source', 'target', 'sentences', 's')
    assert library.load_chapter('target', chapter).chapter['bookId'] == book
    assert memory.snapshot('target', book)['facts'][0]['evidence'][0]['sentence_id'] == sentence
    assert structures.load_span_override('target', sentence, mapped_id('source', 'target', 'spans', 'span'), 'hash', 'v1')['revision'] == 2
    assert service.import_book_transfer('target', package)['skipped_books'] == 1


def test_share_loaded_into_its_own_library_skips_existing_without_exposing_account(durable):
    tmp_path, library, *_ = durable
    package = service.create_book_transfer('source', tmp_path / 'self-share.zip', ['b'])
    manifest, payload = contents(package)
    assert manifest['purpose'] == 'book-share' and payload['source_user_id'] == ''
    result = service.import_book_transfer('source', package)
    assert result['imported_books'] == 0 and result['skipped_books'] == 1
    assert [row['id'] for row in library.load_library_index('source').books] == ['b']


def test_anonymous_shares_with_colliding_ids_keep_different_original_books(durable):
    tmp_path, library, *_ = durable
    package = service.create_book_transfer('source', tmp_path / 'first-share.zip', ['b'])
    assert service.import_book_transfer('target', package)['imported_books'] == 1
    def replace_original(_manifest, payload):
        for row in payload['records']:
            if row['table_name'] == 'chapters':
                row['payload']['text'] = '犬だ。'
            elif row['table_name'] == 'sentences':
                row['payload']['original'] = '犬だ。'
    different = mutate_package(package, tmp_path / 'different-share.zip', replace_original)
    assert service.import_book_transfer('target', different)['imported_books'] == 1
    assert service.import_book_transfer('target', different)['skipped_books'] == 1
    assert service.import_book_transfer('target', package)['skipped_books'] == 1
    with closing(sqlite3.connect(library.LIBRARY_PATH)) as connection:
        texts = [json.loads(row[0])['text'] for row in connection.execute("SELECT payload FROM records WHERE owner_user_id='target' AND table_name='chapters'")]
    assert sorted(texts) == ['犬だ。', '猫だ。']


def test_remapped_share_reimported_in_larger_selection_stays_idempotent(durable):
    tmp_path, library, *_ = durable
    from backend.models import LibraryPatch
    library.apply_library_patch('target', LibraryPatch(upserts={
        'books': [{'id': 'b', 'title': '冲突的原有书'}],
        'chapters': [{'id': 'target-c', 'bookId': 'b', 'text': '犬だ。', 'order': 0}]}))
    first = service.create_book_transfer('source', tmp_path / 'one-share.zip', ['b'])
    assert service.import_book_transfer('target', first)['imported_books'] == 1
    library.apply_library_patch('source', LibraryPatch(upserts={
        'books': [{'id': 'extra', 'title': '其他书'}],
        'chapters': [{'id': 'extra-c', 'bookId': 'extra', 'text': '花だ。', 'order': 0}]}))
    second = service.create_book_transfer('source', tmp_path / 'two-share.zip', ['b', 'extra'])
    result = service.import_book_transfer('target', second)
    assert result['imported_books'] == 1 and result['skipped_books'] == 1
    assert len(library.load_library_index('target').books) == 3


def test_legacy_export_discloses_omissions_and_new_reader_accepts_it(durable):
    tmp_path, *_ = durable
    package = service.create_book_transfer('source', tmp_path / 'legacy.zip', schema_version=2)
    manifest, payload = contents(package)
    assert not manifest['complete_user_data']
    assert 'book_memory.revisions' in manifest['omitted_data']
    assert not payload['snapshots']
    assert service.import_book_transfer('legacy-target', package)['imported_books'] == 1


def test_capability_failure_precedes_any_database_or_asset_write(durable):
    tmp_path, library, *_ = durable
    package = service.create_book_transfer('source', tmp_path / 'base.zip')
    invalid = mutate_package(package, tmp_path / 'invalid.zip', lambda manifest, payload: manifest.update(required_capabilities=['unknown-capability']))
    with pytest.raises(ValueError, match='能力'):
        service.import_book_transfer('target', invalid)
    assert not library.load_library_index('target').books


def test_share_excludes_embedded_private_fields_and_entire_user_stores(durable):
    tmp_path, library, *_ = durable
    with closing(sqlite3.connect(library.LIBRARY_PATH)) as connection:
        sentence = json.loads(connection.execute("SELECT payload FROM records WHERE owner_user_id='source' AND table_name='sentences'").fetchone()[0])
        sentence['personal_facts'] = ['个人秘密事实']
        sentence['learning_events'] = [{'event_type': 'lookup'}]
        connection.execute("UPDATE records SET payload=? WHERE owner_user_id='source' AND table_name='sentences'", (json.dumps(sentence),))
        connection.commit()
    package = service.create_book_transfer('source', tmp_path / 'share.zip', ['b'])
    manifest, payload = contents(package)
    assert payload['snapshots'] == {} and payload['learning'] == {}
    assert payload['source_user_id'] == ''
    assert '个人秘密' not in json.dumps(payload, ensure_ascii=False)
    assert 'learning.*' in manifest['omitted_data']


def test_full_transfer_excludes_uncommitted_generation_from_learning_sources(durable):
    tmp_path, library, *_ = durable
    with closing(sqlite3.connect(library.LIBRARY_PATH)) as connection:
        connection.execute('INSERT INTO segmentation_generations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('source', 'unfinished', 'c', 'b', 'unfinished-job', 'g', 2, 3, 'hash', 'rules', 'staged',
             '{"text":"temporary new body"}', '{}', '{}', 3, None))
        connection.commit()
    _, payload = contents(service.create_book_transfer('source', tmp_path / 'committed-only.zip'))
    generations = payload['snapshots']['library']['segmentation_generations']
    assert [row['generation_id'] for row in generations] == ['g']
    assert 'temporary new body' not in json.dumps(payload, ensure_ascii=False)


def test_failed_extended_import_rolls_back_all_stores_and_staged_assets(durable, monkeypatch):
    tmp_path, library, structures, memory = durable
    package = service.create_book_transfer('source', tmp_path / 'base.zip')
    invalid = mutate_package(package, tmp_path / 'invalid.zip', lambda manifest, payload: payload['snapshots']['book_memory']['objects'][0].update(unsupported_column='future'))
    with pytest.raises(ValueError, match='数据列'):
        service.import_book_transfer('target', invalid)
    assert not library.load_library_index('target').books
    assert memory.snapshot('target', 'b')['entities'] == []
    with structures.session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM span_override_history WHERE user_id='target'").fetchone()[0] == 0


def test_backup_restores_new_stores_without_erasing_other_accounts(durable):
    tmp_path, library, structures, memory = durable
    package = service.create_backup('source', tmp_path / 'backup.zip')
    memory.put('other', 'b', 'entity', {'id': 'other-person', 'name': '保留'})
    structures.save_span_override('source', 's', 'span', 'hash', 'v1', 2, {'selected_candidate': 'later'})
    result = service.restore_backup('source', package)
    assert result['restored_rows'] > 0
    assert structures.load_span_override('source', 's', 'span', 'hash', 'v1')['revision'] == 2
    assert memory.get('other', 'b', 'entity', 'other-person')['name'] == '保留'
    assert len(memory.snapshot('source', 'b')['history']) == 2
