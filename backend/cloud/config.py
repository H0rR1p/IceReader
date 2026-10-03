from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..paths import DATA_DIR


@dataclass(frozen=True, slots=True)
class OidcProvider:
    id: str
    label: str
    kind: str = "oidc"
    discovery_url: str = ""
    client_id: str = ""
    client_secret: str = ""
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    authorization_endpoint: str = ""
    token_endpoint: str = ""
    userinfo_endpoint: str = ""
    emails_endpoint: str = ""

    @classmethod
    def from_dict(cls, value: dict) -> "OidcProvider":
        provider_id = str(value.get("id") or "").strip().casefold()
        if not provider_id or not provider_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("OIDC provider id is invalid")
        scopes = value.get("scopes") or ["openid", "email", "profile"]
        return cls(
            id=provider_id,
            label=str(value.get("label") or provider_id),
            kind=str(value.get("kind") or "oidc").strip().casefold(),
            discovery_url=str(value.get("discovery_url") or "").strip(),
            client_id=str(value.get("client_id") or "").strip(),
            client_secret=str(value.get("client_secret") or "").strip(),
            scopes=tuple(str(item) for item in scopes if str(item).strip()),
            authorization_endpoint=str(value.get("authorization_endpoint") or "").strip(),
            token_endpoint=str(value.get("token_endpoint") or "").strip(),
            userinfo_endpoint=str(value.get("userinfo_endpoint") or "").strip(),
            emails_endpoint=str(value.get("emails_endpoint") or "").strip(),
        )


@dataclass(frozen=True, slots=True)
class CloudConfig:
    database_path: Path
    public_url: str
    secret: str
    allowed_origins: tuple[str, ...]
    dev_mode: bool
    admin_email: str = ""
    admin_password: str = ""
    admin_display_name: str = "冰读管理员"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    providers: dict[str, OidcProvider] = field(default_factory=dict)


def _truthy(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


def _providers() -> dict[str, OidcProvider]:
    raw = os.getenv("BINGDU_CLOUD_OIDC_PROVIDERS", "[]")
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("BINGDU_CLOUD_OIDC_PROVIDERS must be valid JSON") from exc
    if not isinstance(values, list):
        raise RuntimeError("BINGDU_CLOUD_OIDC_PROVIDERS must be a JSON array")
    providers = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        provider = OidcProvider.from_dict(value)
        if provider.client_id and (provider.discovery_url or provider.authorization_endpoint):
            providers[provider.id] = provider
    github_id = os.getenv("BINGDU_GITHUB_CLIENT_ID", "").strip()
    if github_id:
        providers["github"] = OidcProvider(
            id="github", label="GitHub", kind="github",
            client_id=github_id,
            client_secret=os.getenv("BINGDU_GITHUB_CLIENT_SECRET", "").strip(),
            scopes=("read:user", "user:email"),
            authorization_endpoint="https://github.com/login/oauth/authorize",
            token_endpoint="https://github.com/login/oauth/access_token",
            userinfo_endpoint="https://api.github.com/user",
            emails_endpoint="https://api.github.com/user/emails",
        )
    return providers


def load_config() -> CloudConfig:
    public_url = os.getenv("BINGDU_CLOUD_PUBLIC_URL", "http://127.0.0.1:8010").rstrip("/")
    parsed = urlsplit(public_url)
    dev_mode = _truthy("BINGDU_CLOUD_DEV_MODE", parsed.hostname in {"127.0.0.1", "localhost", "::1"})
    secret = os.getenv("BINGDU_CLOUD_SECRET", "").strip()
    if not secret:
        if not dev_mode:
            raise RuntimeError("Production cloud service requires BINGDU_CLOUD_SECRET")
        secret = "bingdu-development-secret-change-before-deployment"
    if not dev_mode and len(secret) < 32:
        raise RuntimeError("Production BINGDU_CLOUD_SECRET must contain at least 32 characters")
    origins = tuple(
        value.strip().rstrip("/")
        for value in os.getenv("BINGDU_CLOUD_ALLOWED_ORIGINS", "").split(",")
        if value.strip()
    )
    database_path = Path(os.getenv("BINGDU_CLOUD_DATABASE", str(DATA_DIR / "cloud" / "cloud.sqlite3"))).resolve()
    return CloudConfig(
        database_path=database_path,
        public_url=public_url,
        secret=secret,
        allowed_origins=origins,
        dev_mode=dev_mode,
        admin_email=os.getenv("BINGDU_CLOUD_ADMIN_EMAIL", "").strip(),
        admin_password=os.getenv("BINGDU_CLOUD_ADMIN_PASSWORD", ""),
        admin_display_name=os.getenv("BINGDU_CLOUD_ADMIN_DISPLAY_NAME", "冰读管理员").strip() or "冰读管理员",
        smtp_host=os.getenv("BINGDU_SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("BINGDU_SMTP_PORT", "587")),
        smtp_username=os.getenv("BINGDU_SMTP_USERNAME", "").strip(),
        smtp_password=os.getenv("BINGDU_SMTP_PASSWORD", ""),
        smtp_from=os.getenv("BINGDU_SMTP_FROM", "").strip(),
        smtp_starttls=_truthy("BINGDU_SMTP_STARTTLS", True),
        providers=_providers(),
    )

