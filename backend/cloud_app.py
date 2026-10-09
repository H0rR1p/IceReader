from __future__ import annotations

import asyncio
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .cloud.config import load_config
from .cloud.mailer import Mailer
from .cloud.oauth import OAuthService
from .cloud.repository import CloudAuthError, CloudConflictError, CloudRepository
from .legal import router as legal_router, source_page


config = load_config()
repository = CloudRepository(config)
mailer = Mailer(config)
oauth = OAuthService(config, repository)
_attempts: dict[str, deque[float]] = defaultdict(deque)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(repository.initialize)
    await asyncio.to_thread(repository.ensure_admin)
    yield


app = FastAPI(title="冰读云端服务", version="0.4.1", lifespan=lifespan)
app.include_router(legal_router)
app.add_api_route("/", source_page, methods=["GET"], include_in_schema=False)
if config.allowed_origins:
    app.add_middleware(
        CORSMiddleware, allow_origins=list(config.allowed_origins), allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response


def _limited(request: Request, bucket: str, maximum: int = 10, window: int = 60) -> None:
    host = request.client.host if request.client else "unknown"
    key = f"{bucket}:{host}"
    now = time.time()
    values = _attempts[key]
    while values and values[0] <= now - window:
        values.popleft()
    if len(values) >= maximum:
        raise HTTPException(429, "请求过于频繁，请稍后再试", headers={"Retry-After": str(window)})
    values.append(now)


def _bearer(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "需要云端登录", headers={"WWW-Authenticate": "Bearer"})
    return authorization.split(" ", 1)[1].strip()


async def cloud_identity(token: str = Depends(_bearer)) -> tuple[dict, str, str]:
    try:
        user, device_id = await asyncio.to_thread(repository.authenticate, token)
        return user, device_id, token
    except CloudAuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from None


async def verified_identity(identity: tuple[dict, str, str] = Depends(cloud_identity)) -> tuple[dict, str, str]:
    if not identity[0].get("email_verified"):
        raise HTTPException(403, "请先验证邮箱再同步数据")
    return identity


async def admin_identity(identity: tuple[dict, str, str] = Depends(cloud_identity)) -> tuple[dict, str, str]:
    if identity[0].get("role") != "admin":
        raise HTTPException(403, "需要管理员权限")
    return identity


class RegisterInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=40)
    device_id: str = Field(min_length=1, max_length=200)
    device_name: str = Field(default="冰读设备", max_length=200)


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)
    device_id: str = Field(min_length=1, max_length=200)
    device_name: str = Field(default="冰读设备", max_length=200)


class RefreshInput(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=500)


class EmailInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class TokenInput(BaseModel):
    token: str = Field(min_length=20, max_length=500)


class ResetInput(TokenInput):
    new_password: str = Field(min_length=10, max_length=256)


class HandoffInput(BaseModel):
    code: str = Field(min_length=20, max_length=500)


class Mutation(BaseModel):
    change_id: str = Field(min_length=1, max_length=200)
    entity_type: str = Field(min_length=1, max_length=50)
    entity_id: str = Field(min_length=1, max_length=300)
    operation: str = "upsert"
    base_version: int | None = None
    payload: dict = Field(default_factory=dict)
    updated_at: float
    deleted_at: float | None = None


class PushBatch(BaseModel):
    changes: list[Mutation] = Field(max_length=1000)
    schema_version: int = 1
    capabilities: list[str] = Field(default_factory=list)


class ResolveInput(BaseModel):
    winner_entity_id: str = Field(min_length=1, max_length=500)


class AdminUserStateInput(BaseModel):
    disabled: bool


class DisplayNameInput(BaseModel):
    display_name: str = Field(min_length=1, max_length=40)


@app.exception_handler(CloudConflictError)
async def conflict_handler(_request: Request, error: CloudConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(error), "code": "conflict"})


@app.exception_handler(CloudAuthError)
async def auth_handler(_request: Request, error: CloudAuthError) -> JSONResponse:
    return JSONResponse(status_code=401, content={"detail": str(error), "code": "cloud_auth_error"})


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "app": "bingdu-cloud", "version": app.version}


@app.get("/v1/admin/users")
async def admin_users(
    query: str = Query(default="", max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: tuple[dict, str, str] = Depends(admin_identity),
) -> dict:
    return await asyncio.to_thread(repository.list_users, query, limit, offset)


@app.patch("/v1/admin/users/{user_id}")
async def admin_update_user(
    user_id: str,
    payload: AdminUserStateInput,
    identity: tuple[dict, str, str] = Depends(admin_identity),
) -> dict:
    try:
        return await asyncio.to_thread(repository.set_user_disabled, identity[0]["id"], user_id, payload.disabled)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/v1/auth/providers")
async def providers() -> list[dict]:
    return [{"id": value.id, "name": value.label, "label": value.label, "kind": value.kind} for value in config.providers.values()]


@app.post("/v1/auth/register")
async def register(payload: RegisterInput, request: Request) -> dict:
    _limited(request, "register", 6, 300)
    result = await asyncio.to_thread(
        repository.register, payload.email, payload.password, payload.display_name,
        payload.device_id, payload.device_name,
    )
    token = await asyncio.to_thread(repository.create_action_token, result["user"]["id"], "verify_email", 24 * 60 * 60)
    link = f"{config.public_url}/verify-email?token={token}"
    delivery = await asyncio.to_thread(
        mailer.send, result["user"]["email"], "验证你的冰读云端邮箱",
        f"请在 24 小时内打开以下链接验证邮箱：\n\n{link}\n\n如果不是你创建的账号，可以忽略这封邮件。",
    )
    result["verification_delivery"] = delivery
    if config.dev_mode:
        result["development_verification_token"] = token
    return result


@app.post("/v1/auth/login")
async def login(payload: LoginInput, request: Request) -> dict:
    _limited(request, "login", 12, 300)
    return await asyncio.to_thread(
        repository.login, payload.email, payload.password, payload.device_id, payload.device_name,
    )


@app.post("/v1/auth/refresh")
async def refresh(payload: RefreshInput, request: Request) -> dict:
    _limited(request, "refresh", 30, 60)
    return await asyncio.to_thread(repository.refresh, payload.refresh_token)


@app.post("/v1/auth/logout")
async def logout(identity: tuple[dict, str, str] = Depends(cloud_identity)) -> dict:
    await asyncio.to_thread(repository.logout, identity[2])
    return {"logged_out": True}


@app.get("/v1/auth/me")
async def me(identity: tuple[dict, str, str] = Depends(cloud_identity)) -> dict:
    return identity[0]


@app.patch("/v1/auth/me")
async def update_me(
    payload: DisplayNameInput,
    identity: tuple[dict, str, str] = Depends(cloud_identity),
) -> dict:
    try:
        return await asyncio.to_thread(repository.update_display_name, identity[0]["id"], payload.display_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/v1/auth/sessions")
async def sessions(identity: tuple[dict, str, str] = Depends(cloud_identity)) -> list[dict]:
    return await asyncio.to_thread(repository.sessions, identity[0]["id"])


@app.delete("/v1/auth/sessions/{session_id}")
async def revoke_session(session_id: str, identity: tuple[dict, str, str] = Depends(cloud_identity)) -> dict:
    if not await asyncio.to_thread(repository.revoke_session, identity[0]["id"], session_id):
        raise HTTPException(404, "设备会话不存在")
    return {"revoked": True}


@app.post("/v1/auth/email/request-verification")
async def request_verification(payload: EmailInput, request: Request) -> dict:
    _limited(request, "verify", 6, 300)
    found = await asyncio.to_thread(repository.request_action_token, payload.email, "verify_email")
    if found:
        user, token = found
        link = f"{config.public_url}/verify-email?token={token}"
        await asyncio.to_thread(mailer.send, user["email"], "验证你的冰读云端邮箱", f"请打开以下链接验证邮箱：\n\n{link}")
    result = {"accepted": True}
    if found and config.dev_mode:
        result["development_token"] = found[1]
    return result


@app.post("/v1/auth/email/verify")
async def verify_email(payload: TokenInput) -> dict:
    return await asyncio.to_thread(repository.verify_email, payload.token)


@app.get("/verify-email")
async def verify_email_link(token: str = Query(min_length=20, max_length=500)) -> JSONResponse:
    try:
        user = await asyncio.to_thread(repository.verify_email, token)
        return JSONResponse({"verified": True, "email": user["email"], "message": "邮箱验证完成，可以返回冰读。"})
    except CloudAuthError as exc:
        return JSONResponse(status_code=400, content={"verified": False, "detail": str(exc)})


@app.post("/v1/auth/password/request-reset")
async def request_password_reset(payload: EmailInput, request: Request) -> dict:
    _limited(request, "reset", 6, 300)
    found = await asyncio.to_thread(repository.request_action_token, payload.email, "reset_password")
    if found:
        user, token = found
        link = f"{config.public_url}/password-reset?token={token}"
        await asyncio.to_thread(mailer.send, user["email"], "重设你的冰读云端密码", f"请在一小时内使用以下链接重设密码：\n\n{link}")
    result = {"accepted": True}
    if found and config.dev_mode:
        result["development_token"] = found[1]
    return result


@app.post("/v1/auth/password/reset")
async def reset_password(payload: ResetInput) -> dict:
    return await asyncio.to_thread(repository.reset_password, payload.token, payload.new_password)


@app.get("/password-reset")
async def password_reset_info(token: str = Query(min_length=20, max_length=500)) -> JSONResponse:
    return JSONResponse({"token": token, "message": "请在冰读登录页的“找回云端账号”中粘贴此重置码。"})


@app.get("/v1/auth/oidc/start/{provider_id}")
async def oidc_start(provider_id: str, return_url: str, device_id: str, device_name: str = "冰读设备") -> RedirectResponse:
    target = await oauth.start(provider_id, return_url, device_id, device_name)
    return RedirectResponse(target, status_code=302)


@app.get("/v1/auth/oidc/callback/{provider_id}")
async def oidc_callback(provider_id: str, state: str, code: str = "", error: str = "") -> RedirectResponse:
    if error:
        return_url = await oauth.cancel(provider_id, state)
        parsed = urlsplit(return_url)
        query = parse_qs(parsed.query)
        query.pop("device_id", None); query.pop("device_name", None)
        clean_query = [(key, item) for key, values in query.items() for item in values]
        clean_query.append(("cloud_error", f"第三方登录未完成：{error}"))
        return RedirectResponse(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(clean_query), parsed.fragment)), status_code=302)
    if not code:
        raise HTTPException(400, "第三方登录缺少授权码")
    return_url, user_id = await oauth.callback(provider_id, state, code)
    parsed = urlsplit(return_url)
    query = parse_qs(parsed.query)
    device_id = str(query.pop("device_id", [secrets.token_urlsafe(12)])[0])
    device_name = str(query.pop("device_name", ["冰读设备"])[0])
    handoff = await asyncio.to_thread(repository.create_handoff, user_id, provider_id, device_id, device_name)
    clean_query = [(key, item) for key, values in query.items() for item in values]
    clean_query.extend([("code", handoff), ("provider", provider_id)])
    target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(clean_query), parsed.fragment))
    return RedirectResponse(target, status_code=302)


@app.post("/v1/auth/oidc/exchange")
async def oidc_exchange(payload: HandoffInput) -> dict:
    return await asyncio.to_thread(repository.exchange_handoff, payload.code)


@app.post("/v1/sync/push")
async def sync_push(payload: PushBatch, identity: tuple[dict, str, str] = Depends(verified_identity)) -> dict:
    try:
        return await asyncio.to_thread(
            repository.push_changes, identity[0]["id"], identity[1],
            [item.model_dump(exclude_none=True) for item in payload.changes],
            schema_version=payload.schema_version, peer_capabilities=payload.capabilities,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@app.get("/v1/sync/pull")
async def sync_pull(after: int = 0, limit: int = 500, schema_version: int = 1, capabilities: str = "", identity: tuple[dict, str, str] = Depends(verified_identity)) -> dict:
    try:
        return await asyncio.to_thread(repository.pull_changes, identity[0]["id"], identity[1], after, limit, schema_version=schema_version, peer_capabilities=capabilities.split(",") if capabilities else [])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/v1/sync/capabilities")
async def sync_capabilities(identity: tuple[dict, str, str] = Depends(verified_identity)) -> dict:
    from .modules.sync.protocol import capabilities
    return capabilities()


@app.get("/v1/sync/status")
async def sync_status(identity: tuple[dict, str, str] = Depends(verified_identity)) -> dict:
    return await asyncio.to_thread(repository.sync_status, identity[0]["id"])


@app.get("/v1/sync/conflicts")
async def sync_conflicts(identity: tuple[dict, str, str] = Depends(verified_identity)) -> list[dict]:
    return await asyncio.to_thread(repository.conflicts, identity[0]["id"])


@app.post("/v1/sync/conflicts/{group_id}/resolve")
async def resolve_conflict(group_id: str, payload: ResolveInput, identity: tuple[dict, str, str] = Depends(verified_identity)) -> dict:
    try:
        return await asyncio.to_thread(
            repository.resolve_conflict, identity[0]["id"], identity[1], group_id, payload.winner_entity_id,
        )
    except KeyError:
        raise HTTPException(404, "同步冲突不存在") from None

