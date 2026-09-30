from fastapi import APIRouter, Depends

from ...ai_store import usage_summary
from ...core.request_context import RequestContext, current_request_context
from ...models import LocalAiSettingsInput, LocalAiSettingsStatus
from ...settings_store import get_pricing, get_settings_status, save_settings


router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/settings", response_model=LocalAiSettingsStatus)
async def read_settings(
    context: RequestContext = Depends(current_request_context),
) -> LocalAiSettingsStatus:
    return get_settings_status(context.user_id)


@router.put("/settings", response_model=LocalAiSettingsStatus)
async def update_settings(
    settings: LocalAiSettingsInput,
    context: RequestContext = Depends(current_request_context),
) -> LocalAiSettingsStatus:
    return save_settings(context.user_id, settings)


@router.get("/ai/usage")
async def read_ai_usage(
    context: RequestContext = Depends(current_request_context),
) -> dict:
    return usage_summary(context.user_id, get_pricing(context.user_id))
