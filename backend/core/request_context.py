from contextvars import ContextVar, Token
from dataclasses import dataclass

from fastapi import Request

from .errors import AuthenticationError


@dataclass(frozen=True, slots=True)
class RequestContext:
    user_id: str
    session_id: str
    device_id: str
    auth_provider: str
    request_id: str


_current_context: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def bind_request_context(context: RequestContext) -> Token:
    return _current_context.set(context)


def reset_request_context(token: Token) -> None:
    _current_context.reset(token)


def current_request_context(request: Request) -> RequestContext:
    context = getattr(request.state, "request_context", None)
    if not isinstance(context, RequestContext):
        raise AuthenticationError()
    return context


def active_request_context() -> RequestContext:
    context = _current_context.get()
    if context is None:
        raise AuthenticationError()
    return context
