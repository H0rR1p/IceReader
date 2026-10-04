from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from ...android_compat import BaseModel, Field

from ...core.request_context import RequestContext, current_request_context
from . import repository, service
from ..identity.router import _set_identity_cookies


router = APIRouter(prefix="/api/cloud", tags=["cloud-account"])


def _finish_authentication(result: dict, response: Response) -> dict:
    identity = result.pop('_identity', None)
    if identity is not None:
        _set_identity_cookies(response, identity.token, identity.device_id)
    return result


class CloudSettingsInput(BaseModel):
    base_url: str = Field(min_length=8, max_length=500)


class CloudRegisterInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=40)


class CloudLoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class EmailInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class VerifyInput(BaseModel):
    token: str = Field(min_length=20, max_length=500)


class ResetInput(VerifyInput):
    new_password: str = Field(min_length=10, max_length=256)


class OidcStartInput(BaseModel):
    callback_url: str = Field(min_length=12, max_length=1000)


class ResolveInput(BaseModel):
    winner_entity_id: str = Field(min_length=1, max_length=500)


class AdminUserStateInput(BaseModel):
    disabled: bool


class DisplayNameInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)


@router.get("/status")
async def status(context: RequestContext = Depends(current_request_context)) -> dict:
    return await asyncio.to_thread(repository.account_status, context.user_id)


@router.get("/admin/users")
async def admin_users(
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    context: RequestContext = Depends(current_request_context),
) -> dict:
    try:
        return await service.admin_users(context.user_id, query, limit, offset)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(403, str(exc)) from None


@router.patch("/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    payload: AdminUserStateInput,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    try:
        return await service.admin_set_user_disabled(context.user_id, user_id, payload.disabled)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(403, str(exc)) from None


@router.put("/settings")
async def settings(payload: CloudSettingsInput) -> dict:
    try:
        return await service.configure(payload.base_url)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"无法连接云端服务：{exc}") from None


@router.get("/health")
async def cloud_health() -> dict:
    try:
        return await service.health()
    except Exception as exc:
        raise HTTPException(502, f"无法连接云端服务：{exc}") from None


@router.post("/register")
async def register(payload: CloudRegisterInput, response: Response, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        result = await service.register(context.user_id, context.device_id, payload.email, payload.password, payload.display_name)
        return _finish_authentication(result, response)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"无法连接云端服务：{exc}") from None
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/login")
async def login(payload: CloudLoginInput, response: Response, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        result = await service.login(context.user_id, context.device_id, payload.email, payload.password)
        return _finish_authentication(result, response)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"无法连接云端服务：{exc}") from None
    except Exception as exc:
        raise HTTPException(401, str(exc)) from None


@router.post("/logout")
async def logout(context: RequestContext = Depends(current_request_context)) -> dict:
    await service.logout(context.user_id)
    return {"logged_out": True}


@router.patch("/profile")
async def update_profile(payload: DisplayNameInput, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        return await service.update_profile(context.user_id, payload.display_name)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/providers")
async def providers() -> list[dict]:
    try:
        return await service.providers()
    except Exception as exc:
        raise HTTPException(502, str(exc)) from None


@router.post("/oidc/start/{provider_id}")
async def oidc_start(provider_id: str, payload: OidcStartInput, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        return {"url": await service.oidc_start_url(provider_id, context.device_id, payload.callback_url)}
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/oidc/complete")
async def oidc_complete(code: str, context: RequestContext = Depends(current_request_context)) -> RedirectResponse:
    try:
        result = await service.exchange_handoff(context.user_id, code, context.device_id)
        response = RedirectResponse("/#/profile?cloud=connected", status_code=302)
        _finish_authentication(result, response)
        return response
    except Exception as exc:
        return RedirectResponse(f"/#/profile?cloud_error={quote(str(exc))}", status_code=302)


@router.post("/email/request-verification")
async def request_verification(payload: EmailInput) -> dict:
    try:
        return await service.request_verification(payload.email)
    except Exception as exc:
        raise HTTPException(502, f"无法发送验证邮件：{exc}") from None


@router.post("/email/verify")
async def verify_email(payload: VerifyInput, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        return await service.verify_email(context.user_id, payload.token)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/password/request-reset")
async def request_password_reset(payload: EmailInput) -> dict:
    try:
        return await service.request_password_reset(payload.email)
    except Exception as exc:
        raise HTTPException(502, f"无法发送密码恢复邮件：{exc}") from None


@router.post("/password/reset")
async def reset_password(payload: ResetInput) -> dict:
    try:
        return await service.reset_password(payload.token, payload.new_password)
    except Exception as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("/sessions")
async def sessions(context: RequestContext = Depends(current_request_context)) -> list[dict]:
    try:
        return await service.cloud_sessions(context.user_id)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"无法读取云端设备：{exc}") from None


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: str, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        await service.revoke_cloud_session(context.user_id, session_id)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"无法撤销云端设备：{exc}") from None
    return {"revoked": True}


@router.post("/sync")
async def sync_now(context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        return await service.sync(context.user_id, context.device_id)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"云端同步失败：{exc}") from None


@router.post("/pull")
async def pull_now(context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        return await service.sync(context.user_id, context.device_id, pull_only=True)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"云端拉取失败：{exc}") from None


@router.get("/conflicts")
async def conflicts(context: RequestContext = Depends(current_request_context)) -> list[dict]:
    try:
        return await service.conflicts(context.user_id)
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"无法读取云端冲突：{exc}") from None


@router.post("/conflicts/{group_id}/resolve")
async def resolve_conflict(group_id: str, payload: ResolveInput, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        result = await service.resolve_conflict(context.user_id, group_id, payload.winner_entity_id)
        await service.sync(context.user_id, context.device_id, pull_only=True)
        return result
    except service.CloudAuthenticationError as exc:
        raise HTTPException(401, str(exc)) from None
    except Exception as exc:
        raise HTTPException(502, f"冲突处理失败：{exc}") from None

