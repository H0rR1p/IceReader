import asyncio
from datetime import date,timedelta

from fastapi import APIRouter,Depends,HTTPException
from ...android_compat import BaseModel,Field

from ...core.request_context import RequestContext,current_request_context
from . import repository


router=APIRouter(prefix="/api/me/activity",tags=["activity"])


class Heartbeat(BaseModel):
    id: str=Field(min_length=1,max_length=200)
    session_id: str=Field(min_length=1,max_length=200)
    activity_type: str
    window_start: float
    window_end: float
    local_date: str=Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    timezone: str="local"
    cards_reviewed: int=Field(default=0,ge=0,le=100)
    sentences_read: int=Field(default=0,ge=0,le=100)
    lookup_count: int=Field(default=0,ge=0,le=100)


@router.post("/heartbeat")
async def heartbeat(payload: Heartbeat,context: RequestContext=Depends(current_request_context)) -> dict:
    try: return await asyncio.to_thread(repository.record_heartbeat,context.user_id,context.device_id,payload.model_dump())
    except ValueError: raise HTTPException(422,"无效的学习活动类型") from None


@router.get("/heatmap")
async def read_heatmap(date_from: str="",date_to: str="",context: RequestContext=Depends(current_request_context)) -> list[dict]:
    today=date.today(); end=date_to or today.isoformat(); start=date_from or (today-timedelta(days=364)).isoformat()
    return await asyncio.to_thread(repository.heatmap,context.user_id,start,end)


@router.get("/summary")
async def read_summary(days: int=7,context: RequestContext=Depends(current_request_context)) -> dict:
    return await asyncio.to_thread(repository.summary,context.user_id,min(366,max(1,days)))
