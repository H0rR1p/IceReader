import asyncio
import pytest
from fastapi import HTTPException
from backend.models import LibraryPatch
from backend.modules.library import repository as library
from backend.modules.linguistics import ambiguity, service, repository
from backend.modules.book_memory import repository as memory
from backend.modules.jobs import repository as jobs, runner


@pytest.fixture
def span_request(tmp_path,monkeypatch):
    monkeypatch.setattr(library,'LIBRARY_PATH',tmp_path/'library.sqlite3'); monkeypatch.setattr(library,'LEGACY_PATH',tmp_path/'absent')
    monkeypatch.setattr(repository,'STORE_PATH',tmp_path/'linguistics.sqlite3'); monkeypatch.setattr(memory,'STORE_PATH',tmp_path/'memory.sqlite3')
    monkeypatch.setattr(jobs,'JOBS_PATH',tmp_path/'jobs.sqlite3')
    text='私は先生に褒められた。'
    library.apply_library_patch('a',LibraryPatch(upserts={'books':[{'id':'b'}],'chapters':[{'id':'c','bookId':'b','text':text}],
        'sentences':[{'id':'s','chapter_id':'c','start':0,'end':len(text),'original':text}]}))
    result=service.structure_results('a',['s'])['results'][0]
    span=next(row for row in result['learning_spans'] if len(row['candidates'])>1)
    return {'sentence_id':'s','span_id':span['id'],'text_hash':result['analysis_manifest']['text_hash'],'version':result['analysis_manifest']['version'],'analysis_revision':1,'expected_override_revision':0},span


def test_manual_legal_choice_and_revision_cas(span_request):
    payload,span=span_request
    choice=ambiguity.choose('a',payload,span['candidates'][0]['id'])
    assert choice['span']['status']=='user_confirmed'
    reopened=service.structure_results('a',['s'])['results'][0]['learning_spans']
    assert next(row for row in reopened if row['id']==span['id'])['source']=='user'
    with pytest.raises(HTTPException) as stale: ambiguity.choose('a',payload,None)
    assert stale.value.status_code==409
    with pytest.raises(HTTPException) as invalid: ambiguity.choose('a',payload,'manufactured')
    assert invalid.value.status_code==422
    cleared=ambiguity.choose('a',{**payload,'expected_override_revision':1},None)
    assert cleared['span']['status']=='ambiguous'
    with repository.session() as connection:
        assert connection.execute('SELECT COUNT(*) FROM span_override_history').fetchone()[0]==2


def test_missing_optional_dependency_preserves_rules(span_request,monkeypatch):
    from backend.modules.linguistics.adapters.ginza_adapter import backend
    monkeypatch.setattr(backend,'available',lambda:False)
    repository.save_preferences('a',{'dependency_enhancement':True})
    result=service.structure_results('a',['s'])['results'][0]
    assert result['learning_spans'] and result['dependency_parse']['version']=='none'



def test_removed_ai_routes_and_handler():
    from backend.modules.linguistics.ambiguity_router import router
    assert {route.path for route in router.routes} == {'/api/grammar/choices'}
    assert 'grammar-disambiguation-v1' not in runner._handlers
    assert service.capabilities()['ambiguity_resolution'] is False


def test_retirement_preserves_manual_choices_and_cancels_only_old_tasks(span_request):
    payload, span = span_request
    ambiguity.choose('a', payload, span['candidates'][0]['id'])
    old = jobs.create_task('a', 'grammar-disambiguation-v1', {'book_id': 'b'}, 'ambiguity:b')
    keep = jobs.create_task('a', 'book-preflight-v1', {'book_id': 'b'}, 'preflight:b')
    jobs.checkpoint_task('a', old['id'], {'done': {'s': 'saved'}}, 1, 2, {'done': ['s']})
    jobs.retire_task_kind('grammar-disambiguation-v1', '已移除')
    jobs.retire_task_kind('grammar-disambiguation-v1', '已移除')
    assert jobs.get_job('a', old['id'])['status'] == 'canceled'
    assert jobs.get_job('a', old['id'])['result'] == {'done': ['s']}
    assert jobs.task_spec('a', old['id'])['checkpoint'] == {'done': {'s': 'saved'}}
    assert jobs.get_job('a', keep['id'])['status'] == 'queued'
    spans = service.structure_results('a', ['s'])['results'][0]['learning_spans']
    assert next(row for row in spans if row['id'] == span['id'])['source'] == 'user'
    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(runner.resume('a', old['id']))
    assert unavailable.value.status_code == 503


def test_old_ai_suggestion_is_not_restored_but_history_remains(span_request):
    payload, span = span_request
    repository.save_span_override('a', 's', span['id'], payload['text_hash'], payload['version'], 0,
        {'source': 'ai', 'choice_id': span['candidates'][0]['id'], 'reason': '旧建议', 'context_hash': 'old'})
    restored = service.structure_results('a', ['s'])['results'][0]['learning_spans']
    assert next(row for row in restored if row['id'] == span['id'])['source'] == 'rule'
    with repository.session() as connection:
        assert connection.execute('SELECT COUNT(*) FROM span_override_history').fetchone()[0] == 1
    revision = next(row for row in restored if row['id'] == span['id'])['override_revision']
    replacement = ambiguity.choose('a', {**payload, 'expected_override_revision': revision}, span['candidates'][0]['id'])
    assert replacement['span']['source'] == 'user'
