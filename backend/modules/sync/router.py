import asyncio

from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field

from ...core.request_context import RequestContext,current_request_context
from . import repository
from .protocol import capabilities


router=APIRouter(prefix="/api/sync",tags=["sync"])


class Mutation(BaseModel):
    change_id: str=Field(min_length=1,max_length=200)
    entity_type: str=Field(min_length=1,max_length=50)
    entity_id: str=Field(min_length=1,max_length=300)
    operation: str="upsert"
    base_version: int | None=None
    payload: dict=Field(default_factory=dict)
    updated_at: float
    deleted_at: float | None=None


class PushBatch(BaseModel):
    changes: list[Mutation]=Field(max_length=1000)
    schema_version: int = 1
    capabilities: list[str] = Field(default_factory=list)


@router.post("/push")
async def push(payload: PushBatch,context: RequestContext=Depends(current_request_context)) -> dict:
    try: return await asyncio.to_thread(repository.push_changes,context.user_id,context.device_id,[value.model_dump(exclude_none=True) for value in payload.changes], schema_version=payload.schema_version,peer_capabilities=payload.capabilities)
    except ValueError as error: raise HTTPException(409,str(error)) from None


@router.get("/pull")
async def pull(after: int=0,limit: int=500,schema_version: int=1,capabilities: str="",context: RequestContext=Depends(current_request_context)) -> dict:
    try:
        return await asyncio.to_thread(repository.pull_changes,context.user_id,context.device_id,after,limit,schema_version=schema_version,peer_capabilities=capabilities.split(",") if capabilities else [])
    except ValueError as error:
        raise HTTPException(409,str(error)) from None


@router.get("/capabilities")
async def sync_capabilities(context: RequestContext=Depends(current_request_context)) -> dict:
    return capabilities()


@router.get("/conflicts")
async def conflicts(context: RequestContext=Depends(current_request_context)) -> list[dict]:
    return await asyncio.to_thread(repository.list_conflicts,context.user_id)


@router.get("/status")
async def status(context: RequestContext=Depends(current_request_context)) -> dict:
    return await asyncio.to_thread(repository.sync_status,context.user_id)


@router.get("/bindings")
async def bindings(context: RequestContext=Depends(current_request_context)) -> list[dict]:
    return await asyncio.to_thread(repository.list_bindings,context.user_id)


@router.post("/bindings")
async def bind(payload: dict,context: RequestContext=Depends(current_request_context)) -> dict:
    provider=str(payload.get("provider") or "").strip(); subject=str(payload.get("provider_subject") or "").strip()
    if not provider or not subject: raise HTTPException(422,"身份提供方和标识不能为空")
    try: return await asyncio.to_thread(repository.bind_identity,context.user_id,provider,subject)
    except ValueError: raise HTTPException(409,"该云身份已绑定其他用户") from None
