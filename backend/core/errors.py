from dataclasses import dataclass


@dataclass(slots=True)
class DomainError(Exception):
    """A stable application error that routers can expose without leaking internals."""

    code: str
    message: str
    status_code: int = 400

    def __str__(self) -> str:
        return self.message


class AuthenticationError(DomainError):
    def __init__(self, message: str = "登录状态无效，请重新登录") -> None:
        super().__init__("authentication_required", message, 401)


class AuthorizationError(DomainError):
    def __init__(self, message: str = "无权访问该资源") -> None:
        super().__init__("access_denied", message, 403)


class ConflictError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__("conflict", message, 409)
