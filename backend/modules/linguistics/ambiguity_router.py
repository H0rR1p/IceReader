import asyncio
from ...model_compat import BaseModel, Field
from fastapi import APIRouter, Depends
from ...core.request_context import RequestContext, current_request_context
from .models import ContextPolicy
from . import ambiguity
from ..sync.emission import after_commit

router=APIRouter(prefix='/api/grammar',tags=['grammar-choices'])


class SpanRequest(BaseModel):
    sentence_id: str
    span_id: str
    text_hash: str = Field(min_length=64,max_length=64)
    version: str
    analysis_revision: int = Field(ge=1)
    expected_override_revision: int = Field(default=0,ge=0)
    context_policy: ContextPolicy | None = None


class ChoiceRequest(SpanRequest):
    choice_id: str | None = None


@router.post('/choices')
async def choices(request: ChoiceRequest,context: RequestContext=Depends(current_request_context)):
    result=await asyncio.to_thread(ambiguity.choose,context.user_id,request.model_dump(),request.choice_id)
    await asyncio.to_thread(after_commit,'span_override',context.user_id,context.device_id,request.sentence_id,request.span_id)
    return result
