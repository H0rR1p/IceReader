import asyncio
from contextlib import asynccontextmanager
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .ai import close_http_client
from .ai_store import close_store as close_ai_store
from .core.errors import DomainError
from .core.request_context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from .modules.analysis.router import router as analysis_router
from .modules.activity.repository import initialize_store as initialize_activity_store
from .modules.activity.router import router as activity_router
from .modules.cards.repository import initialize_store as initialize_cards_store
from .modules.cards.router import router as cards_router
from .modules.content.router import router as content_router
from .modules.identity.repository import (
    initialize_store as initialize_identity_store,
    resolve_or_bootstrap_session,
)
from .modules.identity.router import router as identity_router
from .modules.library.repository import initialize_store as initialize_library_store
from .modules.library.router import router as library_router
from .modules.learning.repository import initialize_store as initialize_learning_store
from .modules.learning.router import router as learning_router
from .modules.jobs.service import initialize_store as initialize_job_store, record_observation
from .modules.jobs.router import router as jobs_router
from .modules.settings.router import router as settings_router
from .modules.sync.repository import initialize_store as initialize_sync_store
from .modules.sync.router import router as sync_router
from .modules.voice.router import router as voice_router
from .paths import DIST_DIR
from .settings_store import migrate_legacy_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        migration_user_id = await asyncio.to_thread(initialize_identity_store)
        await asyncio.to_thread(migrate_legacy_settings, migration_user_id)
        await asyncio.to_thread(initialize_library_store, migration_user_id)
        await asyncio.to_thread(initialize_job_store)
        await asyncio.to_thread(initialize_learning_store)
        await asyncio.to_thread(initialize_cards_store)
        await asyncio.to_thread(initialize_activity_store)
        await asyncio.to_thread(initialize_sync_store)
        yield
    finally:
        try:
            await close_http_client()
        finally:
            close_ai_store()


app = FastAPI(title="冰读本地 API", version="0.2.0", lifespan=lifespan)
app.include_router(identity_router)
app.include_router(content_router)
app.include_router(library_router)
app.include_router(settings_router)
app.include_router(voice_router)
app.include_router(analysis_router)
app.include_router(jobs_router)
app.include_router(learning_router)
app.include_router(cards_router)
app.include_router(activity_router)
app.include_router(sync_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DomainError)
async def handle_domain_error(_request: Request, error: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"detail": error.message, "code": error.code},
    )


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    started_at = time.perf_counter()
    identity = await asyncio.to_thread(
        resolve_or_bootstrap_session,
        request.cookies.get("bingdu_session"),
        request.cookies.get("bingdu_device"),
    )
    context = RequestContext(
        user_id=identity.user_id,
        session_id=identity.session_id,
        device_id=identity.device_id,
        auth_provider=identity.auth_provider,
        request_id=request.headers.get("X-Request-ID") or str(uuid.uuid4()),
    )
    request.state.request_context = context
    token = bind_request_context(context)
    try:
        response = await call_next(request)
    finally:
        reset_request_context(token)
    identity_endpoint_sets_session = request.url.path in {
        "/api/auth/local/register", "/api/auth/local/login",
    }
    if identity.token and not identity_endpoint_sets_session:
        response.set_cookie(
            "bingdu_session", identity.token, max_age=30 * 24 * 60 * 60,
            httponly=True, samesite="strict", secure=False,
        )
    if request.cookies.get("bingdu_device") != identity.device_id:
        response.set_cookie(
            "bingdu_device", identity.device_id, max_age=365 * 24 * 60 * 60,
            httponly=True, samesite="strict", secure=False,
        )
    response.headers["X-Request-ID"] = context.request_id
    duration_ms = (time.perf_counter() - started_at) * 1000
    if duration_ms >= 250 or response.status_code >= 400:
        await asyncio.to_thread(
            record_observation,
            "request",
            request.url.path,
            owner_user_id=context.user_id,
            request_id=context.request_id,
            duration_ms=duration_ms,
            detail={"method": request.method, "status_code": response.status_code},
        )
    if request.url.path in {"/", "/index.html"}:
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "app": "bingdu", "version": app.version}


if DIST_DIR.is_dir():
    app.mount("/", StaticFiles(directory=DIST_DIR, html=True), name="web")
