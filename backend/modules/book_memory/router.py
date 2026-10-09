import asyncio
from fastapi import APIRouter, Depends, Query
from ...model_compat import BaseModel, Field
from ...core.request_context import RequestContext, current_request_context
from .models import EntityInput, FactInput, MergeInput, PreflightInput
from . import service
from . import preflight, repository
from ..jobs import runner
from ..sync.emission import after_commit

router=APIRouter(prefix='/api/books/{book_id}',tags=['book-memory'])


@router.get('/entities')
async def entities(book_id: str, offset: int=Query(0,ge=0),limit: int=Query(40,ge=1,le=100),q: str='',context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(service.read,context.user_id,book_id)
    filtered=[row for row in result['entities'] if not row.get('deleted') and (not q or q.casefold() in row['name'].casefold() or any(q.casefold() in alias.casefold() for alias in row.get('aliases',[])))]
    selected=filtered[offset:offset+limit]; ids={row['id'] for row in selected}
    return {'entities':selected,'facts':[row for row in result['facts'] if row['entity_id'] in ids and not row.get('deleted')],
            'history':result['history'],'total':len(filtered),'offset':offset,'limit':limit}


@router.post('/entities')
async def add_entity(book_id: str,request: EntityInput,context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(service.save_entity,context.user_id,book_id,request)
    await asyncio.to_thread(after_commit,'book_memory',context.user_id,context.device_id,book_id)
    return result


@router.patch('/entities/{entity_id}')
async def edit_entity(book_id: str,entity_id: str,request: EntityInput,context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(service.save_entity,context.user_id,book_id,request,entity_id)
    await asyncio.to_thread(after_commit,'book_memory',context.user_id,context.device_id,book_id)
    return result


@router.post('/entities/{entity_id}/facts')
async def add_fact(book_id: str,entity_id: str,request: FactInput,context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(service.save_fact,context.user_id,book_id,entity_id,request)
    await asyncio.to_thread(after_commit,'book_memory',context.user_id,context.device_id,book_id)
    return result


@router.patch('/entities/{entity_id}/facts/{fact_id}')
async def edit_fact(book_id: str,entity_id: str,fact_id: str,request: FactInput,context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(service.save_fact,context.user_id,book_id,entity_id,request,fact_id)
    await asyncio.to_thread(after_commit,'book_memory',context.user_id,context.device_id,book_id)
    return result


@router.post('/entities/{entity_id}/merge')
async def merge(book_id: str,entity_id: str,request: MergeInput,context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(service.merge,context.user_id,book_id,entity_id,request.target_entity_id,request.expected_revision)
    await asyncio.to_thread(after_commit,'book_memory',context.user_id,context.device_id,book_id)
    return result


@router.post('/entity-revisions/{revision_id}/undo')
async def undo(book_id: str,revision_id: str,context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(service.undo,context.user_id,book_id,revision_id)
    await asyncio.to_thread(after_commit,'book_memory',context.user_id,context.device_id,book_id)
    return result


@router.post('/preflight')
async def start_preflight(book_id: str,request: PreflightInput,context: RequestContext=Depends(current_request_context)):
    frozen=await asyncio.to_thread(preflight.prepare,context.user_id,book_id,request.model_dump())
    job=await runner.submit(context.user_id,preflight.KIND,frozen,lock_key=f'preflight:{book_id}',request_id=context.request_id)
    return {'job_id':job['id'],'job':job}


@router.get('/preflight/summaries')
async def summaries(book_id: str,context: RequestContext=Depends(current_request_context)):
    await asyncio.to_thread(service.read,context.user_id,book_id)
    return await asyncio.to_thread(repository.read_summaries,context.user_id,book_id)
