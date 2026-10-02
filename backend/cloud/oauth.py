from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx
from authlib.jose import JoseError, jwt

from .config import CloudConfig, OidcProvider
from .repository import CloudAuthError, CloudRepository


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def valid_local_return_url(value: str) -> bool:
    try:
        parsed = httpx.URL(value)
    except Exception:
        return False
    return parsed.scheme == "http" and parsed.host in {"127.0.0.1", "localhost", "::1"}


class OAuthService:
    def __init__(self, config: CloudConfig, repository: CloudRepository):
        self.config = config
        self.repository = repository
        self._metadata: dict[str, dict] = {}

    async def _provider_metadata(self, provider: OidcProvider) -> dict:
        if provider.kind == "github":
            return {
                "authorization_endpoint": provider.authorization_endpoint,
                "token_endpoint": provider.token_endpoint,
                "userinfo_endpoint": provider.userinfo_endpoint,
            }
        cached = self._metadata.get(provider.id)
        if cached:
            return cached
        if not provider.discovery_url:
            metadata = {
                "authorization_endpoint": provider.authorization_endpoint,
                "token_endpoint": provider.token_endpoint,
                "userinfo_endpoint": provider.userinfo_endpoint,
            }
        else:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                response = await client.get(provider.discovery_url)
                response.raise_for_status()
                metadata = response.json()
        for name in ("authorization_endpoint", "token_endpoint"):
            if not metadata.get(name):
                raise CloudAuthError(f"{provider.label} 缺少 {name}")
        self._metadata[provider.id] = metadata
        return metadata

    async def start(self, provider_id: str, return_url: str, device_id: str, device_name: str) -> str:
        provider = self.config.providers.get(provider_id)
        if not provider:
            raise CloudAuthError("第三方登录提供方不存在或尚未配置")
        if not valid_local_return_url(return_url):
            raise CloudAuthError("登录返回地址必须是本机 HTTP 地址")
        metadata = await self._provider_metadata(provider)
        verifier = secrets.token_urlsafe(48)
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        nonce = secrets.token_urlsafe(24)
        packed_return = httpx.URL(return_url).copy_add_param("device_id", device_id).copy_add_param("device_name", device_name)
        state = self.repository.create_oauth_state(provider.id, verifier, nonce, str(packed_return))
        query = {
            "client_id": provider.client_id,
            "redirect_uri": f"{self.config.public_url}/v1/auth/oidc/callback/{provider.id}",
            "response_type": "code", "scope": " ".join(provider.scopes),
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        }
        if provider.kind == "oidc":
            query["nonce"] = nonce
        return f"{metadata['authorization_endpoint']}?{urlencode(query)}"

    async def callback(self, provider_id: str, state: str, code: str) -> tuple[str, str]:
        provider = self.config.providers.get(provider_id)
        if not provider:
            raise CloudAuthError("第三方登录提供方不存在")
        stored = self.repository.consume_oauth_state(state, provider_id)
        metadata = await self._provider_metadata(provider)
        token_payload = {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": f"{self.config.public_url}/v1/auth/oidc/callback/{provider.id}",
            "client_id": provider.client_id, "code_verifier": stored["code_verifier"],
        }
        if provider.client_secret:
            token_payload["client_secret"] = provider.client_secret
        async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
            token_response = await client.post(
                metadata["token_endpoint"], data=token_payload,
                headers={"Accept": "application/json"},
            )
            token_response.raise_for_status()
            tokens = token_response.json()
            access_token = str(tokens.get("access_token") or "")
            if not access_token:
                raise CloudAuthError("第三方登录没有返回访问凭据")
            if provider.kind == "github":
                profile_response = await client.get(
                    provider.userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
                )
                profile_response.raise_for_status()
                profile = profile_response.json()
                emails_response = await client.get(
                    provider.emails_endpoint, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
                )
                emails_response.raise_for_status()
                verified = [item for item in emails_response.json() if item.get("verified")]
                primary = next((item for item in verified if item.get("primary")), verified[0] if verified else None)
                if not primary:
                    raise CloudAuthError("GitHub 账号没有可验证的邮箱")
                subject = str(profile.get("id") or "")
                email = str(primary["email"])
                name = str(profile.get("name") or profile.get("login") or email.split("@", 1)[0])
            else:
                id_token = str(tokens.get("id_token") or "")
                if not id_token:
                    raise CloudAuthError("OIDC 提供方没有返回 ID Token")
                jwks_uri = str(metadata.get("jwks_uri") or "")
                issuer = str(metadata.get("issuer") or "")
                if not jwks_uri or not issuer:
                    raise CloudAuthError("OIDC 发现文档缺少签名验证信息")
                jwks_response = await client.get(jwks_uri)
                jwks_response.raise_for_status()
                try:
                    claims = jwt.decode(
                        id_token, jwks_response.json(),
                        claims_options={
                            "iss": {"essential": True, "value": issuer},
                            "aud": {"essential": True, "value": provider.client_id},
                            "nonce": {"essential": True, "value": stored["nonce"]},
                        },
                    )
                    claims.validate()
                except JoseError as exc:
                    raise CloudAuthError("OIDC 身份凭据签名或声明验证失败") from exc
                if not claims.get("email") or claims.get("email_verified") is not True:
                    raise CloudAuthError("第三方账号必须提供已经验证的邮箱")
                subject = str(claims.get("sub") or "")
                email = str(claims["email"])
                name = str(claims.get("name") or claims.get("preferred_username") or email.split("@", 1)[0])
        if not subject:
            raise CloudAuthError("第三方登录缺少稳定用户标识")
        user = self.repository.upsert_oauth_user(provider.id, subject, email, name)
        return str(stored["return_url"]), str(user["id"])

    async def cancel(self, provider_id: str, state: str) -> str:
        if provider_id not in self.config.providers:
            raise CloudAuthError("第三方登录提供方不存在")
        stored = self.repository.consume_oauth_state(state, provider_id)
        return str(stored["return_url"])

