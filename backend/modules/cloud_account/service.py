from __future__ import annotations

import asyncio
import platform
import time
from urllib.parse import urlencode, urlsplit

import httpx

from ..sync import repository as sync_repository
from ..sync.projection import apply_remote_changes
from . import repository


def validate_cloud_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("云端服务地址必须是有效的 HTTP 或 HTTPS 地址")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("远程云端服务必须使用 HTTPS")
    return normalized


def device_name() -> str:
    return f"{platform.system()} · {platform.node() or '冰读设备'}"[:200]


async def _plain_request(method: str, path: str, **kwargs) -> httpx.Response:
    base_url = validate_cloud_url(await asyncio.to_thread(repository.get_base_url))
    async with httpx.AsyncClient(base_url=base_url, timeout=30, follow_redirects=False) as client:
        return await client.request(method, path, **kwargs)


def _error(response: httpx.Response) -> RuntimeError:
    try:
        detail = response.json().get("detail")
    except Exception:
        detail = response.text.strip()
    return RuntimeError(str(detail or f"云端服务返回 {response.status_code}"))


async def _authorized_request(local_user_id: str, method: str, path: str, **kwargs) -> httpx.Response:
    credentials = await asyncio.to_thread(repository.account_credentials, local_user_id)
    if not credentials:
        raise RuntimeError("尚未绑定云端账号")
    if float(credentials["access_expires_at"]) <= time.time() + 30:
        await refresh(local_user_id)
        credentials = await asyncio.to_thread(repository.account_credentials, local_user_id)
    headers = dict(kwargs.pop("headers", {}))
    headers["Authorization"] = f"Bearer {credentials['access_token']}"
    response = await _plain_request(method, path, headers=headers, **kwargs)
    if response.status_code == 401:
        await refresh(local_user_id)
        credentials = await asyncio.to_thread(repository.account_credentials, local_user_id)
        headers["Authorization"] = f"Bearer {credentials['access_token']}"
        response = await _plain_request(method, path, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise _error(response)
    return response


async def health() -> dict:
    response = await _plain_request("GET", "/health")
    if response.status_code >= 400:
        raise _error(response)
    data = response.json()
    if data.get("app") != "bingdu-cloud":
        raise RuntimeError("该地址不是冰读云端服务")
    return data


async def configure(base_url: str) -> dict:
    normalized = validate_cloud_url(base_url)
    previous = await asyncio.to_thread(repository.get_base_url)
    await asyncio.to_thread(repository.set_base_url, normalized)
    try:
        remote = await health()
    except Exception:
        await asyncio.to_thread(repository.set_base_url, previous)
        raise
    return {"base_url": normalized, "remote": remote}


async def register(local_user_id: str, device_id: str, email: str, password: str, display_name: str) -> dict:
    response = await _plain_request("POST", "/v1/auth/register", json={
        "email": email, "password": password, "display_name": display_name,
        "device_id": device_id, "device_name": device_name(),
    })
    if response.status_code >= 400:
        raise _error(response)
    payload = response.json()
    await asyncio.to_thread(repository.save_account, local_user_id, payload)
    return {**repository.account_status(local_user_id), "verification_delivery": payload.get("verification_delivery"), "development_verification_token": payload.get("development_verification_token")}


async def login(local_user_id: str, device_id: str, email: str, password: str) -> dict:
    response = await _plain_request("POST", "/v1/auth/login", json={
        "email": email, "password": password, "device_id": device_id, "device_name": device_name(),
    })
    if response.status_code >= 400:
        raise _error(response)
    await asyncio.to_thread(repository.save_account, local_user_id, response.json())
    return repository.account_status(local_user_id)


async def refresh(local_user_id: str) -> None:
    credentials = await asyncio.to_thread(repository.account_credentials, local_user_id)
    if not credentials:
        raise RuntimeError("尚未绑定云端账号")
    response = await _plain_request("POST", "/v1/auth/refresh", json={"refresh_token": credentials["refresh_token"]})
    if response.status_code >= 400:
        await asyncio.to_thread(repository.update_sync_state, local_user_id, error=str(_error(response)))
        raise _error(response)
    await asyncio.to_thread(repository.update_tokens, local_user_id, response.json())


async def logout(local_user_id: str) -> None:
    try:
        await _authorized_request(local_user_id, "POST", "/v1/auth/logout")
    finally:
        await asyncio.to_thread(repository.delete_account, local_user_id)


async def providers() -> list[dict]:
    response = await _plain_request("GET", "/v1/auth/providers")
    if response.status_code >= 400:
        raise _error(response)
    return response.json()


async def oidc_start_url(provider_id: str, device_id: str, local_callback: str) -> str:
    base = validate_cloud_url(await asyncio.to_thread(repository.get_base_url))
    query = urlencode({"return_url": local_callback,"device_id":device_id,"device_name":device_name()})
    return f"{base}/v1/auth/oidc/start/{provider_id}?{query}"


async def exchange_handoff(local_user_id: str, code: str) -> dict:
    response = await _plain_request("POST", "/v1/auth/oidc/exchange", json={"code": code})
    if response.status_code >= 400:
        raise _error(response)
    await asyncio.to_thread(repository.save_account, local_user_id, response.json())
    return repository.account_status(local_user_id)


async def request_verification(email: str) -> dict:
    response = await _plain_request("POST", "/v1/auth/email/request-verification", json={"email": email})
    if response.status_code >= 400:
        raise _error(response)
    return response.json()


async def verify_email(local_user_id: str, token: str) -> dict:
    response = await _plain_request("POST", "/v1/auth/email/verify", json={"token": token})
    if response.status_code >= 400:
        raise _error(response)
    await asyncio.to_thread(repository.update_account_user, local_user_id, response.json())
    return repository.account_status(local_user_id)


async def request_password_reset(email: str) -> dict:
    response = await _plain_request("POST", "/v1/auth/password/request-reset", json={"email": email})
    if response.status_code >= 400:
        raise _error(response)
    return response.json()


async def reset_password(token: str, new_password: str) -> dict:
    response = await _plain_request("POST", "/v1/auth/password/reset", json={"token": token,"new_password":new_password})
    if response.status_code >= 400:
        raise _error(response)
    return response.json()


async def cloud_sessions(local_user_id: str) -> list[dict]:
    return (await _authorized_request(local_user_id, "GET", "/v1/auth/sessions")).json()


async def revoke_cloud_session(local_user_id: str, session_id: str) -> None:
    await _authorized_request(local_user_id, "DELETE", f"/v1/auth/sessions/{session_id}")


async def admin_users(local_user_id: str, query: str, limit: int, offset: int) -> dict:
    return (await _authorized_request(
        local_user_id, "GET", "/v1/admin/users",
        params={"query": query, "limit": limit, "offset": offset},
    )).json()


async def admin_set_user_disabled(local_user_id: str, user_id: str, disabled: bool) -> dict:
    return (await _authorized_request(
        local_user_id, "PATCH", f"/v1/admin/users/{user_id}", json={"disabled": disabled},
    )).json()


async def sync(local_user_id: str, device_id: str, *, pull_only: bool = False) -> dict:
    account = await asyncio.to_thread(repository.account_credentials, local_user_id)
    if not account:
        raise RuntimeError("尚未绑定云端账号")
    uploaded = downloaded = applied = skipped = 0
    try:
        if not pull_only:
            local_cursor = int(account["local_cursor"])
            while True:
                batch = await asyncio.to_thread(sync_repository.list_changes_after, local_user_id, local_cursor, 500)
                if batch["changes"]:
                    response = await _authorized_request(local_user_id, "POST", "/v1/sync/push", json={"changes": batch["changes"]})
                    result = response.json()
                    uploaded += len(result.get("accepted", [])) + len(result.get("skipped", []))
                    local_cursor = int(batch["cursor"])
                    await asyncio.to_thread(repository.update_sync_state, local_user_id, local_cursor=local_cursor)
                if not batch["has_more"]:
                    break
        remote_cursor = int(account["remote_cursor"])
        while True:
            response = await _authorized_request(local_user_id, "GET", f"/v1/sync/pull?after={remote_cursor}&limit=500")
            batch = response.json()
            changes = batch.get("changes") or []
            if changes:
                await asyncio.to_thread(sync_repository.push_changes, local_user_id, device_id, changes)
                projected = await asyncio.to_thread(apply_remote_changes, local_user_id, device_id, changes)
                applied += projected["applied"]
                skipped += projected["skipped"]
                downloaded += len(changes)
                remote_cursor = int(batch["cursor"])
                await asyncio.to_thread(repository.update_sync_state, local_user_id, remote_cursor=remote_cursor)
            if not batch.get("has_more"):
                break
        await asyncio.to_thread(repository.update_sync_state, local_user_id, error=None, completed=True)
        remote_status = (await _authorized_request(local_user_id, "GET", "/v1/sync/status")).json()
        return {"uploaded":uploaded,"downloaded":downloaded,"applied":applied,"skipped":skipped,"remote":remote_status,**repository.account_status(local_user_id)}
    except Exception as exc:
        await asyncio.to_thread(repository.update_sync_state, local_user_id, error=str(exc))
        raise


async def conflicts(local_user_id: str) -> list[dict]:
    return (await _authorized_request(local_user_id, "GET", "/v1/sync/conflicts")).json()


async def resolve_conflict(local_user_id: str, group_id: str, winner_entity_id: str) -> dict:
    return (await _authorized_request(
        local_user_id, "POST", f"/v1/sync/conflicts/{group_id}/resolve",
        json={"winner_entity_id":winner_entity_id},
    )).json()

