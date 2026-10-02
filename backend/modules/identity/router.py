import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse

from ...core.request_context import RequestContext, current_request_context
from .repository import (
    create_local_user,
    avatar_filename,
    login_local_user,
    list_local_profiles,
    revoke_session,
    revoke_other_session,
    revoke_other_sessions,
    switch_local_profile,
    list_user_sessions,
    update_user_profile,
    user_profile,
)
from ...paths import DATA_DIR
from ...runtime_config import COOKIE_SECURE, PUBLIC_MODE


router = APIRouter(prefix="/api", tags=["identity"])
AVATAR_DIR = DATA_DIR / "users"


def _set_identity_cookies(response: Response, token: str, device_id: str) -> None:
    response.set_cookie(
        "bingdu_session", token, max_age=30 * 24 * 60 * 60,
        httponly=True, samesite="strict", secure=COOKIE_SECURE,
    )
    response.set_cookie(
        "bingdu_device", device_id, max_age=365 * 24 * 60 * 60,
        httponly=True, samesite="strict", secure=COOKIE_SECURE,
    )


@router.get("/me")
async def read_current_user(
    context: RequestContext = Depends(current_request_context),
) -> dict:
    return {
        **user_profile(context.user_id),
        "session_id": context.session_id,
        "device_id": context.device_id,
        "auth_provider": context.auth_provider,
    }


@router.patch("/me")
async def update_current_user(
    payload: dict,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    try:
        return await asyncio.to_thread(
            update_user_profile, context.user_id, str(payload.get("display_name") or ""), None,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/me/avatar")
async def upload_current_user_avatar(
    file: UploadFile = File(...),
    context: RequestContext = Depends(current_request_context),
) -> dict:
    suffixes = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
    suffix = suffixes.get((file.content_type or "").lower())
    if not suffix:
        raise HTTPException(400, "头像只支持 JPG、PNG、WebP 或 GIF 图片")
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "头像图片为空")
    if len(payload) > 5 * 1024 * 1024:
        raise HTTPException(413, "头像图片不能超过 5 MB")
    user_dir = AVATAR_DIR / context.user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    filename = f"avatar{suffix}"
    for old_suffix in suffixes.values():
        (user_dir / f"avatar{old_suffix}").unlink(missing_ok=True)
    (user_dir / filename).write_bytes(payload)
    profile = await asyncio.to_thread(update_user_profile, context.user_id, None, filename)
    profile["avatar_url"] = f"/api/me/avatar?v={time.time_ns()}"
    return profile


@router.get("/me/avatar")
async def read_current_user_avatar(
    context: RequestContext = Depends(current_request_context),
) -> FileResponse:
    filename = await asyncio.to_thread(avatar_filename, context.user_id)
    if not filename or Path(filename).name != filename:
        raise HTTPException(404, "头像不存在")
    target = (AVATAR_DIR / context.user_id / filename).resolve()
    root = AVATAR_DIR.resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, "头像不存在")
    return FileResponse(target)


@router.post("/auth/local/register")
async def register_local_user(payload: dict, response: Response) -> dict:
    if PUBLIC_MODE:
        raise HTTPException(403, "公网版本请使用访客模式或云端账号")
    identity = await asyncio.to_thread(
        create_local_user,
        str(payload.get("display_name") or ""),
        str(payload.get("username") or ""),
    )
    _set_identity_cookies(response, identity.token or "", identity.device_id)
    return user_profile(identity.user_id)


@router.post("/auth/local/login")
async def login_with_local_user(payload: dict, request: Request, response: Response) -> dict:
    if PUBLIC_MODE:
        raise HTTPException(403, "公网版本请使用访客模式或云端账号")
    identity = await asyncio.to_thread(
        login_local_user,
        str(payload.get("username") or ""),
        request.cookies.get("bingdu_device"),
    )
    _set_identity_cookies(response, identity.token or "", identity.device_id)
    return user_profile(identity.user_id)


@router.get("/auth/local/profiles")
async def read_local_profiles() -> list[dict]:
    if PUBLIC_MODE:
        return []
    return await asyncio.to_thread(list_local_profiles)


@router.post("/auth/local/switch")
async def switch_local_user(payload: dict, request: Request, response: Response) -> dict:
    if PUBLIC_MODE:
        raise HTTPException(403, "公网版本不能切换其他访客的数据空间")
    identity = await asyncio.to_thread(
        switch_local_profile,
        str(payload.get("user_id") or ""),
        request.cookies.get("bingdu_device"),
    )
    _set_identity_cookies(response, identity.token or "", identity.device_id)
    return user_profile(identity.user_id)


@router.post("/auth/logout")
async def logout_current_user(
    response: Response,
    context: RequestContext = Depends(current_request_context),
) -> dict:
    await asyncio.to_thread(revoke_session, context.session_id, context.user_id)
    response.delete_cookie("bingdu_session")
    return {"logged_out": True}


@router.get("/me/sessions")
async def read_sessions(context: RequestContext = Depends(current_request_context)) -> list[dict]:
    rows = await asyncio.to_thread(list_user_sessions, context.user_id)
    for row in rows:
        row["current"] = row["id"] == context.session_id
    return rows


@router.delete("/me/sessions/{session_id}")
async def delete_session(session_id: str, context: RequestContext = Depends(current_request_context)) -> dict:
    try:
        revoked = await asyncio.to_thread(revoke_other_session, context.user_id, session_id, context.session_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not revoked:
        raise HTTPException(404, "会话不存在")
    return {"revoked": True}


@router.delete("/me/sessions")
async def delete_other_sessions(context: RequestContext = Depends(current_request_context)) -> dict:
    return {"revoked": await asyncio.to_thread(revoke_other_sessions, context.user_id, context.session_id)}


@router.post("/me/password")
async def update_password(payload: dict, context: RequestContext = Depends(current_request_context)) -> dict:
    raise HTTPException(410, "本机资料空间不再使用密码，请直接修改昵称或切换资料空间")
