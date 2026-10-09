import asyncio
import pytest
from fastapi import HTTPException
from backend.models import LibraryPatch
from backend.core.request_context import RequestContext
from backend.modules.library import repository as library
from backend.modules.learning import repository as learning
from backend.modules.cards import repository as cards, grammar, router
from backend.modules.linguistics import repository as linguistics, service


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(library,'LIBRARY_PATH',tmp_path/'library.sqlite3')
    monkeypatch.setattr(library,'LEGACY_PATH',tmp_path/'absent')
    monkeypatch.setattr(learning,'LEARNING_PATH',tmp_path/'learning.sqlite3')
    monkeypatch.setattr(cards,'CARDS_PATH',tmp_path/'learning.sqlite3')
    monkeypatch.setattr(linguistics,'STORE_PATH',tmp_path/'linguistics.sqlite3')
    text='母に読ませました。'
    library.apply_library_patch('a',LibraryPatch(upserts={
        'books':[{'id':'b','title':'原创'}], 'chapters':[{'id':'c','bookId':'b','text':text}],
        'sentences':[{'id':'s','chapter_id':'c','start':0,'end':len(text),'original':text}]}))
    result=service.structure_results('a',['s'])['results'][0]
    span=next(value for value in result['learning_spans'] if value['kind']=='morphology')
    return { 'sentence_id':'s','span_id':span['id'],'grammar_id':'ja.morph.causative',
        'version':result['analysis_manifest']['version'],'text_hash':result['analysis_manifest']['text_hash'],
        'analysis_revision':1,'card_template':'grammar-recognition'}


def test_grammar_candidate_templates_dedup_review_and_history(source,monkeypatch):
    first=grammar.create('a',source)
    assert grammar.create('a',source)['id']==first['id']
    second=grammar.create('a',{**source,'card_template':'form-restoration'})
    assert second['source']['question']=='母に【____】。'
    firstcard=cards.accept_candidate('a',first['id'])
    secondcard=cards.accept_candidate('a',second['id'])
    due=cards.due_cards('a')
    assert len(due)==2
    assert due[0]['source']['kind']=='grammar'
    async def sync(*args): pass
    monkeypatch.setattr(router,'push_changes',lambda *args: {})
    context=RequestContext(user_id='a',device_id='d',session_id='s',auth_provider='local',request_id='r')
    asyncio.run(router.submit_review(firstcard['id'],router.ReviewInput(id='review1',rating='good'),context))
    item=learning.get_knowledge_item(firstcard['knowledge_item_id'])
    assert item['type']=='grammar' and item['canonical_key']=='ja.morph.causative'
    state=learning.knowledge_states('a',[item])[0]
    assert state['confidence']>0
    with learning._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM knowledge_items WHERE canonical_key LIKE 'card:%'").fetchone()[0]==0
    assert cards.cards_by_ids('a',[secondcard['id']])[0]['reps']==0


def test_reject_foreign_or_old_structure(source):
    with pytest.raises(HTTPException) as foreign:
        grammar.create('other',source)
    assert foreign.value.status_code==404
    with pytest.raises(HTTPException) as stale:
        grammar.create('a',{**source,'analysis_revision':2})
    assert stale.value.status_code==409


def test_legacy_grammar_alias_keeps_events_and_mastery(source):
    learning.append_events('a','d',[{'id':'old','item':{'id':'olditem','type':'grammar','canonical_key':'~ている'},'event_type':'srs_good','occurred_at':1}])
    learning._initialized_path=None
    learning.initialize_store()
    old=learning.get_knowledge_item('olditem')
    assert old['canonical_key']=='ja.te_iru'
    now=learning.ensure_knowledge_item({'id':'newitem','type':'grammar','canonical_key':'ja.te_iru'})
    assert now=='olditem'
    assert learning.knowledge_states('a',[{'type':'grammar','canonical_key':'ja.te_iru'}])[0]['mastery']>.5
