import asyncio
import pytest
from fastapi import HTTPException
from backend.models import LibraryPatch, SentenceOut
from backend.modules.library import repository as library
from backend.modules.book_memory import repository, service, preflight
from backend.modules.book_memory.models import EntityInput, FactInput, Evidence
from backend.modules.linguistics import repository as linguistics
from backend.modules.analysis.context import build_contexts
from backend.modules.jobs import repository as jobs, runner


@pytest.fixture
def owned(tmp_path,monkeypatch):
    monkeypatch.setattr(library,'LIBRARY_PATH',tmp_path/'library.sqlite3'); monkeypatch.setattr(library,'LEGACY_PATH',tmp_path/'absent')
    monkeypatch.setattr(repository,'STORE_PATH',tmp_path/'memory.sqlite3'); monkeypatch.setattr(linguistics,'STORE_PATH',tmp_path/'linguistics.sqlite3')
    monkeypatch.setattr(jobs,'JOBS_PATH',tmp_path/'jobs.sqlite3')
    texts=['田中さんは来た。','佐藤さんは女性だ。']
    library.apply_library_patch('a',LibraryPatch(upserts={'books':[{'id':'b','currentChapterId':'c0'}],
        'chapters':[{'id':f'c{i}','bookId':'b','order':i,'text':text} for i,text in enumerate(texts)],
        'sentences':[{'id':f's{i}','chapter_id':f'c{i}','start':0,'end':len(text),'original':text} for i,text in enumerate(texts)]}))
    return texts


def test_evidence_ownership_known_scope_and_unknown_gender(owned):
    entity=service.save_entity('a','b',EntityInput(name='佐藤'))
    evidence=Evidence(chapter_id='c1',start=0,end=len(owned[1]),quote=owned[1])
    fact=service.save_fact('a','b',entity['id'],FactInput(key='gender',value='女性',status='confirmed',evidence=[evidence]))['fact']
    assert not service.context_facts('a','b','佐藤さん',0,len(owned[0]),budget=2000)
    assert service.context_facts('a','b','佐藤さん',0,len(owned[0]),budget=2000,allow_future=True)[0]['id']==fact['id']
    assert not service.context_facts('a','b','田中さん',1,len(owned[1]),budget=2000)
    with pytest.raises(HTTPException) as denied: service.read('other','b')
    assert denied.value.status_code==404
    with pytest.raises(HTTPException) as invalid:
        service.save_fact('a','b',entity['id'],FactInput(key='gender',value='男',evidence=[Evidence(chapter_id='c1',start=0,end=1,quote='错')]))
    assert invalid.value.status_code==422


def test_conflicts_revision_undo_alias_merge_and_dependency_hash(owned):
    entity=service.save_entity('a','b',EntityInput(name='田中',aliases=['田中さん']))
    first=service.save_fact('a','b',entity['id'],FactInput(key='gender',value='男',status='confirmed'))['fact']
    sentence=SentenceOut(id='s0',chapter_id='c0',start=0,end=len(owned[0]),original=owned[0])
    linguistics.save_preferences('a',{'context_policy':{'entity_token_budget':2000}})
    before=build_contexts('a',[sentence])['s0'].context_hash
    other=service.save_entity('a','b',EntityInput(name='山田'))
    service.save_fact('a','b',other['id'],FactInput(key='gender',value='女',status='confirmed'))
    assert build_contexts('a',[sentence])['s0'].context_hash==before
    second=service.save_fact('a','b',entity['id'],FactInput(key='gender',value='女',status='confirmed'))['fact']
    assert second['status']=='conflict'
    assert repository.get('a','b','fact',first['id'])['status']=='conflict'
    assert build_contexts('a',[sentence])['s0'].context_hash!=before
    with pytest.raises(HTTPException) as stale:
        service.save_entity('a','b',EntityInput(name='田中一郎',expected_revision=0),entity['id'])
    assert stale.value.status_code==409
    merged=service.merge('a','b',other['id'],entity['id'],other['revision'])
    history=next(row for row in service.read('a','b')['history'] if row['object_id']==other['id'])
    assert merged['merged_into']==entity['id']
    restored=service.undo('a','b',history['id'])['object']
    assert not restored.get('merged_into') and restored['revision']>merged['revision']


def test_preflight_opt_in_scope_local_idempotence_and_budget_pause(owned):
    request=preflight.prepare('a','b',{'allow_unread':False,'through_chapter_id':None,'chunk_chars':500,'mode':'local','max_calls':0,'max_tokens':0})
    assert len(request['chunks'])==1
    async def run_local():
        job=await runner.submit('a',preflight.KIND,request,lock_key='preflight:b')
        control=runner.Control('a',job['id'],{})
        runner._stopping=False
        result=await preflight.run(control,request,{})
        again=await preflight.run(control,request,result)
        assert result==again and result['calls']==0
        assert service.read('a','b')['entities']
        ai_request={**request,'mode':'ai'}
        from unittest.mock import patch
        with patch.object(preflight,'resolve_settings',return_value=('key','https://example.test','fixture')):
            with pytest.raises(runner.TaskPaused): await preflight.run(control,ai_request,{})
        assert control.checkpoint['budget_exhausted']
    asyncio.run(run_local())


def test_ai_fact_quote_validation_and_no_confirmation(owned):
    chapter=library.load_chapter('a','c0').chapter
    chunk={'start':0,'end':len(owned[0]),'revision':1}
    candidate={'entities':[{'name':'田中','facts':[{'key':'gender','value':'男','start':0,'end':2,'quote':'田中'}]}]}
    preflight.persist_candidates('a','b',chapter,chunk,candidate)
    snapshot=service.read('a','b')
    assert snapshot['facts'][0]['status']=='candidate'
    assert not service.context_facts('a','b','田中',0,len(owned[0]),budget=2000)
    preflight.persist_candidates('a','b',chapter,chunk,candidate)
    assert len(service.read('a','b')['facts'])==1
