import asyncio
import pytest
from fastapi import HTTPException
from backend.models import LibraryPatch
from backend.modules.library import repository as library, resegmentation_store as store
from backend.modules.analysis import resegmentation as tasks
from backend.modules.jobs import repository as jobs, runner
from backend.modules.linguistics import repository as linguistics


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'LIBRARY_PATH', tmp_path / 'library.sqlite3')
    monkeypatch.setattr(library, 'LEGACY_PATH', tmp_path / 'absent')
    monkeypatch.setattr(jobs, 'JOBS_PATH', tmp_path / 'jobs.sqlite3')
    monkeypatch.setattr(linguistics, 'STORE_PATH', tmp_path / 'linguistics.sqlite3')
    from backend.modules.book_memory import repository as memory
    from backend.modules.sync import repository as sync
    monkeypatch.setattr(memory,'STORE_PATH',tmp_path/'memory.sqlite3')
    monkeypatch.setattr(sync,'SYNC_PATH',tmp_path/'sync.sqlite3')
    text = '本を読む。母に読ませました。'
    library.apply_library_patch('a', LibraryPatch(upserts={
        'books': [{'id':'b','title':'原创','currentChapterId':'c','currentSentenceId':'s'}],
        'chapters': [{'id':'c','bookId':'b','text':text,'order':0,'status':'complete'}],
        'sentences': [{'id':'s','chapter_id':'c','start':0,'end':len(text),'original':text,'translation_zh':'旧合句译文','explanation_status':'complete'}],
        'tokens': [{'id':'t','sentence_id':'s','start':0,'end':1,'surface':'本'}],
        'bookmarks': [{'id':'bm','bookId':'b','chapterId':'c','sentenceId':'s','sentenceStart':0,'text':text}]}))
    return text


def stage():
    request = tasks.prepare_request('a','b','chapter','c')
    return tasks._build_stage('a',request,request['targets'][0],'testjob')


def test_stage_is_invisible_atomic_mapping_history_and_restore(owned):
    result = stage()
    assert library.load_chapter('a','c').sentences[0]['id'] == 's'
    activated = store.activate_generation('a',result['generation_id'])
    current = library.load_chapter('a','c')
    assert len(current.sentences) == 2
    assert all(not s['translation_zh'] for s in current.sentences)
    assert current.chapter['analysis_revision'] == 2
    source = store.resolve_source('a','c','s')
    assert source['historical'] and source['sentence']['translation_zh'] == '旧合句译文'
    assert source['current_sentence_id'] == current.sentences[0]['id']
    bookmarks = library.load_bookmarks('a','b')
    assert bookmarks[0]['text'] == owned
    assert bookmarks[0]['sentenceId'] == current.sentences[0]['id']
    assert store.activate_generation('a',result['generation_id'])['already_committed']
    restored = store.restore_generation('a',result['generation_id'],2)
    assert restored['target_revision'] == 3
    assert library.load_chapter('a','c').sentences[0]['translation_zh'] == '旧合句译文'


def test_late_patches_rejected_without_partial_mutation(owned):
    result = stage()
    store.activate_generation('a',result['generation_id'])
    with pytest.raises(HTTPException) as caught:
        library.apply_library_patch('a',LibraryPatch(upserts={'lexemes':[{'key':'new'}], 'sentences':[{'id':'s','chapter_id':'c','start':0,'end':len(owned),'original':owned,'translation_zh':'迟到'}]}))
    assert caught.value.status_code == 409
    assert not library.load_study_data('a').lexemes
    current = library.load_chapter('a','c')
    row = {**current.sentences[0], 'translation_zh':'当前版本'}
    library.apply_library_patch('a',LibraryPatch(upserts={'sentences':[row]}))
    assert library.load_chapter('a','c').sentences[0]['translation_zh'] == '当前版本'


def test_source_changed_before_activation_and_foreign_scope(owned):
    result = stage()
    current = library.load_chapter('a','c').chapter
    library.apply_library_patch('a',LibraryPatch(upserts={'chapters':[{**current,'analysis_revision':2}]}))
    with pytest.raises(store.ResegmentationConflict):
        store.activate_generation('a',result['generation_id'])
    assert library.load_chapter('a','c').sentences[0]['id'] == 's'
    with pytest.raises(store.ResegmentationNotFound):
        store.list_generations('other','c')


def test_staged_cancellation_resume_and_persisted_worker(owned):
    async def run():
        request = tasks.prepare_request('a','b','book')
        job = await runner.submit('a',tasks.KIND,request,lock_key='resegment:b')
        with pytest.raises(HTTPException) as caught:
            await runner.submit('a',tasks.KIND,request,lock_key='resegment:b')
        assert caught.value.status_code == 409
        canceled = jobs.request_cancel('a',job['id'])
        assert canceled['status'] == 'canceled'
        assert library.load_chapter('a','c').sentences[0]['id'] == 's'
        await runner.resume('a',job['id'])
        await runner.start()
        try:
            for _ in range(200):
                found = jobs.get_job('a',job['id'])
                if found['status'] in {'complete','failed'}:
                    break
                await asyncio.sleep(.01)
            assert found['status'] == 'complete', found
            assert found['progress_current'] == found['progress_total'] == 1
        finally:
            await runner.stop()
    asyncio.run(run())


def test_interrupted_running_job_pauses_without_losing_checkpoint(owned):
    job = jobs.create_task('a',tasks.KIND,{},'r:b')
    jobs.claim_task([tasks.KIND])
    jobs.checkpoint_task('a',job['id'],{'completed':{'c':{}}},1,2)
    jobs.recover_tasks()
    assert jobs.get_job('a',job['id'])['status'] == 'paused'
    assert jobs.task_spec('a',job['id'])['checkpoint']['completed'] == {'c':{}}


def test_activation_outbox_replays_structures_and_evidence_after_process_loss(owned):
    from backend.modules.book_memory import service as memory_service
    from backend.modules.book_memory.models import EntityInput, FactInput, Evidence
    entity=memory_service.save_entity('a','b',EntityInput(name='母'))
    fact=memory_service.save_fact('a','b',entity['id'],FactInput(key='relationship',value='母',evidence=[Evidence(chapter_id='c',start=5,end=6,quote='母',sentence_id='s')]))['fact']
    result=stage()
    structures=store.stage_payload('a',result['generation_id'])['structures']
    assert len(structures)==2
    store.activate_generation('a',result['generation_id'])
    assert store.pending_events('a')
    tasks.replay_events('a')
    assert not store.pending_events('a')
    for row in structures:
        manifest=row['analysis_manifest']
        assert linguistics.get_analysis('a',row['sentence_id'],manifest['text_hash'],manifest['version'],2)
    evidence=next(row for row in memory_service.read('a','b')['facts'] if row['id']==fact['id'])['evidence'][0]
    assert evidence['source_revision']==2 and evidence['original_source_revision']==1
    assert evidence['quote']=='母' and evidence['original_sentence_id']=='s'
    tasks.replay_events('a')
    assert not store.pending_events('a')


def test_complete_library_save_is_atomic_with_owned_projections(owned,monkeypatch):
    snapshot=library.load_library('a')
    before=library.load_library_index('a').books
    changed=snapshot.model_copy(deep=True)
    changed.books[0]['title']='未提交'
    def invalid_metadata(*args): raise ValueError('injected projection failure')
    monkeypatch.setattr(library,'_book_metadata',invalid_metadata)
    with pytest.raises(ValueError,match='projection failure'):
        library.save_library('a',changed)
    assert library.load_library_index('a').books==before


def test_late_generation_event_preserves_current_cache_and_rebases_evidence_to_current_revision(owned):
    from backend.modules.book_memory import service as memory_service
    from backend.modules.book_memory.models import EntityInput, FactInput, Evidence
    # Keep sentence identities stable across both recuts so a late invalidation
    # would delete the current meanings as well as overwrite the analysis.
    library.apply_library_patch('a',LibraryPatch(upserts={'sentences':[
        {'id':'s','chapter_id':'c','start':0,'end':5,'original':owned[:5]},
        {'id':'s2','chapter_id':'c','start':5,'end':len(owned),'original':owned[5:]}]}))
    entity=memory_service.save_entity('a','b',EntityInput(name='母'))
    fact=memory_service.save_fact('a','b',entity['id'],FactInput(key='relationship',value='母',
        evidence=[Evidence(chapter_id='c',start=5,end=6,quote='母',sentence_id='s2')]))['fact']
    request=tasks.prepare_request('a','b','chapter','c')
    g2=tasks._build_stage('a',request,request['targets'][0],'late-event-job')['generation_id']
    store.activate_generation('a',g2)
    old_event=store.pending_events('a')[0]
    request=tasks.prepare_request('a','b','chapter','c')
    g3=tasks._build_stage('a',request,request['targets'][0],'current-event-job')['generation_id']
    store.activate_generation('a',g3)
    for event in store.pending_events('a'):
        if event['id']!=old_event['id']:
            # Model a newer delivery completing while the older one is delayed.
            store.acknowledge_event('a',event['id'])
    current=store.stage_payload('a',g3)['structures'][0]
    manifest=current['analysis_manifest']
    linguistics.put_analysis('a',current['sentence_id'],current)
    senses=[{'span_id':'current-span','gloss_zh':'当前版本释义'}]
    linguistics.save_span_senses('a',current['sentence_id'],senses,manifest,'current-context')
    tasks.replay_events('a')
    chapter=library.load_chapter('a','c').chapter
    assert chapter['active_generation']==g3 and chapter['analysis_revision']==3
    assert linguistics.get_analysis('a',current['sentence_id'],manifest['text_hash'],manifest['version'],3)==current
    assert linguistics.load_span_senses('a',current['sentence_id'],manifest['text_hash'],manifest['version'],3,'current-context')==senses
    evidence=next(row for row in memory_service.read('a','b')['facts'] if row['id']==fact['id'])['evidence'][0]
    assert evidence['source_revision']==3 and evidence['original_source_revision']==1
    assert evidence['sentence_id']=='s2' and evidence['original_sentence_id']=='s2' and evidence['quote']=='母'
    assert not store.pending_events('a')
