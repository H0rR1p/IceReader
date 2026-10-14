import asyncio
from fastapi import APIRouter, Depends, Header
from ...model_compat import BaseModel, Field

from ...core.request_context import RequestContext, current_request_context
from .models import ContextPolicy, StructureRequest
from . import repository, service
from .explanations import ExpressionExplanationRequest, explain_expression

router = APIRouter(prefix="/api", tags=["linguistics"])


class PreferencesInput(BaseModel):
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    dependency_enhancement: bool = False
    ambiguity_resolution: bool = False


@router.get("/analysis/capabilities")
async def read_capabilities(context: RequestContext = Depends(current_request_context)):
    return await asyncio.to_thread(service.capabilities)


@router.get("/analysis/preferences")
async def read_preferences(context: RequestContext = Depends(current_request_context)):
    return await asyncio.to_thread(repository.load_preferences, context.user_id)


@router.put("/analysis/preferences")
async def update_preferences(request: PreferencesInput,
                             context: RequestContext = Depends(current_request_context)):
    return await asyncio.to_thread(repository.save_preferences, context.user_id, request.model_dump())


@router.post("/sentences/structure")
async def structure(request: StructureRequest,
                    context: RequestContext = Depends(current_request_context)):
    return await asyncio.to_thread(service.structure_results, context.user_id, request.sentence_ids,
                                   required_version=request.required_version, force=request.force)


@router.post("/grammar/expressions/explain")
async def explain_selected_expression(request: ExpressionExplanationRequest,
                                      x_api_key: str | None = Header(default=None),
                                      context: RequestContext = Depends(current_request_context)):
    return await explain_expression(request, context.user_id, x_api_key)
